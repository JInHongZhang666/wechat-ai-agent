"""RSS/Atom adapter for first-party AI announcements."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

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
from wechat_agent.domain.relevance import contains_relevance_keyword


class OfficialFeedCollector:
    categories = frozenset({TopicCategory.NEWS})

    def __init__(
        self,
        client: HttpGetter,
        *,
        name: str,
        url: str,
        source_identity: str,
        category: TopicCategory = TopicCategory.NEWS,
        limit: int = 20,
        tier: SourceTier = SourceTier.T1,
        relevance_keywords: tuple[str, ...] = (),
        publisher_group: str | None = None,
        language: str = "auto",
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.name = name
        self.url = url
        self.source_identity = source_identity
        self.source_id = source_identity
        self._category = category
        self.categories = frozenset({category})
        self.limit = limit
        self.tier = tier
        self.feed_url = url
        url_parts = urlsplit(url)
        self.homepage = (
            f"{url_parts.scheme}://{url_parts.netloc}"
            if url_parts.scheme and url_parts.netloc
            else None
        )
        self.kind = SourceKind.RSS
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
        self.parser_version = "rss-atom-v1"
        self.relevance_keywords = tuple(
            keyword.casefold() for keyword in relevance_keywords
        )

    def collect(self, since: datetime, now: datetime) -> CollectionResult:
        try:
            response = self.client.get(self.url)
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception as error:
            return failed_collection_result(self.name, error)

        articles: list[ArticleRecord] = []
        entries = _entries(root)
        for entry in entries:
            article = self._to_article(entry, now)
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
            fetched_count=len(entries),
            skipped_count=max(0, len(entries) - len(articles)),
        )

    def _to_article(
        self, entry: ET.Element, now: datetime
    ) -> ArticleRecord | None:
        title = plain_text(_child_text(entry, "title"), limit=240)
        link = _entry_link(entry)
        published_at = parse_datetime(
            _child_text(entry, "pubDate")
            or _child_text(entry, "published")
            or _child_text(entry, "updated")
        )
        if not title or not link or published_at is None:
            return None
        description = plain_text(
            _child_text(entry, "description")
            or _child_text(entry, "summary")
            or _child_text(entry, "content")
        )
        host = urlsplit(link).hostname or self.source_identity
        summary = plain_text(
            f"{self.name} published this announcement on "
            f"{published_at.date().isoformat()}. {description or title}"
        )
        if self.relevance_keywords and not contains_relevance_keyword(
            title,
            self.relevance_keywords,
        ):
            return None
        source_type = {
            SourceTier.T1: SourceType.OFFICIAL,
            SourceTier.T1_5: SourceType.PRIMARY,
            SourceTier.T2: SourceType.SECONDARY,
        }[self.tier]
        return ArticleRecord(
            id=stable_id("feed", link),
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
                f"{self.name} 官方 RSS/Atom 订阅源 {self.url}；"
                f"文章链接域名为 {host}。"
            ),
            tier=self.tier.value,
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _entries(root: ET.Element) -> list[ET.Element]:
    return [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]


def _child_text(entry: ET.Element, name: str) -> str | None:
    for child in entry:
        if _local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return None


def _entry_link(entry: ET.Element) -> str | None:
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href and href.strip():
            return href.strip()
        text = (child.text or "").strip()
        if text:
            return text
    return None
