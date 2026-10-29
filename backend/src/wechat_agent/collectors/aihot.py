"""Optional AIHOT JSON collector for broad recent-news discovery.

This adapter is deliberately isolated from the conversational Agent extension.
Removing this file and its single registration block in ``defaults.py`` removes
AIHOT ingestion without changing the local scoring or curation pipeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
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
    SourceRunStatus,
    SourceType,
    TopicCategory,
)


AIHOT_ITEMS_URL = "https://aihot.news/api/v1/items"
AIHOT_USER_AGENT = "aihot-api/2.0.0"

_CATEGORY_MAP: dict[str, TopicCategory] = {
    "ai-models": TopicCategory.AI_MODELS,
    "ai-products": TopicCategory.AI_PRODUCTS,
    "industry": TopicCategory.INDUSTRY,
    "paper": TopicCategory.PAPER,
    "tip": TopicCategory.TIP,
    "opinion": TopicCategory.TIP,
}


class AihotCollector:
    """Collect recent AIHOT items as candidates for the local pipeline.

    AIHOT is a discovery channel, not an independent factual authority. Every
    returned item is therefore assigned T2/secondary provenance and still goes
    through the application's own pre-screen, scoring and curation rules.
    """

    categories = frozenset(_CATEGORY_MAP.values())

    def __init__(
        self,
        client: HttpGetter,
        *,
        mode: Literal["all", "selected"] = "all",
        limit: int = 100,
        enabled: bool = True,
        schedule_minutes: int = 30,
    ) -> None:
        if mode not in {"all", "selected"}:
            raise ValueError("mode must be 'all' or 'selected'")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        self.client = client
        self.name = "AIHOT 资讯发现"
        self.source_identity = "aggregator:aihot"
        self.source_id = self.source_identity
        self.tier = SourceTier.T2
        self.feed_url = AIHOT_ITEMS_URL
        self.homepage = "https://aihot.news"
        self.kind = SourceKind.API
        self.publisher_group = self.source_identity
        self.evidence_eligible = False
        self.signal_only = True
        self.language = "zh-CN"
        self.enabled = enabled
        self.schedule_minutes = schedule_minutes
        self.parser_version = "aihot-items-v1"
        self.mode = mode
        self.limit = limit
        self._etag: str | None = None

    def collect(self, since: datetime, now: datetime) -> CollectionResult:
        headers = {
            "Accept": "application/json",
            "User-Agent": AIHOT_USER_AGENT,
        }
        if self._etag:
            headers["If-None-Match"] = self._etag

        try:
            response = self.client.get(
                self.feed_url,
                params={
                    "mode": self.mode,
                    "window": "7d",
                    "by": "timeline",
                    "limit": self.limit,
                },
                headers=headers,
            )
            if response.status_code == 304:
                return CollectionResult(http_status=304, fetched_count=0)
            response.raise_for_status()
            payload = response.json()
            raw_items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(raw_items, list):
                raise ValueError("AIHOT items response must contain an items list")
        except Exception as error:
            return failed_collection_result(self.name, error)

        response_headers = getattr(response, "headers", {})
        etag = response_headers.get("ETag") if response_headers is not None else None
        if isinstance(etag, str) and etag.strip():
            self._etag = etag.strip()

        articles: list[ArticleRecord] = []
        skipped = 0
        for raw_item in raw_items:
            article = self._to_article(raw_item, since=since, now=now)
            if article is None:
                skipped += 1
                continue
            articles.append(article)

        warnings: tuple[str, ...] = ()
        status = SourceRunStatus.SUCCESS
        if skipped:
            warnings = (f"{self.name}跳过 {skipped} 条无效、未知分类或窗口外条目。",)
            status = SourceRunStatus.PARTIAL if articles else SourceRunStatus.FAILED

        return CollectionResult(
            articles=tuple(articles),
            warnings=warnings,
            status=status,
            stage="complete" if articles or not skipped else "parse",
            http_status=response.status_code,
            fetched_count=len(raw_items),
            skipped_count=skipped,
            error_code="item_parse_partial" if skipped and articles else (
                "invalid_items" if skipped else None
            ),
            error_summary="InvalidAihotItems" if skipped else None,
        )

    def _to_article(
        self,
        item: object,
        *,
        since: datetime,
        now: datetime,
    ) -> ArticleRecord | None:
        if not isinstance(item, dict):
            return None

        item_id = str(item.get("id") or "").strip()
        title = plain_text(str(item.get("title") or ""), limit=240)
        source = item.get("source")
        links = item.get("links")
        # AIHOT categories are nullable and may grow over time. Keep unknown
        # values in the candidate pool as industry news; the local classifier
        # will assign the final category before scoring.
        category = _CATEGORY_MAP.get(
            str(item.get("category") or ""),
            TopicCategory.INDUSTRY,
        )
        if (
            not item_id
            or not title
            or not isinstance(source, dict)
            or not isinstance(links, dict)
        ):
            return None

        source_name = plain_text(str(source.get("name") or ""), limit=160)
        original_url = str(links.get("original") or "").strip()
        parsed_url = urlsplit(original_url)
        if (
            not source_name
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
        ):
            return None

        discovered_at = parse_datetime(str(item.get("discoveredAt") or ""))
        published_at = parse_datetime(str(item.get("publishedAt") or ""))
        if discovered_at is None:
            return None
        effective_published_at = published_at or discovered_at
        # The API's timeline can legitimately surface an older article when it
        # is newly discovered/admitted. Keep it when either timestamp is in the
        # requested local window, while rejecting future-dated records.
        if effective_published_at > now or discovered_at > now:
            return None
        if effective_published_at < since and discovered_at < since:
            return None

        summary = plain_text(str(item.get("summary") or ""))
        if not summary:
            summary = plain_text(str(item.get("reason") or title))
        host = parsed_url.hostname.casefold()
        return ArticleRecord(
            id=stable_id("aihot", original_url),
            source_id=self.source_id,
            title=title,
            source_name=source_name,
            source_url=original_url,
            published_at=effective_published_at,
            collected_at=now,
            summary=summary,
            category=category,
            source_confidence=0.65,
            source_type=SourceType.SECONDARY,
            source_identity=f"external:{host}",
            source_type_verified=False,
            source_verification_basis=(
                f"Discovered through AIHOT public API; original link host is {host}. "
                "The source identity and article claims have not been independently verified."
            ),
            tier=self.tier.value,
        )
