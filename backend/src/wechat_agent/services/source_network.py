"""Batch-aware network outage coordination for public source collection.

Per-source circuit breakers are useful when one feed is unhealthy. They are
actively harmful when the local machine loses outbound connectivity: every
source records the same transport failure, opens independently, and later all
half-open probes fire together. This module keeps the durable SourceRun facts
unchanged while coordinating recovery at the collection-batch level.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from threading import RLock
from typing import Literal, Sequence

from wechat_agent.domain.models import (
    CircuitState,
    Source,
    SourceRun,
    SourceRunStatus,
)


TRANSIENT_NETWORK_ERRORS = frozenset(
    {"http_timeout", "network_error", "global_network_outage"}
)


@dataclass(frozen=True)
class SourceClaimPlan:
    """The bounded source set that one scheduler poll may claim."""

    source_ids: tuple[str, ...]
    forced_probe_ids: tuple[str, ...] = ()
    state: Literal["normal", "outage", "recovering"] = "normal"


class SourceNetworkCoordinator:
    """Detect broad transport outages and stage half-open recovery probes."""

    def __init__(
        self,
        *,
        probe_batch_size: int = 3,
        outage_ratio: float = 0.6,
        minimum_outage_sources: int = 4,
        outage_cooldown_seconds: int = 300,
        recovery_delay_seconds: int = 30,
    ) -> None:
        self.probe_batch_size = max(1, probe_batch_size)
        self.outage_ratio = min(1.0, max(0.1, outage_ratio))
        self.minimum_outage_sources = max(2, minimum_outage_sources)
        self.outage_cooldown = timedelta(
            seconds=max(1, outage_cooldown_seconds)
        )
        self.recovery_delay = timedelta(
            seconds=max(0, recovery_delay_seconds)
        )
        self._lock = RLock()
        self._state: Literal["normal", "outage", "recovering"] = "normal"
        self._next_probe_at: datetime | None = None
        self._detected_at: datetime | None = None
        self._last_batch_at: datetime | None = None
        self._last_batch_attempts = 0
        self._last_batch_transient_failures = 0

    def plan_claims(
        self,
        sources: Sequence[Source],
        source_ids: Sequence[str],
        *,
        now: datetime,
        due_only: bool,
    ) -> SourceClaimPlan:
        """Return all normal candidates or a bounded global recovery probe."""
        current = now.astimezone(UTC)
        wanted = set(source_ids)
        enabled = [
            source
            for source in sources
            if source.id in wanted and source.enabled
        ]
        transient_open = [
            source
            for source in enabled
            if source.circuit_state in {CircuitState.OPEN, CircuitState.HALF_OPEN}
            and source.last_error_code in TRANSIENT_NETWORK_ERRORS
        ]
        widespread = self._is_widespread(len(enabled), len(transient_open))

        with self._lock:
            if widespread and self._state == "normal":
                self._state = "outage"
                self._detected_at = current
            elif self._state != "normal" and not transient_open:
                self._state = "normal"
                self._next_probe_at = None

            coordinated = widespread or (
                self._state in {"outage", "recovering"} and bool(transient_open)
            )
            if not coordinated:
                return SourceClaimPlan(
                    source_ids=tuple(source.id for source in enabled),
                )
            if self._next_probe_at is not None and current < self._next_probe_at:
                # Closed sources that are independently due may continue. Only
                # globally-open sources are held behind the shared cooldown.
                closed = tuple(
                    source.id
                    for source in enabled
                    if source.circuit_state is CircuitState.CLOSED
                    and (
                        not due_only
                        or source.next_run_at is None
                        or source.next_run_at <= current
                    )
                )
                return SourceClaimPlan(source_ids=closed, state=self._state)

            probes = sorted(
                transient_open,
                key=lambda source: (
                    source.last_checked_at
                    or datetime.min.replace(tzinfo=UTC),
                    source.id,
                ),
            )[: self.probe_batch_size]
            probe_ids = tuple(source.id for source in probes)
            return SourceClaimPlan(
                source_ids=probe_ids,
                forced_probe_ids=probe_ids,
                state=self._state,
            )

    def observe_runs(
        self,
        runs: Sequence[SourceRun],
        *,
        now: datetime | None = None,
    ) -> bool:
        """Record one batch and return whether its failures are global.

        The return value is true both for the first broad outage batch and for
        later bounded probe batches that fail entirely while the shared outage
        state is active.
        """
        current = (now or datetime.now(UTC)).astimezone(UTC)
        attempted = [
            run for run in runs if run.status is not SourceRunStatus.SKIPPED
        ]
        transient_failures = [
            run
            for run in attempted
            if run.status is SourceRunStatus.FAILED
            and run.error_code in TRANSIENT_NETWORK_ERRORS
        ]
        successes = [
            run
            for run in attempted
            if run.status in {SourceRunStatus.SUCCESS, SourceRunStatus.PARTIAL}
        ]
        broad_outage = (
            len(attempted) >= self.minimum_outage_sources
            and len(transient_failures)
            >= ceil(len(attempted) * self.outage_ratio)
            and not successes
        )
        global_outage = broad_outage
        with self._lock:
            self._last_batch_at = current
            self._last_batch_attempts = len(attempted)
            self._last_batch_transient_failures = len(transient_failures)
            if broad_outage:
                if self._detected_at is None:
                    self._detected_at = current
                self._state = "outage"
                self._next_probe_at = current + self.outage_cooldown
            elif (
                self._state in {"outage", "recovering"}
                and attempted
                and len(transient_failures) == len(attempted)
            ):
                # A bounded half-open batch is intentionally smaller than the
                # broad-outage threshold. If every probe still sees the same
                # transport failure, return to one shared cooldown.
                self._state = "outage"
                self._next_probe_at = current + self.outage_cooldown
                global_outage = True
            elif self._state in {"outage", "recovering"} and successes:
                self._state = "recovering"
                self._next_probe_at = current + self.recovery_delay
        return global_outage

    def snapshot(
        self,
        sources: Sequence[Source],
        *,
        scheduler_running: bool,
        now: datetime | None = None,
    ) -> dict[str, object]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        enabled = [source for source in sources if source.enabled]
        open_sources = [
            source
            for source in enabled
            if source.circuit_state in {CircuitState.OPEN, CircuitState.HALF_OPEN}
        ]
        transient_open = [
            source
            for source in open_sources
            if source.last_error_code in TRANSIENT_NETWORK_ERRORS
        ]
        widespread = self._is_widespread(len(enabled), len(transient_open))
        with self._lock:
            state = self._state
            if widespread and state == "normal":
                state = "outage"
            elif state != "normal" and not transient_open:
                state = "normal"
            next_probe_at = self._next_probe_at
            detected_at = self._detected_at
            last_batch_at = self._last_batch_at
            last_batch_attempts = self._last_batch_attempts
            last_batch_failures = self._last_batch_transient_failures
        last_success_at = max(
            (source.last_success_at for source in enabled if source.last_success_at),
            default=None,
        )
        last_checked_at = max(
            (source.last_checked_at for source in enabled if source.last_checked_at),
            default=None,
        )
        return {
            "schemaVersion": 1,
            "generatedAt": current.isoformat(),
            "state": state,
            "schedulerRunning": scheduler_running,
            "enabledSourceCount": len(enabled),
            "openCircuitCount": len(open_sources),
            "globalNetworkAffectedCount": len(transient_open),
            "probeBatchSize": self.probe_batch_size,
            "nextProbeAt": next_probe_at.isoformat() if next_probe_at else None,
            "outageDetectedAt": detected_at.isoformat() if detected_at else None,
            "lastSuccessAt": last_success_at.isoformat() if last_success_at else None,
            "lastCheckedAt": last_checked_at.isoformat() if last_checked_at else None,
            "lastBatch": {
                "at": last_batch_at.isoformat() if last_batch_at else None,
                "attempts": last_batch_attempts,
                "transientFailures": last_batch_failures,
            },
        }

    def _is_widespread(self, enabled_count: int, affected_count: int) -> bool:
        if enabled_count == 0:
            return False
        return (
            affected_count >= self.minimum_outage_sources
            and affected_count >= ceil(enabled_count * self.outage_ratio)
        )
