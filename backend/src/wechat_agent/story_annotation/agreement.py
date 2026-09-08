"""Double-blind agreement metrics and unlock rules."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .artifacts import canonical_jsonl_bytes, sha256_bytes
from .models import AgreementReport, BatchManifest, StoryAnnotationError, ValidatedSubmission


def build_agreement_report(
    manifest: BatchManifest,
    reviewer_a: ValidatedSubmission,
    reviewer_b: ValidatedSubmission,
    generated_at: datetime,
) -> AgreementReport:
    if not reviewer_b.complete:
        raise StoryAnnotationError(
            "SECOND_REVIEW_INCOMPLETE",
            "Reviewer B must complete the batch before agreement is unlocked",
            {"completedCount": reviewer_b.completed_count, "requiredCount": manifest.pair_count},
        )
    if not reviewer_a.complete:
        raise StoryAnnotationError(
            "FIRST_REVIEW_INCOMPLETE",
            "Reviewer A must complete the batch before agreement is unlocked",
            {"completedCount": reviewer_a.completed_count, "requiredCount": manifest.pair_count},
        )
    pair_ids = tuple(pair.pair_id for pair in manifest.pairs)
    if set(reviewer_a.labels) != set(pair_ids) or set(reviewer_b.labels) != set(pair_ids):
        raise StoryAnnotationError("SUBMISSION_PAIR_SET_MISMATCH", "submission pair set differs from manifest")

    answers_a = [reviewer_a.labels[pair_id].same_story for pair_id in pair_ids]
    answers_b = [reviewer_b.labels[pair_id].same_story for pair_id in pair_ids]
    agreed = sum(left == right for left, right in zip(answers_a, answers_b, strict=True))
    conflicts = tuple(sorted(pair_id for pair_id in pair_ids if reviewer_a.labels[pair_id].same_story != reviewer_b.labels[pair_id].same_story))

    matrix = {"yesYes": 0, "yesNo": 0, "noYes": 0, "noNo": 0}
    deterministic: list[tuple[str, str]] = []
    for left, right in zip(answers_a, answers_b, strict=True):
        if left != "uncertain" and right != "uncertain":
            deterministic.append((left, right))
            matrix[f"{left}{right.title()}"] += 1
    kappa = None
    undefined_reason = None
    deterministic_agreement = None
    if deterministic:
        total = len(deterministic)
        deterministic_agreement = sum(left == right for left, right in deterministic) / total
        count_a = Counter(left for left, _ in deterministic)
        count_b = Counter(right for _, right in deterministic)
        expected = sum((count_a[label] / total) * (count_b[label] / total) for label in ("yes", "no"))
        if expected == 1:
            undefined_reason = "expected_agreement_is_one"
        else:
            kappa = round((deterministic_agreement - expected) / (1 - expected), 4)
    else:
        undefined_reason = "no_deterministic_pairs"

    hard_ids = [
        pair_id for pair_id in pair_ids
        if reviewer_a.labels[pair_id].hard_negative or reviewer_b.labels[pair_id].hard_negative
    ]
    hard_agreement = None
    if hard_ids:
        hard_agreement = sum(
            reviewer_a.labels[pair_id].same_story == reviewer_b.labels[pair_id].same_story
            for pair_id in hard_ids
        ) / len(hard_ids)
    reason_agreement = sum(
        reviewer_a.labels[pair_id].relation_reason == reviewer_b.labels[pair_id].relation_reason
        for pair_id in pair_ids
    ) / manifest.pair_count
    return AgreementReport(
        batchId=manifest.batch_id,
        candidateSha256=manifest.candidate_sha256,
        reviewerASha256=reviewer_a.sha256,
        reviewerBSha256=reviewer_b.sha256,
        reviewerALabelsSha256=sha256_bytes(canonical_jsonl_bytes(reviewer_a.labels[pair_id] for pair_id in sorted(pair_ids))),
        reviewerBLabelsSha256=sha256_bytes(canonical_jsonl_bytes(reviewer_b.labels[pair_id] for pair_id in sorted(pair_ids))),
        pairCount=manifest.pair_count,
        generatedAt=generated_at,
        distributions={
            "reviewer-a": dict(Counter(answers_a)),
            "reviewer-b": dict(Counter(answers_b)),
        },
        rawAgreement=agreed / manifest.pair_count,
        deterministicAgreement=deterministic_agreement,
        deterministicMatrix=matrix,
        cohenKappa=kappa,
        kappaUndefinedReason=undefined_reason,
        hardNegativeAgreement=hard_agreement,
        relationReasonAgreement=reason_agreement,
        conflictPairIds=conflicts,
        boundaryConflictRate=len(conflicts) / manifest.pair_count,
    )
