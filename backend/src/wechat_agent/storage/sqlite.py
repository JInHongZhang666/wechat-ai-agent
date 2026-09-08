"""SQLite persistence for normalized article records and Phase 3 state."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    JSON,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    delete,
    inspect,
    null,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from wechat_agent.domain.models import (
    AccountProfile,
    ArticleRecord,
    CurationDecision,
    CreatorProject,
    ContentOpportunity,
    CreationReplayReport,
    DailyReport,
    DailyReportStatus,
    DraftDocument,
    EngagementMetrics,
    EvidencePack,
    HeatConfidence,
    QualityIssue,
    LlmCallRecord,
    PromptVersion,
    CircuitState,
    Source,
    SourceKind,
    SourceRun,
    SourceRunStatus,
    SourceTier,
    SourceType,
    TopicCategory,
)
from wechat_agent.domain.fingerprints import article_content_fingerprint
from wechat_agent.domain.sync import (
    SelectedChange,
    SelectedChangesPage,
    SelectedFieldMode,
    SelectedSnapshotPage,
    SelectedSyncItem,
    SnapshotRequiredError,
)
from wechat_agent.evaluation.models import (
    BlindSelectionReviewItem,
    BlindSelectionReviewPage,
    DoubleReviewRecord,
    DoubleReviewStatus,
    GoldLabel,
    GoldSample,
    LabelingCandidate,
    ReviewParticipant,
    ReviewerClaimRequest,
    SelectionAdjudicationDecision,
    SelectionConflictItem,
    SelectionConflictPage,
    SelectionReviewDecision,
    SelectionReviewItem,
    SelectionReviewPage,
    SelectionReviewStatus,
    WorkbenchItem,
    WorkbenchPage,
    WorkbenchStatus,
)
from wechat_agent.evaluation.review import (
    AdjudicationAlreadyExistsError,
    AdjudicationNotRequiredError,
    AdjudicatorNotIndependentError,
    FirstReviewerAlreadyClaimedError,
    ReviewAlreadyCompletedError,
    ReviewItemNotFoundError,
    ReviewSetNotFoundError,
    ReviewSubmissionAlreadyExistsError,
    ReviewerNotIndependentError,
    SecondReviewerAlreadyClaimedError,
    SecondReviewIncompleteError,
    normalize_reviewer_identity,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_METADATA = MetaData()

_SCHEMA_MIGRATIONS = Table(
    "schema_migrations",
    _METADATA,
    Column("version", Integer, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("applied_at", String, nullable=False),
)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Articles table (Phase 1)
# ---------------------------------------------------------------------------

_ARTICLES = Table(
    "articles",
    _METADATA,
    Column("source_url", String, primary_key=True),
    Column("id", String, nullable=False),
    Column("source_id", String, nullable=True),
    Column("title", String, nullable=False),
    Column("published_at", String, nullable=False),
    Column("collected_at", String, nullable=False),
    Column("summary", String, nullable=False),
    Column("category", String, nullable=False),
    Column("engagement", JSON, nullable=True),
    Column("source_metadata", JSON, nullable=False),
    Column("content_fingerprint", String, nullable=False),
)
_ARTICLES_PUBLISHED_INDEX = Index(
    "ix_articles_published_id",
    _ARTICLES.c.published_at,
    _ARTICLES.c.id,
)
_ARTICLES_COLLECTED_INDEX = Index(
    "ix_articles_collected_id",
    _ARTICLES.c.collected_at,
    _ARTICLES.c.id,
)

_CURATION_DECISIONS = Table(
    "curation_decisions",
    _METADATA,
    Column("article_id", String, primary_key=True),
    Column("distribution_score", Float, nullable=False),
    Column("selected", Boolean, nullable=False),
    Column("eligible", Boolean, nullable=False, default=True),
    Column("components", JSON, nullable=False),
    Column("threshold", Float, nullable=False),
    Column("reason", Text, nullable=False),
    Column("score_confidence", String, nullable=False),
    Column("strategy_version", String, nullable=False),
    Column("scored_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

# ---------------------------------------------------------------------------
# Source registry and durable per-source runs (Phase 2B)
# ---------------------------------------------------------------------------

_SOURCES = Table(
    "sources",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("homepage", String, nullable=True),
    Column("feed_url", String, nullable=True),
    Column("kind", String, nullable=False),
    Column("tier", String, nullable=False),
    Column("publisher_group", String, nullable=False),
    Column("evidence_eligible", Boolean, nullable=False),
    Column("signal_only", Boolean, nullable=False),
    Column("categories", JSON, nullable=False),
    Column("language", String, nullable=False),
    Column("schedule_minutes", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("parser_version", String, nullable=False),
    Column("circuit_state", String, nullable=False),
    Column("consecutive_failures", Integer, nullable=False),
    Column("last_success_at", String, nullable=True),
    Column("next_run_at", String, nullable=True),
    Column("success_rate_7d", Float, nullable=True),
    Column("last_error_code", String, nullable=True),
    Column("last_checked_at", String, nullable=True),
    Column("lease_owner", String, nullable=True),
    Column("lease_expires_at", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
Index("ix_sources_enabled_tier", _SOURCES.c.enabled, _SOURCES.c.tier)

_SOURCE_RUNS = Table(
    "source_runs",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("source_id", String, nullable=False),
    Column("task_id", String, nullable=True),
    Column("window_days", Integer, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String, nullable=False),
    Column("status", String, nullable=False),
    Column("stage", String, nullable=False),
    Column("http_status", Integer, nullable=True),
    Column("fetched_count", Integer, nullable=False),
    Column("stored_count", Integer, nullable=False),
    Column("skipped_count", Integer, nullable=False),
    Column("error_code", String, nullable=True),
    Column("error_summary", Text, nullable=True),
    Column("reliability_excluded_reason", String, nullable=True),
    Column("retry_after_seconds", Integer, nullable=True),
    Column("duration_ms", Integer, nullable=False),
)
Index(
    "ix_source_runs_source_started",
    _SOURCE_RUNS.c.source_id,
    _SOURCE_RUNS.c.started_at,
)

# ---------------------------------------------------------------------------
# Durable daily reports (Phase 2B)
# ---------------------------------------------------------------------------

_DAILY_REPORTS = Table(
    "daily_reports",
    _METADATA,
    Column("report_date", String, primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("timezone", String, nullable=False),
    Column("status", String, nullable=False),
    Column("lead", Text, nullable=False),
    Column("section_item_ids", JSON, nullable=False),
    Column("flash_item_ids", JSON, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("strategy_version", String, nullable=False),
    Column("source_snapshot_at", String, nullable=False),
    Column("generated_at", String, nullable=False),
    Column("published_at", String, nullable=True),
    Column("item_count", Integer, nullable=False),
    Column("source_count", Integer, nullable=False),
    Column("content_hash", String, nullable=False),
)
Index(
    "ix_daily_reports_date_revision",
    _DAILY_REPORTS.c.report_date,
    _DAILY_REPORTS.c.revision,
)

# ---------------------------------------------------------------------------
# Durable selected-set synchronization (Phase 2C)
# ---------------------------------------------------------------------------

_SELECTED_SYNC_ITEMS = Table(
    "selected_sync_items",
    _METADATA,
    Column("item_id", String, primary_key=True),
    Column("payload_default", JSON, nullable=False),
    Column("payload_minimal", JSON, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

_SELECTED_SYNC_CHANGES = Table(
    "selected_sync_changes",
    _METADATA,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("op", String, nullable=False),
    Column("item_id", String, nullable=False),
    Column("payload_default", JSON, nullable=True),
    Column("payload_minimal", JSON, nullable=True),
    Column("changed_at", String, nullable=False),
)

_SELECTED_SYNC_STATE = Table(
    "selected_sync_state",
    _METADATA,
    Column("id", Integer, primary_key=True),
    Column("latest_sequence", Integer, nullable=False),
    Column("floor_sequence", Integer, nullable=False),
    Column("updated_at", String, nullable=False),
)

_SELECTED_SYNC_CURSORS = Table(
    "selected_sync_cursors",
    _METADATA,
    Column("token", String, primary_key=True),
    Column("sequence", Integer, nullable=False),
    Column("fields", String, nullable=False),
    Column("created_at", String, nullable=False),
)
_SELECTED_SYNC_CURSOR_SEQUENCE_INDEX = Index(
    "ux_selected_sync_cursor_sequence_fields",
    _SELECTED_SYNC_CURSORS.c.sequence,
    _SELECTED_SYNC_CURSORS.c.fields,
    unique=True,
)

_SELECTED_SYNC_SNAPSHOTS = Table(
    "selected_sync_snapshots",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("cursor_token", String, nullable=False),
    Column("fields", String, nullable=False),
    Column("page_limit", Integer, nullable=False),
    Column("items", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("expires_at", String, nullable=False),
)

_SELECTED_SYNC_SNAPSHOT_PAGES = Table(
    "selected_sync_snapshot_pages",
    _METADATA,
    Column("token", String, primary_key=True),
    Column("snapshot_id", String, nullable=False),
    Column("page_index", Integer, nullable=False),
)
_SELECTED_SYNC_SNAPSHOT_PAGE_INDEX = Index(
    "ux_selected_sync_snapshot_page",
    _SELECTED_SYNC_SNAPSHOT_PAGES.c.snapshot_id,
    _SELECTED_SYNC_SNAPSHOT_PAGES.c.page_index,
    unique=True,
)

# ---------------------------------------------------------------------------
# Tasks table (Phase 3.2)
# ---------------------------------------------------------------------------

_TASKS = Table(
    "tasks",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("status_history", Text, nullable=False),       # JSON array
    Column("mode", String, nullable=False, default="demo"),
    Column("collected_article_count", Integer, default=0),
    Column("window_days", Integer, default=0),
    Column("expanded_categories", Text, nullable=True),   # JSON array
    Column("collection_warnings", Text, nullable=True),   # JSON array
    Column("source_counts", Text, nullable=True),         # JSON object
    Column("topic_ids", Text, nullable=True),             # JSON array
    Column("drafted_topic_ids", Text, nullable=True),     # JSON array
    Column("result_json", Text, nullable=True),           # PipelineResult as JSON
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

# ---------------------------------------------------------------------------
# Evidence packs table (Phase 3.2)
# ---------------------------------------------------------------------------

_EVIDENCE = Table(
    "evidence_packs",
    _METADATA,
    Column("topic_id", String, primary_key=True),
    Column("task_id", String, nullable=False),
    Column("pack_json", Text, nullable=False),            # EvidencePack as JSON
    Column("created_at", String, nullable=False),
)

# ---------------------------------------------------------------------------
# Drafts table (Phase 3.2)
# ---------------------------------------------------------------------------

_DRAFTS = Table(
    "drafts",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("topic_id", String, nullable=False),
    Column("task_id", String, nullable=False),
    Column("document_json", Text, nullable=False),         # DraftDocument as JSON
    Column("selected_title", String, nullable=False),
    Column("version", Integer, default=1),
    Column("trusted_body", Text, nullable=False),
    Column("trusted_titles", Text, nullable=False),        # JSON array
    Column("quality_issues_json", Text, nullable=True),    # JSON array
    Column("versions_json", Text, nullable=False),         # JSON array of DraftDocument
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

_CREATOR_PROJECTS = Table(
    "creator_projects",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("topic_id", String, nullable=False, unique=True),
    Column("task_id", String, nullable=False),
    Column("project_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
Index("ix_creator_projects_updated", _CREATOR_PROJECTS.c.updated_at)

_ACCOUNT_PROFILES = Table(
    "account_profiles",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("profile_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("profile_json", Text, nullable=False),
    Column("effective_at", String, nullable=False),
)
Index(
    "ux_account_profiles_profile_version",
    _ACCOUNT_PROFILES.c.profile_id,
    _ACCOUNT_PROFILES.c.version,
    unique=True,
)

_CONTENT_OPPORTUNITIES = Table(
    "content_opportunities",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("source_id", String, nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("creation_score", Float, nullable=False),
    Column("opportunity_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
Index(
    "ix_content_opportunities_status_score",
    _CONTENT_OPPORTUNITIES.c.status,
    _CONTENT_OPPORTUNITIES.c.creation_score,
)

_PROMPT_VERSIONS = Table(
    "prompt_versions",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("stage", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("prompt_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
)
Index(
    "ux_prompt_versions_stage_version",
    _PROMPT_VERSIONS.c.stage,
    _PROMPT_VERSIONS.c.version,
    unique=True,
)

_LLM_CALL_RECORDS = Table(
    "llm_call_records",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("operation_id", String, nullable=False),
    Column("project_id", String, nullable=True),
    Column("draft_id", String, nullable=True),
    Column("stage", String, nullable=False),
    Column("status", String, nullable=False),
    Column("model", String, nullable=True),
    Column("call_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
)
Index("ix_llm_calls_created", _LLM_CALL_RECORDS.c.created_at)
Index("ix_llm_calls_project", _LLM_CALL_RECORDS.c.project_id, _LLM_CALL_RECORDS.c.created_at)

_CREATION_REPLAY_REPORTS = Table(
    "creation_replay_reports",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("requested_mode", String, nullable=False),
    Column("actual_mode", String, nullable=False),
    Column("sample_count", Integer, nullable=False),
    Column("pass_rate", Float, nullable=False),
    Column("report_json", Text, nullable=False),
    Column("created_at", String, nullable=False),
)
Index("ix_creation_replays_created", _CREATION_REPLAY_REPORTS.c.created_at)

# ---------------------------------------------------------------------------
# Frozen evaluation candidates and completed annotations (R1)
# ---------------------------------------------------------------------------

_EVALUATION_CANDIDATES = Table(
    "evaluation_candidates",
    _METADATA,
    Column("article_id", String, primary_key=True),
    Column("position", Integer, nullable=False, unique=True),
    Column("category", String, nullable=False),
    Column("title", Text, nullable=False),
    Column("candidate_json", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)
Index(
    "ix_evaluation_candidates_category_position",
    _EVALUATION_CANDIDATES.c.category,
    _EVALUATION_CANDIDATES.c.position,
)

_EVALUATION_ANNOTATIONS = Table(
    "evaluation_annotations",
    _METADATA,
    Column("article_id", String, primary_key=True),
    Column("label_json", JSON, nullable=False),
    Column("annotation_method", String, nullable=False),
    Column("annotator", String, nullable=False),
    Column("updated_at", String, nullable=False),
)

_EVALUATION_REVIEW_SETS = Table(
    "evaluation_review_sets",
    _METADATA,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("target_count", Integer, nullable=False),
    Column("criteria_json", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)

_EVALUATION_REVIEW_ITEMS = Table(
    "evaluation_review_items",
    _METADATA,
    Column("review_set_id", String, primary_key=True),
    Column("article_id", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("reason", String, nullable=False),
    Column("initial_label_json", JSON, nullable=False),
    Column("review_json", JSON, nullable=True),
    Column("created_at", String, nullable=False),
    Column("reviewed_at", String, nullable=True),
)
_EVALUATION_REVIEW_POSITION_INDEX = Index(
    "ux_evaluation_review_items_set_position",
    _EVALUATION_REVIEW_ITEMS.c.review_set_id,
    _EVALUATION_REVIEW_ITEMS.c.position,
    unique=True,
)

_EVALUATION_REVIEW_PARTICIPANTS = Table(
    "evaluation_review_participants",
    _METADATA,
    Column("review_set_id", String, primary_key=True),
    Column("role", String, primary_key=True),
    Column("reviewer", String, nullable=False),
    Column("reviewer_key", String, nullable=False),
    Column("claimed_at", String, nullable=False),
)

_EVALUATION_REVIEW_SUBMISSIONS = Table(
    "evaluation_review_submissions",
    _METADATA,
    Column("review_set_id", String, primary_key=True),
    Column("item_key", String, primary_key=True),
    Column("reviewer_role", String, primary_key=True),
    Column("reviewer", String, nullable=False),
    Column("reviewer_key", String, nullable=False),
    Column("decision_json", JSON, nullable=False),
    Column("submitted_at", String, nullable=False),
)

_EVALUATION_REVIEW_ADJUDICATIONS = Table(
    "evaluation_review_adjudications",
    _METADATA,
    Column("review_set_id", String, primary_key=True),
    Column("item_key", String, primary_key=True),
    Column("adjudicator", String, nullable=False),
    Column("adjudicator_key", String, nullable=False),
    Column("decision_json", JSON, nullable=False),
    Column("adjudicated_at", String, nullable=False),
)


# ---------------------------------------------------------------------------
# Article repository (Phase 1, unchanged)
# ---------------------------------------------------------------------------


class SqliteArticleRepository:
    """Store ``ArticleRecord`` values using their source URL as the identity."""

    def __init__(self, database_url: str) -> None:
        engine_options: dict[str, object] = {}
        url = make_url(database_url)
        if url.get_backend_name() == "sqlite" and url.database in (None, ":memory:"):
            engine_options = {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        self._engine = create_engine(database_url, **engine_options)
        _run_schema_migrations(self._engine)

    @property
    def engine(self):
        return self._engine

    def save_many(self, articles: Sequence[ArticleRecord]) -> int:
        if not articles:
            return 0

        with self._engine.begin() as connection:
            for article in articles:
                values = _article_values(article)
                statement = insert(_ARTICLES).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=[_ARTICLES.c.source_url],
                    set_={
                        column.name: getattr(statement.excluded, column.name)
                        for column in _ARTICLES.c
                        if column.name != "source_url"
                    },
                )
                connection.execute(statement)
        return len(articles)

    def filter_changed(
        self,
        articles: Sequence[ArticleRecord],
    ) -> list[ArticleRecord]:
        """Return only new URLs or rows whose content fingerprint changed."""
        unique = {article.source_url: article for article in articles}
        if not unique:
            return []
        statement = select(
            _ARTICLES.c.source_url,
            _ARTICLES.c.content_fingerprint,
        ).where(_ARTICLES.c.source_url.in_(tuple(unique)))
        with self._engine.connect() as connection:
            stored = {
                row.source_url: row.content_fingerprint
                for row in connection.execute(statement)
            }
        return [
            article
            for url, article in unique.items()
            if stored.get(url) != article_content_fingerprint(article)
        ]

    def list_recent(self, since: datetime, limit: int = 100) -> list[ArticleRecord]:
        since_utc = _as_utc(since).isoformat()
        statement = (
            select(_ARTICLES)
            .where(_ARTICLES.c.published_at >= since_utc)
            .order_by(_ARTICLES.c.published_at.desc())
            .limit(limit)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_article_from_row(row) for row in rows]

    def list_all(self, limit: int = 10000) -> list[ArticleRecord]:
        statement = (
            select(_ARTICLES)
            .order_by(_ARTICLES.c.published_at.desc(), _ARTICLES.c.id.desc())
            .limit(limit)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_article_from_row(row) for row in rows]


def _article_values(article: ArticleRecord) -> dict[str, object]:
    return {
        "source_url": article.source_url,
        "id": article.id,
        "source_id": article.source_id,
        "title": article.title,
        "published_at": _as_utc(article.published_at).isoformat(),
        "collected_at": _as_utc(article.collected_at).isoformat(),
        "summary": article.summary,
        "category": article.category.value,
        "engagement": (
            article.engagement.model_dump() if article.engagement is not None else None
        ),
        "source_metadata": {
            "source_name": article.source_name,
            "source_url": article.source_url,
            "source_type": article.source_type.value,
            "source_identity": article.source_identity,
            "source_type_verified": article.source_type_verified,
            "source_verification_basis": article.source_verification_basis,
            "source_confidence": article.source_confidence,
            "tier": article.tier,
        },
        "content_fingerprint": article_content_fingerprint(article),
    }


def _article_from_row(row: object) -> ArticleRecord:
    source_metadata = row["source_metadata"]
    engagement = row["engagement"]
    return ArticleRecord(
        id=row["id"],
        source_id=row.get("source_id"),
        title=row["title"],
        source_name=source_metadata["source_name"],
        source_url=row["source_url"],
        source_type=SourceType(source_metadata.get("source_type", "secondary")),
        source_identity=source_metadata.get("source_identity"),
        source_type_verified=source_metadata.get("source_type_verified", False),
        source_verification_basis=source_metadata.get("source_verification_basis"),
        source_confidence=source_metadata.get("source_confidence"),
        tier=source_metadata.get("tier"),
        published_at=datetime.fromisoformat(row["published_at"]),
        collected_at=datetime.fromisoformat(row["collected_at"]),
        summary=row["summary"],
        category=TopicCategory(row["category"]),
        engagement=EngagementMetrics.model_validate(engagement)
        if engagement is not None
        else None,
    )


# ---------------------------------------------------------------------------
# Versioned schema migrations
# ---------------------------------------------------------------------------


def _run_schema_migrations(engine) -> None:
    """Apply ordered, idempotent migrations before repositories are used."""
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "name VARCHAR NOT NULL UNIQUE, "
            "applied_at VARCHAR NOT NULL"
            ")"
        )
        applied = {
            int(row[0])
            for row in connection.exec_driver_sql(
                "SELECT version FROM schema_migrations"
            )
        }

    migrations = (
        (1, "baseline_metadata", _migration_create_current_metadata),
        (2, "curation_decision_eligible", _migration_add_curation_eligible),
        (3, "source_registry", _migration_add_article_source_id),
        (4, "source_run_window", _migration_add_source_run_window),
        (5, "source_claims_retry_after", _migration_add_source_claims),
        (6, "source_run_task_link", _migration_add_source_run_task_link),
        (7, "daily_report_revisions", _migration_create_daily_reports),
        (8, "feed_keyset_indexes", _migration_create_feed_keyset_indexes),
        (9, "selected_sync_protocol", _migration_create_selected_sync),
        (10, "evaluation_workbench", _migration_create_evaluation_workbench),
        (11, "evaluation_selection_review", _migration_create_evaluation_reviews),
        (12, "evaluation_double_review", _migration_create_evaluation_double_review),
        (13, "retire_unreachable_official_feeds", _migration_retire_unreachable_official_feeds),
        (14, "creator_projects", _migration_create_creator_projects),
        (15, "creation_workflow_records", _migration_create_creation_workflow_records),
        (16, "generation_audit_and_replay", _migration_create_generation_audit),
        (17, "source_run_reliability_exclusions", _migration_add_source_run_reliability_exclusions),
        (18, "story_shadow_mode", _migration_create_story_shadow_mode),
    )
    for version, name, migration in migrations:
        if version in applied:
            continue
        with engine.begin() as connection:
            migration(connection)
            connection.execute(
                insert(_SCHEMA_MIGRATIONS).values(
                    version=version,
                    name=name,
                    applied_at=_iso_now(),
                )
            )


def _migration_create_current_metadata(connection) -> None:
    _METADATA.create_all(bind=connection)


def _migration_add_curation_eligible(connection) -> None:
    if _has_column(connection, "curation_decisions", "eligible"):
        return
    connection.exec_driver_sql(
        "ALTER TABLE curation_decisions "
        "ADD COLUMN eligible BOOLEAN NOT NULL DEFAULT 1"
    )


def _migration_add_article_source_id(connection) -> None:
    if not _has_column(connection, "articles", "source_id"):
        connection.exec_driver_sql(
            "ALTER TABLE articles ADD COLUMN source_id VARCHAR"
        )
    rows = connection.exec_driver_sql(
        "SELECT source_url, source_metadata FROM articles "
        "WHERE source_id IS NULL"
    ).all()
    for source_url, raw_metadata in rows:
        metadata = (
            json.loads(raw_metadata)
            if isinstance(raw_metadata, str)
            else raw_metadata
        )
        source_id = (metadata or {}).get("source_identity")
        if source_id:
            connection.exec_driver_sql(
                "UPDATE articles SET source_id = ? WHERE source_url = ?",
                (source_id, source_url),
            )


def _migration_add_source_run_window(connection) -> None:
    if _has_column(connection, "source_runs", "window_days"):
        return
    connection.exec_driver_sql(
        "ALTER TABLE source_runs "
        "ADD COLUMN window_days INTEGER NOT NULL DEFAULT 7"
    )


def _migration_add_source_claims(connection) -> None:
    if not _has_column(connection, "sources", "lease_owner"):
        connection.exec_driver_sql(
            "ALTER TABLE sources ADD COLUMN lease_owner VARCHAR"
        )
    if not _has_column(connection, "sources", "lease_expires_at"):
        connection.exec_driver_sql(
            "ALTER TABLE sources ADD COLUMN lease_expires_at VARCHAR"
        )
    if not _has_column(connection, "source_runs", "retry_after_seconds"):
        connection.exec_driver_sql(
            "ALTER TABLE source_runs ADD COLUMN retry_after_seconds INTEGER"
        )
    if not _has_column(connection, "source_runs", "stage"):
        connection.exec_driver_sql(
            "ALTER TABLE source_runs "
            "ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'complete'"
        )


def _migration_add_source_run_task_link(connection) -> None:
    if _has_column(connection, "source_runs", "task_id"):
        return
    connection.exec_driver_sql(
        "ALTER TABLE source_runs ADD COLUMN task_id VARCHAR"
    )


def _migration_create_daily_reports(connection) -> None:
    _DAILY_REPORTS.create(bind=connection, checkfirst=True)


def _migration_create_feed_keyset_indexes(connection) -> None:
    for index in (_ARTICLES_PUBLISHED_INDEX, _ARTICLES_COLLECTED_INDEX):
        index.create(bind=connection, checkfirst=True)


def _migration_create_selected_sync(connection) -> None:
    for table in (
        _SELECTED_SYNC_ITEMS,
        _SELECTED_SYNC_CHANGES,
        _SELECTED_SYNC_STATE,
        _SELECTED_SYNC_CURSORS,
        _SELECTED_SYNC_SNAPSHOTS,
        _SELECTED_SYNC_SNAPSHOT_PAGES,
    ):
        table.create(bind=connection, checkfirst=True)
    for index in (
        _SELECTED_SYNC_CURSOR_SEQUENCE_INDEX,
        _SELECTED_SYNC_SNAPSHOT_PAGE_INDEX,
    ):
        index.create(bind=connection, checkfirst=True)


def _migration_create_evaluation_workbench(connection) -> None:
    for table in (_EVALUATION_CANDIDATES, _EVALUATION_ANNOTATIONS):
        table.create(bind=connection, checkfirst=True)
    for index in _EVALUATION_CANDIDATES.indexes:
        index.create(bind=connection, checkfirst=True)


def _migration_create_evaluation_reviews(connection) -> None:
    for table in (_EVALUATION_REVIEW_SETS, _EVALUATION_REVIEW_ITEMS):
        table.create(bind=connection, checkfirst=True)
    _EVALUATION_REVIEW_POSITION_INDEX.create(
        bind=connection,
        checkfirst=True,
    )


def _migration_retire_unreachable_official_feeds(connection) -> None:
    """Retire replaced feeds without deleting their source/run history."""
    connection.execute(
        _SOURCES.update()
        .where(
            _SOURCES.c.id.in_(
                (
                    "official:google-ai",
                    "official:hugging-face",
                    "official:mistral-ai",
                )
            )
        )
        .values(
            enabled=False,
            next_run_at=None,
            last_error_code="retired_runtime_unreachable_feed",
            lease_owner=None,
            lease_expires_at=None,
            updated_at=_iso_now(),
        )
    )


def _migration_create_creator_projects(connection) -> None:
    _CREATOR_PROJECTS.create(bind=connection, checkfirst=True)
    for index in _CREATOR_PROJECTS.indexes:
        index.create(bind=connection, checkfirst=True)


def _migration_create_creation_workflow_records(connection) -> None:
    for table in (_ACCOUNT_PROFILES, _CONTENT_OPPORTUNITIES):
        table.create(bind=connection, checkfirst=True)
    for table in (_ACCOUNT_PROFILES, _CONTENT_OPPORTUNITIES):
        for index in table.indexes:
            index.create(bind=connection, checkfirst=True)


def _migration_create_generation_audit(connection) -> None:
    for table in (_PROMPT_VERSIONS, _LLM_CALL_RECORDS, _CREATION_REPLAY_REPORTS):
        table.create(bind=connection, checkfirst=True)
        for index in table.indexes:
            index.create(bind=connection, checkfirst=True)


def _migration_add_source_run_reliability_exclusions(connection) -> None:
    """Add an auditable exclusion marker and backfill confirmed outages.

    The original status and transport error remain untouched.  A task enters
    the outage state only after a broad all-failed transport batch; subsequent
    all-failed probe batches stay excluded until any source succeeds.
    """
    if not _has_column(connection, "source_runs", "reliability_excluded_reason"):
        connection.exec_driver_sql(
            "ALTER TABLE source_runs "
            "ADD COLUMN reliability_excluded_reason VARCHAR"
        )

    rows = connection.exec_driver_sql(
        "SELECT id, task_id, started_at, status, error_code "
        "FROM source_runs WHERE task_id IS NOT NULL "
        "ORDER BY started_at, id"
    ).mappings().all()
    tasks: dict[str, list[object]] = defaultdict(list)
    task_started_at: dict[str, str] = {}
    for row in rows:
        task_id = str(row["task_id"])
        tasks[task_id].append(row)
        task_started_at.setdefault(task_id, str(row["started_at"]))

    transient_errors = {
        "http_timeout",
        "network_error",
        "global_network_outage",
    }
    outage_active = False
    excluded_ids: list[str] = []
    for task_id in sorted(tasks, key=lambda item: (task_started_at[item], item)):
        task_rows = [row for row in tasks[task_id] if row["status"] != "skipped"]
        if not task_rows:
            continue
        successes = [
            row for row in task_rows if row["status"] in {"success", "partial"}
        ]
        transient_failures = [
            row
            for row in task_rows
            if row["status"] == "failed" and row["error_code"] in transient_errors
        ]
        broad_outage = (
            len(task_rows) >= 4
            and len(transient_failures) * 10 >= len(task_rows) * 6
            and not successes
        )
        continued_probe_failure = (
            outage_active
            and len(transient_failures) == len(task_rows)
        )
        if broad_outage or continued_probe_failure:
            outage_active = True
            excluded_ids.extend(str(row["id"]) for row in transient_failures)
        elif successes:
            outage_active = False

    if excluded_ids:
        connection.execute(
            _SOURCE_RUNS.update()
            .where(_SOURCE_RUNS.c.id.in_(excluded_ids))
            .values(reliability_excluded_reason="global_network_outage")
        )


def _migration_create_story_shadow_mode(connection) -> None:
    from wechat_agent.storage.story_sqlite import create_story_shadow_schema

    create_story_shadow_schema(connection)


def _migration_create_evaluation_double_review(connection) -> None:
    for table in (
        _EVALUATION_REVIEW_PARTICIPANTS,
        _EVALUATION_REVIEW_SUBMISSIONS,
        _EVALUATION_REVIEW_ADJUDICATIONS,
    ):
        table.create(bind=connection, checkfirst=True)
    rows = connection.execute(
        select(
            _EVALUATION_REVIEW_ITEMS.c.review_set_id,
            _EVALUATION_REVIEW_ITEMS.c.article_id,
            _EVALUATION_REVIEW_ITEMS.c.review_json,
            _EVALUATION_REVIEW_ITEMS.c.reviewed_at,
        )
        .where(_EVALUATION_REVIEW_ITEMS.c.review_json.is_not(None))
        .order_by(
            _EVALUATION_REVIEW_ITEMS.c.review_set_id,
            _EVALUATION_REVIEW_ITEMS.c.position,
        )
    ).mappings().all()
    by_set: dict[str, list[tuple[str, SelectionReviewDecision, str]]] = (
        defaultdict(list)
    )
    for row in rows:
        decision = SelectionReviewDecision.model_validate(row["review_json"])
        by_set[row["review_set_id"]].append(
            (
                row["article_id"],
                decision,
                row["reviewed_at"] or decision.reviewed_at.isoformat(),
            )
        )
    for review_set_id, reviews in by_set.items():
        reviewer_keys = {
            normalize_reviewer_identity(decision.reviewer)
            for _, decision, _ in reviews
        }
        if len(reviewer_keys) != 1:
            raise FirstReviewerAlreadyClaimedError(review_set_id)
        first_decision = reviews[0][1]
        reviewer_key = normalize_reviewer_identity(first_decision.reviewer)
        connection.execute(
            insert(_EVALUATION_REVIEW_PARTICIPANTS).values(
                review_set_id=review_set_id,
                role="reviewer_a",
                reviewer=first_decision.reviewer,
                reviewer_key=reviewer_key,
                claimed_at=min(submitted_at for _, _, submitted_at in reviews),
            )
        )
        for article_id, decision, submitted_at in reviews:
            connection.execute(
                insert(_EVALUATION_REVIEW_SUBMISSIONS).values(
                    review_set_id=review_set_id,
                    item_key=article_id,
                    reviewer_role="reviewer_a",
                    reviewer=decision.reviewer,
                    reviewer_key=reviewer_key,
                    decision_json=decision.model_dump(mode="json"),
                    submitted_at=submitted_at,
                )
            )


def _has_column(connection, table_name: str, column_name: str) -> bool:
    inspector = inspect(connection)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name)
    }


# ---------------------------------------------------------------------------
# Human evaluation workbench repository (R1)
# ---------------------------------------------------------------------------


class SqliteEvaluationRepository:
    """Keep a stable candidate set and complete, provenance-bound labels."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def candidate_ids(self) -> set[str]:
        with self._engine.connect() as connection:
            return set(
                connection.execute(
                    select(_EVALUATION_CANDIDATES.c.article_id)
                ).scalars()
            )

    def seed_candidates(self, candidates: Sequence[LabelingCandidate]) -> int:
        if not candidates:
            return 0
        inserted = 0
        with self._engine.begin() as connection:
            existing = set(
                connection.execute(
                    select(_EVALUATION_CANDIDATES.c.article_id)
                ).scalars()
            )
            last_position = max(
                connection.execute(
                    select(_EVALUATION_CANDIDATES.c.position)
                ).scalars(),
                default=0,
            )
            for candidate in candidates:
                article_id = candidate.article.id
                if article_id in existing:
                    continue
                last_position += 1
                statement = insert(_EVALUATION_CANDIDATES).values(
                    article_id=article_id,
                    position=last_position,
                    category=candidate.article.category.value,
                    title=candidate.article.title,
                    candidate_json=candidate.model_dump(mode="json"),
                    created_at=_iso_now(),
                ).on_conflict_do_nothing(
                    index_elements=[_EVALUATION_CANDIDATES.c.article_id]
                )
                result = connection.execute(statement)
                if result.rowcount == 1:
                    inserted += 1
                    existing.add(article_id)
        return inserted

    def remove_unlabelled_candidates(self, article_ids: Sequence[str]) -> int:
        """Remove only disposable pool rows; completed labels are never touched."""
        if not article_ids:
            return 0
        with self._engine.begin() as connection:
            labelled_ids = set(
                connection.execute(
                    select(_EVALUATION_ANNOTATIONS.c.article_id).where(
                        _EVALUATION_ANNOTATIONS.c.article_id.in_(article_ids)
                    )
                ).scalars()
            )
            removable = set(article_ids) - labelled_ids
            if not removable:
                return 0
            result = connection.execute(
                delete(_EVALUATION_CANDIDATES).where(
                    _EVALUATION_CANDIDATES.c.article_id.in_(removable)
                )
            )
        return int(result.rowcount or 0)

    def list_items(
        self,
        *,
        status: str = "all",
        category: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> WorkbenchPage:
        statement = (
            select(
                _EVALUATION_CANDIDATES.c.position,
                _EVALUATION_CANDIDATES.c.category,
                _EVALUATION_CANDIDATES.c.title,
                _EVALUATION_CANDIDATES.c.candidate_json,
                _EVALUATION_ANNOTATIONS.c.label_json,
            )
            .select_from(
                _EVALUATION_CANDIDATES.outerjoin(
                    _EVALUATION_ANNOTATIONS,
                    _EVALUATION_CANDIDATES.c.article_id
                    == _EVALUATION_ANNOTATIONS.c.article_id,
                )
            )
            .order_by(_EVALUATION_CANDIDATES.c.position)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        normalized_query = (query or "").strip().casefold()
        matching = [
            row
            for row in rows
            if (category is None or row["category"] == category)
            and (
                status == "all"
                or (status == "labelled" and row["label_json"] is not None)
                or (status == "unlabelled" and row["label_json"] is None)
            )
            and (
                not normalized_query
                or normalized_query in str(row["title"]).casefold()
            )
        ]
        items = [
            WorkbenchItem(
                position=row["position"],
                candidate=LabelingCandidate.model_validate(
                    row["candidate_json"]
                ),
                label=(
                    GoldLabel.model_validate(row["label_json"])
                    if row["label_json"] is not None
                    else None
                ),
            )
            for row in matching[offset : offset + limit]
        ]
        return WorkbenchPage(
            items=items,
            total=len(matching),
            offset=offset,
            limit=limit,
        )

    def save_label(self, article_id: str, label: GoldLabel) -> WorkbenchItem:
        with self._engine.begin() as connection:
            candidate = connection.execute(
                select(_EVALUATION_CANDIDATES).where(
                    _EVALUATION_CANDIDATES.c.article_id == article_id
                )
            ).mappings().first()
            if candidate is None:
                raise KeyError(article_id)
            statement = insert(_EVALUATION_ANNOTATIONS).values(
                article_id=article_id,
                label_json=label.model_dump(mode="json"),
                annotation_method=label.annotation_method,
                annotator=label.annotator,
                updated_at=_iso_now(),
            ).on_conflict_do_update(
                index_elements=[_EVALUATION_ANNOTATIONS.c.article_id],
                set_={
                    "label_json": label.model_dump(mode="json"),
                    "annotation_method": label.annotation_method,
                    "annotator": label.annotator,
                    "updated_at": _iso_now(),
                },
            )
            connection.execute(statement)
        return WorkbenchItem(
            position=candidate["position"],
            candidate=LabelingCandidate.model_validate(
                candidate["candidate_json"]
            ),
            label=label,
        )

    def seed_review_set(
        self,
        *,
        review_set_id: str,
        kind: str,
        target_count: int,
        criteria: dict[str, object],
        items: Sequence[WorkbenchItem],
    ) -> int:
        """Freeze one versioned review set without changing it on retries."""
        with self._engine.begin() as connection:
            result = connection.execute(
                insert(_EVALUATION_REVIEW_SETS)
                .values(
                    id=review_set_id,
                    kind=kind,
                    target_count=target_count,
                    criteria_json=criteria,
                    created_at=_iso_now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[_EVALUATION_REVIEW_SETS.c.id]
                )
            )
            if result.rowcount != 1:
                return 0
            for position, item in enumerate(items, start=1):
                if item.label is None:
                    raise ValueError("review items require a completed initial label")
                connection.execute(
                    insert(_EVALUATION_REVIEW_ITEMS).values(
                        review_set_id=review_set_id,
                        article_id=item.candidate.article.id,
                        position=position,
                        reason="human-relevant-selected",
                        initial_label_json=item.label.model_dump(mode="json"),
                        review_json=null(),
                        created_at=_iso_now(),
                        reviewed_at=None,
                    )
                )
        return len(items)

    def review_status(self, review_set_id: str) -> SelectionReviewStatus:
        with self._engine.connect() as connection:
            review_set = connection.execute(
                select(_EVALUATION_REVIEW_SETS).where(
                    _EVALUATION_REVIEW_SETS.c.id == review_set_id
                )
            ).mappings().first()
            if review_set is None:
                raise ReviewSetNotFoundError(review_set_id)
            rows = connection.execute(
                select(
                    _EVALUATION_REVIEW_ITEMS.c.initial_label_json,
                    _EVALUATION_REVIEW_ITEMS.c.review_json,
                ).where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id
                )
            ).mappings().all()
        completed = [row for row in rows if row["review_json"] is not None]
        changed_count = sum(
            bool(row["initial_label_json"]["selected"])
            != bool(row["review_json"]["selected"])
            for row in completed
        )
        independent_count = sum(
            row["initial_label_json"]["annotator"]
            != row["review_json"]["reviewer"]
            for row in completed
        )
        total = len(rows)
        return SelectionReviewStatus(
            review_set_id=review_set_id,
            target_count=review_set["target_count"],
            total_count=total,
            completed_count=len(completed),
            pending_count=total - len(completed),
            changed_count=changed_count,
            independent_review_count=independent_count,
            completion_rate=(len(completed) / total if total else 0.0),
        )

    def double_review_status(self, review_set_id: str) -> DoubleReviewStatus:
        with self._engine.connect() as connection:
            exists = connection.execute(
                select(_EVALUATION_REVIEW_SETS.c.id).where(
                    _EVALUATION_REVIEW_SETS.c.id == review_set_id
                )
            ).scalar_one_or_none()
            if exists is None:
                raise ReviewSetNotFoundError(review_set_id)
            total = len(
                connection.execute(
                    select(_EVALUATION_REVIEW_ITEMS.c.article_id).where(
                        _EVALUATION_REVIEW_ITEMS.c.review_set_id
                        == review_set_id
                    )
                ).scalars().all()
            )
            participant = connection.execute(
                select(_EVALUATION_REVIEW_PARTICIPANTS).where(
                    _EVALUATION_REVIEW_PARTICIPANTS.c.review_set_id
                    == review_set_id,
                    _EVALUATION_REVIEW_PARTICIPANTS.c.role == "reviewer_b",
                )
            ).mappings().first()
            rows = connection.execute(
                select(
                    _EVALUATION_REVIEW_SUBMISSIONS.c.item_key,
                    _EVALUATION_REVIEW_SUBMISSIONS.c.reviewer_role,
                    _EVALUATION_REVIEW_SUBMISSIONS.c.decision_json,
                ).where(
                    _EVALUATION_REVIEW_SUBMISSIONS.c.review_set_id
                    == review_set_id
                )
            ).mappings().all()
            adjudicated_keys = set(
                connection.execute(
                    select(_EVALUATION_REVIEW_ADJUDICATIONS.c.item_key).where(
                        _EVALUATION_REVIEW_ADJUDICATIONS.c.review_set_id
                        == review_set_id
                    )
                ).scalars()
            )
        submissions: dict[str, dict[str, SelectionReviewDecision]] = defaultdict(dict)
        for row in rows:
            submissions[row["item_key"]][row["reviewer_role"]] = (
                SelectionReviewDecision.model_validate(row["decision_json"])
            )
        paired = [
            decisions
            for decisions in submissions.values()
            if "reviewer_a" in decisions and "reviewer_b" in decisions
        ]
        consensus_count = sum(
            pair["reviewer_a"].selected == pair["reviewer_b"].selected
            for pair in paired
        )
        conflict_keys = {
            item_key
            for item_key, pair in submissions.items()
            if "reviewer_a" in pair
            and "reviewer_b" in pair
            and pair["reviewer_a"].selected != pair["reviewer_b"].selected
        }
        second_completed_count = sum(
            "reviewer_b" in decisions for decisions in submissions.values()
        )
        adjudicated_count = len(conflict_keys & adjudicated_keys)
        pending_adjudication_count = len(conflict_keys) - adjudicated_count
        if participant is None:
            lifecycle = "awaiting_second_reviewer"
        elif second_completed_count < total:
            lifecycle = "second_reviewing"
        elif pending_adjudication_count:
            lifecycle = "awaiting_adjudication"
        else:
            lifecycle = "complete"
        return DoubleReviewStatus(
            review_set_id=review_set_id,
            lifecycle=lifecycle,
            total_count=total,
            second_completed_count=second_completed_count,
            second_pending_count=max(0, total - second_completed_count),
            consensus_count=consensus_count,
            conflict_count=len(conflict_keys),
            adjudicated_count=adjudicated_count,
            pending_adjudication_count=pending_adjudication_count,
            second_reviewer=(participant["reviewer"] if participant else None),
            completion_rate=(second_completed_count / total if total else 0.0),
        )

    def claim_second_reviewer(
        self,
        review_set_id: str,
        claim: ReviewerClaimRequest,
    ) -> ReviewParticipant:
        reviewer_key = normalize_reviewer_identity(claim.reviewer)
        with self._engine.begin() as connection:
            exists = connection.execute(
                select(_EVALUATION_REVIEW_SETS.c.id).where(
                    _EVALUATION_REVIEW_SETS.c.id == review_set_id
                )
            ).scalar_one_or_none()
            if exists is None:
                raise ReviewSetNotFoundError(review_set_id)
            existing = connection.execute(
                select(_EVALUATION_REVIEW_PARTICIPANTS).where(
                    _EVALUATION_REVIEW_PARTICIPANTS.c.review_set_id
                    == review_set_id,
                    _EVALUATION_REVIEW_PARTICIPANTS.c.role == "reviewer_b",
                )
            ).mappings().first()
            if existing is not None:
                if existing["reviewer_key"] != reviewer_key:
                    raise SecondReviewerAlreadyClaimedError(review_set_id)
                return ReviewParticipant(
                    review_set_id=review_set_id,
                    role="reviewer_b",
                    reviewer=existing["reviewer"],
                    claimed_at=existing["claimed_at"],
                )
            initial_labels = connection.execute(
                select(_EVALUATION_REVIEW_ITEMS.c.initial_label_json).where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id
                )
            ).scalars().all()
            reserved_keys = {
                normalize_reviewer_identity(
                    GoldLabel.model_validate(label).annotator
                )
                for label in initial_labels
            }
            reserved_keys.update(
                connection.execute(
                    select(_EVALUATION_REVIEW_PARTICIPANTS.c.reviewer_key).where(
                        _EVALUATION_REVIEW_PARTICIPANTS.c.review_set_id
                        == review_set_id,
                        _EVALUATION_REVIEW_PARTICIPANTS.c.role == "reviewer_a",
                    )
                ).scalars()
            )
            if reviewer_key in reserved_keys:
                raise ReviewerNotIndependentError(claim.reviewer)
            reviewer = claim.reviewer.strip()
            connection.execute(
                insert(_EVALUATION_REVIEW_PARTICIPANTS).values(
                    review_set_id=review_set_id,
                    role="reviewer_b",
                    reviewer=reviewer,
                    reviewer_key=reviewer_key,
                    claimed_at=claim.claimed_at.isoformat(),
                )
            )
        return ReviewParticipant(
            review_set_id=review_set_id,
            role="reviewer_b",
            reviewer=reviewer,
            claimed_at=claim.claimed_at,
        )

    def list_blind_selection_reviews(
        self,
        review_set_id: str,
        *,
        status: str,
        category: str | None,
        offset: int,
        limit: int,
    ) -> BlindSelectionReviewPage:
        with self._engine.connect() as connection:
            exists = connection.execute(
                select(_EVALUATION_REVIEW_SETS.c.id).where(
                    _EVALUATION_REVIEW_SETS.c.id == review_set_id
                )
            ).scalar_one_or_none()
            if exists is None:
                raise ReviewSetNotFoundError(review_set_id)
            participant = connection.execute(
                select(_EVALUATION_REVIEW_PARTICIPANTS.c.reviewer).where(
                    _EVALUATION_REVIEW_PARTICIPANTS.c.review_set_id
                    == review_set_id,
                    _EVALUATION_REVIEW_PARTICIPANTS.c.role == "reviewer_b",
                )
            ).scalar_one_or_none()
            if participant is None:
                raise ReviewerNotIndependentError("second reviewer is not claimed")
            rows = connection.execute(
                select(
                    _EVALUATION_REVIEW_ITEMS.c.position,
                    _EVALUATION_REVIEW_ITEMS.c.article_id,
                    _EVALUATION_CANDIDATES.c.category,
                    _EVALUATION_CANDIDATES.c.candidate_json,
                    _EVALUATION_REVIEW_SUBMISSIONS.c.decision_json,
                )
                .select_from(
                    _EVALUATION_REVIEW_ITEMS.join(
                        _EVALUATION_CANDIDATES,
                        _EVALUATION_CANDIDATES.c.article_id
                        == _EVALUATION_REVIEW_ITEMS.c.article_id,
                    ).outerjoin(
                        _EVALUATION_REVIEW_SUBMISSIONS,
                        and_(
                            _EVALUATION_REVIEW_SUBMISSIONS.c.review_set_id
                            == _EVALUATION_REVIEW_ITEMS.c.review_set_id,
                            _EVALUATION_REVIEW_SUBMISSIONS.c.item_key
                            == _EVALUATION_REVIEW_ITEMS.c.article_id,
                            _EVALUATION_REVIEW_SUBMISSIONS.c.reviewer_role
                            == "reviewer_b",
                        ),
                    )
                )
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id
                )
                .order_by(_EVALUATION_REVIEW_ITEMS.c.position)
            ).mappings().all()
        matching = [
            row
            for row in rows
            if (category is None or row["category"] == category)
            and (
                status == "all"
                or (status == "pending" and row["decision_json"] is None)
                or (status == "completed" and row["decision_json"] is not None)
            )
        ]
        items = []
        for row in matching[offset : offset + limit]:
            candidate = LabelingCandidate.model_validate(row["candidate_json"])
            items.append(
                BlindSelectionReviewItem(
                    position=row["position"],
                    article=candidate.article,
                    review=(
                        SelectionReviewDecision.model_validate(
                            row["decision_json"]
                        )
                        if row["decision_json"] is not None
                        else None
                    ),
                )
            )
        return BlindSelectionReviewPage(
            items=items,
            total=len(matching),
            offset=offset,
            limit=limit,
        )

    def save_second_selection_review(
        self,
        review_set_id: str,
        article_id: str,
        decision: SelectionReviewDecision,
    ) -> BlindSelectionReviewItem:
        reviewer_key = normalize_reviewer_identity(decision.reviewer)
        with self._engine.begin() as connection:
            exists = connection.execute(
                select(_EVALUATION_REVIEW_SETS.c.id).where(
                    _EVALUATION_REVIEW_SETS.c.id == review_set_id
                )
            ).scalar_one_or_none()
            if exists is None:
                raise ReviewSetNotFoundError(review_set_id)
            row = connection.execute(
                select(
                    _EVALUATION_REVIEW_ITEMS.c.position,
                    _EVALUATION_CANDIDATES.c.candidate_json,
                )
                .select_from(
                    _EVALUATION_REVIEW_ITEMS.join(
                        _EVALUATION_CANDIDATES,
                        _EVALUATION_CANDIDATES.c.article_id
                        == _EVALUATION_REVIEW_ITEMS.c.article_id,
                    )
                )
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id,
                    _EVALUATION_REVIEW_ITEMS.c.article_id == article_id,
                )
            ).mappings().first()
            if row is None:
                raise ReviewItemNotFoundError(article_id)
            participant = connection.execute(
                select(_EVALUATION_REVIEW_PARTICIPANTS).where(
                    _EVALUATION_REVIEW_PARTICIPANTS.c.review_set_id
                    == review_set_id,
                    _EVALUATION_REVIEW_PARTICIPANTS.c.role == "reviewer_b",
                )
            ).mappings().first()
            if participant is None or participant["reviewer_key"] != reviewer_key:
                raise ReviewerNotIndependentError(decision.reviewer)
            result = connection.execute(
                insert(_EVALUATION_REVIEW_SUBMISSIONS)
                .values(
                    review_set_id=review_set_id,
                    item_key=article_id,
                    reviewer_role="reviewer_b",
                    reviewer=participant["reviewer"],
                    reviewer_key=reviewer_key,
                    decision_json=decision.model_dump(mode="json"),
                    submitted_at=decision.reviewed_at.isoformat(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        _EVALUATION_REVIEW_SUBMISSIONS.c.review_set_id,
                        _EVALUATION_REVIEW_SUBMISSIONS.c.item_key,
                        _EVALUATION_REVIEW_SUBMISSIONS.c.reviewer_role,
                    ]
                )
            )
            if result.rowcount != 1:
                raise ReviewSubmissionAlreadyExistsError(article_id)
        candidate = LabelingCandidate.model_validate(row["candidate_json"])
        return BlindSelectionReviewItem(
            position=row["position"],
            article=candidate.article,
            review=decision,
        )

    def list_selection_conflicts(
        self,
        review_set_id: str,
        *,
        status: str,
        offset: int,
        limit: int,
    ) -> SelectionConflictPage:
        double_status = self.double_review_status(review_set_id)
        if double_status.second_completed_count < double_status.total_count:
            raise SecondReviewIncompleteError(review_set_id)
        reviewer_a = _EVALUATION_REVIEW_SUBMISSIONS.alias("reviewer_a_submission")
        reviewer_b = _EVALUATION_REVIEW_SUBMISSIONS.alias("reviewer_b_submission")
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    _EVALUATION_REVIEW_ITEMS.c.position,
                    _EVALUATION_CANDIDATES.c.candidate_json,
                    reviewer_a.c.decision_json.label("reviewer_a_json"),
                    reviewer_b.c.decision_json.label("reviewer_b_json"),
                    _EVALUATION_REVIEW_ADJUDICATIONS.c.decision_json.label(
                        "adjudication_json"
                    ),
                )
                .select_from(
                    _EVALUATION_REVIEW_ITEMS.join(
                        _EVALUATION_CANDIDATES,
                        _EVALUATION_CANDIDATES.c.article_id
                        == _EVALUATION_REVIEW_ITEMS.c.article_id,
                    )
                    .join(
                        reviewer_a,
                        and_(
                            reviewer_a.c.review_set_id
                            == _EVALUATION_REVIEW_ITEMS.c.review_set_id,
                            reviewer_a.c.item_key
                            == _EVALUATION_REVIEW_ITEMS.c.article_id,
                            reviewer_a.c.reviewer_role == "reviewer_a",
                        ),
                    )
                    .join(
                        reviewer_b,
                        and_(
                            reviewer_b.c.review_set_id
                            == _EVALUATION_REVIEW_ITEMS.c.review_set_id,
                            reviewer_b.c.item_key
                            == _EVALUATION_REVIEW_ITEMS.c.article_id,
                            reviewer_b.c.reviewer_role == "reviewer_b",
                        ),
                    )
                    .outerjoin(
                        _EVALUATION_REVIEW_ADJUDICATIONS,
                        and_(
                            _EVALUATION_REVIEW_ADJUDICATIONS.c.review_set_id
                            == _EVALUATION_REVIEW_ITEMS.c.review_set_id,
                            _EVALUATION_REVIEW_ADJUDICATIONS.c.item_key
                            == _EVALUATION_REVIEW_ITEMS.c.article_id,
                        ),
                    )
                )
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id
                )
                .order_by(_EVALUATION_REVIEW_ITEMS.c.position)
            ).mappings().all()
        conflicts = []
        for row in rows:
            first = SelectionReviewDecision.model_validate(row["reviewer_a_json"])
            second = SelectionReviewDecision.model_validate(row["reviewer_b_json"])
            if first.selected == second.selected:
                continue
            adjudication = (
                SelectionAdjudicationDecision.model_validate(
                    row["adjudication_json"]
                )
                if row["adjudication_json"] is not None
                else None
            )
            if not (
                status == "all"
                or (status == "pending" and adjudication is None)
                or (status == "completed" and adjudication is not None)
            ):
                continue
            candidate = LabelingCandidate.model_validate(row["candidate_json"])
            conflicts.append(
                SelectionConflictItem(
                    position=row["position"],
                    article=candidate.article,
                    reviewer_a=first,
                    reviewer_b=second,
                    adjudication=adjudication,
                )
            )
        return SelectionConflictPage(
            items=conflicts[offset : offset + limit],
            total=len(conflicts),
            offset=offset,
            limit=limit,
        )

    def save_selection_adjudication(
        self,
        review_set_id: str,
        article_id: str,
        decision: SelectionAdjudicationDecision,
    ) -> SelectionConflictItem:
        double_status = self.double_review_status(review_set_id)
        if double_status.second_completed_count < double_status.total_count:
            raise SecondReviewIncompleteError(review_set_id)
        adjudicator_key = normalize_reviewer_identity(decision.adjudicator)
        with self._engine.begin() as connection:
            row = connection.execute(
                select(
                    _EVALUATION_REVIEW_ITEMS.c.position,
                    _EVALUATION_CANDIDATES.c.candidate_json,
                    _EVALUATION_ANNOTATIONS.c.label_json,
                )
                .select_from(
                    _EVALUATION_REVIEW_ITEMS.join(
                        _EVALUATION_CANDIDATES,
                        _EVALUATION_CANDIDATES.c.article_id
                        == _EVALUATION_REVIEW_ITEMS.c.article_id,
                    ).join(
                        _EVALUATION_ANNOTATIONS,
                        _EVALUATION_ANNOTATIONS.c.article_id
                        == _EVALUATION_REVIEW_ITEMS.c.article_id,
                    )
                )
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id,
                    _EVALUATION_REVIEW_ITEMS.c.article_id == article_id,
                )
            ).mappings().first()
            if row is None:
                raise ReviewItemNotFoundError(article_id)
            submission_rows = connection.execute(
                select(
                    _EVALUATION_REVIEW_SUBMISSIONS.c.reviewer_role,
                    _EVALUATION_REVIEW_SUBMISSIONS.c.decision_json,
                ).where(
                    _EVALUATION_REVIEW_SUBMISSIONS.c.review_set_id
                    == review_set_id,
                    _EVALUATION_REVIEW_SUBMISSIONS.c.item_key == article_id,
                )
            ).mappings().all()
            submissions = {
                submission["reviewer_role"]: SelectionReviewDecision.model_validate(
                    submission["decision_json"]
                )
                for submission in submission_rows
            }
            if "reviewer_a" not in submissions or "reviewer_b" not in submissions:
                raise SecondReviewIncompleteError(review_set_id)
            if (
                submissions["reviewer_a"].selected
                == submissions["reviewer_b"].selected
            ):
                raise AdjudicationNotRequiredError(article_id)
            second_reviewer_key = connection.execute(
                select(_EVALUATION_REVIEW_PARTICIPANTS.c.reviewer_key).where(
                    _EVALUATION_REVIEW_PARTICIPANTS.c.review_set_id
                    == review_set_id,
                    _EVALUATION_REVIEW_PARTICIPANTS.c.role == "reviewer_b",
                )
            ).scalar_one()
            if adjudicator_key == second_reviewer_key:
                raise AdjudicatorNotIndependentError(decision.adjudicator)
            insert_result = connection.execute(
                insert(_EVALUATION_REVIEW_ADJUDICATIONS)
                .values(
                    review_set_id=review_set_id,
                    item_key=article_id,
                    adjudicator=decision.adjudicator.strip(),
                    adjudicator_key=adjudicator_key,
                    decision_json=decision.model_dump(mode="json"),
                    adjudicated_at=decision.adjudicated_at.isoformat(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        _EVALUATION_REVIEW_ADJUDICATIONS.c.review_set_id,
                        _EVALUATION_REVIEW_ADJUDICATIONS.c.item_key,
                    ]
                )
            )
            if insert_result.rowcount != 1:
                raise AdjudicationAlreadyExistsError(article_id)
            current_label = GoldLabel.model_validate(row["label_json"])
            audit_note = (
                f"[{review_set_id}:adjudication:{decision.reason_code}]"
            )
            if decision.notes.strip():
                audit_note = f"{audit_note} {decision.notes.strip()}"
            updated_notes = "\n".join(
                value
                for value in (current_label.notes.strip(), audit_note)
                if value
            )
            updated_label = current_label.model_copy(
                update={
                    "selected": decision.selected,
                    "notes": updated_notes,
                }
            )
            connection.execute(
                update(_EVALUATION_ANNOTATIONS)
                .where(_EVALUATION_ANNOTATIONS.c.article_id == article_id)
                .values(
                    label_json=updated_label.model_dump(mode="json"),
                    updated_at=_iso_now(),
                )
            )
        candidate = LabelingCandidate.model_validate(row["candidate_json"])
        return SelectionConflictItem(
            position=row["position"],
            article=candidate.article,
            reviewer_a=submissions["reviewer_a"],
            reviewer_b=submissions["reviewer_b"],
            adjudication=decision,
        )

    def list_double_review_records(
        self,
        review_set_id: str,
    ) -> list[DoubleReviewRecord]:
        self.double_review_status(review_set_id)
        reviewer_a = _EVALUATION_REVIEW_SUBMISSIONS.alias("report_reviewer_a")
        reviewer_b = _EVALUATION_REVIEW_SUBMISSIONS.alias("report_reviewer_b")
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    _EVALUATION_REVIEW_ITEMS.c.article_id,
                    _EVALUATION_REVIEW_ITEMS.c.position,
                    _EVALUATION_CANDIDATES.c.candidate_json,
                    reviewer_a.c.decision_json.label("reviewer_a_json"),
                    reviewer_b.c.decision_json.label("reviewer_b_json"),
                    _EVALUATION_REVIEW_ADJUDICATIONS.c.decision_json.label(
                        "adjudication_json"
                    ),
                )
                .select_from(
                    _EVALUATION_REVIEW_ITEMS.join(
                        _EVALUATION_CANDIDATES,
                        _EVALUATION_CANDIDATES.c.article_id
                        == _EVALUATION_REVIEW_ITEMS.c.article_id,
                    )
                    .join(
                        reviewer_a,
                        and_(
                            reviewer_a.c.review_set_id
                            == _EVALUATION_REVIEW_ITEMS.c.review_set_id,
                            reviewer_a.c.item_key
                            == _EVALUATION_REVIEW_ITEMS.c.article_id,
                            reviewer_a.c.reviewer_role == "reviewer_a",
                        ),
                    )
                    .join(
                        reviewer_b,
                        and_(
                            reviewer_b.c.review_set_id
                            == _EVALUATION_REVIEW_ITEMS.c.review_set_id,
                            reviewer_b.c.item_key
                            == _EVALUATION_REVIEW_ITEMS.c.article_id,
                            reviewer_b.c.reviewer_role == "reviewer_b",
                        ),
                    )
                    .outerjoin(
                        _EVALUATION_REVIEW_ADJUDICATIONS,
                        and_(
                            _EVALUATION_REVIEW_ADJUDICATIONS.c.review_set_id
                            == _EVALUATION_REVIEW_ITEMS.c.review_set_id,
                            _EVALUATION_REVIEW_ADJUDICATIONS.c.item_key
                            == _EVALUATION_REVIEW_ITEMS.c.article_id,
                        ),
                    )
                )
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id
                )
                .order_by(_EVALUATION_REVIEW_ITEMS.c.position)
            ).mappings().all()
        return [
            DoubleReviewRecord(
                item_key=row["article_id"],
                position=row["position"],
                article=LabelingCandidate.model_validate(
                    row["candidate_json"]
                ).article,
                reviewer_a=SelectionReviewDecision.model_validate(
                    row["reviewer_a_json"]
                ),
                reviewer_b=SelectionReviewDecision.model_validate(
                    row["reviewer_b_json"]
                ),
                adjudication=(
                    SelectionAdjudicationDecision.model_validate(
                        row["adjudication_json"]
                    )
                    if row["adjudication_json"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def list_review_items(
        self,
        review_set_id: str,
        *,
        status: str,
        category: str | None,
        offset: int,
        limit: int,
    ) -> SelectionReviewPage:
        with self._engine.connect() as connection:
            exists = connection.execute(
                select(_EVALUATION_REVIEW_SETS.c.id).where(
                    _EVALUATION_REVIEW_SETS.c.id == review_set_id
                )
            ).scalar_one_or_none()
            if exists is None:
                raise ReviewSetNotFoundError(review_set_id)
            statement = (
                select(
                    _EVALUATION_REVIEW_ITEMS,
                    _EVALUATION_CANDIDATES.c.category,
                    _EVALUATION_CANDIDATES.c.candidate_json,
                )
                .join(
                    _EVALUATION_CANDIDATES,
                    _EVALUATION_CANDIDATES.c.article_id
                    == _EVALUATION_REVIEW_ITEMS.c.article_id,
                )
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id
                )
                .order_by(_EVALUATION_REVIEW_ITEMS.c.position)
            )
            rows = connection.execute(statement).mappings().all()
        matching = [
            row
            for row in rows
            if (category is None or row["category"] == category)
            and (
                status == "all"
                or (status == "pending" and row["review_json"] is None)
                or (status == "completed" and row["review_json"] is not None)
            )
        ]
        items = [
            SelectionReviewItem(
                position=row["position"],
                reason=row["reason"],
                candidate=LabelingCandidate.model_validate(
                    row["candidate_json"]
                ),
                initial_label=GoldLabel.model_validate(
                    row["initial_label_json"]
                ),
                review=(
                    SelectionReviewDecision.model_validate(row["review_json"])
                    if row["review_json"] is not None
                    else None
                ),
            )
            for row in matching[offset : offset + limit]
        ]
        return SelectionReviewPage(
            items=items,
            total=len(matching),
            offset=offset,
            limit=limit,
        )

    def save_selection_review(
        self,
        review_set_id: str,
        article_id: str,
        decision: SelectionReviewDecision,
    ) -> SelectionReviewItem:
        with self._engine.begin() as connection:
            review_set = connection.execute(
                select(_EVALUATION_REVIEW_SETS.c.id).where(
                    _EVALUATION_REVIEW_SETS.c.id == review_set_id
                )
            ).scalar_one_or_none()
            if review_set is None:
                raise ReviewSetNotFoundError(review_set_id)
            row = connection.execute(
                select(
                    _EVALUATION_REVIEW_ITEMS,
                    _EVALUATION_CANDIDATES.c.candidate_json,
                )
                .join(
                    _EVALUATION_CANDIDATES,
                    _EVALUATION_CANDIDATES.c.article_id
                    == _EVALUATION_REVIEW_ITEMS.c.article_id,
                )
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id,
                    _EVALUATION_REVIEW_ITEMS.c.article_id == article_id,
                )
            ).mappings().first()
            if row is None:
                raise ReviewItemNotFoundError(article_id)
            if row["review_json"] is not None:
                raise ReviewAlreadyCompletedError(article_id)
            annotation = connection.execute(
                select(_EVALUATION_ANNOTATIONS.c.label_json).where(
                    _EVALUATION_ANNOTATIONS.c.article_id == article_id
                )
            ).scalar_one_or_none()
            if annotation is None:
                raise ReviewItemNotFoundError(article_id)
            reviewer_key = normalize_reviewer_identity(decision.reviewer)
            participant = connection.execute(
                select(_EVALUATION_REVIEW_PARTICIPANTS).where(
                    _EVALUATION_REVIEW_PARTICIPANTS.c.review_set_id
                    == review_set_id,
                    _EVALUATION_REVIEW_PARTICIPANTS.c.role == "reviewer_a",
                )
            ).mappings().first()
            if participant is None:
                connection.execute(
                    insert(_EVALUATION_REVIEW_PARTICIPANTS).values(
                        review_set_id=review_set_id,
                        role="reviewer_a",
                        reviewer=decision.reviewer,
                        reviewer_key=reviewer_key,
                        claimed_at=decision.reviewed_at.isoformat(),
                    )
                )
            elif participant["reviewer_key"] != reviewer_key:
                raise FirstReviewerAlreadyClaimedError(review_set_id)
            current_label = GoldLabel.model_validate(annotation)
            audit_note = f"[{review_set_id}:{decision.reason_code}]"
            if decision.notes.strip():
                audit_note = f"{audit_note} {decision.notes.strip()}"
            updated_notes = "\n".join(
                value
                for value in (current_label.notes.strip(), audit_note)
                if value
            )
            updated_label = current_label.model_copy(
                update={
                    "selected": decision.selected,
                    "annotator": decision.reviewer,
                    "annotated_at": decision.reviewed_at,
                    "notes": updated_notes,
                }
            )
            review_result = connection.execute(
                update(_EVALUATION_REVIEW_ITEMS)
                .where(
                    _EVALUATION_REVIEW_ITEMS.c.review_set_id == review_set_id,
                    _EVALUATION_REVIEW_ITEMS.c.article_id == article_id,
                    _EVALUATION_REVIEW_ITEMS.c.review_json.is_(None),
                )
                .values(
                    review_json=decision.model_dump(mode="json"),
                    reviewed_at=decision.reviewed_at.isoformat(),
                )
            )
            if review_result.rowcount != 1:
                raise ReviewAlreadyCompletedError(article_id)
            connection.execute(
                insert(_EVALUATION_REVIEW_SUBMISSIONS).values(
                    review_set_id=review_set_id,
                    item_key=article_id,
                    reviewer_role="reviewer_a",
                    reviewer=decision.reviewer,
                    reviewer_key=reviewer_key,
                    decision_json=decision.model_dump(mode="json"),
                    submitted_at=decision.reviewed_at.isoformat(),
                )
            )
            connection.execute(
                update(_EVALUATION_ANNOTATIONS)
                .where(_EVALUATION_ANNOTATIONS.c.article_id == article_id)
                .values(
                    label_json=updated_label.model_dump(mode="json"),
                    annotation_method=updated_label.annotation_method,
                    annotator=updated_label.annotator,
                    updated_at=_iso_now(),
                )
            )
        return SelectionReviewItem(
            position=row["position"],
            reason=row["reason"],
            candidate=LabelingCandidate.model_validate(row["candidate_json"]),
            initial_label=GoldLabel.model_validate(
                row["initial_label_json"]
            ),
            review=decision,
        )

    def status(self, target_count: int = 200) -> WorkbenchStatus:
        page = self.list_items(limit=max(1, target_count * 10))
        labels = [item.label for item in page.items if item.label is not None]
        human_count = sum(
            label.annotation_method == "human" for label in labels
        )
        synthetic_count = len(labels) - human_count
        return WorkbenchStatus(
            target_count=target_count,
            candidate_count=page.total,
            labelled_count=len(labels),
            human_label_count=human_count,
            synthetic_label_count=synthetic_count,
            remaining_count=max(0, target_count - human_count),
            completion_rate=min(1.0, human_count / target_count),
            ready_for_baseline=human_count >= target_count,
        )

    def list_gold_samples(self, *, human_only: bool = True) -> list[GoldSample]:
        page = self.list_items(limit=10000)
        samples = []
        for item in page.items:
            if item.label is None:
                continue
            if human_only and item.label.annotation_method != "human":
                continue
            samples.append(
                GoldSample(
                    evaluation_group=item.candidate.evaluation_group,
                    article=item.candidate.article,
                    label=item.label,
                )
            )
        return samples


# ---------------------------------------------------------------------------
# Selected snapshot/changes repository (Phase 2C)
# ---------------------------------------------------------------------------


class SqliteSelectedSyncRepository:
    """Persist frozen snapshots and an ordered selected-set change sequence."""

    def __init__(
        self,
        engine,
        *,
        max_changes: int = 10000,
        snapshot_ttl_seconds: int = 900,
    ) -> None:
        self._engine = engine
        self._max_changes = max(1, max_changes)
        self._snapshot_ttl = timedelta(
            seconds=max(60, snapshot_ttl_seconds)
        )

    def reconcile(self, items: list[SelectedSyncItem]) -> int:
        """Diff the authoritative selected set and append durable mutations."""
        incoming = {item.id: item for item in items}
        changed = 0
        with self._engine.begin() as connection:
            state = self._state(connection)
            current_rows = connection.execute(
                select(_SELECTED_SYNC_ITEMS)
            ).mappings().all()
            current = {row["item_id"]: row for row in current_rows}
            latest_sequence = int(state["latest_sequence"])
            now = _iso_now()

            for item_id in sorted(incoming):
                item = incoming[item_id]
                content_hash = _selected_sync_hash(item)
                existing = current.get(item_id)
                if (
                    existing is not None
                    and existing["content_hash"] == content_hash
                ):
                    continue
                values = {
                    "item_id": item.id,
                    "payload_default": item.default,
                    "payload_minimal": item.minimal,
                    "content_hash": content_hash,
                    "updated_at": now,
                }
                statement = insert(_SELECTED_SYNC_ITEMS).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=[_SELECTED_SYNC_ITEMS.c.item_id],
                    set_={
                        column.name: getattr(statement.excluded, column.name)
                        for column in _SELECTED_SYNC_ITEMS.c
                        if column.name != "item_id"
                    },
                )
                connection.execute(statement)
                result = connection.execute(
                    _SELECTED_SYNC_CHANGES.insert().values(
                        op="upsert",
                        item_id=item.id,
                        payload_default=item.default,
                        payload_minimal=item.minimal,
                        changed_at=now,
                    )
                )
                latest_sequence = int(result.inserted_primary_key[0])
                changed += 1

            for item_id in sorted(set(current) - set(incoming)):
                connection.execute(
                    _SELECTED_SYNC_ITEMS.delete().where(
                        _SELECTED_SYNC_ITEMS.c.item_id == item_id
                    )
                )
                result = connection.execute(
                    _SELECTED_SYNC_CHANGES.insert().values(
                        op="remove",
                        item_id=item_id,
                        payload_default=None,
                        payload_minimal=None,
                        changed_at=now,
                    )
                )
                latest_sequence = int(result.inserted_primary_key[0])
                changed += 1

            if changed:
                connection.execute(
                    _SELECTED_SYNC_STATE.update()
                    .where(_SELECTED_SYNC_STATE.c.id == 1)
                    .values(
                        latest_sequence=latest_sequence,
                        updated_at=now,
                    )
                )
                self._prune_changes(connection)
        return changed

    def snapshot(
        self,
        *,
        fields: SelectedFieldMode,
        limit: int,
        page: str | None = None,
    ) -> SelectedSnapshotPage:
        with self._engine.begin() as connection:
            self._cleanup_expired_snapshots(connection)
            if page is not None:
                page_row = connection.execute(
                    select(_SELECTED_SYNC_SNAPSHOT_PAGES).where(
                        _SELECTED_SYNC_SNAPSHOT_PAGES.c.token == page
                    )
                ).mappings().first()
                if page_row is None:
                    raise SnapshotRequiredError
                snapshot_row = connection.execute(
                    select(_SELECTED_SYNC_SNAPSHOTS).where(
                        _SELECTED_SYNC_SNAPSHOTS.c.id
                        == page_row["snapshot_id"]
                    )
                ).mappings().first()
                if (
                    snapshot_row is None
                    or snapshot_row["fields"] != fields
                    or int(snapshot_row["page_limit"]) != limit
                ):
                    raise SnapshotRequiredError
                return self._snapshot_page(
                    connection,
                    snapshot_row,
                    int(page_row["page_index"]),
                )

            state = self._state(connection)
            cursor = self._cursor_for(
                connection,
                sequence=int(state["latest_sequence"]),
                fields=fields,
            )
            active_snapshot = connection.execute(
                select(_SELECTED_SYNC_SNAPSHOTS)
                .where(
                    _SELECTED_SYNC_SNAPSHOTS.c.cursor_token == cursor,
                    _SELECTED_SYNC_SNAPSHOTS.c.fields == fields,
                    _SELECTED_SYNC_SNAPSHOTS.c.page_limit == limit,
                )
                .order_by(_SELECTED_SYNC_SNAPSHOTS.c.created_at.desc())
                .limit(1)
            ).mappings().first()
            if active_snapshot is not None:
                return self._snapshot_page(connection, active_snapshot, 0)
            payload_column = (
                _SELECTED_SYNC_ITEMS.c.payload_minimal
                if fields == "minimal"
                else _SELECTED_SYNC_ITEMS.c.payload_default
            )
            rows = connection.execute(
                select(_SELECTED_SYNC_ITEMS.c.item_id, payload_column)
            ).all()
            items = [row[1] for row in rows]
            items.sort(
                key=lambda item: (
                    str(item.get("timelineAt") or ""),
                    str(item.get("id") or ""),
                ),
                reverse=True,
            )
            now = datetime.now(UTC)
            snapshot_id = f"ss_{uuid4().hex}"
            connection.execute(
                _SELECTED_SYNC_SNAPSHOTS.insert().values(
                    id=snapshot_id,
                    cursor_token=cursor,
                    fields=fields,
                    page_limit=limit,
                    items=items,
                    created_at=now.isoformat(),
                    expires_at=(now + self._snapshot_ttl).isoformat(),
                )
            )
            page_count = max(1, (len(items) + limit - 1) // limit)
            for page_index in range(1, page_count):
                connection.execute(
                    _SELECTED_SYNC_SNAPSHOT_PAGES.insert().values(
                        token=f"sp_{uuid4().hex}",
                        snapshot_id=snapshot_id,
                        page_index=page_index,
                    )
                )
            snapshot_row = {
                "id": snapshot_id,
                "cursor_token": cursor,
                "fields": fields,
                "page_limit": limit,
                "items": items,
            }
            return self._snapshot_page(connection, snapshot_row, 0)

    def changes(self, *, cursor: str, limit: int) -> SelectedChangesPage:
        with self._engine.begin() as connection:
            state = self._state(connection)
            cursor_row = connection.execute(
                select(_SELECTED_SYNC_CURSORS).where(
                    _SELECTED_SYNC_CURSORS.c.token == cursor
                )
            ).mappings().first()
            if (
                cursor_row is None
                or int(cursor_row["sequence"])
                < int(state["floor_sequence"])
            ):
                raise SnapshotRequiredError

            rows = connection.execute(
                select(_SELECTED_SYNC_CHANGES)
                .where(
                    _SELECTED_SYNC_CHANGES.c.sequence
                    > int(cursor_row["sequence"])
                )
                .order_by(_SELECTED_SYNC_CHANGES.c.sequence.asc())
                .limit(limit + 1)
            ).mappings().all()
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            fields = str(cursor_row["fields"])
            next_cursor = cursor
            if page_rows:
                next_cursor = self._cursor_for(
                    connection,
                    sequence=int(page_rows[-1]["sequence"]),
                    fields=fields,
                )
            changes = [
                SelectedChange(
                    op=row["op"],
                    id=row["item_id"],
                    item=(
                        row["payload_minimal"]
                        if fields == "minimal"
                        else row["payload_default"]
                    )
                    if row["op"] == "upsert"
                    else None,
                )
                for row in page_rows
            ]
            return SelectedChangesPage(
                fields=fields,  # type: ignore[arg-type]
                changes=changes,
                cursor=next_cursor,
                has_more=has_more,
            )

    def _state(self, connection):
        state = connection.execute(
            select(_SELECTED_SYNC_STATE).where(
                _SELECTED_SYNC_STATE.c.id == 1
            )
        ).mappings().first()
        if state is not None:
            return state
        now = _iso_now()
        connection.execute(
            _SELECTED_SYNC_STATE.insert().values(
                id=1,
                latest_sequence=0,
                floor_sequence=0,
                updated_at=now,
            )
        )
        return {
            "id": 1,
            "latest_sequence": 0,
            "floor_sequence": 0,
            "updated_at": now,
        }

    def _cursor_for(
        self,
        connection,
        *,
        sequence: int,
        fields: SelectedFieldMode | str,
    ) -> str:
        existing = connection.execute(
            select(_SELECTED_SYNC_CURSORS.c.token).where(
                _SELECTED_SYNC_CURSORS.c.sequence == sequence,
                _SELECTED_SYNC_CURSORS.c.fields == fields,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return str(existing)
        token = f"sc_{uuid4().hex}"
        connection.execute(
            _SELECTED_SYNC_CURSORS.insert().values(
                token=token,
                sequence=sequence,
                fields=fields,
                created_at=_iso_now(),
            )
        )
        return token

    def _snapshot_page(
        self,
        connection,
        snapshot_row,
        page_index: int,
    ) -> SelectedSnapshotPage:
        limit = int(snapshot_row["page_limit"])
        items = list(snapshot_row["items"] or [])
        start = page_index * limit
        page_items = items[start : start + limit]
        has_more = start + limit < len(items)
        next_page = None
        if has_more:
            next_page = connection.execute(
                select(_SELECTED_SYNC_SNAPSHOT_PAGES.c.token).where(
                    _SELECTED_SYNC_SNAPSHOT_PAGES.c.snapshot_id
                    == snapshot_row["id"],
                    _SELECTED_SYNC_SNAPSHOT_PAGES.c.page_index
                    == page_index + 1,
                )
            ).scalar_one_or_none()
        return SelectedSnapshotPage(
            fields=snapshot_row["fields"],
            items=page_items,
            cursor=snapshot_row["cursor_token"],
            has_more=has_more,
            next_page=str(next_page) if next_page is not None else None,
        )

    def _cleanup_expired_snapshots(self, connection) -> None:
        now = _iso_now()
        snapshot_ids = connection.execute(
            select(_SELECTED_SYNC_SNAPSHOTS.c.id).where(
                _SELECTED_SYNC_SNAPSHOTS.c.expires_at < now
            )
        ).scalars().all()
        if not snapshot_ids:
            return
        connection.execute(
            _SELECTED_SYNC_SNAPSHOT_PAGES.delete().where(
                _SELECTED_SYNC_SNAPSHOT_PAGES.c.snapshot_id.in_(snapshot_ids)
            )
        )
        connection.execute(
            _SELECTED_SYNC_SNAPSHOTS.delete().where(
                _SELECTED_SYNC_SNAPSHOTS.c.id.in_(snapshot_ids)
            )
        )

    def _prune_changes(self, connection) -> None:
        sequences = connection.execute(
            select(_SELECTED_SYNC_CHANGES.c.sequence).order_by(
                _SELECTED_SYNC_CHANGES.c.sequence.asc()
            )
        ).scalars().all()
        if len(sequences) <= self._max_changes:
            return
        floor_sequence = int(sequences[-self._max_changes]) - 1
        connection.execute(
            _SELECTED_SYNC_CHANGES.delete().where(
                _SELECTED_SYNC_CHANGES.c.sequence <= floor_sequence
            )
        )
        connection.execute(
            _SELECTED_SYNC_CURSORS.delete().where(
                _SELECTED_SYNC_CURSORS.c.sequence < floor_sequence
            )
        )
        connection.execute(
            _SELECTED_SYNC_STATE.update()
            .where(_SELECTED_SYNC_STATE.c.id == 1)
            .values(floor_sequence=floor_sequence, updated_at=_iso_now())
        )


def _selected_sync_hash(item: SelectedSyncItem) -> str:
    body = json.dumps(
        {"default": item.default, "minimal": item.minimal},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Source registry repositories (Phase 2B)
# ---------------------------------------------------------------------------


class SqliteSourceRepository:
    """Persist sources while keeping seed initialization non-destructive."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def seed_many(self, sources: Sequence[Source]) -> int:
        if not sources:
            return 0
        with self._engine.begin() as connection:
            source_ids = [source.id for source in sources]
            existing_rows = connection.execute(
                select(_SOURCES).where(_SOURCES.c.id.in_(source_ids))
            ).mappings().all()
            existing_by_id = {row["id"]: row for row in existing_rows}
            existing_ids = set(existing_by_id)
            for source in sources:
                statement = insert(_SOURCES).values(**_source_values(source))
                update_values = {
                    field: getattr(statement.excluded, field)
                    for field in (
                        "name",
                        "homepage",
                        "feed_url",
                        "kind",
                        "publisher_group",
                        "evidence_eligible",
                        "signal_only",
                        "categories",
                        "language",
                        "schedule_minutes",
                        "parser_version",
                    )
                }
                existing = existing_by_id.get(source.id)
                collector_contract_changed = existing is not None and any(
                    existing[field] != _source_values(source)[field]
                    for field in ("feed_url", "kind", "parser_version")
                )
                if (
                    collector_contract_changed
                    and existing["circuit_state"] == CircuitState.OPEN.value
                ):
                    update_values.update(
                        circuit_state=CircuitState.CLOSED.value,
                        consecutive_failures=0,
                        next_run_at=None,
                        last_error_code=None,
                    )
                statement = statement.on_conflict_do_update(
                    index_elements=[_SOURCES.c.id],
                    set_=update_values,
                )
                connection.execute(statement)
        return len(set(source_ids) - existing_ids)

    def save(self, source: Source) -> None:
        values = _source_values(source)
        statement = insert(_SOURCES).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[_SOURCES.c.id],
            set_={
                column.name: getattr(statement.excluded, column.name)
                for column in _SOURCES.c
                if column.name not in {"id", "created_at"}
            },
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def get(self, source_id: str) -> Source | None:
        statement = select(_SOURCES).where(_SOURCES.c.id == source_id)
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _source_from_row(row) if row is not None else None

    def list_all(self) -> list[Source]:
        statement = select(_SOURCES).order_by(_SOURCES.c.name, _SOURCES.c.id)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_source_from_row(row) for row in rows]

    def claim_many(
        self,
        source_ids: Sequence[str],
        *,
        now: datetime,
        lease_owner: str,
        lease_expires_at: datetime,
        due_only: bool,
        force_source_ids: Sequence[str] = (),
    ) -> list[str]:
        if not source_ids:
            return []
        now_iso = _as_utc(now).isoformat()
        lease_expires_iso = _as_utc(lease_expires_at).isoformat()
        forced = set(force_source_ids)
        claimed: list[str] = []
        with self._engine.begin() as connection:
            rows = connection.execute(
                select(_SOURCES).where(_SOURCES.c.id.in_(list(source_ids)))
            ).mappings().all()
            for row in sorted(rows, key=lambda item: item["id"]):
                lease_available = (
                    row["lease_expires_at"] is None
                    or row["lease_expires_at"] <= now_iso
                )
                due = row["next_run_at"] is None or row["next_run_at"] <= now_iso
                force_probe = row["id"] in forced
                circuit_allows = (
                    row["circuit_state"] == CircuitState.CLOSED.value
                    or due
                    or force_probe
                )
                if (
                    not bool(row["enabled"])
                    or not lease_available
                    or not circuit_allows
                    or (due_only and not due and not force_probe)
                ):
                    continue
                result = connection.execute(
                    _SOURCES.update()
                    .where(
                        _SOURCES.c.id == row["id"],
                        _SOURCES.c.enabled.is_(True),
                        (
                            _SOURCES.c.lease_expires_at.is_(None)
                            | (_SOURCES.c.lease_expires_at <= now_iso)
                        ),
                    )
                    .values(
                        lease_owner=lease_owner,
                        lease_expires_at=lease_expires_iso,
                        circuit_state=(
                            CircuitState.HALF_OPEN.value
                            if row["circuit_state"]
                            in {
                                CircuitState.OPEN.value,
                                CircuitState.HALF_OPEN.value,
                            }
                            else row["circuit_state"]
                        ),
                        updated_at=now_iso,
                    )
                )
                if result.rowcount == 1:
                    claimed.append(row["id"])
        return claimed

    def release_many(
        self,
        source_ids: Sequence[str],
        *,
        lease_owner: str,
    ) -> None:
        if not source_ids:
            return
        with self._engine.begin() as connection:
            connection.execute(
                _SOURCES.update()
                .where(
                    _SOURCES.c.id.in_(list(source_ids)),
                    _SOURCES.c.lease_owner == lease_owner,
                )
                .values(lease_owner=None, lease_expires_at=None)
            )


class SqliteSourceRunRepository:
    """Persist structured collection diagnostics for source health work."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, run: SourceRun) -> None:
        statement = insert(_SOURCE_RUNS).values(**_source_run_values(run))
        statement = statement.on_conflict_do_update(
            index_elements=[_SOURCE_RUNS.c.id],
            set_={
                column.name: getattr(statement.excluded, column.name)
                for column in _SOURCE_RUNS.c
                if column.name != "id"
            },
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def list_recent(
        self,
        source_id: str,
        since: datetime,
        limit: int = 100,
    ) -> list[SourceRun]:
        statement = (
            select(_SOURCE_RUNS)
            .where(
                _SOURCE_RUNS.c.source_id == source_id,
                _SOURCE_RUNS.c.started_at >= _as_utc(since).isoformat(),
            )
            .order_by(_SOURCE_RUNS.c.started_at.desc(), _SOURCE_RUNS.c.id.desc())
            .limit(limit)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_source_run_from_row(row) for row in rows]


class SqliteDailyReportRepository:
    """Persist immutable daily revisions and return the latest per date."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, report: DailyReport) -> None:
        statement = insert(_DAILY_REPORTS).values(
            **_daily_report_values(report)
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=[
                _DAILY_REPORTS.c.report_date,
                _DAILY_REPORTS.c.revision,
            ]
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def get(self, report_date: date) -> DailyReport | None:
        statement = (
            select(_DAILY_REPORTS)
            .where(_DAILY_REPORTS.c.report_date == report_date.isoformat())
            .order_by(_DAILY_REPORTS.c.revision.desc())
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _daily_report_from_row(row) if row is not None else None

    def latest(self) -> DailyReport | None:
        statement = (
            select(_DAILY_REPORTS)
            .order_by(
                _DAILY_REPORTS.c.report_date.desc(),
                _DAILY_REPORTS.c.revision.desc(),
            )
            .limit(1)
        )
        with self._engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _daily_report_from_row(row) if row is not None else None

    def list_recent(self, limit: int = 30) -> list[DailyReport]:
        statement = select(_DAILY_REPORTS).order_by(
            _DAILY_REPORTS.c.report_date.desc(),
            _DAILY_REPORTS.c.revision.desc(),
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        reports: list[DailyReport] = []
        seen_dates: set[str] = set()
        for row in rows:
            if row["report_date"] in seen_dates:
                continue
            seen_dates.add(row["report_date"])
            reports.append(_daily_report_from_row(row))
            if len(reports) >= limit:
                break
        return reports


def _source_values(source: Source) -> dict[str, object]:
    return {
        "id": source.id,
        "name": source.name,
        "homepage": source.homepage,
        "feed_url": source.feed_url,
        "kind": source.kind.value,
        "tier": source.tier.value,
        "publisher_group": source.publisher_group,
        "evidence_eligible": source.evidence_eligible,
        "signal_only": source.signal_only,
        "categories": [category.value for category in source.categories],
        "language": source.language,
        "schedule_minutes": source.schedule_minutes,
        "enabled": source.enabled,
        "parser_version": source.parser_version,
        "circuit_state": source.circuit_state.value,
        "consecutive_failures": source.consecutive_failures,
        "last_success_at": _optional_iso(source.last_success_at),
        "next_run_at": _optional_iso(source.next_run_at),
        "success_rate_7d": source.success_rate_7d,
        "last_error_code": source.last_error_code,
        "last_checked_at": _optional_iso(source.last_checked_at),
        "lease_owner": source.lease_owner,
        "lease_expires_at": _optional_iso(source.lease_expires_at),
        "created_at": _as_utc(source.created_at).isoformat(),
        "updated_at": _as_utc(source.updated_at).isoformat(),
    }


def _source_from_row(row: object) -> Source:
    return Source(
        id=row["id"],
        name=row["name"],
        homepage=row["homepage"],
        feed_url=row["feed_url"],
        kind=SourceKind(row["kind"]),
        tier=SourceTier(row["tier"]),
        publisher_group=row["publisher_group"],
        evidence_eligible=bool(row["evidence_eligible"]),
        signal_only=bool(row["signal_only"]),
        categories=tuple(TopicCategory(value) for value in row["categories"]),
        language=row["language"],
        schedule_minutes=int(row["schedule_minutes"]),
        enabled=bool(row["enabled"]),
        parser_version=row["parser_version"],
        circuit_state=CircuitState(row["circuit_state"]),
        consecutive_failures=int(row["consecutive_failures"]),
        last_success_at=_optional_datetime(row["last_success_at"]),
        next_run_at=_optional_datetime(row["next_run_at"]),
        success_rate_7d=(
            float(row["success_rate_7d"])
            if row["success_rate_7d"] is not None
            else None
        ),
        last_error_code=row["last_error_code"],
        last_checked_at=_optional_datetime(row["last_checked_at"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=_optional_datetime(row["lease_expires_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _source_run_values(run: SourceRun) -> dict[str, object]:
    return {
        "id": run.id,
        "source_id": run.source_id,
        "task_id": run.task_id,
        "window_days": run.window_days,
        "started_at": _as_utc(run.started_at).isoformat(),
        "finished_at": _as_utc(run.finished_at).isoformat(),
        "status": run.status.value,
        "stage": run.stage,
        "http_status": run.http_status,
        "fetched_count": run.fetched_count,
        "stored_count": run.stored_count,
        "skipped_count": run.skipped_count,
        "error_code": run.error_code,
        "error_summary": run.error_summary,
        "reliability_excluded_reason": run.reliability_excluded_reason,
        "retry_after_seconds": run.retry_after_seconds,
        "duration_ms": run.duration_ms,
    }


def _source_run_from_row(row: object) -> SourceRun:
    return SourceRun(
        id=row["id"],
        source_id=row["source_id"],
        task_id=row["task_id"],
        window_days=int(row["window_days"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]),
        status=SourceRunStatus(row["status"]),
        stage=row["stage"],
        http_status=row["http_status"],
        fetched_count=int(row["fetched_count"]),
        stored_count=int(row["stored_count"]),
        skipped_count=int(row["skipped_count"]),
        error_code=row["error_code"],
        error_summary=row["error_summary"],
        reliability_excluded_reason=row["reliability_excluded_reason"],
        retry_after_seconds=(
            int(row["retry_after_seconds"])
            if row["retry_after_seconds"] is not None
            else None
        ),
        duration_ms=int(row["duration_ms"]),
    )


def _daily_report_values(report: DailyReport) -> dict[str, object]:
    return {
        "report_date": report.date.isoformat(),
        "revision": report.revision,
        "timezone": report.timezone,
        "status": report.status.value,
        "lead": report.lead,
        "section_item_ids": {
            slug: list(item_ids)
            for slug, item_ids in report.section_item_ids.items()
        },
        "flash_item_ids": list(report.flash_item_ids),
        "payload_json": json.dumps(
            report.payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "strategy_version": report.strategy_version,
        "source_snapshot_at": _as_utc(report.source_snapshot_at).isoformat(),
        "generated_at": _as_utc(report.generated_at).isoformat(),
        "published_at": _optional_iso(report.published_at),
        "item_count": report.item_count,
        "source_count": report.source_count,
        "content_hash": report.content_hash,
    }


def _daily_report_from_row(row: object) -> DailyReport:
    return DailyReport(
        date=date.fromisoformat(row["report_date"]),
        timezone=row["timezone"],
        revision=int(row["revision"]),
        status=DailyReportStatus(row["status"]),
        lead=row["lead"],
        section_item_ids={
            slug: tuple(item_ids)
            for slug, item_ids in (row["section_item_ids"] or {}).items()
        },
        flash_item_ids=tuple(row["flash_item_ids"] or ()),
        payload=json.loads(row["payload_json"]),
        strategy_version=row["strategy_version"],
        source_snapshot_at=datetime.fromisoformat(row["source_snapshot_at"]),
        generated_at=datetime.fromisoformat(row["generated_at"]),
        published_at=_optional_datetime(row["published_at"]),
        item_count=int(row["item_count"]),
        source_count=int(row["source_count"]),
        content_hash=row["content_hash"],
    )


def _optional_iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _optional_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


# ---------------------------------------------------------------------------
# Curation decisions (Phase 2A distribution source of truth)
# ---------------------------------------------------------------------------


class SqliteCurationDecisionRepository:
    """Persist the precomputed decision consumed by Web, API, and RSS."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save_many(self, decisions: Sequence[CurationDecision]) -> int:
        if not decisions:
            return 0
        with self._engine.begin() as connection:
            for decision in decisions:
                values = {
                    "article_id": decision.article_id,
                    "distribution_score": decision.distribution_score,
                    "selected": decision.selected,
                    "eligible": decision.eligible,
                    "components": decision.components,
                    "threshold": decision.threshold,
                    "reason": decision.reason,
                    "score_confidence": decision.score_confidence.value,
                    "strategy_version": decision.strategy_version,
                    "scored_at": _as_utc(decision.scored_at).isoformat(),
                    "updated_at": _iso_now(),
                }
                statement = insert(_CURATION_DECISIONS).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=[_CURATION_DECISIONS.c.article_id],
                    set_={
                        column.name: getattr(statement.excluded, column.name)
                        for column in _CURATION_DECISIONS.c
                        if column.name != "article_id"
                    },
                )
                connection.execute(statement)
        return len(decisions)

    def get_many(
        self,
        article_ids: Sequence[str],
    ) -> dict[str, CurationDecision]:
        if not article_ids:
            return {}
        statement = select(_CURATION_DECISIONS).where(
            _CURATION_DECISIONS.c.article_id.in_(list(article_ids))
        )
        with self._engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return {
            row["article_id"]: CurationDecision(
                article_id=row["article_id"],
                distribution_score=float(row["distribution_score"]),
                selected=bool(row["selected"]),
                eligible=bool(row["eligible"]),
                components=row["components"] or {},
                threshold=float(row["threshold"]),
                reason=row["reason"],
                score_confidence=HeatConfidence(row["score_confidence"]),
                strategy_version=row["strategy_version"],
                scored_at=datetime.fromisoformat(row["scored_at"]),
            )
            for row in rows
        }


# ---------------------------------------------------------------------------
# Task repository (Phase 3.2)
# ---------------------------------------------------------------------------


class SqliteTaskRepository:
    """Persist task lifecycle records."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(
        self,
        task_id: str,
        status: str,
        status_history: list[str],
        mode: str,
        collected_article_count: int,
        window_days: int,
        expanded_categories: list[str],
        collection_warnings: list[str],
        source_counts: dict[str, int],
        topic_ids: list[str],
        drafted_topic_ids: list[str],
        result_json: str | None,
    ) -> None:
        now = _iso_now()
        values = {
            "id": task_id,
            "status": status,
            "status_history": json.dumps(status_history, ensure_ascii=False),
            "mode": mode,
            "collected_article_count": collected_article_count,
            "window_days": window_days,
            "expanded_categories": json.dumps(expanded_categories, ensure_ascii=False),
            "collection_warnings": json.dumps(collection_warnings, ensure_ascii=False),
            "source_counts": json.dumps(source_counts, ensure_ascii=False),
            "topic_ids": json.dumps(topic_ids, ensure_ascii=False),
            "drafted_topic_ids": json.dumps(drafted_topic_ids, ensure_ascii=False),
            "result_json": result_json,
            "created_at": now,
            "updated_at": now,
        }
        with self._engine.begin() as connection:
            stmt = insert(_TASKS).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[_TASKS.c.id],
                set_={
                    "status": values["status"],
                    "status_history": values["status_history"],
                    "topic_ids": values["topic_ids"],
                    "drafted_topic_ids": values["drafted_topic_ids"],
                    "result_json": values["result_json"],
                    "updated_at": now,
                },
            )
            connection.execute(stmt)

    def load(self, task_id: str) -> dict | None:
        stmt = select(_TASKS).where(_TASKS.c.id == task_id)
        with self._engine.connect() as connection:
            row = connection.execute(stmt).mappings().first()
        return _task_dict_from_row(row) if row is not None else None

    def list_all(self) -> list[dict]:
        stmt = select(_TASKS).order_by(_TASKS.c.created_at.desc())
        with self._engine.connect() as connection:
            rows = connection.execute(stmt).mappings().all()
        return [_task_dict_from_row(row) for row in rows]

    def update_status(
        self,
        task_id: str,
        status: str,
        status_history: list[str],
        topic_ids: list[str],
        drafted_topic_ids: list[str],
    ) -> None:
        now = _iso_now()
        with self._engine.begin() as connection:
            connection.execute(
                _TASKS.update()
                .where(_TASKS.c.id == task_id)
                .values(
                    status=status,
                    status_history=json.dumps(status_history, ensure_ascii=False),
                    topic_ids=json.dumps(topic_ids, ensure_ascii=False),
                    drafted_topic_ids=json.dumps(
                        list(drafted_topic_ids), ensure_ascii=False
                    ),
                    updated_at=now,
                )
            )


def _task_dict_from_row(row: object) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "status_history": json.loads(row["status_history"]),
        "mode": row["mode"],
        "collected_article_count": row["collected_article_count"],
        "window_days": row["window_days"],
        "expanded_categories": json.loads(row["expanded_categories"] or "[]"),
        "collection_warnings": json.loads(row["collection_warnings"] or "[]"),
        "source_counts": json.loads(row["source_counts"] or "{}"),
        "topic_ids": json.loads(row["topic_ids"] or "[]"),
        "drafted_topic_ids": json.loads(row["drafted_topic_ids"] or "[]"),
        "result_json": row["result_json"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# Evidence repository (Phase 3.2)
# ---------------------------------------------------------------------------


class SqliteEvidenceRepository:
    """Persist evidence packs keyed by topic id."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, topic_id: str, task_id: str, pack: EvidencePack) -> None:
        now = _iso_now()
        values = {
            "topic_id": topic_id,
            "task_id": task_id,
            "pack_json": pack.model_dump_json(),
            "created_at": now,
        }
        with self._engine.begin() as connection:
            stmt = insert(_EVIDENCE).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[_EVIDENCE.c.topic_id],
                set_={"pack_json": values["pack_json"]},
            )
            connection.execute(stmt)

    def load(self, topic_id: str) -> EvidencePack | None:
        stmt = select(_EVIDENCE).where(_EVIDENCE.c.topic_id == topic_id)
        with self._engine.connect() as connection:
            row = connection.execute(stmt).mappings().first()
        if row is None:
            return None
        return EvidencePack.model_validate_json(row["pack_json"])


# ---------------------------------------------------------------------------
# Draft repository (Phase 3.2)
# ---------------------------------------------------------------------------


class SqliteDraftRepository:
    """Persist draft records with full version history."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(
        self,
        draft_id: str,
        topic_id: str,
        task_id: str,
        document: DraftDocument,
        selected_title: str,
        version: int,
        trusted_body: str,
        trusted_titles: tuple[str, ...],
        quality_issues: list[QualityIssue],
        versions: list[DraftDocument],
    ) -> None:
        now = _iso_now()
        values = {
            "id": draft_id,
            "topic_id": topic_id,
            "task_id": task_id,
            "document_json": document.model_dump_json(),
            "selected_title": selected_title,
            "version": version,
            "trusted_body": trusted_body,
            "trusted_titles": json.dumps(list(trusted_titles), ensure_ascii=False),
            "quality_issues_json": json.dumps(
                [issue.model_dump() for issue in quality_issues],
                ensure_ascii=False,
            ),
            "versions_json": json.dumps(
                [v.model_dump(mode="json") for v in versions],
                ensure_ascii=False,
            ),
            "created_at": now,
            "updated_at": now,
        }
        with self._engine.begin() as connection:
            stmt = insert(_DRAFTS).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[_DRAFTS.c.id],
                set_={
                    "document_json": values["document_json"],
                    "selected_title": values["selected_title"],
                    "version": values["version"],
                    "quality_issues_json": values["quality_issues_json"],
                    "versions_json": values["versions_json"],
                    "updated_at": now,
                },
            )
            connection.execute(stmt)

    def load(self, draft_id: str) -> dict | None:
        stmt = select(_DRAFTS).where(_DRAFTS.c.id == draft_id)
        with self._engine.connect() as connection:
            row = connection.execute(stmt).mappings().first()
        return _draft_dict_from_row(row) if row is not None else None

    def list_by_task(self, task_id: str) -> list[dict]:
        stmt = (
            select(_DRAFTS)
            .where(_DRAFTS.c.task_id == task_id)
            .order_by(_DRAFTS.c.created_at.desc())
        )
        with self._engine.connect() as connection:
            rows = connection.execute(stmt).mappings().all()
        return [_draft_dict_from_row(row) for row in rows]


def _draft_dict_from_row(row: object) -> dict:
    return {
        "id": row["id"],
        "topic_id": row["topic_id"],
        "task_id": row["task_id"],
        "document": DraftDocument.model_validate_json(row["document_json"]),
        "selected_title": row["selected_title"],
        "version": row["version"],
        "trusted_body": row["trusted_body"],
        "trusted_titles": tuple(json.loads(row["trusted_titles"])),
        "quality_issues": [
            QualityIssue.model_validate(item)
            for item in json.loads(row["quality_issues_json"] or "[]")
        ],
        "versions": [
            DraftDocument.model_validate(item)
            for item in json.loads(row["versions_json"])
        ],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class SqliteCreatorProjectRepository:
    """Persist creation workflow state without duplicating task/topic data."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, project: CreatorProject) -> None:
        values = {
            "id": project.id,
            "topic_id": project.topic_id,
            "task_id": project.task_id,
            "project_json": project.model_dump_json(),
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }
        with self._engine.begin() as connection:
            statement = insert(_CREATOR_PROJECTS).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[_CREATOR_PROJECTS.c.id],
                set_={
                    "project_json": values["project_json"],
                    "updated_at": values["updated_at"],
                },
            )
            connection.execute(statement)

    def get(self, project_id: str) -> CreatorProject | None:
        statement = select(_CREATOR_PROJECTS.c.project_json).where(
            _CREATOR_PROJECTS.c.id == project_id
        )
        with self._engine.connect() as connection:
            value = connection.execute(statement).scalar_one_or_none()
        return CreatorProject.model_validate_json(value) if value else None

    def get_by_topic(self, topic_id: str) -> CreatorProject | None:
        statement = select(_CREATOR_PROJECTS.c.project_json).where(
            _CREATOR_PROJECTS.c.topic_id == topic_id
        )
        with self._engine.connect() as connection:
            value = connection.execute(statement).scalar_one_or_none()
        return CreatorProject.model_validate_json(value) if value else None

    def list_all(self) -> list[CreatorProject]:
        statement = select(_CREATOR_PROJECTS.c.project_json).order_by(
            _CREATOR_PROJECTS.c.updated_at.desc(),
            _CREATOR_PROJECTS.c.id.desc(),
        )
        with self._engine.connect() as connection:
            values = connection.execute(statement).scalars().all()
        return [CreatorProject.model_validate_json(value) for value in values]


class SqliteAccountProfileRepository:
    """Persist all profile versions and return the latest effective one."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, profile: AccountProfile) -> None:
        values = {
            "id": f"{profile.id}:v{profile.version}",
            "profile_id": profile.id,
            "version": profile.version,
            "profile_json": profile.model_dump_json(),
            "effective_at": profile.effective_at.isoformat(),
        }
        with self._engine.begin() as connection:
            statement = insert(_ACCOUNT_PROFILES).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[_ACCOUNT_PROFILES.c.id],
                set_={
                    "profile_json": values["profile_json"],
                    "effective_at": values["effective_at"],
                },
            )
            connection.execute(statement)

    def get_current(self, profile_id: str = "local-default") -> AccountProfile | None:
        statement = (
            select(_ACCOUNT_PROFILES.c.profile_json)
            .where(_ACCOUNT_PROFILES.c.profile_id == profile_id)
            .order_by(_ACCOUNT_PROFILES.c.version.desc())
            .limit(1)
        )
        with self._engine.connect() as connection:
            value = connection.execute(statement).scalar_one_or_none()
        return AccountProfile.model_validate_json(value) if value else None

    def list_versions(self, profile_id: str = "local-default") -> list[AccountProfile]:
        statement = (
            select(_ACCOUNT_PROFILES.c.profile_json)
            .where(_ACCOUNT_PROFILES.c.profile_id == profile_id)
            .order_by(_ACCOUNT_PROFILES.c.version.desc())
        )
        with self._engine.connect() as connection:
            values = connection.execute(statement).scalars().all()
        return [AccountProfile.model_validate_json(value) for value in values]


class SqliteContentOpportunityRepository:
    """Persist creation score components and the user's adopt/ignore decision."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save(self, opportunity: ContentOpportunity) -> None:
        now = _iso_now()
        values = {
            "id": opportunity.id,
            "source_id": opportunity.source_id,
            "status": opportunity.status,
            "creation_score": opportunity.creation_score,
            "opportunity_json": opportunity.model_dump_json(),
            "created_at": opportunity.created_at.isoformat(),
            "updated_at": now,
        }
        with self._engine.begin() as connection:
            statement = insert(_CONTENT_OPPORTUNITIES).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[_CONTENT_OPPORTUNITIES.c.id],
                set_={
                    "status": values["status"],
                    "creation_score": values["creation_score"],
                    "opportunity_json": values["opportunity_json"],
                    "updated_at": now,
                },
            )
            connection.execute(statement)

    def get(self, opportunity_id: str) -> ContentOpportunity | None:
        statement = select(_CONTENT_OPPORTUNITIES.c.opportunity_json).where(
            _CONTENT_OPPORTUNITIES.c.id == opportunity_id
        )
        with self._engine.connect() as connection:
            value = connection.execute(statement).scalar_one_or_none()
        return ContentOpportunity.model_validate_json(value) if value else None

    def get_by_source(self, source_id: str) -> ContentOpportunity | None:
        statement = select(_CONTENT_OPPORTUNITIES.c.opportunity_json).where(
            _CONTENT_OPPORTUNITIES.c.source_id == source_id
        )
        with self._engine.connect() as connection:
            value = connection.execute(statement).scalar_one_or_none()
        return ContentOpportunity.model_validate_json(value) if value else None

    def list_all(self) -> list[ContentOpportunity]:
        statement = select(_CONTENT_OPPORTUNITIES.c.opportunity_json).order_by(
            _CONTENT_OPPORTUNITIES.c.creation_score.desc(),
            _CONTENT_OPPORTUNITIES.c.created_at.desc(),
        )
        with self._engine.connect() as connection:
            values = connection.execute(statement).scalars().all()
        return [ContentOpportunity.model_validate_json(value) for value in values]


class SqliteGenerationAuditRepository:
    """Durable PromptVersion, LLM call and creation replay audit store."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def save_prompt_version(self, prompt: PromptVersion) -> None:
        values = {
            "id": prompt.id,
            "stage": prompt.stage,
            "version": prompt.version,
            "prompt_json": prompt.model_dump_json(),
            "created_at": prompt.created_at.isoformat(),
        }
        with self._engine.begin() as connection:
            connection.execute(
                insert(_PROMPT_VERSIONS).values(**values).on_conflict_do_nothing(
                    index_elements=[_PROMPT_VERSIONS.c.id]
                )
            )

    def list_prompt_versions(self) -> list[PromptVersion]:
        statement = select(_PROMPT_VERSIONS.c.prompt_json).order_by(
            _PROMPT_VERSIONS.c.stage,
            _PROMPT_VERSIONS.c.version.desc(),
        )
        with self._engine.connect() as connection:
            values = connection.execute(statement).scalars().all()
        return [PromptVersion.model_validate_json(value) for value in values]

    def save_call(self, call: LlmCallRecord) -> None:
        values = {
            "id": call.id,
            "operation_id": call.operation_id,
            "project_id": call.project_id,
            "draft_id": call.draft_id,
            "stage": call.stage,
            "status": call.status,
            "model": call.model,
            "call_json": call.model_dump_json(),
            "created_at": call.created_at.isoformat(),
        }
        with self._engine.begin() as connection:
            connection.execute(
                insert(_LLM_CALL_RECORDS).values(**values).on_conflict_do_nothing(
                    index_elements=[_LLM_CALL_RECORDS.c.id]
                )
            )

    def list_calls(self, limit: int = 100) -> list[LlmCallRecord]:
        statement = select(_LLM_CALL_RECORDS.c.call_json).order_by(
            _LLM_CALL_RECORDS.c.created_at.desc()
        ).limit(limit)
        with self._engine.connect() as connection:
            values = connection.execute(statement).scalars().all()
        return [LlmCallRecord.model_validate_json(value) for value in values]

    def save_replay(self, report: CreationReplayReport) -> None:
        values = {
            "id": report.id,
            "requested_mode": report.requested_mode,
            "actual_mode": report.actual_mode,
            "sample_count": report.sample_count,
            "pass_rate": report.pass_rate,
            "report_json": report.model_dump_json(),
            "created_at": report.created_at.isoformat(),
        }
        with self._engine.begin() as connection:
            connection.execute(
                insert(_CREATION_REPLAY_REPORTS).values(**values).on_conflict_do_update(
                    index_elements=[_CREATION_REPLAY_REPORTS.c.id],
                    set_={"report_json": values["report_json"]},
                )
            )

    def latest_replay(self) -> CreationReplayReport | None:
        statement = select(_CREATION_REPLAY_REPORTS.c.report_json).order_by(
            _CREATION_REPLAY_REPORTS.c.created_at.desc()
        ).limit(1)
        with self._engine.connect() as connection:
            value = connection.execute(statement).scalar_one_or_none()
        return CreationReplayReport.model_validate_json(value) if value else None

    def list_replays(self, limit: int = 20) -> list[CreationReplayReport]:
        statement = select(_CREATION_REPLAY_REPORTS.c.report_json).order_by(
            _CREATION_REPLAY_REPORTS.c.created_at.desc()
        ).limit(limit)
        with self._engine.connect() as connection:
            values = connection.execute(statement).scalars().all()
        return [CreationReplayReport.model_validate_json(value) for value in values]
