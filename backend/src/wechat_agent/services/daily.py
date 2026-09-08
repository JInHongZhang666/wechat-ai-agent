"""Durable daily-report generation following AIHOT's pre-processing model.

Reports are immutable revisions over an Asia/Shanghai calendar day.  Web,
JSON API, and RSS all read the same persisted payload; ingestion creates a new
revision only when the deterministic content hash changes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any, Sequence
from zoneinfo import ZoneInfo

from wechat_agent.domain.models import (
    ArticleRecord,
    DailyReport,
    DailyReportStatus,
    public_category_slug,
)

if TYPE_CHECKING:
    from wechat_agent.api.routes import ContentApiService


REPORT_TIMEZONE_NAME = "Asia/Shanghai"
REPORT_TIMEZONE = ZoneInfo(REPORT_TIMEZONE_NAME)

_CATEGORY_LABELS = {
    "ai-models": "AI 模型发布/更新",
    "ai-products": "AI 产品发布/更新",
    "industry": "行业动态",
    "paper": "论文研究",
    "tip": "技巧与观点",
}


def generate_daily_report(
    service: ContentApiService,
    report_date: date | None = None,
) -> dict[str, Any]:
    """Return the latest persisted revision, materializing only if absent."""
    repository = service.daily_report_repository
    if report_date is None and repository is not None:
        latest = repository.latest()
        if latest is not None:
            return daily_report_payload(latest)
    target_date = report_date or _latest_available_date(service)
    return materialize_daily_report(service, target_date, rebuild=False)


def generate_daily_for_date(
    service: ContentApiService,
    date_str: str,
) -> dict[str, Any] | None:
    """Return one persisted report revision for a YYYY-MM-DD date."""
    try:
        report_date = date.fromisoformat(date_str)
    except ValueError:
        return None
    return materialize_daily_report(service, report_date, rebuild=False)


def materialize_daily_report(
    service: ContentApiService,
    report_date: date,
    *,
    rebuild: bool,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Create an idempotent immutable revision and return its public payload."""
    with service.lock:
        repository = service.daily_report_repository
        existing = repository.get(report_date) if repository is not None else None
        if existing is not None and not rebuild:
            return daily_report_payload(existing)

        effective_generated_at = (generated_at or datetime.now(UTC)).astimezone(UTC)
        rows = _get_articles_for_date(service, report_date)
        sections = _bucket_by_category(rows)
        flashes = _extract_flashes(rows[:10])
        lead = _generate_lead(rows)
        strategies = sorted({decision.strategy_version for _, decision in rows})
        strategy_version = ",".join(strategies) or "distribution-v3"
        snapshot_candidates = [
            timestamp
            for article, decision in rows
            for timestamp in (article.collected_at, decision.scored_at)
        ]
        source_snapshot_at = max(
            snapshot_candidates,
            default=_report_bounds(report_date)[1],
        ).astimezone(UTC)
        payload: dict[str, object] = {
            "sections": sections,
            "flashes": flashes,
        }
        content_hash = _content_hash(
            report_date=report_date,
            lead=lead,
            payload=payload,
            strategy_version=strategy_version,
        )
        if existing is not None and existing.content_hash == content_hash:
            return daily_report_payload(existing)

        report = DailyReport(
            date=report_date,
            revision=(existing.revision + 1 if existing is not None else 1),
            status=DailyReportStatus.PUBLISHED,
            lead=lead,
            section_item_ids={
                section["slug"]: tuple(
                    item["id"] for item in section["items"]
                )
                for section in sections
            },
            flash_item_ids=tuple(article.id for article, _ in rows[:10]),
            payload=payload,
            strategy_version=strategy_version,
            source_snapshot_at=source_snapshot_at,
            generated_at=effective_generated_at,
            published_at=effective_generated_at,
            item_count=len(rows),
            source_count=len(
                {
                    article.source_id or article.source_name
                    for article, _ in rows
                }
            ),
            content_hash=content_hash,
        )
        if repository is not None:
            repository.save(report)
        return daily_report_payload(report)


def ensure_recent_daily_reports(
    service: ContentApiService,
    *,
    limit: int = 30,
) -> list[DailyReport]:
    """Backfill missing report dates once during service initialization."""
    cutoff = datetime.now(UTC) - timedelta(days=max(limit + 2, 32))
    dates = sorted(
        {
            article.published_at.astimezone(REPORT_TIMEZONE).date()
            for article, decision in service.list_distribution_items(
                cutoff,
                limit=5000,
            )
            if decision.selected and decision.eligible
        },
        reverse=True,
    )[:limit]
    for report_date in dates:
        materialize_daily_report(service, report_date, rebuild=False)
    return list_daily_reports(service, limit=limit)


