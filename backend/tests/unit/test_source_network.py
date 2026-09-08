from datetime import UTC, datetime, timedelta

from wechat_agent.domain.models import (
    CircuitState,
    Source,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    SourceTier,
)
from wechat_agent.services.source_network import SourceNetworkCoordinator


NOW = datetime(2026, 8, 13, 3, tzinfo=UTC)


def _source(
    index: int,
    *,
    circuit_state: CircuitState = CircuitState.CLOSED,
    error_code: str | None = None,
) -> Source:
    return Source(
        id=f"official:source-{index}",
        name=f"Source {index}",
        kind=SourceKind.RSS,
        tier=SourceTier.T1,
        publisher_group=f"official:source-{index}",
        circuit_state=circuit_state,
        consecutive_failures=3 if circuit_state is CircuitState.OPEN else 0,
        last_error_code=error_code,
        last_checked_at=NOW - timedelta(minutes=index),
        next_run_at=NOW + timedelta(hours=1),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
    )


def _run(index: int, status: SourceRunStatus, error_code: str | None = None) -> SourceRun:
    return SourceRun(
        id=f"run-{index}",
        source_id=f"official:source-{index}",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        status=status,
        error_code=error_code,
        duration_ms=1000,
    )


def test_widespread_transient_failure_enters_shared_cooldown():
    coordinator = SourceNetworkCoordinator(outage_cooldown_seconds=300)
    runs = [
        _run(index, SourceRunStatus.FAILED, "http_timeout")
        for index in range(6)
    ]

    assert coordinator.observe_runs(runs, now=NOW) is True

    sources = [
        _source(index, circuit_state=CircuitState.OPEN, error_code="http_timeout")
        for index in range(6)
    ]
    plan = coordinator.plan_claims(
        sources,
        [source.id for source in sources],
        now=NOW + timedelta(minutes=1),
        due_only=True,
    )

    assert plan.state == "outage"
    assert plan.source_ids == ()
    assert coordinator.snapshot(
        sources,
        scheduler_running=True,
        now=NOW + timedelta(minutes=1),
    )["nextProbeAt"] == (NOW + timedelta(minutes=5)).isoformat()


def test_outage_recovery_forces_only_a_bounded_probe_batch():
    coordinator = SourceNetworkCoordinator(probe_batch_size=2)
    sources = [
        _source(index, circuit_state=CircuitState.OPEN, error_code="network_error")
        for index in range(6)
    ]

    plan = coordinator.plan_claims(
        sources,
        [source.id for source in sources],
        now=NOW,
        due_only=True,
    )

    assert plan.state == "outage"
    assert len(plan.source_ids) == 2
    assert plan.source_ids == plan.forced_probe_ids


def test_failed_probe_batch_returns_to_shared_cooldown():
    coordinator = SourceNetworkCoordinator(
        probe_batch_size=2,
        outage_cooldown_seconds=300,
    )
    sources = [
        _source(index, circuit_state=CircuitState.OPEN, error_code="network_error")
        for index in range(6)
    ]
    first = coordinator.plan_claims(
        sources,
        [source.id for source in sources],
        now=NOW,
        due_only=True,
    )

    assert coordinator.observe_runs(
        [
            _run(index, SourceRunStatus.FAILED, "http_timeout")
            for index, _ in enumerate(first.source_ids)
        ],
        now=NOW,
    ) is True

    second = coordinator.plan_claims(
        sources,
        [source.id for source in sources],
        now=NOW + timedelta(minutes=1),
        due_only=True,
    )
    assert second.source_ids == ()


def test_one_bad_source_does_not_create_a_global_outage():
    coordinator = SourceNetworkCoordinator()
    sources = [
        _source(0, circuit_state=CircuitState.OPEN, error_code="http_timeout"),
        *[_source(index) for index in range(1, 6)],
    ]

    plan = coordinator.plan_claims(
        sources,
        [source.id for source in sources],
        now=NOW,
        due_only=False,
    )

    assert plan.state == "normal"
    assert len(plan.source_ids) == 6
    assert plan.forced_probe_ids == ()
