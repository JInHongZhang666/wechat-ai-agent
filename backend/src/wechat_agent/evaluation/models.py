"""Strict JSONL contracts for human labels and non-gold suggestions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from wechat_agent.domain.models import (
    ArticleRecord,
    PUBLIC_TOPIC_CATEGORIES,
    TopicCategory,
)


class PredictionSnapshot(BaseModel):
    """A model/code suggestion shown to annotators, never a ground-truth label."""

    relevant: bool
    category: TopicCategory
    selected: bool
    distribution_score: float | None = Field(default=None, ge=0, le=100)
    strategy_version: str | None = None
    reason: str | None = None


class LabelingCandidate(BaseModel):
    """Unlabelled workbench row exported from the persistent article store."""

    schema_version: int = 1
    evaluation_group: str
    article: ArticleRecord
    suggestion: PredictionSnapshot


class GoldLabel(BaseModel):
    """Required human judgement used as the evaluation source of truth."""

    relevant: bool
    category: TopicCategory
    selected: bool
    primary_source_correct: bool
    story_key: str | None
    creation_worthy: bool
    evidence_sufficient: bool
    annotation_method: Literal["human", "synthetic"]
    data_license: str = Field(min_length=1)
    annotator: str = Field(min_length=1)
    annotated_at: datetime
    notes: str = ""

    @field_validator("annotated_at")
    @classmethod
    def annotated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_label_consistency(self) -> Self:
        if self.category not in PUBLIC_TOPIC_CATEGORIES:
            raise ValueError("gold category must use one of the five public categories")
        if not self.relevant and self.selected:
            raise ValueError("an irrelevant article cannot be selected")
        return self


class GoldSample(BaseModel):
    """One fully human-labelled sample. Partial labels are deliberately invalid."""

    schema_version: int = 1
    evaluation_group: str = Field(min_length=1)
    article: ArticleRecord
    label: GoldLabel


SelectionReviewReason = Literal[
    "timely_firsthand",
    "high_information_gain",
    "wechat_value",
    "other_keep",
    "low_information_gain",
    "weak_source",
    "outdated",
    "too_niche",
    "duplicate_or_followup",
    "other_remove",
]

_SELECTION_KEEP_REASONS = {
    "timely_firsthand",
    "high_information_gain",
    "wechat_value",
    "other_keep",
}


class SelectionReviewDecision(BaseModel):
    """One complete, provenance-bound second-pass selection judgement."""

    selected: bool
    reason_code: SelectionReviewReason
    notes: str = ""
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    annotation_method: Literal["human"] = "human"

    @field_validator("reviewed_at")
    @classmethod
    def reviewed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_reason_direction(self) -> Self:
        keep_reason = self.reason_code in _SELECTION_KEEP_REASONS
        if self.selected != keep_reason:
            raise ValueError("selection review reason must match the decision")
        return self


ReviewLifecycleStatus = Literal[
    "awaiting_second_reviewer",
    "second_reviewing",
    "awaiting_adjudication",
    "complete",
]
ReviewRole = Literal["reviewer_a", "reviewer_b"]


def _identity_must_not_be_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("reviewer identity must not be blank")
    return value


class ReviewParticipant(BaseModel):
    """One immutable human binding for a review-set role."""

    review_set_id: str = Field(min_length=1)
    role: ReviewRole
    reviewer: str = Field(min_length=1)
    claimed_at: datetime

    @field_validator("reviewer")
    @classmethod
    def reviewer_must_not_be_blank(cls, value: str) -> str:
        return _identity_must_not_be_blank(value)

    @field_validator("claimed_at")
    @classmethod
    def claimed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class ReviewerClaimRequest(BaseModel):
    """Request to bind the second-reviewer identity once."""

    reviewer: str = Field(min_length=1)
    claimed_at: datetime

    @field_validator("reviewer")
    @classmethod
    def reviewer_must_not_be_blank(cls, value: str) -> str:
        return _identity_must_not_be_blank(value)

    @field_validator("claimed_at")
    @classmethod
    def claimed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class BlindSelectionReviewItem(BaseModel):
    """Reviewer-B-safe item without first-pass or strategy decisions."""

    position: int = Field(ge=1)
    article: ArticleRecord
    review: SelectionReviewDecision | None = None


class BlindSelectionReviewPage(BaseModel):
    """Internal offset page containing only blind second-review fields."""

    items: list[BlindSelectionReviewItem]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class DoubleReviewStatus(BaseModel):
    """Lifecycle and progress for an immutable A/B review set."""

    schema_version: int = 1
    review_set_id: str = Field(min_length=1)
    lifecycle: ReviewLifecycleStatus
    total_count: int = Field(ge=0)
    second_completed_count: int = Field(ge=0)
    second_pending_count: int = Field(ge=0)
    consensus_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    adjudicated_count: int = Field(ge=0)
    pending_adjudication_count: int = Field(ge=0)
    second_reviewer: str | None = None
    completion_rate: float = Field(ge=0, le=1)


class SelectionAdjudicationDecision(BaseModel):
    """One immutable human resolution for an A/B selection conflict."""

    selected: bool
    reason_code: SelectionReviewReason
    notes: str = ""
    adjudicator: str = Field(min_length=1)
    adjudicated_at: datetime
    annotation_method: Literal["human"] = "human"

    @field_validator("adjudicator")
    @classmethod
    def adjudicator_must_not_be_blank(cls, value: str) -> str:
        return _identity_must_not_be_blank(value)

    @field_validator("adjudicated_at")
    @classmethod
    def adjudicated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_reason_direction(self) -> Self:
        keep_reason = self.reason_code in _SELECTION_KEEP_REASONS
        if self.selected != keep_reason:
            raise ValueError("adjudication reason must match the decision")
        return self


class SelectionConflictItem(BaseModel):
    """One A/B disagreement plus its optional immutable resolution."""

    position: int = Field(ge=1)
    article: ArticleRecord
    reviewer_a: SelectionReviewDecision
    reviewer_b: SelectionReviewDecision
    adjudication: SelectionAdjudicationDecision | None = None


class SelectionConflictPage(BaseModel):
    """Internal offset page of selection-review conflicts."""

    items: list[SelectionConflictItem]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class DoubleReviewRecord(BaseModel):
    """One complete A/B pair with its optional immutable adjudication."""

    item_key: str = Field(min_length=1)
    position: int = Field(ge=1)
    article: ArticleRecord
    reviewer_a: SelectionReviewDecision
    reviewer_b: SelectionReviewDecision
    adjudication: SelectionAdjudicationDecision | None = None


class WorkbenchItem(BaseModel):
    """One frozen candidate plus its optional completed annotation."""

    position: int = Field(ge=1)
    candidate: LabelingCandidate
    label: GoldLabel | None = None


class SelectionReviewItem(BaseModel):
    """One frozen second-pass item and its optional immutable decision."""

    position: int = Field(ge=1)
    reason: str = Field(min_length=1)
    candidate: LabelingCandidate
    initial_label: GoldLabel
    review: SelectionReviewDecision | None = None


class SelectionReviewPage(BaseModel):
    """Internal offset page for a versioned review set."""

    items: list[SelectionReviewItem]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class SelectionReviewStatus(BaseModel):
    """Progress and change counts for one frozen review set."""

    schema_version: int = 1
    review_set_id: str = Field(min_length=1)
    target_count: int = Field(ge=1)
    total_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    changed_count: int = Field(ge=0)
    independent_review_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)


class WorkbenchPage(BaseModel):
    """Internal offset page; the public AIHOT API remains cursor-based."""

    items: list[WorkbenchItem]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class WorkbenchStatus(BaseModel):
    """Progress towards the human-gold portion of the R1 evidence gate."""

    schema_version: int = 1
    target_count: int = Field(default=200, ge=1)
    candidate_count: int = Field(ge=0)
    labelled_count: int = Field(ge=0)
    human_label_count: int = Field(ge=0)
    synthetic_label_count: int = Field(ge=0)
    remaining_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    ready_for_baseline: bool
