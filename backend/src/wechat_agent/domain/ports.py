"""Domain ports implemented by storage and generation adapters."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from wechat_agent.domain.models import (
    AccountProfile,
    ArticleRecord,
    ContentBrief,
    CurationDecision,
    CreatorProject,
    ContentOpportunity,
    CreationReplayReport,
    DailyReport,
    DraftDocument,
    EvidencePack,
    QualityIssue,
    LlmCallRecord,
    PromptVersion,
    Source,
    SourceRun,
    TopicCandidate,
    WritingProfile,
)


class ArticleRepository(Protocol):
    def save_many(self, articles: Sequence[ArticleRecord]) -> int: ...

    def filter_changed(
        self,
        articles: Sequence[ArticleRecord],
    ) -> list[ArticleRecord]: ...

    def list_recent(self, since: datetime, limit: int = 100) -> list[ArticleRecord]: ...

    def list_all(self, limit: int = 10000) -> list[ArticleRecord]: ...


class CurationDecisionRepository(Protocol):
    """Store the code-owned decision that powers every distribution surface."""

    def save_many(self, decisions: Sequence[CurationDecision]) -> int: ...

    def get_many(self, article_ids: Sequence[str]) -> dict[str, CurationDecision]: ...


class SourceRepository(Protocol):
    """Refresh seed configuration without overwriting operator runtime state."""

    def seed_many(self, sources: Sequence[Source]) -> int: ...

    def save(self, source: Source) -> None: ...

    def get(self, source_id: str) -> Source | None: ...

    def list_all(self) -> list[Source]: ...

    def claim_many(
        self,
        source_ids: Sequence[str],
        *,
        now: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
        due_only: bool,
        force_source_ids: Sequence[str] = (),
    ) -> list[str]: ...

    def release_many(
        self,
        source_ids: Sequence[str],
        *,
        lease_owner: str,
    ) -> None: ...


class SourceRunRepository(Protocol):
    """Store structured per-source collection diagnostics."""

    def save(self, run: SourceRun) -> None: ...

    def list_recent(
        self,
        source_id: str,
        since: datetime,
        limit: int = 100,
    ) -> list[SourceRun]: ...


class DailyReportRepository(Protocol):
    """Store immutable revisions and expose one latest revision per date."""

    def save(self, report: DailyReport) -> None: ...

    def get(self, report_date: date) -> DailyReport | None: ...

    def latest(self) -> DailyReport | None: ...

    def list_recent(self, limit: int = 30) -> list[DailyReport]: ...


class DraftProvider(Protocol):
    def generate(
        self,
        pack: EvidencePack,
        profile: WritingProfile,
        *,
        brief: ContentBrief | None = None,
    ) -> DraftDocument: ...


class TaskRepository(Protocol):
    """Persist task lifecycle records across restarts."""

    def save(self, task_id: str, status: str, status_history: list[str],
             mode: str, collected_article_count: int, window_days: int,
             expanded_categories: list[str], collection_warnings: list[str],
             source_counts: dict[str, int], topic_ids: list[str],
             drafted_topic_ids: list[str], result_json: str | None) -> None: ...

    def load(self, task_id: str) -> dict | None: ...

    def list_all(self) -> list[dict]: ...

    def update_status(self, task_id: str, status: str,
                      status_history: list[str],
                      topic_ids: list[str],
                      drafted_topic_ids: list[str]) -> None: ...


class EvidenceRepository(Protocol):
    """Persist evidence packs keyed by topic id."""

    def save(self, topic_id: str, task_id: str, pack: EvidencePack) -> None: ...

    def load(self, topic_id: str) -> EvidencePack | None: ...


class DraftRepository(Protocol):
    """Persist draft records with full version history."""

    def save(self, draft_id: str, topic_id: str, task_id: str,
             document: DraftDocument, selected_title: str, version: int,
             trusted_body: str, trusted_titles: tuple[str, ...],
             quality_issues: list[QualityIssue],
             versions: list[DraftDocument]) -> None: ...

    def load(self, draft_id: str) -> dict | None: ...

    def list_by_task(self, task_id: str) -> list[dict]: ...


class CreatorProjectRepository(Protocol):
    """Persist the server-owned creation workflow and optimistic version."""

    def save(self, project: CreatorProject) -> None: ...

    def get(self, project_id: str) -> CreatorProject | None: ...

    def get_by_topic(self, topic_id: str) -> CreatorProject | None: ...

    def list_all(self) -> list[CreatorProject]: ...


class AccountProfileRepository(Protocol):
    """Persist immutable account-profile versions."""

    def save(self, profile: AccountProfile) -> None: ...

    def get_current(self, profile_id: str = "local-default") -> AccountProfile | None: ...

    def list_versions(self, profile_id: str = "local-default") -> list[AccountProfile]: ...


class ContentOpportunityRepository(Protocol):
    """Persist scored creation opportunities and operator decisions."""

    def save(self, opportunity: ContentOpportunity) -> None: ...

    def get(self, opportunity_id: str) -> ContentOpportunity | None: ...

    def get_by_source(self, source_id: str) -> ContentOpportunity | None: ...

    def list_all(self) -> list[ContentOpportunity]: ...


class GenerationAuditRepository(Protocol):
    """Persist prompt contracts, model calls and content-quality replays."""

    def save_prompt_version(self, prompt: PromptVersion) -> None: ...

    def list_prompt_versions(self) -> list[PromptVersion]: ...

    def save_call(self, call: LlmCallRecord) -> None: ...

    def list_calls(self, limit: int = 100) -> list[LlmCallRecord]: ...

    def save_replay(self, report: CreationReplayReport) -> None: ...

    def latest_replay(self) -> CreationReplayReport | None: ...

    def list_replays(self, limit: int = 20) -> list[CreationReplayReport]: ...
