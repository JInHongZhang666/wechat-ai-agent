"""Collector contracts and immutable collection results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import ceil
from typing import Literal, Protocol
from xml.etree import ElementTree as ET

import httpx

from wechat_agent.domain.models import (
    ArticleRecord,
    SourceRun,
    SourceRunStatus,
    SourceTier,
    TopicCategory,
)


@dataclass(frozen=True)
class CollectionResult:
    """Records and non-fatal diagnostics returned by one source."""

    articles: tuple[ArticleRecord, ...] = ()
    warnings: tuple[str, ...] = ()
    status: SourceRunStatus = SourceRunStatus.SUCCESS
    stage: Literal["fetch", "parse", "collect", "complete"] = "complete"
    http_status: int | None = None
    fetched_count: int | None = None
    skipped_count: int = 0
    error_code: str | None = None
    error_summary: str | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class LiveCollectionResult:
    """Combined 7-day radar, with an explicit 30-day fallback when required."""

    articles: tuple[ArticleRecord, ...] = ()
    warnings: tuple[str, ...] = ()
    window_days: int = 7
    expanded_categories: tuple[TopicCategory, ...] = ()
    source_counts: dict[str, int] = field(default_factory=dict)
    source_runs: tuple[SourceRun, ...] = ()


class SourceCollector(Protocol):
    """One bounded, public source adapter."""

    name: str
    categories: frozenset[TopicCategory]
    tier: SourceTier
    enabled: bool

    def collect(self, since: datetime, now: datetime) -> CollectionResult: ...


class LiveCollector(Protocol):
    def collect(self, now: datetime) -> LiveCollectionResult: ...


def failed_collection_result(
    source_name: str,
    error: Exception,
) -> CollectionResult:
    """Return a bounded, stable failure record without leaking request details."""
    http_status: int | None = None
    retry_after_seconds: int | None = None
    if isinstance(error, httpx.HTTPStatusError):
        http_status = error.response.status_code
        error_code = f"http_{http_status}"
        retry_after_seconds = _retry_after_seconds(error.response)
        stage = "fetch"
    elif isinstance(error, httpx.TimeoutException):
        error_code = "http_timeout"
        stage = "fetch"
    elif isinstance(error, ET.ParseError):
        error_code = "parse_error"
        stage = "parse"
    elif isinstance(error, httpx.RequestError):
        error_code = "network_error"
        stage = "fetch"
    else:
        error_code = "collector_error"
        stage = "collect"

    error_type = type(error).__name__
    return CollectionResult(
        warnings=(f"{source_name}采集失败：{error_type}",),
        status=SourceRunStatus.FAILED,
        stage=stage,
        fetched_count=0,
        http_status=http_status,
        error_code=error_code,
        error_summary=error_type,
        retry_after_seconds=retry_after_seconds,
    )


def _retry_after_seconds(response: httpx.Response) -> int | None:
    raw_value = response.headers.get("Retry-After")
    if raw_value is None:
        return None
    value = raw_value.strip()
    try:
        seconds = int(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = ceil((retry_at - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return min(max(seconds, 0), 86400)


def merge_by_url(
    groups: Sequence[Sequence[ArticleRecord]],
) -> tuple[ArticleRecord, ...]:
    """Keep the newest collected version of each canonical public URL."""
    by_url: dict[str, ArticleRecord] = {}
    for group in groups:
        for article in group:
            existing = by_url.get(article.source_url)
            if existing is None or article.collected_at >= existing.collected_at:
                by_url[article.source_url] = article
    return tuple(
        sorted(
            by_url.values(),
            key=lambda item: (item.published_at, item.id),
            reverse=True,
        )
    )
