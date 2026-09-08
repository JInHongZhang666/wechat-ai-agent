"""Official Hacker News API adapter for bounded AI topic discovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from wechat_agent.collectors.base import (
    CollectionResult,
    SourceRunStatus,
    SourceTier,
    failed_collection_result,
)
from wechat_agent.collectors.http import HttpGetter
from wechat_agent.collectors.utils import plain_text
from wechat_agent.domain.models import (
    ArticleRecord,
    EngagementMetrics,
    SourceKind,
    SourceType,
    TopicCategory,
)
from wechat_agent.domain.relevance import (
    AI_RELEVANCE_KEYWORDS,
    contains_relevance_keyword,
)


_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
_HN_AI_KEYWORDS = AI_RELEVANCE_KEYWORDS + (
    "machine learning",
    "neural network",
    "language model",
    "computer vision",
    "transformer",
)


class HackerNewsCollector:
    """Read HN's official API instead of depending on a third-party RSS proxy."""

    categories = frozenset({TopicCategory.INDUSTRY})

    def __init__(
        self,
        client: HttpGetter,
        *,
        limit: int = 15,
        scan_limit: int = 60,
        minimum_points: int = 20,
        max_item_workers: int = 8,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.name = "Hacker News AI"
        self.source_identity = "aggregator:hacker-news"
        self.source_id = self.source_identity
        self.limit = limit
        self.scan_limit = max(limit, scan_limit)
        self.minimum_points = minimum_points
        self.max_item_workers = max(1, max_item_workers)
        self.tier = SourceTier.T2
        self.feed_url = _TOP_STORIES_URL
        self.homepage = "https://news.ycombinator.com"
        self.kind = SourceKind.API
        self.publisher_group = self.source_identity
        self.evidence_eligible = True
        self.signal_only = False
        self.language = "en"
        self.enabled = enabled
        self.schedule_minutes = 30
        self.parser_version = "hacker-news-official-api-v1"

    def collect(self, since: datetime, now: datetime) -> CollectionResult:
        try:
            response = self.client.get(_TOP_STORIES_URL)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("topstories payload must be a list")
        except Exception as error:
            return failed_collection_result(self.name, error)

        item_ids: list[int] = []
        invalid_id_count = 0
        for raw_item_id in payload[: self.scan_limit]:
            try:
                item_ids.append(int(raw_item_id))
            except (TypeError, ValueError, OverflowError):
                invalid_id_count += 1

        workers = min(self.max_item_workers, max(1, len(item_ids)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            item_results = list(executor.map(self._fetch_item, item_ids))

        articles: list[ArticleRecord] = []
        inspected_count = 0
        failed_count = invalid_id_count
        for item, failed in item_results:
            if failed:
                failed_count += 1
                continue
            if item is None:
                continue
            inspected_count += 1
            article = self._to_article(item, since=since, now=now)
            if article is not None:
                articles.append(article)
            if len(articles) >= self.limit:
                break

        if failed_count and inspected_count == 0:
            return CollectionResult(
                warnings=(f"{self.name}条目接口全部失败：{failed_count} 条",),
                status=SourceRunStatus.FAILED,
                stage="fetch",
                http_status=getattr(response, "status_code", 200),
                fetched_count=0,
                skipped_count=failed_count,
                error_code="item_fetch_failed",
                error_summary="ItemFetchFailed",
            )

        is_partial = failed_count > 0
        return CollectionResult(
            articles=tuple(articles),
            warnings=(
                (f"{self.name}部分条目采集失败：{failed_count} 条",)
                if is_partial
                else ()
            ),
            status=(
                SourceRunStatus.PARTIAL if is_partial else SourceRunStatus.SUCCESS
            ),
            stage="complete",
            http_status=getattr(response, "status_code", 200),
            fetched_count=inspected_count,
            skipped_count=max(0, inspected_count - len(articles)) + failed_count,
            error_code="item_fetch_partial" if is_partial else None,
            error_summary="ItemFetchPartial" if is_partial else None,
        )

    def _fetch_item(self, item_id: int) -> tuple[dict[str, object] | None, bool]:
        try:
            response = self.client.get(_ITEM_URL.format(item_id=item_id))
            response.raise_for_status()
            payload = response.json()
            if payload is None:
                return None, False
            if not isinstance(payload, dict):
                raise ValueError("item payload must be an object")
            return payload, False
        except Exception:
            return None, True

    def _to_article(
        self,
        item: dict[str, object],
        *,
        since: datetime,
        now: datetime,
    ) -> ArticleRecord | None:
        if item.get("type") != "story" or item.get("deleted") or item.get("dead"):
            return None
        title = plain_text(str(item.get("title") or ""), limit=240)
        if not title or not contains_relevance_keyword(title, _HN_AI_KEYWORDS):
            return None
        try:
            item_id = int(item["id"])
            published_at = datetime.fromtimestamp(int(item["time"]), tz=UTC)
            score = max(0, int(item.get("score") or 0))
            comments = max(0, int(item.get("descendants") or 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if published_at < since or published_at > now or score < self.minimum_points:
            return None

        discussion_url = f"https://news.ycombinator.com/item?id={item_id}"
        raw_url = str(item.get("url") or "").strip()
        source_url = raw_url if raw_url.startswith(("http://", "https://")) else discussion_url
        return ArticleRecord(
            id=f"hn-{item_id}",
            source_id=self.source_id,
            title=title,
            source_name=self.name,
            source_url=source_url,
            published_at=published_at,
            collected_at=now,
            summary=(
                f"Hacker News submission with {score} points and "
                f"{comments} comments. Discussion: {discussion_url}"
            ),
            category=TopicCategory.INDUSTRY,
            source_confidence=0.75,
            source_type=SourceType.SECONDARY,
            source_identity=self.source_identity,
            source_type_verified=True,
            source_verification_basis=(
                "Submission metadata collected from the official Hacker News "
                "Firebase API; the linked article remains an external source."
            ),
            engagement=EngagementMetrics(
                comments=comments,
                verified_proxy_signals=("comments",),
            ),
            tier=self.tier.value,
        )
