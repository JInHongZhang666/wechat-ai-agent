"""Framework-neutral models for the deterministic Story shadow engine."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from wechat_agent.domain.models import ArticleRecord


STORY_STRATEGY_VERSION = "story-shadow-v1"
STORY_RECALL_HOURS = 24
STORY_SETTLE_HOURS = 48


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must be timezone-aware")
    return value


class StoryModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class StoryStatus(StrEnum):
    ACTIVE = "active"
    SETTLED = "settled"
    MERGED = "merged"


class PairSuggestion(StrEnum):
    SAME = "same"
    AMBIGUOUS = "ambiguous"
    DIFFERENT = "different"


class PairReviewState(StrEnum):
    UNREVIEWED = "unreviewed"
    LABELED = "labeled"
    EXCLUDED = "excluded"


class StoryTrigger(StrEnum):
    CREATED = "created"
    REPORT_ATTACHED = "report_attached"
    PRIMARY_CHANGED = "primary_changed"
    SETTLED = "settled"
    REACTIVATED = "reactivated"
    MERGED = "merged"
    SPLIT = "split"


class StoryReportRole(StrEnum):
    PRIMARY = "primary"
    REPORT = "report"
    ANALYSIS = "analysis"
    UPDATE = "update"


class StoryRelationType(StrEnum):
    STORYLINE = "storyline"
    RELATED = "related"


class NormalizedArticle(StoryModel):
    article: ArticleRecord
    normalized_url: str
    normalized_title: str
    title_shingles: frozenset[str]
    organizations: frozenset[str] = frozenset()
    products: frozenset[str] = frozenset()
    people: frozenset[str] = frozenset()
    versions: frozenset[str] = frozenset()
    identifiers: frozenset[str] = frozenset()
    action_codes: frozenset[str] = frozenset()


def story_pair_key(article_a_id: str, article_b_id: str) -> str:
    """Return the stable unordered identity for two distinct articles."""
    first, second = sorted((article_a_id, article_b_id))
    if first == second:
        raise ValueError("a Story candidate pair requires two distinct articles")
    return sha256(f"{first}\0{second}".encode("utf-8")).hexdigest()


class StoryCandidatePair(StoryModel):
    pair_key: str = Field(min_length=64, max_length=64)
    article_a_id: str
    article_b_id: str
    published_delta_seconds: int = Field(ge=0)
    features: dict[str, float] = Field(default_factory=dict)
    score: float = Field(default=0.0, ge=0, le=1)
    suggestion: PairSuggestion = PairSuggestion.AMBIGUOUS
    veto_reason: str | None = None
    strategy_version: str = STORY_STRATEGY_VERSION
    generated_at: datetime
    review_state: PairReviewState = PairReviewState.UNREVIEWED

    @field_validator("generated_at")
    @classmethod
    def generated_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def validate_pair_identity(self) -> Self:
        if self.article_a_id >= self.article_b_id:
            raise ValueError("candidate article IDs must be distinct and sorted")
        expected = story_pair_key(self.article_a_id, self.article_b_id)
        if self.pair_key != expected:
            raise ValueError("pair_key does not match candidate article IDs")
        return self

    @classmethod
    def from_articles(
        cls,
        left: ArticleRecord,
        right: ArticleRecord,
        *,
        now: datetime,
        features: dict[str, float] | None = None,
        score: float = 0.0,
        suggestion: PairSuggestion = PairSuggestion.AMBIGUOUS,
        veto_reason: str | None = None,
        strategy_version: str = STORY_STRATEGY_VERSION,
    ) -> StoryCandidatePair:
        article_a_id, article_b_id = sorted((left.id, right.id))
        return cls.model_validate(
            {
                "pair_key": (
                    story_pair_key(article_a_id, article_b_id)
                    if article_a_id != article_b_id
                    else "0" * 64
                ),
                "article_a_id": article_a_id,
                "article_b_id": article_b_id,
                "published_delta_seconds": int(
                    abs((left.published_at - right.published_at).total_seconds())
                ),
                "features": features or {},
                "score": score,
                "suggestion": suggestion,
                "veto_reason": veto_reason,
                "strategy_version": strategy_version,
                "generated_at": now,
            }
        )


class ProvisionalCluster(StoryModel):
    article_ids: tuple[str, ...]
    primary_article_id: str
    first_seen_at: datetime
    latest_at: datetime
    source_count: int = Field(ge=1)
    publisher_group_count: int = Field(ge=1)
    signal_count: int = Field(ge=1)
    veto_reasons: tuple[str, ...] = ()
    strategy_version: str = STORY_STRATEGY_VERSION

    @field_validator("first_seen_at", "latest_at")
    @classmethod
    def cluster_datetimes_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class Story(StoryModel):
    id: str
    public_id: str = Field(pattern=r"^st_[0-9a-f]{32}$")
    status: StoryStatus
    primary_article_id: str
    first_seen_at: datetime
    latest_at: datetime
    source_count: int = Field(ge=1)
    publisher_group_count: int = Field(ge=1)
    signal_count: int = Field(ge=1)
    report_count: int = Field(ge=1)
    strategy_version: str = STORY_STRATEGY_VERSION
    merged_into_id: str | None = None
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("first_seen_at", "latest_at", "created_at", "updated_at")
    @classmethod
    def story_datetimes_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class StoryReport(StoryModel):
    story_id: str
    article_id: str
    role: StoryReportRole = StoryReportRole.REPORT
    edge_score: float | None = Field(default=None, ge=0, le=1)
    edge_features: dict[str, float] = Field(default_factory=dict)
    edge_reasons: tuple[str, ...] = ()
    attached_at: datetime
    strategy_version: str = STORY_STRATEGY_VERSION
    source_id: str | None = None
    publisher_group: str

    @field_validator("attached_at")
    @classmethod
    def attached_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class StoryRelation(StoryModel):
    story_id: str
    related_story_id: str
    relation_type: StoryRelationType
    created_at: datetime
    strategy_version: str = STORY_STRATEGY_VERSION

    @field_validator("created_at")
    @classmethod
    def relation_created_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def relation_must_reference_another_story(self) -> Self:
        if self.story_id == self.related_story_id:
            raise ValueError("a Story relation cannot reference itself")
        return self


class StoryRevision(StoryModel):
    story_id: str
    revision: int = Field(ge=1)
    member_article_ids: tuple[str, ...]
    primary_article_id: str
    status: StoryStatus
    source_count: int = Field(ge=1)
    publisher_group_count: int = Field(ge=1)
    signal_count: int = Field(ge=1)
    report_count: int = Field(ge=1)
    strategy_version: str = STORY_STRATEGY_VERSION
    merged_into_id: str | None = None
    trigger: StoryTrigger
    created_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)

    @field_validator("created_at")
    @classmethod
    def revision_created_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]

    @classmethod
    def calculate_content_hash(
        cls,
        *,
        member_article_ids: tuple[str, ...],
        primary_article_id: str,
        status: StoryStatus,
        source_count: int,
        publisher_group_count: int,
        signal_count: int,
        report_count: int,
        strategy_version: str = STORY_STRATEGY_VERSION,
        merged_into_id: str | None = None,
    ) -> str:
        payload = {
            "memberArticleIds": sorted(member_article_ids),
            "primaryArticleId": primary_article_id,
            "status": status.value,
            "sourceCount": source_count,
            "publisherGroupCount": publisher_group_count,
            "signalCount": signal_count,
            "reportCount": report_count,
            "strategyVersion": strategy_version,
            "mergedIntoId": merged_into_id,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class StoryArticleState(StoryModel):
    article_id: str
    content_fingerprint: str
    strategy_version: str = STORY_STRATEGY_VERSION
    processed_at: datetime

    @field_validator("processed_at")
    @classmethod
    def processed_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class StoryShadowRun(StoryModel):
    id: str
    mode: Literal["incremental", "replay"]
    status: Literal["success", "failed", "skipped"]
    window_start: datetime
    window_end: datetime
    input_article_count: int = Field(default=0, ge=0)
    processed_article_count: int = Field(default=0, ge=0)
    candidate_pair_count: int = Field(default=0, ge=0)
    same_count: int = Field(default=0, ge=0)
    ambiguous_count: int = Field(default=0, ge=0)
    hard_veto_count: int = Field(default=0, ge=0)
    created_story_count: int = Field(default=0, ge=0)
    updated_story_count: int = Field(default=0, ge=0)
    created_revision_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_summary: str | None = Field(default=None, max_length=500)
    skip_reason: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    strategy_version: str = STORY_STRATEGY_VERSION
    started_at: datetime
    finished_at: datetime

    @field_validator("window_start", "window_end", "started_at", "finished_at")
    @classmethod
    def run_datetimes_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class StoryPersistenceBatch(StoryModel):
    pairs: tuple[StoryCandidatePair, ...] = ()
    stories: tuple[Story, ...] = ()
    reports: tuple[StoryReport, ...] = ()
    revisions: tuple[StoryRevision, ...] = ()
    relations: tuple[StoryRelation, ...] = ()
    article_states: tuple[StoryArticleState, ...] = ()
    run: StoryShadowRun


class StoryWindowState(StoryModel):
    articles: tuple[ArticleRecord, ...] = ()
    stories: tuple[Story, ...] = ()
    reports: tuple[StoryReport, ...] = ()
    article_states: tuple[StoryArticleState, ...] = ()


class StoryApplyResult(StoryModel):
    run: StoryShadowRun
    created_story_count: int = Field(default=0, ge=0)
    updated_story_count: int = Field(default=0, ge=0)
    created_revision_count: int = Field(default=0, ge=0)


class StoryDetail(StoryModel):
    story: Story
    reports: tuple[StoryReport, ...] = ()
    revisions: tuple[StoryRevision, ...] = ()
    pairs: tuple[StoryCandidatePair, ...] = ()


class StoryPage(StoryModel):
    items: tuple[Story, ...] = ()
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class PairPage(StoryModel):
    items: tuple[StoryCandidatePair, ...] = ()
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class StoryShadowStatus(StoryModel):
    strategy_version: str = STORY_STRATEGY_VERSION
    latest_run: StoryShadowRun | None = None
    story_count: int = Field(default=0, ge=0)
    active_story_count: int = Field(default=0, ge=0)
    pair_count: int = Field(default=0, ge=0)
    pending_pair_count: int = Field(default=0, ge=0)


class StoryAuditBlocker(StoryModel):
    code: str
    detail: str
    count: int = Field(default=0, ge=0)


class StoryShadowAuditReport(StoryModel):
    strategy_version: str = STORY_STRATEGY_VERSION
    status: Literal["passing", "blocked"]
    generated_at: datetime
    window_hours: int = Field(ge=1)
    story_count: int = Field(ge=0)
    pair_count: int = Field(ge=0)
    run_count: int = Field(ge=0)
    failed_run_count: int = Field(ge=0)
    max_run_gap_hours: float | None = None
    oversized_story_ids: tuple[str, ...] = ()
    single_publisher_story_ids: tuple[str, ...] = ()
    pair_suggestion_counts: dict[str, int] = Field(default_factory=dict)
    revision_trigger_counts: dict[str, int] = Field(default_factory=dict)
    blockers: tuple[StoryAuditBlocker, ...] = ()
    public_cutover_enabled: Literal[False] = False
