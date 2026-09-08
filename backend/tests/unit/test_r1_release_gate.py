from datetime import UTC, datetime, timedelta

import pytest

from wechat_agent.domain.models import (
    ArticleRecord,
    Source,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    SourceTier,
    TopicCategory,
)
from wechat_agent.evaluation.models import GoldLabel, GoldSample
from wechat_agent.evaluation.__main__ import _parse_as_of
from wechat_agent.evaluation.release_gate import build_r1_evidence_report
from wechat_agent.services.reliability import build_source_reliability_report
from wechat_agent.storage.memory import (
    InMemorySourceRepository,
    InMemorySourceRunRepository,
)


NOW = datetime(2026, 8, 2, 4, tzinfo=UTC)


def test_release_gate_as_of_parser_requires_timezone_and_normalizes_to_utc():
    assert _parse_as_of("2026-08-21T11:25:00+08:00") == datetime(
        2026, 8, 21, 3, 25, tzinfo=UTC
    )

    with pytest.raises(ValueError, match="timezone"):
        _parse_as_of("2026-08-21T11:25:00")


def _source(identity: str, tier: SourceTier = SourceTier.T1) -> Source:
    return Source(
        id=identity,
        name=identity,
        kind=SourceKind.RSS,
        tier=tier,
        publisher_group=identity,
        categories=(TopicCategory.AI_MODELS,),
        schedule_minutes=30,
        created_at=NOW - timedelta(days=8),
        updated_at=NOW,
    )


def _run(
    identity: str,
    source_id: str,
    started_at: datetime,
    status: SourceRunStatus = SourceRunStatus.SUCCESS,
) -> SourceRun:
    return SourceRun(
        id=identity,
        source_id=source_id,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        status=status,
        duration_ms=1000,
    )


def _reliability_repositories(
    *, latest_run_age: timedelta = timedelta(minutes=15),
    continuous_history: bool = True,
):
    source_repository = InMemorySourceRepository()
    run_repository = InMemorySourceRunRepository()
    source_repository.save(_source("official:test"))
    run_repository.save(
        _run(
            "boundary",
            "official:test",
            NOW - timedelta(days=7, minutes=30),
        )
    )
    latest_at = NOW - latest_run_age
    if continuous_history:
        cursor = NOW - timedelta(days=7, minutes=30) + timedelta(hours=12)
        index = 0
        while cursor < latest_at:
            run_repository.save(
                _run(f"bridge-{index}", "official:test", cursor)
            )
            cursor += timedelta(hours=12)
            index += 1
    run_repository.save(_run("current", "official:test", latest_at))
    return source_repository, run_repository


def _gold_sample(identity: str) -> GoldSample:
    article = ArticleRecord(
        id=identity,
        source_id="official:test",
        title=f"Official AI release {identity}",
        source_name="Test",
        source_url=f"https://example.test/{identity}",
        published_at=NOW - timedelta(hours=1),
        collected_at=NOW,
        summary="An official model release.",
        category=TopicCategory.AI_MODELS,
        source_identity="official:test",
        tier="t1",
    )
    return GoldSample(
        evaluation_group="human-batch",
        article=article,
        label=GoldLabel(
            relevant=True,
            category=TopicCategory.AI_MODELS,
            selected=True,
            primary_source_correct=True,
            story_key=identity,
            creation_worthy=True,
            evidence_sufficient=True,
            annotation_method="human",
            data_license="public-source-metadata-only",
            annotator="reviewer-a",
            annotated_at=NOW,
        ),
    )


def test_reliability_uses_boundary_evidence_but_scores_window_only():
    source_repository, run_repository = _reliability_repositories()

    report = build_source_reliability_report(
        source_repository,
        run_repository,
        days=7,
        now=NOW,
    )

    assert report["passing"] is True
    assert report["sourceCount"] == 1
    assert report["items"][0]["attempts"] == 15
    assert report["items"][0]["observationDays"] > 7


def test_reliability_excludes_confirmed_global_network_outages():
    source_repository, run_repository = _reliability_repositories()
    run_repository.save(
        _run(
            "global-outage",
            "official:test",
            NOW - timedelta(hours=12),
            SourceRunStatus.FAILED,
        ).model_copy(
            update={
                "error_code": "http_timeout",
                "reliability_excluded_reason": "global_network_outage",
            }
        )
    )

    report = build_source_reliability_report(
        source_repository,
        run_repository,
        days=7,
        now=NOW,
    )

    assert report["passing"] is True
    assert report["excludedGlobalOutageRuns"] == 1
    assert report["items"][0]["attempts"] == 15
    assert report["items"][0]["failedRuns"] == 0
    assert report["items"][0]["excludedGlobalOutageRuns"] == 1


