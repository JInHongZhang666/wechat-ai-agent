"""Request and response schemas for the content API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from wechat_agent.domain.models import (
    AccountProfile,
    AuthorInput,
    ContentBrief,
    ContentBriefSection,
    QualityIssue,
    WritingProfile,
)


class AccountProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int = Field(ge=1)
    account_name: str = Field(min_length=1, max_length=100, alias="accountName")
    positioning: str = Field(min_length=1, max_length=1000)
    target_audience: str = Field(min_length=1, max_length=1000, alias="targetAudience")
    content_pillars: list[str] = Field(min_length=1, max_length=20, alias="contentPillars")
    excluded_topics: list[str] = Field(default_factory=list, max_length=30, alias="excludedTopics")
    tone_keywords: list[str] = Field(min_length=1, max_length=20, alias="toneKeywords")
    target_characters: int = Field(ge=500, le=5000, alias="targetCharacters")
    paragraph_limit: int = Field(ge=40, le=160, alias="paragraphLimit")
    title_preferences: list[str] = Field(default_factory=list, max_length=20, alias="titlePreferences")
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=50, alias="forbiddenPhrases")
    accepted_source_tiers: list[str] = Field(min_length=1, max_length=10, alias="acceptedSourceTiers")
    citation_style: str = Field(min_length=1, max_length=1000, alias="citationStyle")
    risk_tolerance: str = Field(pattern="^(low|medium|high)$", alias="riskTolerance")
    verified_experience_inventory: list[str] = Field(default_factory=list, max_length=50, alias="verifiedExperienceInventory")
    fixed_viewpoints: list[str] = Field(default_factory=list, max_length=50, alias="fixedViewpoints")

    def to_domain(self) -> AccountProfile:
        return AccountProfile.model_validate(self.model_dump())


class OpportunityDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    decision: str = Field(pattern="^(adopted|ignored)$")
    reason: str | None = Field(default=None, max_length=1000)


class AuthorInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_version: int = Field(ge=1, alias="projectVersion")
    id: str | None = None
    kind: str = Field(pattern="^(experience|opinion|example|data)$")
    content: str = Field(min_length=1, max_length=5000)
    confirmed: bool = False
    source_urls: list[str] = Field(default_factory=list, max_length=20, alias="sourceUrls")

    def to_domain(self, *, input_id: str) -> AuthorInput:
        return AuthorInput(
            id=input_id,
            kind=self.kind,
            content=self.content,
            confirmed=self.confirmed,
            source_urls=self.source_urls,
        )


class DraftApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int = Field(ge=1)
    confirmation: str = Field(pattern="^我已核对事实、作者材料和表达边界$")


class WritingProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    account_name: str = Field(default="AI信息小屋", alias="accountName")
    audience: str = "希望高效了解实用 AI 的公众号读者"
    target_characters: int = Field(
        default=800,
        ge=650,
        le=950,
        alias="targetCharacters",
    )
    paragraph_limit: int = Field(default=80, ge=40, le=80, alias="paragraphLimit")
    writing_style: str = Field(
        default="专业理性",
        pattern="^(专业理性|温暖治愈|犀利直接|轻松口语|故事感强|干货清单)$",
        alias="writingStyle",
    )
    creation_mode: str = Field(
        default="synthesized",
        pattern="^(synthesized|rewrite)$",
        alias="creationMode",
    )
    rewrite_goal: str = Field(
        default="new_article",
        pattern="^(new_article|new_angle|more_conversational|more_actionable)$",
        alias="rewriteGoal",
    )
    prompt_variant: str = Field(
        default="aigc",
        pattern="^(aigc|classic)$",
        alias="promptVariant",
    )
    reference_focus: str = Field(
        default="mixed",
        pattern="^(mixed|structure|tone|opening)$",
        alias="referenceFocus",
    )
    reference_level: str = Field(
        default="medium",
        pattern="^(low|medium|high)$",
        alias="referenceLevel",
    )
    custom_instruction: str = Field(
        default="",
        max_length=1000,
        alias="customInstruction",
    )

    def to_domain(self) -> WritingProfile:
        return WritingProfile.model_validate(self.model_dump())


class DraftPolishRequest(WritingProfileRequest):
    version: int = Field(ge=1)


class DraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    selected_title: str = Field(min_length=1, alias="selectedTitle")
    body: str
    version: int | None = Field(default=None, ge=1)


class CreatorProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    topic_id: str = Field(min_length=1, alias="topicId")


class InspirationPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    primary_url: str = Field(min_length=8, max_length=2048, alias="primaryUrl")
    primary_title: str | None = Field(
        default=None,
        max_length=300,
        alias="primaryTitle",
    )
    primary_text: str | None = Field(
        default=None,
        min_length=120,
        max_length=50000,
        alias="primaryText",
    )
    source_kind: str = Field(
        default="user_url",
        pattern="^(hot|user_url)$",
        alias="sourceKind",
    )


class InspirationProjectCreateRequest(InspirationPreviewRequest):
    supporting_urls: list[str] = Field(
        default_factory=list,
        max_length=5,
        alias="supportingUrls",
    )


class ContentBriefUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int = Field(ge=1)
    core_thesis: str = Field(min_length=1, max_length=1000, alias="coreThesis")
    audience_benefit: str = Field(
        min_length=1,
        max_length=1000,
        alias="audienceBenefit",
    )
    angle: str = Field(min_length=1, max_length=1000)
    differentiation: str = Field(min_length=1, max_length=1000)
    sections: list[ContentBriefSection] = Field(min_length=1, max_length=12)
    required_source_urls: list[str] = Field(
        default_factory=list,
        alias="requiredSourceUrls",
    )
    risks: list[str] = Field(default_factory=list, max_length=20)
    author_inputs: list[str] = Field(
        default_factory=list,
        max_length=20,
        alias="authorInputs",
    )

    def to_domain(self) -> ContentBrief:
        return ContentBrief.model_validate(self.model_dump())


class QualityReport(BaseModel):
    issues: list[QualityIssue]
    blocking_issue_count: int = Field(ge=0)
    can_review: bool
    can_export: bool

    @classmethod
    def from_issues(cls, issues: list[QualityIssue]) -> "QualityReport":
        blocking_issue_count = sum(issue.blocking for issue in issues)
        return cls(
            issues=issues,
            blocking_issue_count=blocking_issue_count,
            can_review=blocking_issue_count == 0,
            can_export=blocking_issue_count == 0,
        )


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorBody
