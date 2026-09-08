from datetime import UTC, datetime, timedelta

from tests.story_factories import NOW, make_candidate_pair, make_story_article
from wechat_agent.story.audit import build_story_shadow_audit
from wechat_agent.story.models import (
    PairPage,
    Story,
    StoryDetail,
    StoryPage,
    StoryReport,
    StoryReportRole,
    StoryRevision,
    StoryShadowRun,
    StoryStatus,
    StoryTrigger,
)


def story(story_id: str, *, reports: int, publishers: int = 2) -> Story:
    return Story(
        id=story_id,
        public_id=f"st_{story_id[-1] * 32}",
        status=StoryStatus.ACTIVE,
        primary_article_id=f"{story_id}-a0",
        first_seen_at=NOW,
        latest_at=NOW,
        source_count=reports,
        publisher_group_count=publishers,
        signal_count=publishers,
        report_count=reports,
        created_at=NOW,
        updated_at=NOW,
    )


def revision(story_id: str, trigger: StoryTrigger) -> StoryRevision:
    return StoryRevision(
        story_id=story_id,
        revision=1,
        member_article_ids=(f"{story_id}-a0",),
        primary_article_id=f"{story_id}-a0",
        status=StoryStatus.ACTIVE,
        source_count=1,
        publisher_group_count=1,
        signal_count=1,
        report_count=1,
        trigger=trigger,
        created_at=NOW,
        content_hash="a" * 64,
    )


def run(run_id: str, started: datetime, status="success") -> StoryShadowRun:
    return StoryShadowRun(
        id=run_id,
        mode="incremental",
        status=status,
        window_start=started - timedelta(hours=24),
        window_end=started,
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        error_code="boom" if status == "failed" else None,
    )


class FakeStoryRepository:
    def __init__(self):
        self._stories = (
            story("story_1", reports=2, publishers=2),
            story("story_2", reports=9, publishers=1),
        )
        self._runs = (
            run("run-old", NOW - timedelta(hours=30)),
            run("run-new", NOW, "failed"),
        )
        self._revisions = (
            revision("story_1", StoryTrigger.CREATED),
            revision("story_2", StoryTrigger.SPLIT),
            revision("story_2", StoryTrigger.PRIMARY_CHANGED),
        )
        left = make_story_article(id="left")
        right = make_story_article(id="right")
        self._pairs = (make_candidate_pair(left, right, score=0.91),)

    def list_stories(self, *, status, offset, limit):
        return StoryPage(items=self._stories[offset : offset + limit], total=len(self._stories), offset=offset, limit=limit)

    def get_story(self, story_id):
        item = next(story for story in self._stories if story.id == story_id)
        reports = tuple(
            StoryReport(
                story_id=item.id,
                article_id=f"{item.id}-a{index}",
                role=StoryReportRole.PRIMARY if index == 0 else StoryReportRole.REPORT,
                attached_at=NOW,
                publisher_group="same-group",
            )
            for index in range(item.report_count)
        )
        return StoryDetail(story=item, reports=reports, revisions=())

    def list_pairs(self, *, suggestion, offset, limit):
        return PairPage(items=self._pairs[offset : offset + limit], total=len(self._pairs), offset=offset, limit=limit)

    def list_runs(self, *, since, offset, limit):
        items = tuple(item for item in self._runs if item.started_at >= since)
        return items[offset : offset + limit]

    def list_revisions(self, *, since, offset, limit):
        items = tuple(item for item in self._revisions if item.created_at >= since)
        return items[offset : offset + limit]


def test_story_shadow_audit_flags_structural_risks_without_human_labels():
    report = build_story_shadow_audit(
        FakeStoryRepository(),
        generated_at=NOW + timedelta(hours=1),
        window_hours=48,
        max_story_reports=8,
        max_run_gap_hours=24,
    )

    assert report.status == "blocked"
    assert {item.code for item in report.blockers} >= {
        "FAILED_SHADOW_RUN",
        "RUN_GAP_EXCEEDED",
        "OVERSIZED_STORY",
        "SINGLE_PUBLISHER_STORY",
    }
    assert report.public_cutover_enabled is False
    assert report.story_count == 2
    assert report.pair_suggestion_counts["same"] == 1
    assert report.revision_trigger_counts["split"] == 1
