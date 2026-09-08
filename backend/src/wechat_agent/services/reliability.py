"""Deterministic source reliability reporting for the R1 evidence gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wechat_agent.domain.models import SourceRunStatus, SourceTier
from wechat_agent.domain.ports import SourceRepository, SourceRunRepository


MAX_SOURCE_BACKOFF = timedelta(hours=12)


def build_source_reliability_report(
    source_repository: SourceRepository,
    source_run_repository: SourceRunRepository,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, object]:
    """Summarize only durable, non-skipped runs in one bounded window."""
    if days < 1 or days > 30:
        raise ValueError("days must be between 1 and 30")
    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    since = generated_at - timedelta(days=days)
    items: list[dict[str, object]] = []
    for source in source_repository.list_all():
        if not source.enabled:
            continue
        boundary_grace = timedelta(minutes=source.schedule_minutes * 2)
        history = [
            run
            for run in source_run_repository.list_recent(
                source.id,
                since - boundary_grace,
                limit=5000,
            )
            if run.status is not SourceRunStatus.SKIPPED
            and run.started_at <= generated_at
        ]
        observed_runs = [run for run in history if run.started_at >= since]
        excluded_runs = [
            run
            for run in observed_runs
            if run.reliability_excluded_reason == "global_network_outage"
        ]
        runs = [
            run
            for run in observed_runs
            if run.reliability_excluded_reason is None
        ]
        last_attempt_at = max(
            (run.started_at for run in history),
            default=None,
        )
        freshness_due_at = source.next_run_at or (
            last_attempt_at + timedelta(minutes=source.schedule_minutes)
            if last_attempt_at is not None
            else None
        )
        freshness_grace = timedelta(minutes=source.schedule_minutes * 2)
        fresh = (
            freshness_due_at is not None
            and generated_at <= freshness_due_at + freshness_grace
        )
        stale_by_seconds = (
            max(
                0,
                int(
                    (
                        generated_at
                        - (freshness_due_at + freshness_grace)
                    ).total_seconds()
                ),
            )
            if freshness_due_at is not None
            else None
        )
        ordered_attempts = sorted(run.started_at for run in history)
        gap_seconds = [
            int((current - previous).total_seconds())
            for previous, current in zip(
                ordered_attempts,
                ordered_attempts[1:],
            )
        ]
        maximum_gap_seconds = max(gap_seconds, default=None)
        allowed_gap_seconds = int(
            (
                MAX_SOURCE_BACKOFF
                + timedelta(minutes=source.schedule_minutes * 2)
            ).total_seconds()
        )
        continuous = (
            maximum_gap_seconds is not None
            and maximum_gap_seconds <= allowed_gap_seconds
        )
        successful = sum(
            run.status in {SourceRunStatus.SUCCESS, SourceRunStatus.PARTIAL}
            for run in runs
        )
        attempts = len(runs)
        success_rate = successful / attempts if attempts else None
        observation_started_at = min(
            (run.started_at for run in history),
            default=None,
        )
        observation_days = (
            max(
                0.0,
                (generated_at - observation_started_at).total_seconds()
                / 86400,
            )
            if observation_started_at is not None
            else 0.0
        )
        window_complete = (
            bool(runs)
            and observation_started_at is not None
            and observation_started_at <= since
        )
        target = 0.95 if source.tier is SourceTier.T1 else 0.90
        meets_target = success_rate is not None and success_rate >= target
        status_label = (
            "no_data"
            if attempts == 0
            else (
                "collecting"
                if not window_complete
                else (
                    "stale"
                    if not fresh
                    else (
                        "interrupted"
                        if not continuous
                        else ("passing" if meets_target else "failing")
                    )
                )
            )
        )
        items.append(
            {
                "sourceId": source.id,
                "name": source.name,
                "tier": source.tier.value,
                "attempts": attempts,
                "successfulRuns": successful,
                "failedRuns": attempts - successful,
                "excludedGlobalOutageRuns": len(excluded_runs),
                "successRate": success_rate,
                "target": target,
                "meetsTarget": meets_target,
                "status": status_label,
                "observationStartedAt": (
                    observation_started_at.isoformat()
                    if observation_started_at is not None
                    else None
                ),
                "observationDays": round(observation_days, 3),
                "windowComplete": window_complete,
                "fresh": fresh,
                "lastAttemptAt": (
                    last_attempt_at.isoformat()
                    if last_attempt_at is not None
                    else None
                ),
                "freshnessDueAt": (
                    freshness_due_at.isoformat()
                    if freshness_due_at is not None
                    else None
                ),
                "freshnessGraceMinutes": int(
                    freshness_grace.total_seconds() / 60
                ),
                "staleBySeconds": stale_by_seconds,
                "continuous": continuous,
                "maximumGapSeconds": maximum_gap_seconds,
                "allowedGapSeconds": allowed_gap_seconds,
                "lastRunAt": (
                    max(run.started_at for run in runs).isoformat()
                    if runs
                    else None
                ),
            }
        )
    items.sort(key=lambda item: (item["tier"], item["sourceId"]))
    window_complete = bool(items) and all(
        bool(item["windowComplete"]) for item in items
    )
    all_meet_target = bool(items) and all(
        bool(item["meetsTarget"]) for item in items
    )
    all_sources_fresh = bool(items) and all(
        bool(item["fresh"]) for item in items
    )
    all_sources_continuous = bool(items) and all(
        bool(item["continuous"]) for item in items
    )
    return {
        "schemaVersion": 1,
        "windowDays": days,
        "generatedAt": generated_at.isoformat(),
        "windowComplete": window_complete,
        "allSourcesMeetTarget": all_meet_target,
        "allSourcesFresh": all_sources_fresh,
        "allSourcesContinuous": all_sources_continuous,
        "passing": window_complete and all_meet_target,
        "qualificationPassing": window_complete and all_meet_target,
        "operationalReady": all_sources_fresh and all_sources_continuous,
        "sourceCount": len(items),
        "sourcesWithSamples": sum(item["attempts"] > 0 for item in items),
        "sourcesMeetingTarget": sum(
            bool(item["meetsTarget"]) for item in items
        ),
        "staleSourceCount": sum(
            not bool(item["fresh"]) for item in items
        ),
        "interruptedSourceCount": sum(
            not bool(item["continuous"]) for item in items
        ),
        "excludedGlobalOutageRuns": sum(
            int(item["excludedGlobalOutageRuns"]) for item in items
        ),
        "items": items,
    }
