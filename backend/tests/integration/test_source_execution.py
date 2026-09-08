from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
import httpx

from wechat_agent.api.routes import ApiError, ContentApiService
from wechat_agent.collectors.base import CollectionResult
from wechat_agent.collectors.live import LiveRadarCollector
from wechat_agent.collectors.registry import sources_from_live_collector
from wechat_agent.domain.models import (
    ArticleRecord,
    CircuitState,
    SourceKind,
    SourceTier,
    TopicCategory,
)
from wechat_agent.services.pipeline import ContentPipeline
from wechat_agent.services.reliability import build_source_reliability_report
from wechat_agent.services.source_network import SourceNetworkCoordinator
from wechat_agent.storage.memory import (
    InMemoryArticleRepository,
    InMemorySourceRepository,
    InMemorySourceRunRepository,
)
from wechat_agent.writing.provider import RuleBasedDraftProvider


class ManagedCollector:
    kind = SourceKind.RSS
    tier = SourceTier.T1
    categories = frozenset({TopicCategory.AI_MODELS})
    evidence_eligible = True
    signal_only = False
    language = "en"
    schedule_minutes = 10
    parser_version = "managed-test-v1"
    enabled = True

    def __init__(self, source_id: str, *, blocking: bool = False) -> None:
        self.source_id = source_id
        self.source_identity = source_id
        self.publisher_group = source_id
        self.name = source_id
        self.feed_url = f"https://example.com/{source_id}.xml"
        self.homepage = "https://example.com"
        self.calls: list[int] = []
        self.blocking = blocking
        self.started = Event()
        self.release = Event()

    def collect(self, since, now):
        self.calls.append((now - since).days)
        if self.blocking:
            self.started.set()
            assert self.release.wait(timeout=5)
        return CollectionResult(
            articles=(
                ArticleRecord(
                    id=f"article:{self.source_id}",
                    source_id=self.source_id,
                    title=f"AI model update from {self.source_id}",
                    source_name=self.name,
                    source_url=f"https://example.com/{self.source_id}/article",
                    published_at=now - timedelta(minutes=1),
                    collected_at=now,
                    summary="A verified official AI model update.",
                    category=TopicCategory.AI_MODELS,
                    source_confidence=1.0,
                    source_type="official",
                    source_identity=self.source_id,
                    source_type_verified=True,
                    source_verification_basis="Managed collector fixture.",
                    tier=self.tier.value,
                ),
            ),
            http_status=200,
            fetched_count=1,
        )


class FailingManagedCollector(ManagedCollector):
    def collect(self, since, now):
        self.calls.append((now - since).days)
        raise httpx.ReadTimeout("shared outbound network timeout")


def _service(collectors):
    live = LiveRadarCollector(list(collectors), target_per_category=0)
    source_repository = InMemorySourceRepository()
    source_repository.seed_many(sources_from_live_collector(live))
    run_repository = InMemorySourceRunRepository()
    service = ContentApiService(
        ContentPipeline(InMemoryArticleRepository(), enable_pre_screen=False),
        Path("unused-live-fixture.json"),
        RuleBasedDraftProvider(),
        live,
        source_repo=source_repository,
        source_run_repo=run_repository,
    )
    return service, source_repository, run_repository


def test_scheduled_collection_claims_only_due_sources_and_releases_leases():
    due = ManagedCollector("official:due")
    future = ManagedCollector("official:future")
    service, sources, runs = _service([due, future])
    now = datetime.now(UTC)
    future_source = sources.get(future.source_id)
    assert future_source is not None
    sources.save(
        future_source.model_copy(
            update={"next_run_at": now + timedelta(hours=1)}
        )
    )

    task = service.create_scheduled_live_task()

    assert task is not None
    assert task.mode == "scheduled"
    assert due.calls == [7]
    assert future.calls == []
    assert len(runs.list_recent(due.source_id, now - timedelta(minutes=1))) == 1
    assert sources.get(due.source_id).lease_owner is None  # type: ignore[union-attr]
    assert service.create_scheduled_live_task() is None

    manual_task = service.create_live_task()
    assert manual_task is not None
    assert due.calls == [7, 7]
    assert future.calls == [7]


