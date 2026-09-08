"""Translate collector configuration into persistent source seed records."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from wechat_agent.domain.ports import SourceRepository

from wechat_agent.domain.models import (
    CircuitState,
    Source,
    SourceKind,
    SourceTier,
    TopicCategory,
)


def sources_from_live_collector(
    live_collector: object | None,
    *,
    now: datetime | None = None,
) -> list[Source]:
    """Return deterministic seeds without assuming a concrete live collector."""
    collectors = getattr(live_collector, "collectors", ())
    seeded_at = now or datetime.now(UTC)
    sources = [_source_from_collector(collector, seeded_at) for collector in collectors]
    by_id = {source.id: source for source in sources}
    return [by_id[source_id] for source_id in sorted(by_id)]


def _source_from_collector(collector: Any, seeded_at: datetime) -> Source:
    name = str(getattr(collector, "name", "Unnamed source"))
    source_id = str(
        getattr(collector, "source_id", None)
        or getattr(collector, "source_identity", None)
        or f"collector:{_slug(name)}"
    )
    categories = tuple(
        sorted(
            (
                category
                if isinstance(category, TopicCategory)
                else TopicCategory(str(category))
                for category in getattr(collector, "categories", ())
            ),
            key=lambda category: category.value,
        )
    )
    raw_tier = getattr(collector, "tier", SourceTier.T2)
    tier = raw_tier if isinstance(raw_tier, SourceTier) else SourceTier(str(raw_tier))
    raw_kind = getattr(collector, "kind", SourceKind.RSS)
    kind = raw_kind if isinstance(raw_kind, SourceKind) else SourceKind(str(raw_kind))
    return Source(
        id=source_id,
        name=name,
        homepage=getattr(collector, "homepage", None),
        feed_url=getattr(collector, "feed_url", None),
        kind=kind,
        tier=tier,
        publisher_group=str(
            getattr(collector, "publisher_group", None) or source_id
        ),
        evidence_eligible=bool(
            getattr(collector, "evidence_eligible", True)
        ),
        signal_only=bool(getattr(collector, "signal_only", False)),
        categories=categories,
        language=str(getattr(collector, "language", "auto")),
        schedule_minutes=int(getattr(collector, "schedule_minutes", 30)),
        enabled=bool(getattr(collector, "enabled", True)),
        parser_version=str(getattr(collector, "parser_version", "unknown")),
        circuit_state=CircuitState.CLOSED,
        created_at=seeded_at,
        updated_at=seeded_at,
    )


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "unknown"


def apply_source_registry_state(
    live_collector: object | None,
    source_repository: SourceRepository,
) -> None:
    """Make persistent operator enable/tier settings control live execution."""
    now = datetime.now(UTC)
    for collector in getattr(live_collector, "collectors", ()):
        source_id = str(
            getattr(collector, "source_id", None)
            or getattr(collector, "source_identity", None)
            or ""
        )
        source = source_repository.get(source_id)
        if source is None:
            continue
        circuit_allows_run = (
            source.circuit_state is not CircuitState.OPEN
            or source.next_run_at is None
            or source.next_run_at <= now
        )
        collector.enabled = source.enabled and circuit_allows_run
        collector.tier = source.tier
        collector.schedule_minutes = source.schedule_minutes