def test_historical_replay_excludes_runs_after_the_as_of_time():
    source_repository, run_repository = _reliability_repositories()
    run_repository.save(
        _run(
            "future-failure",
            "official:test",
            NOW + timedelta(hours=1),
            SourceRunStatus.FAILED,
        )
    )

    report = build_source_reliability_report(
        source_repository,
        run_repository,
        days=7,
        now=NOW,
    )

    assert report["items"][0]["attempts"] == 15
    assert report["items"][0]["successRate"] == 1.0


def test_release_gate_reports_missing_gold_without_hiding_reliability(tmp_path):
    source_repository, run_repository = _reliability_repositories()

    report = build_r1_evidence_report(
        source_repository,
        run_repository,
        tmp_path / "missing.jsonl",
        required_gold=2,
        now=NOW,
    )

    assert report["status"] == "blocked"
    assert report["criteria"]["sourceReliability"]["status"] == "passing"
    assert report["criteria"]["humanGold"]["status"] == "missing"
    assert [blocker["code"] for blocker in report["blockers"]] == [
        "GOLD_FILE_MISSING"
    ]


def test_release_gate_counts_only_human_labels_and_replays_baseline(tmp_path):
    source_repository, run_repository = _reliability_repositories()
    gold_path = tmp_path / "gold.jsonl"
    samples = [_gold_sample("human-1"), _gold_sample("human-2")]
    gold_path.write_text(
        "".join(sample.model_dump_json() + "\n" for sample in samples),
        encoding="utf-8",
    )

    report = build_r1_evidence_report(
        source_repository,
        run_repository,
        gold_path,
        required_gold=2,
        now=NOW,
    )

    gold = report["criteria"]["humanGold"]
    assert report["status"] == "passing"
    assert gold["status"] == "passing"
    assert gold["humanSampleCount"] == 2
    assert gold["syntheticSampleCount"] == 0
    assert gold["baseline"]["sampleCount"] == 2


def test_release_gate_blocks_when_enabled_source_has_stopped_running(tmp_path):
    source_repository, run_repository = _reliability_repositories(
        latest_run_age=timedelta(days=1),
    )
    gold_path = tmp_path / "gold.jsonl"
    samples = [_gold_sample("human-1"), _gold_sample("human-2")]
    gold_path.write_text(
        "".join(sample.model_dump_json() + "\n" for sample in samples),
        encoding="utf-8",
    )

    report = build_r1_evidence_report(
        source_repository,
        run_repository,
        gold_path,
        required_gold=2,
        now=NOW,
    )

    reliability = report["criteria"]["sourceReliability"]["report"]
    assert report["status"] == "passing"
    assert reliability["items"][0]["fresh"] is False
    readiness = report["criteria"]["operationalReadiness"]
    assert readiness["status"] == "blocked"
    assert [blocker["code"] for blocker in readiness["blockers"]] == [
        "SOURCE_RUN_STALE"
    ]
    assert report["blockers"] == []


def test_release_gate_blocks_a_long_silent_gap_inside_the_window(tmp_path):
    source_repository, run_repository = _reliability_repositories(
        continuous_history=False,
    )
    gold_path = tmp_path / "gold.jsonl"
    samples = [_gold_sample("human-1"), _gold_sample("human-2")]
    gold_path.write_text(
        "".join(sample.model_dump_json() + "\n" for sample in samples),
        encoding="utf-8",
    )

    report = build_r1_evidence_report(
        source_repository,
        run_repository,
        gold_path,
        required_gold=2,
        now=NOW,
    )

    item = report["criteria"]["sourceReliability"]["report"]["items"][0]
    assert report["status"] == "passing"
    assert item["fresh"] is True
    assert item["continuous"] is False
    assert item["maximumGapSeconds"] > item["allowedGapSeconds"]
    readiness = report["criteria"]["operationalReadiness"]
    assert readiness["status"] == "blocked"
    assert [blocker["code"] for blocker in readiness["blockers"]] == [
        "SOURCE_RUN_GAP"
    ]
    assert report["blockers"] == []
