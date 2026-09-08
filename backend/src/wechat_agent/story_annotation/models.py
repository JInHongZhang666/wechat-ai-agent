"""Strict contracts for human Story pair annotation artifacts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


RelationReason = Literal[
    "same_event_republication",
    "official_and_coverage",
    "same_event_update",
    "same_event_analysis",
    "unrelated_events",
    "same_org_different_event",
    "same_product_different_version",
    "same_topic_different_actor",
    "prediction_vs_occurrence",
    "overview_vs_specific_event",
    "similar_title_different_time_or_place",
    "followup_new_event",
    "same_research_area_different_work",
    "insufficient_evidence",
]


class StoryAnnotationModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class StoryAnnotationError(ValueError):
    def __init__(self, code: str, detail: str, context: dict | None = None):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.context = context or {}


class StoryPairArticle(StoryAnnotationModel):
    id: str = Field(min_length=1)
    title: str
    summary: str
    source_name: str
    source_url: str
    publisher_group: str
    published_at: datetime
    category: str

    @field_validator("published_at")
    @classmethod
    def publication_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("publishedAt must be timezone-aware")
        return value


class StoryPair(StoryAnnotationModel):
    pair_id: str = Field(min_length=1)
    article_a: StoryPairArticle
    article_b: StoryPairArticle

    @model_validator(mode="after")
    def articles_must_differ(self) -> Self:
        if self.article_a.id == self.article_b.id:
            raise ValueError("a pair requires two distinct articles")
        return self


class BatchManifest(StoryAnnotationModel):
    schema_version: Literal[1] = 1
    batch_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pair_count: int = Field(ge=1)
    created_at: datetime
    pairs: tuple[StoryPair, ...]

    @field_validator("created_at")
    @classmethod
    def creation_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("createdAt must be timezone-aware")
        return value

    @model_validator(mode="after")
    def count_must_match(self) -> Self:
        if self.pair_count != len(self.pairs):
            raise ValueError("pairCount does not match pairs")
        return self


class ValidatedSubmission(StoryAnnotationModel):
    reviewer: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_count: int = Field(ge=0)
    missing_pair_ids: tuple[str, ...] = ()
    labels: dict[str, StoryAnnotationLabel]

    @property
    def complete(self) -> bool:
        return not self.missing_pair_ids


class AgreementReport(StoryAnnotationModel):
    schema_version: Literal[1] = 1
    batch_id: str
    candidate_sha256: str
    reviewer_a_sha256: str
    reviewer_b_sha256: str
    reviewer_a_labels_sha256: str
    reviewer_b_labels_sha256: str
    pair_count: int
    generated_at: datetime
    distributions: dict[str, dict[str, int]]
    raw_agreement: float
    deterministic_agreement: float | None
    deterministic_matrix: dict[str, int]
    cohen_kappa: float | None
    kappa_undefined_reason: str | None = None
    hard_negative_agreement: float | None
    relation_reason_agreement: float
    conflict_pair_ids: tuple[str, ...]
    boundary_conflict_rate: float

    @property
    def conflict_count(self) -> int:
        return len(self.conflict_pair_ids)


class AdjudicationRecord(StoryAnnotationModel):
    schema_version: Literal[1] = 1
    batch_id: str
    pair_id: str
    resolved_same_story: Literal["yes", "no"]
    relation_reason: RelationReason
    hard_negative: bool = False
    primary_source_article_id: str | None = None
    adjudication_method: Literal["human"] = "human"
    adjudicator: str = Field(min_length=1)
    adjudicated_at: datetime
    notes: str = Field(min_length=1)
    reviewer_a_label_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_b_label_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agreement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("adjudicated_at")
    @classmethod
    def adjudication_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("adjudicatedAt must be timezone-aware")
        return value

    @model_validator(mode="after")
    def resolved_fields_must_agree(self) -> Self:
        if self.resolved_same_story == "yes" and self.hard_negative:
            raise ValueError("positive adjudication cannot be a hard negative")
        if self.resolved_same_story == "no" and self.primary_source_article_id is not None:
            raise ValueError("negative adjudication cannot name a primary source")
        return self


class ValidatedAdjudication(StoryAnnotationModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_count: int
    missing_pair_ids: tuple[str, ...]
    rows: tuple[AdjudicationRecord, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_pair_ids

    @model_validator(mode="after")
    def counts_must_match_rows(self) -> Self:
        if self.completed_count != len(self.rows):
            raise ValueError("completedCount does not match adjudication rows")
        pair_ids = [row.pair_id for row in self.rows]
        if len(pair_ids) != len(set(pair_ids)) or set(pair_ids) & set(self.missing_pair_ids):
            raise ValueError("adjudication pair sets are inconsistent")
        return self


class GateBlocker(StoryAnnotationModel):
    code: str
    detail: str


class StoryAnnotationGateReport(StoryAnnotationModel):
    schema_version: Literal[1] = 1
    stage: Literal["pilot-50", "calibration-200", "gold-500"]
    status: Literal["passing", "blocked"]
    blockers: tuple[GateBlocker, ...]
    public_cutover_enabled: Literal[False] = False


class EntityAnchors(StoryAnnotationModel):
    organizations: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()
    people: tuple[str, ...] = ()

    @field_validator("organizations", "products", "versions", "people", mode="before")
    @classmethod
    def normalize_values(cls, value):
        return tuple(sorted({str(item).strip() for item in (value or ()) if str(item).strip()}))


class StoryAnnotationLabel(StoryAnnotationModel):
    schema_version: Literal[1] = 1
    batch_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    same_story: Literal["yes", "no", "uncertain"]
    relation_reason: RelationReason
    entity_anchors: EntityAnchors
    time_anchor: date | None = None
    location_anchor: str | None = None
    hard_negative: bool = False
    primary_source_article_id: str | None = None
    annotation_method: Literal["human"] = "human"
    data_license: str = Field(min_length=1)
    annotator: str = Field(min_length=1)
    annotated_at: datetime
    notes: str = ""

    @field_validator("annotated_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("annotatedAt must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        if self.same_story == "uncertain":
            if self.relation_reason != "insufficient_evidence" or not self.notes.strip():
                raise ValueError("uncertain labels require insufficient_evidence and notes")
            if self.hard_negative or self.primary_source_article_id is not None:
                raise ValueError("uncertain labels cannot be hard negatives or name a primary")
        if self.same_story == "no" and self.primary_source_article_id is not None:
            raise ValueError("negative labels cannot name a primary source")
        if self.same_story == "yes" and self.hard_negative:
            raise ValueError("positive labels cannot be hard negatives")
        if self.hard_negative and self.same_story != "no":
            raise ValueError("hard negatives must be negative labels")
        return self
