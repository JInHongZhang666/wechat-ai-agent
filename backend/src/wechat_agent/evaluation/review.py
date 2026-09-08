"""Pure contracts and deterministic sampling for selection re-review."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from unicodedata import normalize

from wechat_agent.evaluation.models import (
    DoubleReviewRecord,
    DoubleReviewStatus,
    GoldSample,
    SelectionReviewItem,
    WorkbenchItem,
)
from wechat_agent.evaluation.replay import replay_samples


SELECTION_REVIEW_SET_ID = "selection-audit-v1"
SELECTION_REVIEW_TARGET = 40


class ReviewSetNotFoundError(KeyError):
    """Raised when a requested versioned review set does not exist."""


class ReviewItemNotFoundError(KeyError):
    """Raised when an article is not part of the requested review set."""


class ReviewAlreadyCompletedError(ValueError):
    """Raised when an immutable review item receives a second decision."""


class ReviewerNotIndependentError(ValueError):
    """Raised when a reviewer identity collides with an earlier participant."""


class FirstReviewerAlreadyClaimedError(ValueError):
    """Raised when a different human attempts to replace Reviewer A."""


class SecondReviewerAlreadyClaimedError(ValueError):
    """Raised when a different human attempts to replace Reviewer B."""


class ReviewSubmissionAlreadyExistsError(ValueError):
    """Raised when an immutable A/B submission is written twice."""


class SecondReviewIncompleteError(ValueError):
    """Raised when conflicts are requested before Reviewer B finishes."""


class AdjudicationNotRequiredError(ValueError):
    """Raised when a consensus item is sent for adjudication."""


class AdjudicationAlreadyExistsError(ValueError):
    """Raised when an immutable adjudication is written twice."""


class AdjudicatorNotIndependentError(ValueError):
    """Raised when Reviewer B attempts to adjudicate their own conflict."""


def normalize_reviewer_identity(value: str) -> str:
    """Return the comparison key used for independent human identities."""
    normalized = normalize("NFKC", value).strip().casefold()
    if not normalized:
        raise ValueError("reviewer identity must not be blank")
    return normalized


def calculate_binary_agreement(
    *, reviewer_a: Sequence[bool], reviewer_b: Sequence[bool]
) -> dict[str, object]:
    """Calculate a literal 2x2 selection matrix, agreement, and Cohen kappa."""
    if len(reviewer_a) != len(reviewer_b):
        raise ValueError("review sequences must have equal length")
    total = len(reviewer_a)
    matrix = {
        "a_keep_b_keep": 0,
        "a_keep_b_remove": 0,
        "a_remove_b_keep": 0,
        "a_remove_b_remove": 0,
    }
    for first, second in zip(reviewer_a, reviewer_b, strict=True):
        key = (
            "a_keep_b_keep"
            if first and second
            else "a_keep_b_remove"
            if first
            else "a_remove_b_keep"
            if second
            else "a_remove_b_remove"
        )
        matrix[key] += 1
    if total == 0:
        return {
            "matrix": matrix,
            "rawAgreement": 0.0,
            "cohenKappa": None,
            "undefinedReason": "no_completed_pairs",
        }
    observed = (matrix["a_keep_b_keep"] + matrix["a_remove_b_remove"]) / total
    a_keep = (matrix["a_keep_b_keep"] + matrix["a_keep_b_remove"]) / total
    b_keep = (matrix["a_keep_b_keep"] + matrix["a_remove_b_keep"]) / total
    expected = a_keep * b_keep + (1 - a_keep) * (1 - b_keep)
    if expected == 1:
        kappa, undefined = None, "expected_agreement_is_one"
    else:
        kappa = round((observed - expected) / (1 - expected), 4)
        undefined = None
    return {
        "matrix": matrix,
        "rawAgreement": round(observed, 4),
        "cohenKappa": kappa,
        "undefinedReason": undefined,
    }


def selection_review_sort_key(article_id: str) -> str:
    """Return a stable, version-bound ordering key for one candidate."""
    payload = f"{SELECTION_REVIEW_SET_ID}:{article_id}".encode("utf-8")
    return sha256(payload).hexdigest()


def select_selection_review_items(
    items: Sequence[WorkbenchItem],
    *,
    target_count: int = SELECTION_REVIEW_TARGET,
) -> list[WorkbenchItem]:
    """Select only human relevant+selected labels with stable category balance."""
    if target_count < 1:
        return []
    eligible = [
        item
        for item in items
        if item.label is not None
        and item.label.annotation_method == "human"
        and item.label.relevant
        and item.label.selected
    ]
    by_category: dict[str, list[WorkbenchItem]] = defaultdict(list)
    for item in eligible:
        by_category[item.label.category.value].append(item)  # type: ignore[union-attr]
    for bucket in by_category.values():
        bucket.sort(
            key=lambda item: selection_review_sort_key(
                item.candidate.article.id
            )
        )

    picked: list[WorkbenchItem] = []
    picked_ids: set[str] = set()
    for category in sorted(by_category):
        for item in by_category[category][:8]:
            picked.append(item)
            picked_ids.add(item.candidate.article.id)

    remainder: dict[tuple[str, str], deque[WorkbenchItem]] = defaultdict(deque)
    for item in eligible:
        if item.candidate.article.id in picked_ids:
            continue
        tier = item.candidate.article.tier
        tier_key = str(getattr(tier, "value", tier) or "unknown")
        remainder[(item.label.category.value, tier_key)].append(item)  # type: ignore[union-attr]
    for bucket in remainder.values():
        ordered = sorted(
            bucket,
            key=lambda item: selection_review_sort_key(
                item.candidate.article.id
            ),
        )
        bucket.clear()
        bucket.extend(ordered)
    while len(picked) < target_count and any(remainder.values()):
        for key in sorted(remainder):
            if remainder[key] and len(picked) < target_count:
                picked.append(remainder[key].popleft())
    return picked[:target_count]


def build_selection_audit_report(
    *,
    review_set_id: str,
    reviews: Sequence[SelectionReviewItem],
    before_samples: Sequence[GoldSample],
    after_samples: Sequence[GoldSample],
) -> dict[str, object]:
    """Compare a frozen first pass with current adjudicated labels."""
    completed = [item for item in reviews if item.review is not None]
    changed_count = sum(
        item.initial_label.selected != item.review.selected
        for item in completed
        if item.review is not None
    )
    reason_counts = Counter(
        item.review.reason_code
        for item in completed
        if item.review is not None
    )
    independent_count = sum(
        item.initial_label.annotator != item.review.reviewer
        for item in completed
        if item.review is not None
    )
    return {
        "schemaVersion": 1,
        "reviewSetId": review_set_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": (
            "complete"
            if reviews and len(completed) == len(reviews)
            else "collecting"
        ),
        "totalCount": len(reviews),
        "completedCount": len(completed),
        "changedCount": changed_count,
        "changeRate": round(
            changed_count / len(completed),
            4,
        ) if completed else 0.0,
        "independentReviewCount": independent_count,
        "reasonCounts": dict(sorted(reason_counts.items())),
        "beforeMetrics": replay_samples(list(before_samples)),
        "afterMetrics": replay_samples(list(after_samples)),
    }


def build_double_review_report(
    *,
    review_set_id: str,
    status: DoubleReviewStatus,
    records: Sequence[DoubleReviewRecord],
    before_samples: Sequence[GoldSample],
    after_samples: Sequence[GoldSample],
    baseline_sha256: str,
    current_sha256: str,
) -> dict[str, object]:
    """Build the immutable agreement and adjudication evidence report."""
    agreement = calculate_binary_agreement(
        reviewer_a=[record.reviewer_a.selected for record in records],
        reviewer_b=[record.reviewer_b.selected for record in records],
    )
    reviewer_a_reasons = Counter(
        record.reviewer_a.reason_code for record in records
    )
    reviewer_b_reasons = Counter(
        record.reviewer_b.reason_code for record in records
    )
    final_reasons = Counter()
    conflicts = []
    for record in records:
        is_conflict = record.reviewer_a.selected != record.reviewer_b.selected
        if is_conflict:
            if record.adjudication is not None:
                final_reasons[record.adjudication.reason_code] += 1
            conflicts.append(
                {
                    "itemKey": record.item_key,
                    "position": record.position,
                    "articleId": record.article.id,
                    "title": record.article.title,
                    "reviewerA": record.reviewer_a.model_dump(mode="json"),
                    "reviewerB": record.reviewer_b.model_dump(mode="json"),
                    "adjudication": (
                        record.adjudication.model_dump(mode="json")
                        if record.adjudication is not None
                        else None
                    ),
                }
            )
        else:
            final_reasons[record.reviewer_b.reason_code] += 1
    return {
        "schemaVersion": 1,
        "reviewSetId": review_set_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "status": status.lifecycle,
        "totalCount": status.total_count,
        "secondCompletedCount": status.second_completed_count,
        "consensusCount": status.consensus_count,
        "conflictCount": status.conflict_count,
        "adjudicatedCount": status.adjudicated_count,
        "pendingAdjudicationCount": status.pending_adjudication_count,
        "agreement": agreement,
        "reviewerAReasonCounts": dict(sorted(reviewer_a_reasons.items())),
        "reviewerBReasonCounts": dict(sorted(reviewer_b_reasons.items())),
        "finalReasonCounts": dict(sorted(final_reasons.items())),
        "conflicts": conflicts,
        "baselineSampleCount": len(before_samples),
        "currentSampleCount": len(after_samples),
        "baselineSha256": baseline_sha256,
        "currentSha256": current_sha256,
        "beforeMetrics": replay_samples(list(before_samples)),
        "afterMetrics": replay_samples(list(after_samples)),
    }
