"""FastAPI routes backed by isolated in-process Phase 1 state.

Phase 3.2 adds optional SQLite-backed persistence. When repositories are
injected the service syncs task/evidence/draft state to disk; otherwise
state stays in memory as before.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, Query, status
from fastapi.responses import PlainTextResponse

from openai import OpenAI

from wechat_agent.api.schemas import (
    DraftUpdateRequest,
    QualityReport,
    WritingProfileRequest,
)
from wechat_agent.collectors.base import LiveCollector, LiveCollectionResult
from wechat_agent.collectors.registry import apply_source_registry_state
from wechat_agent.config import LlmConfig
from wechat_agent.domain.models import (
    AccountProfile,
    ArticleRecord,
    CircuitState,
    CurationDecision,
    CreatorProject,
    ContentOpportunity,
    DraftDocument,
    EvidencePack,
    QualityIssue,
    SourceRun,
    SourceRunStatus,
    TopicCandidate,
)
from wechat_agent.domain.ports import (
    AccountProfileRepository,
    ContentOpportunityRepository,
    CreatorProjectRepository,
    DailyReportRepository,
    DraftProvider,
    DraftRepository,
    EvidenceRepository,
    GenerationAuditRepository,
    SourceRepository,
    SourceRunRepository,
    TaskRepository,
)
from wechat_agent.domain.sync import SelectedSyncRepository
from wechat_agent.evidence.build import build_evidence_pack
from wechat_agent.services.pipeline import (
    ContentPipeline,
    PipelineResult,
    TaskStatus,
)
from wechat_agent.services.source_network import (
    TRANSIENT_NETWORK_ERRORS,
    SourceNetworkCoordinator,
)
from wechat_agent.story.service import StoryShadowService
from wechat_agent.writing.quality import check_quality


logger = logging.getLogger(__name__)
SOURCE_LEASE_MINUTES = 15


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class TaskRecord:
    id: str
    status: TaskStatus
    status_history: list[TaskStatus]
    result: PipelineResult | None = None
    topic_ids: list[str] = field(default_factory=list)
    drafted_topic_ids: set[str] = field(default_factory=set)
    mode: str = "demo"
    collected_article_count: int = 0
    window_days: int = 0
    expanded_categories: list[str] = field(default_factory=list)
    collection_warnings: list[str] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)

    def transition(self, status_value: TaskStatus) -> None:
        self.status = status_value
        self.status_history.append(status_value)


@dataclass
class TopicRecord:
    task_id: str
    candidate: TopicCandidate
    related_articles: list[ArticleRecord]


@dataclass
class DraftRecord:
    id: str
    topic_id: str
    document: DraftDocument
    selected_title: str
    version: int
    evidence: EvidencePack
    trusted_body: str
    trusted_titles: tuple[str, ...]
    quality_issues: list[QualityIssue] = field(default_factory=list)
    versions: list[DraftDocument] = field(default_factory=list)


class ContentApiService:
    """Own mutable Phase 1 API state for one process and app instance only.

    Run Uvicorn with ``--workers 1``. Multi-worker deployments are unsupported
    because tasks, evidence packs, and draft versions are held in memory.

    When ``task_repo``, ``evidence_repo``, and ``draft_repo`` are all provided
    the service persists state to SQLite and loads it back on restart.
    """

    def __init__(
        self,
        pipeline: ContentPipeline,
        fixture_path: Path,
        draft_provider: DraftProvider,
        live_collector: LiveCollector | None = None,
        *,
        task_repo: TaskRepository | None = None,
        evidence_repo: EvidenceRepository | None = None,
        draft_repo: DraftRepository | None = None,
        source_repo: SourceRepository | None = None,
        source_run_repo: SourceRunRepository | None = None,
        daily_report_repo: DailyReportRepository | None = None,
        selected_sync_repo: SelectedSyncRepository | None = None,
        creator_project_repo: CreatorProjectRepository | None = None,
        account_profile_repo: AccountProfileRepository | None = None,
        content_opportunity_repo: ContentOpportunityRepository | None = None,
        generation_audit_repo: GenerationAuditRepository | None = None,
        llm_config: LlmConfig | None = None,
        source_network_coordinator: SourceNetworkCoordinator | None = None,
        scheduler_running_provider: Callable[[], bool] | None = None,
        story_shadow_service: StoryShadowService | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.fixture_path = fixture_path
        self.provider = draft_provider
        self.live_collector = live_collector
        self._task_repo = task_repo
        self._evidence_repo = evidence_repo
        self._draft_repo = draft_repo
        self.source_repository = source_repo
        self.source_run_repository = source_run_repo
        self.daily_report_repository = daily_report_repo
        self.selected_sync_repository = selected_sync_repo
        self.creator_project_repository = creator_project_repo
        self.account_profile_repository = account_profile_repo
        self.content_opportunity_repository = content_opportunity_repo
        self.generation_audit_repository = generation_audit_repo
        self._llm_config = llm_config
        self.source_network_coordinator = (
            source_network_coordinator or SourceNetworkCoordinator()
        )
        self._scheduler_running_provider = (
            scheduler_running_provider or (lambda: False)
        )
        self.story_shadow_service = story_shadow_service
        self._llm_client: OpenAI | None = (
            OpenAI(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
                timeout=llm_config.request_timeout,
            )
            if llm_config is not None and llm_config.is_available
            else None
        )
        self.tasks: dict[str, TaskRecord] = {}
        self.topics: dict[str, TopicRecord] = {}
        self.evidence: dict[str, EvidencePack] = {}
        self.drafts: dict[str, DraftRecord] = {}
        self.creator_projects: dict[str, CreatorProject] = {}
        self.opportunities: dict[str, ContentOpportunity] = {}
        self.account_profile = (
            account_profile_repo.get_current()
            if account_profile_repo is not None
            else None
        ) or AccountProfile()
        if account_profile_repo is not None and account_profile_repo.get_current() is None:
            account_profile_repo.save(self.account_profile)
        self.lock = RLock()
        self._restore_from_persistence()
        self._backfill_distribution_decisions()

    @property
    def _persist_enabled(self) -> bool:
        return (
            self._task_repo is not None
            and self._evidence_repo is not None
            and self._draft_repo is not None
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _restore_from_persistence(self) -> None:
        if not self._persist_enabled:
            return
        for data in self._task_repo.list_all():  # type: ignore[union-attr]
            result_json = data.get("result_json")
            result = None
            if result_json:
                try:
                    result = (
                        PipelineResult.model_validate_json(result_json)
                        if isinstance(result_json, str)
                        else PipelineResult.model_validate(result_json)
                    )
                except (TypeError, ValueError):
                    # Keep legacy task metadata readable even if a very old
                    # pipeline payload can no longer be reconstructed.
                    result = None
            task = TaskRecord(
                id=data["id"],
                status=TaskStatus(data["status"]),
                status_history=[TaskStatus(s) for s in data["status_history"]],
                mode=data["mode"],
                collected_article_count=data["collected_article_count"],
                window_days=data["window_days"],
                expanded_categories=data["expanded_categories"],
                collection_warnings=data["collection_warnings"],
                source_counts=data["source_counts"],
                topic_ids=data["topic_ids"],
                drafted_topic_ids=set(data["drafted_topic_ids"]),
                result=result,
            )
            self.tasks[task.id] = task
            if result is not None:
                for topic_id, candidate in zip(
                    task.topic_ids,
                    result.candidates,
                    strict=False,
                ):
                    self.topics[topic_id] = TopicRecord(
                        task_id=task.id,
                        candidate=candidate.model_copy(update={"id": topic_id}),
                        related_articles=result.related_articles_by_topic.get(
                            candidate.id,
                            [],
                        ),
                    )
        for task in list(self.tasks.values()):
            for topic_id in task.topic_ids:
                pack = self._evidence_repo.load(topic_id)  # type: ignore[union-attr]
                if pack is not None:
                    self.evidence[topic_id] = pack
        for task_id in self.tasks:
            for data in self._draft_repo.list_by_task(task_id):  # type: ignore[union-attr]
                record = DraftRecord(
                    id=data["id"],
                    topic_id=data["topic_id"],
                    document=data["document"],
                    selected_title=data["selected_title"],
                    version=data["version"],
                    evidence=self.evidence.get(
                        data["topic_id"],
                        EvidencePack(topic_id=data["topic_id"]),
                    ),
                    trusted_body=data["trusted_body"],
                    trusted_titles=data["trusted_titles"],
                    quality_issues=data["quality_issues"],
                    versions=data["versions"],
                )
                self.drafts[record.id] = record
        if self.creator_project_repository is not None:
            for project in self.creator_project_repository.list_all():
                self.creator_projects[project.id] = project
        if self.content_opportunity_repository is not None:
            for opportunity in self.content_opportunity_repository.list_all():
                self.opportunities[opportunity.id] = opportunity

    def _backfill_distribution_decisions(self) -> None:
        """Migrate old task-result scoring into the new feed source of truth."""
        for task in self.tasks.values():
            if task.result is not None:
                self.pipeline.backfill_legacy_result(task.result)

    def list_distribution_items(
        self,
        since: datetime,
        *,
        limit: int = 1000,
    ) -> list[tuple[ArticleRecord, CurationDecision]]:
        """Return precomputed feed rows without consulting task state."""
        return self.pipeline.list_distribution_items(since, limit=limit)

    def list_all_distribution_items(
        self,
        *,
        limit: int = 10000,
    ) -> list[tuple[ArticleRecord, CurationDecision]]:
        """Return the complete durable pool used by selected synchronization."""
        return self.pipeline.list_all_distribution_items(limit=limit)

    def _persist_task(self, task: TaskRecord) -> None:
        if not self._persist_enabled:
            return
        self._task_repo.save(  # type: ignore[union-attr]
            task_id=task.id,
            status=task.status.value,
            status_history=[s.value for s in task.status_history],
            mode=task.mode,
            collected_article_count=task.collected_article_count,
            window_days=task.window_days,
            expanded_categories=task.expanded_categories,
            collection_warnings=task.collection_warnings,
            source_counts=task.source_counts,
            topic_ids=task.topic_ids,
            drafted_topic_ids=list(task.drafted_topic_ids),
            result_json=(
                task.result.model_dump_json()
                if task.result is not None
                else None
            ),
        )

    def _persist_evidence(self, topic_id: str, task_id: str, pack: EvidencePack) -> None:
        if not self._persist_enabled:
            return
        self._evidence_repo.save(topic_id, task_id, pack)  # type: ignore[union-attr]

    def _persist_draft(self, record: DraftRecord, task_id: str) -> None:
        if not self._persist_enabled:
            return
        self._draft_repo.save(  # type: ignore[union-attr]
            draft_id=record.id,
            topic_id=record.topic_id,
            task_id=task_id,
            document=record.document,
            selected_title=record.selected_title,
            version=record.version,
            trusted_body=record.trusted_body,
            trusted_titles=record.trusted_titles,
            quality_issues=record.quality_issues,
            versions=record.versions,
        )

    def persist_creator_project(self, project: CreatorProject) -> None:
        self.creator_projects[project.id] = project
        if self.creator_project_repository is not None:
            self.creator_project_repository.save(project)

    def persist_account_profile(self, profile: AccountProfile) -> None:
        self.account_profile = profile
        if self.account_profile_repository is not None:
            self.account_profile_repository.save(profile)

    def persist_opportunity(self, opportunity: ContentOpportunity) -> None:
        self.opportunities[opportunity.id] = opportunity
        if self.content_opportunity_repository is not None:
            self.content_opportunity_repository.save(opportunity)

    def _persist_task_status(self, task: TaskRecord) -> None:
        if not self._persist_enabled:
            return
        self._task_repo.update_status(  # type: ignore[union-attr]
            task_id=task.id,
            status=task.status.value,
            status_history=[s.value for s in task.status_history],
            topic_ids=task.topic_ids,
            drafted_topic_ids=list(task.drafted_topic_ids),
        )

    # ------------------------------------------------------------------
    # Task creation
    # ------------------------------------------------------------------

    def create_demo_task(self) -> TaskRecord:
        with self.lock:
            return self._create_demo_task()

    def _create_demo_task(self) -> TaskRecord:
        task_id = str(uuid4())
        task = TaskRecord(
            id=task_id,
            status=TaskStatus.QUEUED,
            status_history=[TaskStatus.QUEUED],
            mode="demo",
        )
        self.tasks[task_id] = task
        try:
            task.transition(TaskStatus.COLLECTING)
            articles = self._load_fixture()
            task.collected_article_count = len(articles)
            task.window_days = 0
            task.transition(TaskStatus.RANKING)
            now = max(
                (article.collected_at for article in articles),
                default=datetime.now(UTC),
            )
            result = self.pipeline.run(articles, now)
            self._refresh_daily_reports(result.articles)
            self._store_topics(task, result)
            task.transition(TaskStatus.REVIEW_READY)
            self._persist_task(task)
            return task
        except Exception as error:
            task.transition(TaskStatus.FAILED)
            self._persist_task(task)
            raise ApiError(
                500,
                "INTERNAL_FAILURE",
                "演示任务执行失败。",
                retryable=True,
            ) from error

    def create_live_task(
        self,
        *,
        due_only: bool = False,
        skip_if_none: bool = False,
    ) -> TaskRecord | None:
        if self.live_collector is None:
            raise ApiError(
                503,
                "LIVE_COLLECTION_DISABLED",
                "真实公开数据源尚未启用。",
                retryable=False,
            )
        task_id = str(uuid4())
        claim_time = datetime.now(UTC)
        if self.source_repository is not None:
            apply_source_registry_state(
                self.live_collector,
                self.source_repository,
            )
        claimed_source_ids = self._claim_source_ids(
            task_id,
            claim_time,
            due_only=due_only,
        )
        if claimed_source_ids == ():
            if skip_if_none:
                return None
            raise ApiError(
                409,
                "NO_RUNNABLE_SOURCES",
                "当前没有可运行的公开来源；来源可能尚未到期、已熔断或正在采集。",
                retryable=True,
            )
        with self.lock:
            task = TaskRecord(
                id=task_id,
                status=TaskStatus.QUEUED,
                status_history=[TaskStatus.QUEUED],
                mode="scheduled" if due_only else "live",
            )
            self.tasks[task_id] = task
            task.transition(TaskStatus.COLLECTING)
        collection: LiveCollectionResult | None = None
        runs_persisted = False
        runs_persist_attempted = False
        try:
            if claimed_source_ids is None:
                collection = self.live_collector.collect(datetime.now(UTC))
            else:
                collect_sources = getattr(
                    self.live_collector,
                    "collect_sources",
                )
                collection = collect_sources(
                    datetime.now(UTC),
                    frozenset(claimed_source_ids),
                    expand_shortages=not due_only,
                )
            with self.lock:
                self._apply_collection_metadata(task, collection)
                task.transition(TaskStatus.RANKING)
                processing_now = datetime.now(UTC)
                result = self.pipeline.run(
                    collection.articles,
                    processing_now,
                )
                runs_persist_attempted = True
                self._persist_source_runs(
                    collection,
                    result.ingested_articles,
                    task_id=task_id,
                )
                runs_persisted = True
                self._refresh_daily_reports(result.articles)
                self._store_topics(task, result)
                task.transition(TaskStatus.REVIEW_READY)
                self._persist_task(task)
            self._run_story_shadow(
                result.articles,
                processing_now,
                task_id=task_id,
            )
            return task
        except Exception as error:
            if (
                collection is not None
                and not runs_persisted
                and not runs_persist_attempted
            ):
                try:
                    self._persist_source_runs(
                        collection,
                        (),
                        task_id=task_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to persist source runs after pipeline failure"
                    )
            with self.lock:
                task.transition(TaskStatus.FAILED)
                self._persist_task(task)
            raise ApiError(
                502,
                "COLLECTION_FAILED",
                "公开数据源采集失败，请稍后重试。",
                retryable=True,
            ) from error
        finally:
            if claimed_source_ids:
                try:
                    self.source_repository.release_many(  # type: ignore[union-attr]
                        claimed_source_ids,
                        lease_owner=task_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to release source leases for task %s",
                        task_id,
                    )

    def create_scheduled_live_task(self) -> TaskRecord | None:
        """Collect only sources whose persistent nextRunAt is due."""
        return self.create_live_task(due_only=True, skip_if_none=True)

    def _run_story_shadow(
        self,
        articles: list[ArticleRecord] | tuple[ArticleRecord, ...],
        now: datetime,
        *,
        task_id: str,
    ) -> None:
        """Run Story after formal persistence and never affect task success."""
        if self.story_shadow_service is None:
            return
        source_ids = sorted(
            {
                article.source_id
                for article in articles
                if article.source_id is not None
            }
        )
        try:
            self.story_shadow_service.process(tuple(articles), now)
        except Exception as error:
            logger.exception(
                "Story shadow processing failed after task persistence task=%s sources=%s",
                task_id,
                source_ids,
            )
            try:
                self.story_shadow_service.record_failure(now, error)
            except Exception:
                logger.exception(
                    "Story shadow failure recording also failed task=%s sources=%s",
                    task_id,
                    source_ids,
                )

    def _refresh_daily_reports(
        self,
        articles: list[ArticleRecord] | tuple[ArticleRecord, ...],
    ) -> None:
        """Update only affected persisted daily reports after ingestion."""
        if self.daily_report_repository is None or not articles:
            return
        from wechat_agent.services.daily import refresh_daily_reports_for_articles

        refresh_daily_reports_for_articles(self, articles)

    def _claim_source_ids(
        self,
        task_id: str,
        now: datetime,
        *,
        due_only: bool,
    ) -> tuple[str, ...] | None:
        if self.source_repository is None or self.live_collector is None:
            return None
        collect_sources = getattr(self.live_collector, "collect_sources", None)
        claim_many = getattr(self.source_repository, "claim_many", None)
        collectors = getattr(self.live_collector, "collectors", None)
        if not callable(collect_sources) or not callable(claim_many) or collectors is None:
            return None
        configured_source_ids = tuple(
            dict.fromkeys(
                str(getattr(collector, "source_id", ""))
                for collector in collectors
                if getattr(collector, "source_id", None)
            )
        )
        plan = self.source_network_coordinator.plan_claims(
            self.source_repository.list_all(),
            configured_source_ids,
            now=now,
            due_only=due_only,
        )
        claimed = claim_many(
            plan.source_ids,
            now=now,
            lease_owner=task_id,
            lease_expires_at=now + timedelta(minutes=SOURCE_LEASE_MINUTES),
            due_only=due_only,
            force_source_ids=plan.forced_probe_ids,
        )
        return tuple(claimed)

    def source_network_status(self) -> dict[str, object]:
        """Return internal batch-network and recovery-probe diagnostics."""
        if self.source_repository is None:
            return self.source_network_coordinator.snapshot(
                (),
                scheduler_running=self._scheduler_running_provider(),
            )
        return self.source_network_coordinator.snapshot(
            self.source_repository.list_all(),
            scheduler_running=self._scheduler_running_provider(),
        )

    def _persist_source_runs(
        self,
        collection: LiveCollectionResult,
        stored_articles: list[ArticleRecord] | tuple[ArticleRecord, ...],
        *,
        task_id: str,
    ) -> None:
        if self.source_run_repository is None:
            return

        stored_by_source = Counter(
            article.source_id
            for article in stored_articles
            if article.source_id is not None
        )
        latest_run_id_by_source: dict[str, str] = {}
        for run in collection.source_runs:
            latest_run_id_by_source[run.source_id] = run.id

        latest_runs = [
            run
            for run in collection.source_runs
            if latest_run_id_by_source.get(run.source_id) == run.id
        ]
        global_network_outage = self.source_network_coordinator.observe_runs(
            latest_runs
        )

        for run in collection.source_runs:
            exclude_from_reliability = (
                global_network_outage
                and run.status is SourceRunStatus.FAILED
                and run.error_code in TRANSIENT_NETWORK_ERRORS
            )
            persisted_run = run.model_copy(
                update={
                    "task_id": task_id,
                    "reliability_excluded_reason": (
                        "global_network_outage"
                        if exclude_from_reliability
                        else None
                    ),
                    "stored_count": (
                        stored_by_source.get(run.source_id, 0)
                        if latest_run_id_by_source.get(run.source_id) == run.id
                        else 0
                    )
                }
            )
            self.source_run_repository.save(persisted_run)
            self._refresh_source_health(persisted_run)

    def _refresh_source_health(self, run: SourceRun) -> None:
        if self.source_repository is None or self.source_run_repository is None:
            return
        source = self.source_repository.get(run.source_id)
        if source is None:
            return

        successful = run.status in {
            SourceRunStatus.SUCCESS,
            SourceRunStatus.PARTIAL,
        }
        global_network_outage = (
            run.reliability_excluded_reason == "global_network_outage"
        )
        attempted = (
            run.status is not SourceRunStatus.SKIPPED
            and not global_network_outage
        )
        permanent_client_error = (
            run.http_status is not None
            and 400 <= run.http_status < 500
            and run.http_status not in {408, 425, 429}
        )
        if successful:
            consecutive_failures = 0
            circuit_state = CircuitState.CLOSED
        elif attempted:
            consecutive_failures = (
                max(3, source.consecutive_failures + 1)
                if permanent_client_error
                else source.consecutive_failures + 1
            )
            circuit_state = (
                CircuitState.OPEN
                if consecutive_failures >= 3
                else source.circuit_state
            )
        elif global_network_outage:
            consecutive_failures = source.consecutive_failures
            circuit_state = CircuitState.OPEN
        else:
            consecutive_failures = source.consecutive_failures
            circuit_state = source.circuit_state

        recent_runs = self.source_run_repository.list_recent(
            source.id,
            run.finished_at - timedelta(days=7),
            limit=1000,
        )
        attempts = [
            item
            for item in recent_runs
            if item.status is not SourceRunStatus.SKIPPED
            and item.reliability_excluded_reason is None
        ]
        success_rate = (
            sum(
                item.status
                in {SourceRunStatus.SUCCESS, SourceRunStatus.PARTIAL}
                for item in attempts
            )
            / len(attempts)
            if attempts
            else None
        )
        if successful:
            delay = timedelta(minutes=source.schedule_minutes)
        elif global_network_outage:
            delay = self.source_network_coordinator.outage_cooldown
        elif permanent_client_error:
            delay = timedelta(hours=12)
        else:
            backoff_multiplier = 2 ** min(consecutive_failures, 5)
            delay = timedelta(
                minutes=min(
                    source.schedule_minutes * backoff_multiplier,
                    12 * 60,
                )
            )
        if run.retry_after_seconds is not None:
            delay = max(
                delay,
                timedelta(seconds=run.retry_after_seconds),
            )
        self.source_repository.save(
            source.model_copy(
                update={
                    "circuit_state": circuit_state,
                    "consecutive_failures": consecutive_failures,
                    "last_success_at": (
                        run.finished_at if successful else source.last_success_at
                    ),
                    "next_run_at": run.finished_at + delay,
                    "success_rate_7d": success_rate,
                    "last_error_code": (
                        None
                        if successful
                        else (
                            "global_network_outage"
                            if global_network_outage
                            else run.error_code
                        )
                    ),
                    "last_checked_at": run.finished_at,
                    "updated_at": run.finished_at,
                }
            )
        )

    def _apply_collection_metadata(
        self,
        task: TaskRecord,
        collection: LiveCollectionResult,
    ) -> None:
        task.collected_article_count = len(collection.articles)
        task.window_days = collection.window_days
        task.expanded_categories = [
            category.value for category in collection.expanded_categories
        ]
        task.collection_warnings = list(collection.warnings)
        task.source_counts = dict(collection.source_counts)

    def _store_topics(
        self,
        task: TaskRecord,
        result: PipelineResult,
    ) -> None:
        task.result = result
        for candidate in result.candidates:
            api_topic_id = f"{task.id}:{candidate.id}"
            api_candidate = candidate.model_copy(update={"id": api_topic_id})
            self.topics[api_topic_id] = TopicRecord(
                task_id=task.id,
                candidate=api_candidate,
                related_articles=result.related_articles_by_topic[
                    candidate.id
                ],
            )
            task.topic_ids.append(api_topic_id)

    def _load_fixture(self) -> list[ArticleRecord]:
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("fixture root must be a list")
        return [ArticleRecord.model_validate(item) for item in payload]


ServiceProvider = Callable[[], ContentApiService]


# ---------------------------------------------------------------------------
# Export helpers (Phase 3.5)
# ---------------------------------------------------------------------------


def _export_markdown(draft: DraftDocument, pack: EvidencePack) -> str:
    """Export a draft as Markdown suitable for 秀米 paste."""
    title = draft.title
    body = draft.body or draft.content
    lines = [f"# {title}", "", body, "", "---", "", "## 参考来源"]
    for i, url in enumerate(draft.cited_source_urls, start=1):
        lines.append(f"- [来源{i}]({url})")
    if pack.unverified_claims:
        lines.append("")
        lines.append("## 待核实内容")
        for claim in pack.unverified_claims:
            lines.append(f"> ⚠️ 待核实：{claim}")
    return "\n".join(lines) + "\n"


def _export_html(draft: DraftDocument, pack: EvidencePack) -> str:
    """Export a draft as rich-text HTML for 公众号 editor paste."""
    title = draft.title
    body_html = (draft.body or "").replace("\n\n", "</p><p>").replace("\n", "<br>")
    body_html = f"<p>{body_html}</p>"
    sources = "".join(
        f'<li><a href="{url}">来源{i+1}</a></li>'
        for i, url in enumerate(draft.cited_source_urls)
    )
    unverified = ""
    if pack.unverified_claims:
        claims_html = "".join(
            f"<li>⚠️ 待核实：{claim}</li>"
            for claim in pack.unverified_claims
        )
        unverified = f"<h2>待核实内容</h2><ul>{claims_html}</ul>"
    return (
        f"<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head><body>"
        f"<h1>{title}</h1>{body_html}<hr><h2>参考来源</h2><ul>{sources}</ul>"
        f"{unverified}</body></html>"
    )


def _export_text(draft: DraftDocument) -> str:
    """Export a draft as plain text."""
    title = draft.title
    body = draft.body or ""
    return f"{title}\n\n{body}\n"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_router(service_provider: ServiceProvider) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/tasks/demo", status_code=status.HTTP_201_CREATED)
    def create_demo_task() -> dict[str, object]:
        task = service_provider().create_demo_task()
        return _task_payload(task)

    @router.post("/tasks/live", status_code=status.HTTP_201_CREATED)
    def create_live_task() -> dict[str, object]:
        task = service_provider().create_live_task()
        return _task_payload(task)

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            return _task_payload(_task_or_404(service, task_id))

    @router.get("/tasks/{task_id}/topics")
    def get_topics(task_id: str) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            task = _task_or_404(service, task_id)
            candidates = [
                service.topics[topic_id].candidate
                for topic_id in task.topic_ids
            ]
            return {
                "items": [
                    candidate.model_dump(mode="json") for candidate in candidates
                ],
                "shortage": (
                    {
                        category.value: count
                        for category, count in task.result.shortage.items()
                    }
                    if task.result is not None
                    else {}
                ),
            }

    @router.get("/topics/{topic_id}/evidence")
    def get_evidence(topic_id: str) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            pack = _evidence_for(service, topic_id)
            return pack.model_dump(mode="json")

    @router.post(
        "/topics/{topic_id}/draft",
        status_code=status.HTTP_201_CREATED,
    )
    def create_draft(
        topic_id: str,
        profile_request: WritingProfileRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            topic = _topic_or_404(service, topic_id)
            pack = _evidence_for(service, topic_id)
            task = _task_or_404(service, topic.task_id)
            if task.status is not TaskStatus.COMPLETED:
                task.transition(TaskStatus.DRAFTING)
            document = service.provider.generate(
                pack, profile_request.to_domain()
            )
            issues = check_quality(
                document, pack,
                llm_client=service._llm_client,
                llm_model=(
                    service._llm_config.model
                    if service._llm_config is not None
                    else "deepseek-chat"
                ),
            )
            record = DraftRecord(
                id=str(uuid4()),
                topic_id=topic_id,
                document=document,
                selected_title=document.title,
                version=1,
                evidence=pack,
                trusted_body=document.body,
                trusted_titles=tuple(document.titles),
                quality_issues=issues,
                versions=[document.model_copy(deep=True)],
            )
            service.drafts[record.id] = record
            service._persist_draft(record, task.id)
            task.drafted_topic_ids.add(topic_id)
            _sync_task_quality_status(service, task)
            return _draft_payload(record)

    @router.put("/drafts/{draft_id}")
    def update_draft(
        draft_id: str,
        update: DraftUpdateRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            record = service.drafts.get(draft_id)
            if record is None:
                raise _not_found()
            titles = [update.selected_title] + [
                title
                for title in record.document.titles
                if title != update.selected_title
            ]
            document = DraftDocument(
                titles=titles[:5],
                body=update.body,
                cited_source_urls=record.document.cited_source_urls,
                stage="research",
            )
            record.document = document
            record.selected_title = update.selected_title
            record.version += 1
            record.quality_issues = check_quality(
                document,
                record.evidence,
                trusted_body=record.trusted_body,
                trusted_titles=record.trusted_titles,
                llm_client=service._llm_client,
                llm_model=(
                    service._llm_config.model
                    if service._llm_config is not None
                    else "deepseek-chat"
                ),
            )
            record.versions.append(document.model_copy(deep=True))
            topic = _topic_or_404(service, record.topic_id)
            task = _task_or_404(service, topic.task_id)
            service._persist_draft(record, task.id)
            _sync_task_quality_status(service, task)
            return _draft_payload(record)

    @router.get("/drafts/{draft_id}/export")
    def export_draft(
        draft_id: str,
        format: str = Query("markdown", alias="format"),
    ) -> PlainTextResponse:
        """Export a draft in Markdown, HTML, or plain-text format.

        ``GET /api/drafts/{draft_id}/export?format=markdown`` (default)
        """
        service = service_provider()
        with service.lock:
            record = service.drafts.get(draft_id)
            if record is None:
                raise _not_found()
            if any(issue.blocking for issue in record.quality_issues):
                raise ApiError(
                    409,
                    "QUALITY_BLOCKED",
                    "草稿仍有质量阻断项，修复后才能导出。",
                )
            if format == "html":
                content = _export_html(record.document, record.evidence)
                media_type = "text/html; charset=utf-8"
            elif format == "text":
                content = _export_text(record.document)
                media_type = "text/plain; charset=utf-8"
            else:
                content = _export_markdown(record.document, record.evidence)
                media_type = "text/markdown; charset=utf-8"
            return PlainTextResponse(
                content=content,
                media_type=media_type,
            )

    return router


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _task_or_404(service: ContentApiService, task_id: str) -> TaskRecord:
    task = service.tasks.get(task_id)
    if task is None:
        raise _not_found()
    return task


def _topic_or_404(service: ContentApiService, topic_id: str) -> TopicRecord:
    topic = service.topics.get(topic_id)
    if topic is None:
        raise _not_found()
    return topic


def _evidence_for(
    service: ContentApiService, topic_id: str
) -> EvidencePack:
    topic = _topic_or_404(service, topic_id)
    pack = service.evidence.get(topic_id)
    if pack is None:
        pack = build_evidence_pack(
            topic.candidate, topic.related_articles
        )
        service.evidence[topic_id] = pack
        service._persist_evidence(topic_id, topic.task_id, pack)
    return pack


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "请求的资源不存在。")


def _task_payload(task: TaskRecord) -> dict[str, object]:
    return {
        "id": task.id,
        "status": task.status.value,
        "status_history": [status_value.value for status_value in task.status_history],
        "mode": task.mode,
        "collected_article_count": task.collected_article_count,
        "window_days": task.window_days,
        "expanded_categories": task.expanded_categories,
        "collection_warnings": task.collection_warnings,
        "source_counts": task.source_counts,
    }


def _draft_payload(record: DraftRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "topic_id": record.topic_id,
        "version": record.version,
        "available_versions": list(range(1, len(record.versions) + 1)),
        "selected_title": record.selected_title,
        **record.document.model_dump(mode="json"),
        "quality": QualityReport.from_issues(
            record.quality_issues
        ).model_dump(mode="json"),
    }


def _sync_task_quality_status(
    service: ContentApiService,
    task: TaskRecord,
) -> None:
    """Keep task completion aligned with current review/export eligibility."""
    quality_ready_topics = {
        record.topic_id
        for record in service.drafts.values()
        if record.topic_id in task.topic_ids
        and not any(issue.blocking for issue in record.quality_issues)
    }
    desired = (
        TaskStatus.COMPLETED
        if task.topic_ids
        and all(topic_id in quality_ready_topics for topic_id in task.topic_ids)
        else TaskStatus.REVIEW_READY
    )
    if task.status is not desired:
        task.transition(desired)
        service._persist_task_status(task)
