"""Structural quality audit for Story shadow output."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Iterable

from wechat_agent.story.models import (
    PairSuggestion,
    StoryAuditBlocker,
    StoryShadowAuditReport,
    StoryStatus,
)
from wechat_agent.story.ports import StoryRepository


def _paged(fetch, *, limit: int = 100) -> tuple:
    values = []
    offset = 0
    while True:
        page = fetch(offset, limit)
        items = tuple(page.items) if hasattr(page, "items") else tuple(page)
        values.extend(items)
        offset += len(items)
        total = getattr(page, "total", None)
        if not items or (total is not None and offset >= total):
            break
    return tuple(values)


def _max_gap_hours(times: Iterable[datetime]) -> float | None:
    ordered = sorted(times)
    if len(ordered) < 2:
        return None
    return max(
        (right - left).total_seconds() / 3600
        for left, right in zip(ordered, ordered[1:])
    )


def build_story_shadow_audit(
    repository: StoryRepository,
    *,
    generated_at: datetime,
    window_hours: int = 168,
    max_story_reports: int = 8,
    max_run_gap_hours: int = 24,
) -> StoryShadowAuditReport:
    since = generated_at - timedelta(hours=window_hours)
    stories = _paged(
        lambda offset, limit: repository.list_stories(
            status=None,
            offset=offset,
            limit=limit,
        )
    )
    pairs = _paged(
        lambda offset, limit: repository.list_pairs(
            suggestion=None,
            offset=offset,
            limit=limit,
        )
    )
    runs = tuple(repository.list_runs(since=since, offset=0, limit=1000))
    revisions = tuple(repository.list_revisions(since=since, offset=0, limit=1000))

    oversized = tuple(
        sorted(story.id for story in stories if story.report_count > max_story_reports)
    )
    single_publisher = tuple(
        sorted(
            story.id
            for story in stories
            if story.status is StoryStatus.ACTIVE
            and story.report_count > 1
            and story.publisher_group_count <= 1
        )
    )
    failed_runs = tuple(run for run in runs if run.status == "failed")
    observed_gap = _max_gap_hours(run.started_at for run in runs)
    blockers: list[StoryAuditBlocker] = []
    if failed_runs:
        blockers.append(
            StoryAuditBlocker(
                code="FAILED_SHADOW_RUN",
                detail="Story shadow has failed runs in the audit window",
                count=len(failed_runs),
            )
        )
    if observed_gap is not None and observed_gap > max_run_gap_hours:
        blockers.append(
            StoryAuditBlocker(
                code="RUN_GAP_EXCEEDED",
                detail="Story shadow run gap exceeds the configured limit",
                count=1,
            )
        )
    if oversized:
        blockers.append(
            StoryAuditBlocker(
                code="OVERSIZED_STORY",
                detail="One or more Stories have unusually many reports",
                count=len(oversized),
            )
        )
    if single_publisher:
        blockers.append(
            StoryAuditBlocker(
                code="SINGLE_PUBLISHER_STORY",
                detail="Multi-report active Stories should have more than one publisher group before public use",
                count=len(single_publisher),
            )
        )

    suggestion_counts = Counter(pair.suggestion.value for pair in pairs)
    for suggestion in PairSuggestion:
        suggestion_counts.setdefault(suggestion.value, 0)
    trigger_counts = Counter(revision.trigger.value for revision in revisions)

    return StoryShadowAuditReport(
        status="blocked" if blockers else "passing",
        generatedAt=generated_at,
        windowHours=window_hours,
        storyCount=len(stories),
        pairCount=len(pairs),
        runCount=len(runs),
        failedRunCount=len(failed_runs),
        maxRunGapHours=round(observed_gap, 2) if observed_gap is not None else None,
        oversizedStoryIds=oversized,
        singlePublisherStoryIds=single_publisher,
        pairSuggestionCounts=dict(sorted(suggestion_counts.items())),
        revisionTriggerCounts=dict(sorted(trigger_counts.items())),
        blockers=tuple(blockers),
        publicCutoverEnabled=False,
    )
