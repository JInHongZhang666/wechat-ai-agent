"""Framework-independent domain data for content research and drafting."""

from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _require_source_url(value: str) -> str:
    if not value.strip():
        raise ValueError("source_url must not be empty")
    return value


class TopicCategory(StrEnum):
    TOOL = "tool"                    # Legacy
    NEWS = "news"                    # Legacy
    AI_MODELS = "ai-models"          # 模型发布/更新
    AI_PRODUCTS = "ai-products"      # AI 产品发布/更新
    INDUSTRY = "industry"            # 行业动态
    PAPER = "paper"                  # 论文研究
    TIP = "tip"                      # 技巧与观点


PUBLIC_TOPIC_CATEGORIES: tuple[TopicCategory, ...] = (
    TopicCategory.AI_MODELS,
    TopicCategory.AI_PRODUCTS,
    TopicCategory.INDUSTRY,
    TopicCategory.PAPER,
    TopicCategory.TIP,
)


def public_category_slug(category: TopicCategory) -> str:
    """Return the stable five-category slug, including legacy migration."""
    if category is TopicCategory.TOOL:
        return TopicCategory.AI_PRODUCTS.value
    if category is TopicCategory.NEWS:
        return TopicCategory.INDUSTRY.value
    return category.value


def is_product_category(category: TopicCategory) -> bool:
    """Return whether content should use the product/tool writing rules."""
    return category in {TopicCategory.TOOL, TopicCategory.AI_PRODUCTS}


class HeatConfidence(StrEnum):
    MEASURED = "measured"
    ESTIMATED = "estimated"


class SourceType(StrEnum):
    OFFICIAL = "official"
    PRIMARY = "primary"
    SECONDARY = "secondary"


class SourceTier(StrEnum):
    """Operational source grade used by code-owned curation rules."""

    T1 = "t1"
    T1_5 = "t1_5"
    T2 = "t2"


class SourceKind(StrEnum):
    RSS = "rss"
    BLOG = "blog"
    API = "api"
    MP_ACCOUNT = "mp_account"
    MP_HOT = "mp_hot"
    SOCIAL = "social"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class SourceRunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class DailyReportStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class Source(BaseModel):
    """Persisted source configuration plus operator-owned runtime state."""

    id: str
    name: str
    homepage: str | None = None
    feed_url: str | None = None
    kind: SourceKind
    tier: SourceTier
    publisher_group: str
    evidence_eligible: bool = True
    signal_only: bool = False
    categories: tuple[TopicCategory, ...] = ()
    language: str = "auto"
    schedule_minutes: int = Field(default=30, ge=1, le=1440)
    enabled: bool = True
    parser_version: str = "unknown"
    circuit_state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = Field(default=0, ge=0)
    last_success_at: datetime | None = None
    next_run_at: datetime | None = None
    success_rate_7d: float | None = Field(default=None, ge=0, le=1)
    last_error_code: str | None = None
    last_checked_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "last_success_at",
        "next_run_at",
        "last_checked_at",
        "lease_expires_at",
        "created_at",
        "updated_at",
    )
    @classmethod
    def source_datetimes_must_be_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value