def test_database_claim_prevents_overlapping_manual_collection():
    collector = ManagedCollector("official:blocking", blocking=True)
    service, sources, _ = _service([collector])

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.create_live_task)
        assert collector.started.wait(timeout=2)
        try:
            with pytest.raises(ApiError) as error:
                service.create_live_task()
            assert error.value.status_code == 409
            assert error.value.code == "NO_RUNNABLE_SOURCES"
            leased = sources.get(collector.source_id)
            assert leased is not None
            assert leased.lease_owner is not None
        finally:
            collector.release.set()
        task = future.result(timeout=5)

    assert task is not None
    assert sources.get(collector.source_id).lease_owner is None  # type: ignore[union-attr]
    assert collector.calls == [7]


def test_due_open_circuit_runs_half_open_probe_and_closes_on_success():
    collector = ManagedCollector("official:recovering")
    service, sources, _ = _service([collector])
    source = sources.get(collector.source_id)
    assert source is not None
    sources.save(
        source.model_copy(
            update={
                "circuit_state": CircuitState.OPEN,
                "consecutive_failures": 3,
                "next_run_at": datetime.now(UTC) - timedelta(seconds=1),
                "last_error_code": "http_timeout",
            }
        )
    )

    task = service.create_scheduled_live_task()

    assert task is not None
    recovered = sources.get(collector.source_id)
    assert recovered is not None
    assert recovered.circuit_state.value == "closed"
    assert recovered.consecutive_failures == 0
    assert recovered.last_error_code is None
    assert recovered.lease_owner is None


def test_widespread_open_circuits_recover_in_bounded_probe_batches():
    collectors = [
        ManagedCollector(f"official:recovering-{index}")
        for index in range(6)
    ]
    service, sources, _ = _service(collectors)
    now = datetime.now(UTC)
    for collector in collectors:
        source = sources.get(collector.source_id)
        assert source is not None
        sources.save(
            source.model_copy(
                update={
                    "circuit_state": CircuitState.OPEN,
                    "consecutive_failures": 3,
                    "next_run_at": now + timedelta(hours=1),
                    "last_error_code": "http_timeout",
                    "last_checked_at": now - timedelta(minutes=1),
                }
            )
        )
    service.source_network_coordinator = SourceNetworkCoordinator(
        probe_batch_size=2,
        recovery_delay_seconds=0,
    )

    first = service.create_scheduled_live_task()
    second = service.create_scheduled_live_task()

    assert first is not None
    assert second is not None
    assert sum(len(collector.calls) for collector in collectors) == 4
    assert sum(
        sources.get(collector.source_id).circuit_state is CircuitState.CLOSED  # type: ignore[union-attr]
        for collector in collectors
    ) == 4
    assert sum(
        sources.get(collector.source_id).circuit_state is CircuitState.OPEN  # type: ignore[union-attr]
        for collector in collectors
    ) == 2


def test_global_network_batch_is_audited_but_not_scored_per_source():
    collectors = [
        FailingManagedCollector(f"official:offline-{index}")
        for index in range(6)
    ]
    service, sources, runs = _service(collectors)

    task = service.create_live_task()

    assert task is not None
    persisted = [
        run
        for collector in collectors
        for run in runs.list_recent(
            collector.source_id,
            datetime.now(UTC) - timedelta(minutes=1),
        )
    ]
    assert len(persisted) == 6
    assert {
        run.reliability_excluded_reason for run in persisted
    } == {"global_network_outage"}
    assert all(
        sources.get(collector.source_id).consecutive_failures == 0  # type: ignore[union-attr]
        for collector in collectors
    )

    report = build_source_reliability_report(sources, runs, days=7)
    assert report["excludedGlobalOutageRuns"] == 6
    assert all(item["attempts"] == 0 for item in report["items"])
