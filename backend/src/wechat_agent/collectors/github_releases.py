"""High-signal official GitHub release collector.

The public Releases API is used instead of repository activity feeds so the
distribution layer receives stable product releases rather than nightly build
noise, pull requests, or ordinary commits.
"""

from __future__ import annotations

from datetime import datetime
import os
import re
from urllib.parse import urlsplit

from wechat_agent.collectors.base import (
    CollectionResult,
    SourceTier,
    failed_collection_result,
)
from wechat_agent.collectors.http import HttpGetter
from wechat_agent.collectors.utils import parse_datetime, plain_text, stable_id
from wechat_agent.domain.models import (
    ArticleRecord,
    SourceKind,
    SourceType,
    TopicCategory,
)


_UNSTABLE_RELEASE = re.compile(
    r"(?:^|[.\-_\s])(?:nightly|alpha|beta|rc|dev|canary)(?:[.\-_\s]|\d|$)",
    re.IGNORECASE,
)


class GitHubReleaseCollector:
    """Collect stable releases from a first-party GitHub repository."""

    def __init__(
        self,
        client: HttpGetter,
        *,
        name: str,
        repository: str,
        source_identity: str,
        category: TopicCategory,
        limit: int = 10,
        tier: SourceTier = SourceTier.T1,
        publisher_group: str | None = None,
        language: str = "en",
        enabled: bool = True,
    ) -> None:
        if repository.count("/") != 1:
            raise ValueError("repository must use the owner/name form")
        self.client = client
        self.name = name
        self.repository = repository
        self.source_identity = source_identity
        self.source_id = source_identity
        self._category = category
        self.categories = frozenset({category})
        self.limit = limit
        self.tier = tier
        self.feed_url = (
            f"https://api.github.com/repos/{repository}/releases?per_page=30"
        )
        self.homepage = f"https://github.com/{repository}"
        self.kind = SourceKind.API
        self.publisher_group = publisher_group or source_identity
        self.evidence_eligible = True
        self.signal_only = False
        self.language = language
        self.enabled = enabled
        self.schedule_minutes = {
            SourceTier.T1: 10,
            SourceTier.T1_5: 15,
            SourceTier.T2: 30,
        }[tier]
        self.parser_version = "github-stable-releases-v1"

    def collect(self, since: datetime, now: datetime) -> CollectionResult:
        try:
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            token = os.getenv("GITHUB_TOKEN", "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            response = self.client.get(
                self.feed_url,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("GitHub releases response must be a list")
        except Exception as error:
            return failed_collection_result(self.name, error)

        articles: list[ArticleRecord] = []
        for release in payload:
            article = self._to_article(release, now)
            if (
                article is not None
                and since <= article.published_at <= now
            ):
                articles.append(article)
            if len(articles) >= self.limit:
                break
        return CollectionResult(
            articles=tuple(articles),
            http_status=getattr(response, "status_code", 200),
            fetched_count=len(payload),
            skipped_count=max(0, len(payload) - len(articles)),
        )

    def _to_article(
        self,
        release: object,
        now: datetime,
    ) -> ArticleRecord | None:
        if not isinstance(release, dict):
            return None
        if release.get("draft") or release.get("prerelease"):
            return None
        title = plain_text(
            str(release.get("name") or release.get("tag_name") or ""),
            limit=240,
        )
        if not title or _UNSTABLE_RELEASE.search(title):
            return None
        link = str(release.get("html_url") or "").strip()
        published_at = parse_datetime(
            str(release.get("published_at") or release.get("created_at") or "")
        )
        if not link or published_at is None:
            return None
        description = plain_text(str(release.get("body") or ""))
        host = urlsplit(link).hostname or "github.com"
        summary = plain_text(
            f"{self.name} published stable release {title} on "
            f"{published_at.date().isoformat()}. {description or title}"
        )
        source_type = {
            SourceTier.T1: SourceType.OFFICIAL,
            SourceTier.T1_5: SourceType.PRIMARY,
            SourceTier.T2: SourceType.SECONDARY,
        }[self.tier]
        return ArticleRecord(
            id=stable_id("github-release", link),
            source_id=self.source_id,
            title=title,
            source_name=self.name,
            source_url=link,
            published_at=published_at,
            collected_at=now,
            summary=summary,
            category=self._category,
            source_confidence=0.95,
            source_type=source_type,
            source_identity=self.source_identity,
            source_type_verified=True,
            source_verification_basis=(
                f"{self.repository} official GitHub Releases API "
                f"{self.feed_url}; release link host is {host}."
            ),
            tier=self.tier.value,
        )
