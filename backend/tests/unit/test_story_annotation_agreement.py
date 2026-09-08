from __future__ import annotations

from datetime import datetime, timezone

import pytest

from wechat_agent.story_annotation.agreement import build_agreement_report
from wechat_agent.story_annotation.adjudication import label_sha256, validate_adjudications
from wechat_agent.story_annotation.models import (
    BatchManifest,
    StoryAnnotationError,
    StoryAnnotationLabel,
    StoryPair,
    ValidatedSubmission,
)


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def article(article_id):
    return {
        "id": article_id,
        "title": article_id,
        "summary": "summary",
        "sourceName": "source",
        "sourceUrl": f"https://example.com/{article_id}",
        "publisherGroup": "publisher:example",
        "publishedAt": NOW,
        "category": "industry",
    }


def manifest(count=4):
    pairs = tuple(
        StoryPair(pairId=f"p{index}", articleA=article(f"a{index}"), articleB=article(f"b{index}"))
        for index in range(1, count + 1)
    )
    return BatchManifest(
        batchId="batch",
        strategyVersion="story-shadow-v1",
        candidateSha256="a" * 64,
        pairCount=count,
        createdAt=NOW,
        pairs=pairs,
    )


def label(pair_id, reviewer, answer, *, hard_negative=False):
    reason = {
        "yes": "official_and_coverage",
        "no": "same_org_different_event" if hard_negative else "unrelated_events",
        "uncertain": "insufficient_evidence",
    }[answer]
    return StoryAnnotationLabel(
        batchId="batch",
        pairId=pair_id,
        sameStory=answer,
        relationReason=reason,
        entityAnchors={},
        hardNegative=hard_negative,
        primarySourceArticleId=None,
        annotator=reviewer,
        annotatedAt=NOW,
        notes="missing source" if answer == "uncertain" else "",
        dataLicense="public-source-metadata-only",
    )


def submission(reviewer, answers, *, hard_negative=()):
    labels = {
        f"p{index}": label(f"p{index}", reviewer, answer, hard_negative=index in hard_negative)
        for index, answer in enumerate(answers, 1)
    }
    return ValidatedSubmission(
        reviewer=reviewer,
        sha256=("b" if reviewer == "reviewer-a" else "c") * 64,
        completedCount=len(labels),
        missingPairIds=(),
        labels=labels,
    )


def test_agreement_stays_locked_until_both_reviewers_complete():
    reviewer_b = ValidatedSubmission(
        reviewer="reviewer-b",
        sha256="c" * 64,
        completedCount=3,
        missingPairIds=("p4",),
        labels=submission("reviewer-b", ["yes", "no", "no"]).labels,
    )
    with pytest.raises(StoryAnnotationError) as error:
        build_agreement_report(
            manifest(), submission("reviewer-a", ["yes", "yes", "no", "uncertain"]), reviewer_b, NOW
        )
    assert error.value.code == "SECOND_REVIEW_INCOMPLETE"
    assert "pairId" not in error.value.context


def test_agreement_reports_hand_checked_matrix_kappa_and_conflicts():
    report = build_agreement_report(
        manifest(),
        submission("reviewer-a", ["yes", "yes", "no", "uncertain"], hard_negative=(3,)),
        submission("reviewer-b", ["yes", "no", "no", "uncertain"], hard_negative=(2, 3)),
        NOW,
    )
    assert report.raw_agreement == 0.75
    assert report.deterministic_matrix == {"yesYes": 1, "yesNo": 1, "noYes": 0, "noNo": 1}
    assert report.cohen_kappa == pytest.approx(0.4)
    assert report.hard_negative_agreement == 0.5
    assert report.conflict_pair_ids == ("p2",)
    assert report.distributions["reviewer-a"] == {"yes": 2, "no": 1, "uncertain": 1}


def test_kappa_reports_reason_when_expected_agreement_is_one():
    report = build_agreement_report(
        manifest(2), submission("reviewer-a", ["no", "no"]), submission("reviewer-b", ["no", "no"]), NOW
    )
    assert report.cohen_kappa is None
    assert report.kappa_undefined_reason == "expected_agreement_is_one"


def conflict_inputs():
    batch = manifest()
    reviewer_a = submission("reviewer-a", ["yes", "yes", "no", "uncertain"])
    reviewer_b = submission("reviewer-b", ["yes", "no", "no", "uncertain"])
    report = build_agreement_report(batch, reviewer_a, reviewer_b, NOW)
    return report, reviewer_a, reviewer_b


def adjudication_row(report, reviewer_a, reviewer_b):
    return {
        "schemaVersion": 1,
        "batchId": "batch",
        "pairId": "p2",
        "resolvedSameStory": "no",
        "relationReason": "same_org_different_event",
        "hardNegative": True,
        "primarySourceArticleId": None,
        "adjudicationMethod": "human",
        "adjudicator": "reviewer-c",
        "adjudicatedAt": "2026-08-27T00:00:00Z",
        "notes": "The core actions differ.",
        "reviewerALabelSha256": label_sha256(reviewer_a.labels["p2"]),
        "reviewerBLabelSha256": label_sha256(reviewer_b.labels["p2"]),
        "agreementSha256": "d" * 64,
    }


def test_adjudication_requires_every_conflict_and_preserves_original_hashes(tmp_path):
    report, reviewer_a, reviewer_b = conflict_inputs()
    path = tmp_path / "adjudication.jsonl"
    import json
    path.write_text(json.dumps(adjudication_row(report, reviewer_a, reviewer_b)) + "\n", encoding="utf-8")
    result = validate_adjudications(report, reviewer_a, reviewer_b, path, agreement_sha256="d" * 64)
    assert result.complete is True
    assert result.completed_count == 1
    assert result.rows[0].reviewer_a_label_sha256 == label_sha256(reviewer_a.labels["p2"])


def test_adjudication_rejects_changed_reviewer_input(tmp_path):
    report, reviewer_a, reviewer_b = conflict_inputs()
    changed_labels = dict(reviewer_a.labels)
    changed_labels["p2"] = label("p2", "reviewer-a", "no")
    changed_a = reviewer_a.model_copy(update={"labels": changed_labels})
    path = tmp_path / "adjudication.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(StoryAnnotationError) as error:
        validate_adjudications(report, changed_a, reviewer_b, path, agreement_sha256="d" * 64)
    assert error.value.code == "ADJUDICATION_INPUT_DRIFT"