class SourceRun(BaseModel):
    """One durable, structured execution record for one source."""

    id: str
    source_id: str
    task_id: str | None = None
    window_days: int = Field(default=7, ge=1)
    started_at: datetime
    finished_at: datetime
    status: SourceRunStatus
    stage: Literal["fetch", "parse", "collect", "complete"] = "complete"
    http_status: int | None = Field(default=None, ge=100, le=599)
    fetched_count: int = Field(default=0, ge=0)
    stored_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_summary: str | None = None
    reliability_excluded_reason: Literal["global_network_outage"] | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)
    duration_ms: int = Field(ge=0)

    @field_validator("started_at", "finished_at")
    @classmethod
    def source_run_datetimes_must_be_timezone_aware(
        cls,
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value

    @model_validator(mode="after")
    def source_run_must_finish_after_it_starts(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        return self


class DailyReport(BaseModel):
    """Immutable persisted revision of one Asia/Shanghai daily report."""

    date: date
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    revision: int = Field(default=1, ge=1)
    status: DailyReportStatus = DailyReportStatus.PUBLISHED
    lead: str
    section_item_ids: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    flash_item_ids: tuple[str, ...] = ()
    payload: dict[str, object] = Field(default_factory=dict)
    strategy_version: str
    source_snapshot_at: datetime
    generated_at: datetime
    published_at: datetime | None = None
    item_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    content_hash: str = Field(min_length=64, max_length=64)

    @field_validator("source_snapshot_at", "generated_at", "published_at")
    @classmethod
    def daily_report_datetimes_must_be_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value


class EngagementMetrics(BaseModel):
    views: int = Field(default=0, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int = Field(default=0, ge=0)
    shares: int = Field(default=0, ge=0)
    stars: int = Field(default=0, ge=0)
    forks: int = Field(default=0, ge=0)
    verified_proxy_signals: tuple[
        Literal["views", "comments", "shares", "stars", "forks"], ...
    ] = ()


class ArticleRecord(BaseModel):
    id: str
    source_id: str | None = None
    title: str
    source_name: str
    source_url: str
    published_at: datetime
    collected_at: datetime
    summary: str
    category: TopicCategory
    source_confidence: float | None = Field(default=None, ge=0, le=1)
    source_type: SourceType = SourceType.SECONDARY
    source_identity: str | None = None
    source_type_verified: bool = False
    source_verification_basis: str | None = None
    engagement: EngagementMetrics | None = None
    tier: str | None = Field(default=None)  # SourceTier value (t1/t1_5/t2)
    # Internal collector-input identity.  It is deliberately excluded from
    # API serialization, but survives model_copy() while classification may
    # replace summary/category later in the ingestion pipeline.
    ingestion_fingerprint: str | None = Field(default=None, exclude=True)

    @field_validator("source_url")
    @classmethod
    def source_url_must_not_be_empty(cls, value: str) -> str:
        return _require_source_url(value)

    @field_validator("published_at", "collected_at")
    @classmethod
    def datetime_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class HeatScore(BaseModel):
    value: float = Field(ge=0)
    confidence: HeatConfidence
    measured_likes: int | None = Field(default=None, ge=0)
    components: dict[str, float] = Field(default_factory=dict)


class CurationDecision(BaseModel):
    """Persisted, reproducible distribution decision for one article."""

    article_id: str
    distribution_score: float = Field(ge=0, le=100)
    selected: bool
    eligible: bool = True
    components: dict[str, float] = Field(default_factory=dict)
    threshold: float = Field(ge=0, le=100)
    reason: str
    score_confidence: HeatConfidence
    strategy_version: str = "distribution-v1"
    scored_at: datetime

    @field_validator("scored_at")
    @classmethod
    def scored_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class TopicCandidate(BaseModel):
    id: str
    title: str
    category: TopicCategory
    heat_score: HeatScore
    summary: str
    recommendation_reason: str


class EvidenceSource(BaseModel):
    source_name: str
    source_identity: str
    source_type: SourceType = SourceType.SECONDARY
    source_type_verified: bool = False
    provenance: str | None = None


class EvidenceFact(BaseModel):
    claim_id: str = ""
    claim: str
    fact_type: Literal["fact", "number", "date", "version", "entity"] = "fact"
    status: Literal["verified", "conflicting", "unverified"] = "verified"
    subject: str = ""
    numbers: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    versions: list[str] = Field(default_factory=list)
    last_verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_name: str = ""
    source_url: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    sources: dict[str, EvidenceSource] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def include_legacy_source_url(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        source_url = values.get("source_url")
        source_urls = values.get("source_urls")
        if source_url is not None and not source_urls:
            return {**values, "source_urls": [source_url]}
        return values

    @field_validator("source_url")
    @classmethod
    def source_url_must_not_be_empty(cls, value: str | None) -> str | None:
        return _require_source_url(value) if value is not None else None

    @field_validator("source_urls")
    @classmethod
    def source_urls_must_not_be_empty(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("source_urls must contain at least one URL")
        return [_require_source_url(value) for value in values]

    @model_validator(mode="after")
    def validate_source_traceability(self) -> Self:
        self.source_urls = list(dict.fromkeys(self.source_urls))
        if not self.source_urls:
            raise ValueError("source_urls must contain at least one URL")
        if self.source_url is not None and self.source_url not in self.source_urls:
            raise ValueError("source_url must be included in source_urls")
        if self.sources and set(self.sources) != set(self.source_urls):
            raise ValueError("sources must map every canonical source URL")
        if not self.claim_id.strip():
            identity = "\n".join(
                [self.claim.strip().casefold(), *sorted(self.source_urls)]
            )
            self.claim_id = f"claim-{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
        return self


class EvidenceConflict(BaseModel):
    subject: str
    claims: list[str]
    source_urls: list[str]


class EvidencePack(BaseModel):
    topic_id: str
    topic_title: str = ""
    category: TopicCategory = TopicCategory.INDUSTRY
    facts: list[EvidenceFact] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    suggested_angle: str = ""
    inspiration_blueprint: list[str] = Field(default_factory=list, max_length=8)
    primary_inspiration_url: str | None = None
    originality_threshold: float | None = Field(default=None, ge=0, le=1)
    ready_for_draft: bool = False


class WritingProfile(BaseModel):
    account_name: str = "AI信息小屋"
    audience: str = "希望高效了解实用 AI 的公众号读者"
    target_characters: int = Field(default=800, ge=650, le=950)
    paragraph_limit: int = Field(default=80, ge=40, le=80)
    writing_style: Literal[
        "专业理性",
        "温暖治愈",
        "犀利直接",
        "轻松口语",
        "故事感强",
        "干货清单",
    ] = "专业理性"
    creation_mode: Literal["synthesized", "rewrite"] = "synthesized"
    rewrite_goal: Literal[
        "new_article",
        "new_angle",
        "more_conversational",
        "more_actionable",
    ] = "new_article"
    prompt_variant: Literal["aigc", "classic"] = "aigc"
    reference_focus: Literal["mixed", "structure", "tone", "opening"] = "mixed"
    reference_level: Literal["low", "medium", "high"] = "medium"
    custom_instruction: str = Field(default="", max_length=1000)


class AccountProfile(BaseModel):
    """Versioned local account policy used by every creation artifact."""

    id: str = "local-default"
    version: int = Field(default=1, ge=1)
    account_name: str = "AI信息小屋"
    positioning: str = "帮助普通读者高效理解值得关注、能够落地的 AI 信息"
    target_audience: str = "希望高效了解实用 AI 的公众号读者"
    content_pillars: list[str] = Field(
        default_factory=lambda: ["AI 模型", "AI 产品", "行业变化", "实用方法"]
    )
    excluded_topics: list[str] = Field(default_factory=lambda: ["无来源传闻", "纯流量炒作"])
    tone_keywords: list[str] = Field(default_factory=lambda: ["准确", "清晰", "克制", "实用"])
    target_characters: int = Field(default=800, ge=500, le=5000)
    paragraph_limit: int = Field(default=80, ge=40, le=160)
    title_preferences: list[str] = Field(default_factory=lambda: ["信息明确", "不夸张", "体现读者收益"])
    forbidden_phrases: list[str] = Field(default_factory=lambda: ["震惊", "颠覆一切", "闭眼入"])
    accepted_source_tiers: list[str] = Field(default_factory=lambda: ["T1", "T1.5", "T2"])
    citation_style: str = "文末列出可点击来源，关键数字必须有事实卡支持"
    risk_tolerance: Literal["low", "medium", "high"] = "low"
    verified_experience_inventory: list[str] = Field(default_factory=list)
    fixed_viewpoints: list[str] = Field(default_factory=list)
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_writing_profile(self) -> WritingProfile:
        return WritingProfile(
            account_name=self.account_name,
            audience=self.target_audience,
            target_characters=min(max(self.target_characters, 650), 950),
            paragraph_limit=min(max(self.paragraph_limit, 40), 80),
        )


class OpportunityScores(BaseModel):
    account_fit: float = Field(ge=0, le=100)
    audience_value: float = Field(ge=0, le=100)
    differentiation: float = Field(ge=0, le=100)
    event_impact: float = Field(ge=0, le=100)
    evidence_completeness: float = Field(ge=0, le=100)
    timeliness: float = Field(ge=0, le=100)
    production_efficiency: float = Field(ge=0, le=100)

    @property
    def weighted_total(self) -> float:
        return round(
            self.account_fit * 0.20
            + self.audience_value * 0.20
            + self.differentiation * 0.15
            + self.event_impact * 0.15
            + self.evidence_completeness * 0.15
            + self.timeliness * 0.10
            + self.production_efficiency * 0.05,
            2,
        )


class ContentOpportunity(BaseModel):
    id: str
    source_kind: Literal["topic", "item", "story"] = "topic"
    source_id: str
    title: str
    category: str
    scores: OpportunityScores
    creation_score: float = Field(ge=0, le=100)
    recommended_angle: str
    reader_benefit: str
    time_window_hours: int = Field(default=48, ge=1, le=720)
    risks: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    status: Literal["new", "adopted", "ignored", "blocked"] = "new"
    ignore_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class InspirationStructure(BaseModel):
    """Copyright-safe structural traits extracted from one reference article."""

    heading_count: int = Field(default=0, ge=0)
    paragraph_count: int = Field(default=0, ge=0)
    average_paragraph_characters: int = Field(default=0, ge=0)
    short_paragraph_ratio: float = Field(default=0, ge=0, le=1)
    list_item_count: int = Field(default=0, ge=0)
    question_count: int = Field(default=0, ge=0)
    opening_approach: str = "直接进入主题"
    reusable_blueprint: list[str] = Field(default_factory=list, max_length=8)


class InspirationSource(BaseModel):
    """A fetched reference used for research, structure and similarity checks.

    ``content_text`` is persisted with the project because the hard similarity
    gate must remain reproducible after a restart. API payloads deliberately
    expose only its hash and character count, never the copied article body.
    """

    id: str
    role: Literal["primary", "supporting"]
    source_kind: Literal["hot", "user_url", "supporting_url"]
    url: str
    title: str
    publisher: str
    publisher_identity: str
    content_text: str = Field(min_length=1, max_length=50000)
    content_sha256: str = Field(min_length=64, max_length=64)
    source_type: SourceType = SourceType.SECONDARY
    source_type_verified: bool = False
    source_verification_basis: str | None = None
    category: TopicCategory = TopicCategory.INDUSTRY
    published_at: datetime | None = None
    imported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    structure: InspirationStructure = Field(default_factory=InspirationStructure)

    @field_validator("url")
    @classmethod
    def inspiration_url_must_not_be_empty(cls, value: str) -> str:
        return _require_source_url(value)

    @field_validator("published_at", "imported_at")
    @classmethod
    def inspiration_datetimes_must_be_timezone_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value


class SimilarityReport(BaseModel):
    """Reproducible asymmetric overlap from the draft into one source."""

    id: str
    draft_id: str
    draft_version: int = Field(ge=1)
    source_id: str
    source_url: str
    threshold: float = Field(default=0.10, ge=0, le=1)
    score: float = Field(ge=0, le=1)
    ngram_overlap: float = Field(ge=0, le=1)
    longest_match_ratio: float = Field(ge=0, le=1)
    matched_fragments: list[str] = Field(default_factory=list, max_length=5)
    passed: bool
    algorithm: str = "normalized-char-12gram-v1"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source_url")
    @classmethod
    def similarity_source_url_must_not_be_empty(cls, value: str) -> str:
        return _require_source_url(value)


class AuthorInput(BaseModel):
    id: str
    kind: Literal["experience", "opinion", "example", "data"]
    content: str = Field(min_length=1, max_length=5000)
    confirmed: bool = False
    source_urls: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContentBriefSection(BaseModel):
    """One intentional article section bound to approved evidence claims."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    heading: str
    intent: str
    claim_ids: list[str] = Field(default_factory=list, alias="claimIds")


class ContentBrief(BaseModel):
    """Editable, evidence-gated plan used before prose generation."""

    version: int = Field(default=1, ge=1)
    core_thesis: str
    audience_benefit: str
    angle: str
    differentiation: str
    sections: list[ContentBriefSection] = Field(min_length=1)
    required_source_urls: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    author_inputs: list[str] = Field(default_factory=list)
    ready_for_draft: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class CreatorProject(BaseModel):
    """Durable bridge from an existing topic into the creation workflow."""

    id: str
    topic_id: str
    task_id: str
    origin: Literal["topic", "inspiration"] = "topic"
    inspiration_sources: list[InspirationSource] = Field(default_factory=list)
    similarity_reports: list[SimilarityReport] = Field(default_factory=list)
    opportunity: ContentOpportunity | None = None
    profile_version: int = 1
    stage: Literal[
        "opportunity",
        "research",
        "evidence_ready",
        "brief_ready",
        "drafting",
        "review_blocked",
        "review_passed",
        "exported",
    ] = "research"
    brief: ContentBrief | None = None
    author_inputs: list[AuthorInput] = Field(default_factory=list)
    current_draft_id: str | None = None
    draft_versions: list["DraftVersion"] = Field(default_factory=list)
    quality_reports: list["QualityReportRecord"] = Field(default_factory=list)
    export_records: list["ExportRecord"] = Field(default_factory=list)
    human_approved_draft_version: int | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def project_datetimes_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class DraftDocument(BaseModel):
    titles: list[str]
    body: str
    cited_source_urls: list[str] = Field(default_factory=list)
    stage: Literal["research", "review_ready"] = "research"

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_draft_fields(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if "titles" not in normalized and "title" in normalized:
            normalized["titles"] = [normalized["title"]]
        if "body" not in normalized and "content" in normalized:
            normalized["body"] = normalized["content"]
        if "cited_source_urls" not in normalized and "source_urls" in normalized:
            normalized["cited_source_urls"] = normalized["source_urls"]
        return normalized

    @property
    def title(self) -> str:
        return self.titles[0] if self.titles else ""

    @property
    def content(self) -> str:
        return self.body

    @property
    def source_urls(self) -> list[str]:
        return self.cited_source_urls


class QualityIssue(BaseModel):
    code: str
    message: str
    blocking: bool = True
    location: str | None = None
    evidence_claim_ids: list[str] = Field(default_factory=list)
    fix_suggestion: str | None = None


class DraftVersion(BaseModel):
    id: str
    draft_id: str
    version: int = Field(ge=1)
    parent_version: int | None = None
    generation_method: Literal["ai", "rule", "author_edit", "finalize"]
    model: str | None = None
    prompt_hash: str
    prompt_version_id: str | None = None
    llm_call_ids: list[str] = Field(default_factory=list)
    profile_version: int = Field(ge=1)
    selected_title: str
    document: DraftDocument
    cited_claim_ids: list[str] = Field(default_factory=list)
    author_input_ids: list[str] = Field(default_factory=list)
    change_summary: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QualityReportRecord(BaseModel):
    id: str
    draft_id: str
    draft_version: int = Field(ge=1)
    profile_version: int = Field(ge=1)
    issues: list[QualityIssue] = Field(default_factory=list)
    passed: bool
    human_confirmed: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExportRecord(BaseModel):
    id: str
    draft_id: str
    draft_version: int = Field(ge=1)
    format: Literal["markdown", "html", "text", "wechat_html"]
    quality_report_id: str
    content_sha256: str
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PromptVersion(BaseModel):
    """Immutable prompt contract used by one generation stage."""

    id: str
    stage: Literal["probe", "outline", "section", "editor", "semantic", "fallback"]
    version: int = Field(ge=1)
    template_hash: str
    output_schema: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LlmCallRecord(BaseModel):
    """One observable model call or explicit rule-fallback event."""

    id: str
    operation_id: str
    project_id: str | None = None
    draft_id: str | None = None
    stage: Literal["probe", "outline", "section", "editor", "semantic", "fallback"]
    status: Literal["success", "error", "fallback", "skipped"]
    provider: str
    model: str | None = None
    prompt_version_id: str
    prompt_hash: str
    evidence_claim_ids: list[str] = Field(default_factory=list)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    duration_ms: float = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    error_code: str | None = None
    fallback_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GenerationOutcome(BaseModel):
    """Draft plus the provenance needed to create a truthful DraftVersion."""

    document: DraftDocument
    operation_id: str
    method: Literal["ai", "rule"]
    status: Literal["success", "fallback"]
    model: str | None = None
    prompt_version_id: str
    prompt_hash: str
    call_ids: list[str] = Field(default_factory=list)


class CreationReplayItem(BaseModel):
    topic_id: str
    title: str
    generation_method: Literal["ai", "rule"]
    passed: bool
    blocking_issue_codes: list[str] = Field(default_factory=list)
    cited_source_count: int = Field(ge=0)
    evidence_claim_count: int = Field(ge=0)
    visible_characters: int = Field(ge=0)
    duration_ms: float = Field(ge=0)


class CreationReplayReport(BaseModel):
    id: str
    requested_mode: Literal["rule", "current"]
    actual_mode: Literal["ai", "rule", "mixed"]
    profile_version: int = Field(ge=1)
    prompt_version_ids: list[str] = Field(default_factory=list)
    sample_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    source_coverage_rate: float = Field(ge=0, le=1)
    unsupported_number_count: int = Field(ge=0)
    fabricated_experience_count: int = Field(ge=0)
    profile_mismatch_count: int = Field(ge=0)
    median_duration_ms: float = Field(ge=0)
    items: list[CreationReplayItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


CreatorProject.model_rebuild()
