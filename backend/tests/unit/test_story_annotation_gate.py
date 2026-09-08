from __future__ import annotations

from datetime import datetime, timezone

from wechat_agent.story_annotation.gate import build_release_gate
from wechat_agent.story_annotation.models import (
    AgreementReport,
    BatchManifest,
    StoryPair,
    ValidatedAdjudication,
)


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def manifest(count):
    article = lambda value: {
        "id": value, "title": value, "summary": "", "sourceName": "source",
        "sourceUrl": f"https://example.com/{value}", "publisherGroup": "publisher:test",
        "publishedAt": NOW, "category": "industry",
    }
    pairs = tuple(
        StoryPair(pairId=f"p{i}", articleA=article(f"a{i}"), articleB=article(f"b{i}"))
        for i in range(count)
    )
    return BatchManifest(
        batchId=f"batch-{count}", strategyVersion="story-shadow-v1",
        candidateSha256="a" * 64, pairCount=count, createdAt=NOW, pairs=pairs,
    )


def agreement(count=50, *, deterministic=.9, hard=.9, boundary=.1, yes_yes=10, conflicts=()):
    no_no = count - yes_yes - len(conflicts)
    return AgreementReport(
        batchId=f"batch-{count}", candidateSha256="a" * 64,
        reviewerASha256="b" * 64, reviewerBSha256="c" * 64,
        reviewerALabelsSha256="d" * 64, reviewerBLabelsSha256="e" * 64,
        pairCount=count, generatedAt=NOW,
        distributions={"reviewer-a": {"yes": yes_yes, "no": count - yes_yes}, "reviewer-b": {"yes": yes_yes, "no": count - yes_yes}},
        rawAgreement=deterministic, deterministicAgreement=deterministic,
        deterministicMatrix={"yesYes": yes_yes, "yesNo": len(conflicts), "noYes": 0, "noNo": no_no},
        cohenKappa=.8, hardNegativeAgreement=hard, relationReasonAgreement=.9,
        conflictPairIds=conflicts, boundaryConflictRate=boundary,
    )


def complete_adjudication():
    return ValidatedAdjudication(sha256="f" * 64, completedCount=0, missingPairIds=(), rows=())


def test_pilot_gate_blocks_low_agreement_and_missing_adjudication():
    gate = build_release_gate(
        "pilot-50", manifest(50),
        agreement(deterministic=.80, conflicts=("p1",)), None,
    )
    assert gate.status == "blocked"
    assert {item.code for item in gate.blockers} == {
        "AGREEMENT_BELOW_TARGET", "ADJUDICATION_REQUIRED",
    }


def test_gate_rejects_forged_complete_adjudication_without_conflict_rows():
    report = agreement(conflicts=("p1",))
    gate = build_release_gate("pilot-50", manifest(50), report, complete_adjudication())
    assert "ADJUDICATION_REQUIRED" in {item.code for item in gate.blockers}


def test_pilot_gate_blocks_when_human_positive_coverage_is_insufficient():
    gate = build_release_gate("pilot-50", manifest(50), agreement(yes_yes=0), complete_adjudication())
    assert "COVERAGE_INSUFFICIENT" in {item.code for item in gate.blockers}


def test_pilot_gate_passes_complete_quality_evidence():
    gate = build_release_gate("pilot-50", manifest(50), agreement(), complete_adjudication())
    assert gate.status == "passing"
    assert gate.blockers == ()


def test_gold_gate_requires_algorithm_and_group_split_metrics():
    gate = build_release_gate("gold-500", manifest(500), agreement(500), complete_adjudication())
    assert {item.code for item in gate.blockers} >= {
        "ALGORITHM_METRICS_MISSING", "GROUP_SPLIT_EVIDENCE_MISSING",
    }


def test_gold_gate_checks_algorithm_thresholds():
    gate = build_release_gate(
        "gold-500", manifest(500), agreement(500), complete_adjudication(),
        algorithm_metrics={"pairPrecision": .89, "pairRecall": .79, "duplicateDisplayRate": .06},
        group_split_evidence=True,
    )
    assert {item.code for item in gate.blockers} >= {
        "PAIR_PRECISION_BELOW_TARGET", "PAIR_RECALL_BELOW_TARGET", "DUPLICATE_DISPLAY_RATE_ABOVE_TARGET",
    }