def refresh_daily_reports_for_articles(
    service: ContentApiService,
    articles: Sequence[ArticleRecord],
) -> None:
    """Refresh affected dates after ingestion, revising only changed content."""
    report_dates = sorted(
        {
            article.published_at.astimezone(REPORT_TIMEZONE).date()
            for article in articles
        }
    )
    for report_date in report_dates:
        existing = (
            service.daily_report_repository.get(report_date)
            if service.daily_report_repository is not None
            else None
        )
        if existing is None and not _get_articles_for_date(service, report_date):
            continue
        materialize_daily_report(service, report_date, rebuild=True)


def list_daily_reports(
    service: ContentApiService,
    *,
    limit: int = 30,
) -> list[DailyReport]:
    repository = service.daily_report_repository
    if repository is None:
        return []
    return repository.list_recent(limit=limit)


def daily_report_payload(report: DailyReport) -> dict[str, Any]:
    """Serialize the stored revision without recalculating any report content."""
    return {
        "schemaVersion": 1,
        "date": report.date.isoformat(),
        "timezone": report.timezone,
        "revision": report.revision,
        "status": report.status.value,
        "lead": report.lead,
        "sections": report.payload.get("sections", []),
        "flashes": report.payload.get("flashes", []),
        "strategyVersion": report.strategy_version,
        "sourceSnapshotAt": report.source_snapshot_at.isoformat(),
        "generatedAt": report.generated_at.isoformat(),
        "publishedAt": (
            report.published_at.isoformat()
            if report.published_at is not None
            else None
        ),
        "itemCount": report.item_count,
        "sourceCount": report.source_count,
        "contentHash": report.content_hash,
    }


def _get_articles_for_date(
    service: ContentApiService,
    report_date: date,
):
    day_start, day_end = _report_bounds(report_date)
    rows = service.list_distribution_items(day_start, limit=5000)
    return sorted(
        [
            (article, decision)
            for article, decision in rows
            if (
                decision.selected
                and decision.eligible
                and article.published_at < day_end
            )
        ],
        key=lambda row: (row[0].published_at, row[0].collected_at, row[0].id),
        reverse=True,
    )


def _report_bounds(report_date: date) -> tuple[datetime, datetime]:
    local_start = datetime.combine(report_date, time.min, tzinfo=REPORT_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def _latest_available_date(service: ContentApiService) -> date:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    dates = [
        article.published_at.astimezone(REPORT_TIMEZONE).date()
        for article, decision in service.list_distribution_items(cutoff, limit=5000)
        if decision.selected and decision.eligible
    ]
    return max(dates, default=datetime.now(REPORT_TIMEZONE).date())


def _bucket_by_category(rows: list) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, object]]] = {
        slug: [] for slug in _CATEGORY_LABELS
    }
    for article, decision in rows:
        slug = public_category_slug(article.category)
        if slug not in buckets:
            continue
        buckets[slug].append(
            {
                "id": article.id,
                "title": article.title,
                "originalTitle": None,
                "summary": article.summary,
                "links": {
                    "aihot": article.source_url,
                    "original": article.source_url,
                },
                "source": {
                    "name": article.source_name,
                    "tier": {
                        "t1": "T1",
                        "t1_5": "T1.5",
                        "t2": "T2",
                    }.get(article.tier or ""),
                    "type": article.source_type.value,
                },
                "publishedAt": article.published_at.isoformat(),
                "discoveredAt": article.collected_at.isoformat(),
                "timelineAt": article.published_at.isoformat(),
                "category": slug,
                "score": decision.distribution_score,
                "selected": decision.selected,
                "reason": decision.reason,
            }
        )
    return [
        {
            "label": _CATEGORY_LABELS[slug],
            "slug": slug,
            "count": len(items),
            "items": items[:8],
        }
        for slug, items in buckets.items()
        if items
    ]


def _extract_flashes(rows: list) -> list[dict[str, object]]:
    return [
        {
            "title": article.title,
            "links": {
                "aihot": None,
                "original": article.source_url,
            },
        }
        for article, _ in rows
    ]


def _generate_lead(rows: list) -> str:
    if not rows:
        return "今日暂无 AI 行业动态更新。"
    sources = {article.source_id or article.source_name for article, _ in rows}
    return (
        f"今日 AI 信息小屋共收录 {len(rows)} 条 AI 行业动态，"
        f"来自 {len(sources)} 个信源。涵盖模型发布、产品更新、"
        f"行业事件及论文研究等领域。"
    )


def _content_hash(
    *,
    report_date: date,
    lead: str,
    payload: dict[str, object],
    strategy_version: str,
) -> str:
    material = json.dumps(
        {
            "date": report_date.isoformat(),
            "timezone": REPORT_TIMEZONE_NAME,
            "lead": lead,
            "payload": payload,
            "strategyVersion": strategy_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(material.encode("utf-8")).hexdigest()
