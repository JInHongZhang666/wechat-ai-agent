"""API v1 routes — stable, versioned endpoints for AI Cabin.

New clients should use /api/v1/*. The legacy /api/* routes remain for
backward compatibility but are marked as deprecated.

Features:
  - ETag / If-None-Match support for conditional requests
  - Problem JSON error responses
  - Cursor-based pagination for items
  - Standard query parameters (mode, window, category, q, limit)
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import md5, sha256
import json
import re
from statistics import median
from time import perf_counter
import unicodedata
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from wechat_agent.api.errors import (
    AppError,
    ProblemDetail,
    invalid_cursor,
    not_found,
    snapshot_required,
    temporarily_unavailable,
)
from wechat_agent.api.schemas import (
    AccountProfileUpdateRequest,
    AuthorInputRequest,
    ContentBriefUpdateRequest,
    CreatorProjectCreateRequest,
    DraftPolishRequest,
    InspirationPreviewRequest,
    InspirationProjectCreateRequest,
    DraftUpdateRequest,
    DraftApprovalRequest,
    OpportunityDecisionRequest,
    WritingProfileRequest,
)
from wechat_agent.api.public_schemas import (
    DailyIndexResponse,
    DailyReportResponse,
    FingerprintResponse,
    HotTopicsResponse,
    ItemsResponse,
    SelectedChangesResponse,
    SelectedSnapshotResponse,
)
from wechat_agent.api.routes import ContentApiService, DraftRecord, TaskRecord, TopicRecord
from wechat_agent.services.daily import (
    daily_report_payload,
    ensure_recent_daily_reports,
    generate_daily_for_date,
    generate_daily_report,
    list_daily_reports,
    materialize_daily_report,
)
from wechat_agent.services.reliability import build_source_reliability_report
from wechat_agent.scoring.llm_guard import (
    distribution_llm_snapshot,
    open_distribution_llm_circuit,
    reset_distribution_llm_circuit,
)
from wechat_agent.domain.models import (
    AccountProfile,
    ArticleRecord,
    CircuitState,
    CurationDecision,
    CreatorProject,
    CreationReplayItem,
    CreationReplayReport,
    ExportRecord,
    InspirationSource,
    Source,
    SourceRun,
    HeatScore,
    SimilarityReport,
    public_category_slug,
)
from wechat_agent.services.pipeline import PipelineResult, TaskStatus
from wechat_agent.writing.brief import build_content_brief, validate_content_brief
from wechat_agent.writing.quality import check_quality
from wechat_agent.writing.inspiration import (
    InspirationImportError,
    build_inspiration_research,
    fetch_inspiration_source,
    related_stored_articles,
    source_from_manual_text,
    source_from_stored_article,
)
from wechat_agent.writing.originality import evaluate_originality
from wechat_agent.writing.provider import llm_error_message
from wechat_agent.writing.workflow import (
    build_draft_version,
    build_opportunity,
    build_quality_report,
    confirmed_author_section,
)
from wechat_agent.writing.export import export_wechat_html
from wechat_agent.domain.sync import SelectedSyncItem, SnapshotRequiredError


def _etag_for(data: str) -> str:
    """Generate a weak ETag from response content."""
    return f'"{md5(data.encode()).hexdigest()}"'


def _check_etag(request: Request, current_etag: str) -> bool:
    """Return True if the client's cached version is still fresh."""
    if_none_match = request.headers.get("If-None-Match", "")
    return current_etag in {
        value.strip() for value in if_none_match.split(",") if value.strip()
    }


def _not_modified(
    etag: str,
    cache_control: str,
    *,
    result_count: int | None = None,
) -> Response:
    """Return a bodyless 304 without JSON content-length corruption."""
    headers = {"ETag": etag, "Cache-Control": cache_control}
    if result_count is not None:
        headers["X-Result-Count"] = str(result_count)
    return Response(
        status_code=status.HTTP_304_NOT_MODIFIED,
        headers=headers,
    )


ServiceProvider = Callable[[], ContentApiService]


def create_v1_router(service_provider: ServiceProvider) -> APIRouter:
    """Create the /api/v1 router with stable endpoints."""
    router = APIRouter(prefix="/api/v1")

    @router.get("/sources")
    def list_sources(
        request: Request,
        health: str = Query(
            "all",
            pattern="^(all|healthy|degraded|failing|unknown|disabled)$",
        ),
        limit: int = Query(100, ge=1, le=500),
    ) -> JSONResponse:
        """List persistent source configuration and seven-day health state."""
        service = service_provider()
        if service.source_repository is None:
            raise not_found()
        sources = service.source_repository.list_all()
        if health != "all":
            sources = [
                source
                for source in sources
                if _source_health_label(source) == health
            ]
        sources = sources[:limit]
        payload = {
            "schemaVersion": 1,
            "items": [_source_payload(source) for source in sources],
            "count": len(sources),
        }
        body = json.dumps(payload, ensure_ascii=False, default=str)
        etag = _etag_for(body)
        if _check_etag(request, etag):
            return _not_modified(etag, "private, max-age=15")
        return JSONResponse(
            content=json.loads(body),
            headers={"ETag": etag, "Cache-Control": "private, max-age=15"},
        )

    @router.get("/sources/reliability")
    def source_reliability(
        days: int = Query(7, ge=1, le=30),
    ) -> dict[str, object]:
        """Summarize durable source-run evidence for the release gate."""
        service = service_provider()
        if (
            service.source_repository is None
            or service.source_run_repository is None
        ):
            raise not_found()
        return build_source_reliability_report(
            service.source_repository,
            service.source_run_repository,
            days=days,
        )

    @router.get("/sources/network-status", include_in_schema=False)
    def source_network_status() -> dict[str, object]:
        """Expose internal global-outage and staged-recovery diagnostics."""
        return service_provider().source_network_status()

    @router.get("/sources/{source_id}/runs")
    def list_source_runs(
        source_id: str,
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, object]:
        """Return the most recent structured executions for one source."""
        service = service_provider()
        if (
            service.source_repository is None
            or service.source_run_repository is None
            or service.source_repository.get(source_id) is None
        ):
            raise not_found()
        runs = service.source_run_repository.list_recent(
            source_id,
            datetime.now(UTC) - timedelta(days=30),
            limit=limit,
        )
        return {
            "schemaVersion": 1,
            "sourceId": source_id,
            "items": [_source_run_payload(run) for run in runs],
            "count": len(runs),
        }

    # ── Tasks ────────────────────────────────────────────────────────

    @router.post("/tasks/demo", status_code=status.HTTP_201_CREATED)
    def create_demo_task() -> dict[str, object]:
        """Create a demo task using fixture data."""
        task = service_provider().create_demo_task()
        return _v1_task_payload(task)

    @router.post("/tasks/live", status_code=status.HTTP_201_CREATED)
    def create_live_task() -> dict[str, object]:
        """Create a live task from real public sources."""
        task = service_provider().create_live_task()
        return _v1_task_payload(task)

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, object]:
        """Get task status and metadata."""
        service = service_provider()
        with service.lock:
            task = service.tasks.get(task_id)
            if task is None:
                raise not_found()
            return _v1_task_payload(task)

    @router.get("/tasks/{task_id}/topics")
    def get_topics(
        task_id: str,
        request: Request,
    ) -> JSONResponse:
        """List topic candidates for a task."""
        service = service_provider()
        with service.lock:
            task = service.tasks.get(task_id)
            if task is None:
                raise not_found()
            candidates = [
                service.topics[topic_id].candidate
                for topic_id in task.topic_ids
            ]
            payload = {
                "schemaVersion": 1,
                "items": [c.model_dump(mode="json") for c in candidates],
                "shortage": (
                    {
                        category.value: count
                        for category, count in task.result.shortage.items()
                    }
                    if task.result is not None
                    else {}
                ),
            }
            body = json.dumps(payload, ensure_ascii=False, default=str)
            etag = _etag_for(body)
            if _check_etag(request, etag):
                return _not_modified(etag, "private, max-age=60")
            return JSONResponse(
                content=json.loads(body),
                headers={"ETag": etag, "Cache-Control": "private, max-age=60"},
            )

    # ── Evidence & Drafts ─────────────────────────────────────────────

    @router.get("/topics/{topic_id}/evidence")
    def get_evidence(topic_id: str, request: Request) -> JSONResponse:
        """Get evidence pack for a topic."""
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _evidence_for
            pack = _evidence_for(service, topic_id)
            payload = pack.model_dump(mode="json")
            body = json.dumps(payload, ensure_ascii=False, default=str)
            etag = _etag_for(body)
            if _check_etag(request, etag):
                return _not_modified(etag, "private, max-age=60")
            return JSONResponse(
                content=json.loads(body),
                headers={"ETag": etag, "Cache-Control": "private, max-age=60"},
            )

    @router.get("/topics/{topic_id}/draft")
    def get_draft(topic_id: str) -> dict[str, object]:
        """Get the current draft for a topic."""
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _topic_or_404, _evidence_for
            topic = _topic_or_404(service, topic_id)
            # Return the latest draft if it exists
            for record in service.drafts.values():
                if record.topic_id == topic_id:
                    from wechat_agent.api.routes import _draft_payload
                    return _draft_payload(record)
            # No draft yet — return evidence pack as preview
            pack = _evidence_for(service, topic_id)
            return {
                "topic_id": topic_id,
                "has_draft": False,
                "evidence": pack.model_dump(mode="json"),
            }

    # ── Creation workflow ─────────────────────────────────────────────

    @router.get("/creation/profile")
    def get_creation_profile() -> dict[str, object]:
        service = service_provider()
        with service.lock:
            return _account_profile_payload(service.account_profile)

    @router.get("/creation/runtime")
    def get_creation_runtime() -> dict[str, object]:
        service = service_provider()
        repository = service.generation_audit_repository
        calls = repository.list_calls(100) if repository is not None else []
        prompts = repository.list_prompt_versions() if repository is not None else []
        latest_replay = repository.latest_replay() if repository is not None else None
        configured = service._llm_config is not None
        current_model = service._llm_config.model if configured else None
        model_calls = [
            call for call in calls
            if call.provider == "openai-compatible" and call.model == current_model
        ][:20]
        successful = sum(call.status == "success" for call in model_calls)
        errors = sum(call.status == "error" for call in model_calls)
        latest_model_call = model_calls[0] if model_calls else None
        latest_success = next((call for call in model_calls if call.status == "success"), None)
        latest_error = next((call for call in model_calls if call.status == "error"), None)
        if not configured:
            health_state = "unconfigured"
        elif latest_model_call is None:
            health_state = "untested"
        elif latest_model_call.status == "success":
            health_state = "healthy"
        else:
            health_state = "degraded"
        return {
            "schemaVersion": 1,
            "configured": configured,
            "verified": health_state == "healthy",
            "healthState": health_state,
            "activeMode": "ai" if configured else "rule",
            "provider": "openai-compatible" if configured else "rule-engine",
            "model": current_model,
            "distributionProcessing": distribution_llm_snapshot(service._llm_config),
            "generationStages": ["outline", "section", "editor"],
            "promptVersions": [_prompt_payload(prompt) for prompt in prompts],
            "recentCalls": {
                "count": len(calls), "successful": successful,
                "fallbacks": sum(call.status in {"fallback", "skipped"} for call in calls),
                "errors": errors,
                "modelAttempts": len(model_calls),
            },
            "lastAttemptAt": latest_model_call.created_at.isoformat() if latest_model_call else None,
            "lastSuccessAt": latest_success.created_at.isoformat() if latest_success else None,
            "lastError": (
                {
                    "code": latest_error.error_code or "LLM_UNKNOWN_ERROR",
                    "message": llm_error_message(latest_error.error_code),
                    "stage": latest_error.stage,
                    "createdAt": latest_error.created_at.isoformat(),
                }
                if latest_error is not None else None
            ),
            "latestReplay": _creation_replay_payload(latest_replay) if latest_replay else None,
        }

    @router.post("/creation/runtime/probe")
    def probe_creation_runtime() -> dict[str, object]:
        """Verify the configured model without creating projects or drafts."""
        service = service_provider()
        if service._llm_config is None or not hasattr(service.provider, "probe"):
            raise AppError(
                409,
                "LLM_NOT_CONFIGURED",
                "尚未配置可用的模型密钥，当前只能使用安全规则回退。",
            )
        record = service.provider.probe()
        if record.status == "success":
            reset_distribution_llm_circuit(service._llm_config)
        elif record.error_code in {"LLM_INSUFFICIENT_BALANCE", "LLM_AUTH_FAILED"}:
            open_distribution_llm_circuit(service._llm_config, record.error_code)
        return {
            "schemaVersion": 1,
            "ok": record.status == "success",
            "healthState": "healthy" if record.status == "success" else "degraded",
            "message": (
                "模型连通性验证通过，可以开始 AI 影子回放。"
                if record.status == "success"
                else llm_error_message(record.error_code)
            ),
            "call": _llm_call_payload(record),
        }

    @router.get("/creation/generation-calls")
    def list_generation_calls(limit: int = Query(50, ge=1, le=200)) -> dict[str, object]:
        repository = service_provider().generation_audit_repository
        calls = repository.list_calls(limit) if repository is not None else []
        return {"schemaVersion": 1, "items": [_llm_call_payload(call) for call in calls], "count": len(calls)}

    @router.get("/creation/replays")
    def list_creation_replays(limit: int = Query(20, ge=1, le=100)) -> dict[str, object]:
        repository = service_provider().generation_audit_repository
        reports = repository.list_replays(limit) if repository is not None else []
        return {"schemaVersion": 1, "items": [_creation_replay_payload(report) for report in reports], "count": len(reports)}

    @router.post("/creation/replays", status_code=status.HTTP_201_CREATED)
    def run_creation_replay(
        mode: str = Query("rule", pattern="^(rule|current)$"),
        limit: int = Query(10, ge=1, le=30),
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _evidence_for
            from wechat_agent.writing.brief import build_content_brief
            from wechat_agent.writing.provider import RuleBasedDraftProvider

            provider = service.provider if mode == "current" else RuleBasedDraftProvider()
            ranked = sorted(
                service.topics.values(),
                key=lambda item: (item.candidate.heat_score.value, item.candidate.id),
                reverse=True,
            )
            items: list[CreationReplayItem] = []
            for topic in ranked:
                if len(items) >= limit:
                    break
                try:
                    pack = _evidence_for(service, topic.candidate.id)
                except Exception:
                    continue
                brief = build_content_brief(pack)
                if not brief.ready_for_draft:
                    continue
                started = perf_counter()
                if hasattr(provider, "generate_with_audit"):
                    outcome = provider.generate_with_audit(
                        pack, service.account_profile.to_writing_profile(), brief=brief
                    )
                    document = outcome.document
                    method = outcome.method
                else:
                    document = provider.generate(
                        pack, service.account_profile.to_writing_profile(), brief=brief
                    )
                    method = "rule"
                issues = check_quality(document, pack, profile=service.account_profile)
                blocking = [issue.code for issue in issues if issue.blocking]
                items.append(CreationReplayItem(
                    topic_id=topic.candidate.id,
                    title=topic.candidate.title,
                    generation_method=method,
                    passed=not blocking,
                    blocking_issue_codes=blocking,
                    cited_source_count=len(document.cited_source_urls),
                    evidence_claim_count=len(pack.facts),
                    visible_characters=len(re.sub(r"\s+", "", document.body)),
                    duration_ms=(perf_counter() - started) * 1000,
                ))
            methods = {item.generation_method for item in items}
            actual_mode = next(iter(methods)) if len(methods) == 1 else "mixed"
            passed_count = sum(item.passed for item in items)
            report = CreationReplayReport(
                id=str(uuid4()), requested_mode=mode, actual_mode=actual_mode,
                profile_version=service.account_profile.version,
                prompt_version_ids=[item.id for item in service.generation_audit_repository.list_prompt_versions()] if service.generation_audit_repository else [],
                sample_count=len(items), passed_count=passed_count,
                pass_rate=passed_count / len(items) if items else 0,
                source_coverage_rate=(sum(item.cited_source_count >= 2 for item in items) / len(items) if items else 0),
                unsupported_number_count=sum("unsupported_number" in item.blocking_issue_codes for item in items),
                fabricated_experience_count=sum("unverified_personal_test" in item.blocking_issue_codes for item in items),
                profile_mismatch_count=sum("profile_length_mismatch" in item.blocking_issue_codes for item in items),
                median_duration_ms=median([item.duration_ms for item in items]) if items else 0,
                items=items,
            )
            if service.generation_audit_repository is not None:
                service.generation_audit_repository.save_replay(report)
            return _creation_replay_payload(report)

    @router.put("/creation/profile")
    def update_creation_profile(
        request: AccountProfileUpdateRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            if request.version != service.account_profile.version:
                raise AppError(
                    409,
                    "PROFILE_VERSION_CONFLICT",
                    "账号画像已更新，请刷新后重新保存。",
                )
            profile = request.to_domain().model_copy(
                update={
                    "id": service.account_profile.id,
                    "version": service.account_profile.version + 1,
                    "effective_at": datetime.now(UTC),
                }
            )
            service.persist_account_profile(profile)
            return _account_profile_payload(profile)

    @router.get("/creation/opportunities")
    def list_creation_opportunities() -> dict[str, object]:
        service = service_provider()
        with service.lock:
            opportunities = _refresh_opportunities(service)
            return {
                "schemaVersion": 1,
                "items": [_opportunity_payload(item) for item in opportunities[:10]],
                "count": min(len(opportunities), 10),
            }

    @router.post("/creation/opportunities/{opportunity_id}/decision")
    def decide_creation_opportunity(
        opportunity_id: str,
        request: OpportunityDecisionRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            opportunity = service.opportunities.get(opportunity_id)
            if opportunity is None:
                _refresh_opportunities(service)
                opportunity = service.opportunities.get(opportunity_id)
            if opportunity is None:
                raise not_found()
            if request.decision == "adopted" and opportunity.blockers:
                raise AppError(
                    409,
                    "OPPORTUNITY_BLOCKED",
                    "该机会仍有证据阻断项，不能进入正式创作。",
                )
            opportunity = opportunity.model_copy(
                update={
                    "status": request.decision,
                    "ignore_reason": request.reason if request.decision == "ignored" else None,
                    "decided_at": datetime.now(UTC),
                }
            )
            service.persist_opportunity(opportunity)
            return _opportunity_payload(opportunity)

    @router.post(
        "/creation/projects",
        status_code=status.HTTP_201_CREATED,
    )
    def create_creator_project(
        request: CreatorProjectCreateRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _evidence_for, _topic_or_404

            topic = _topic_or_404(service, request.topic_id)
            existing = (
                service.creator_project_repository.get_by_topic(request.topic_id)
                if service.creator_project_repository is not None
                else next(
                    (
                        item
                        for item in service.creator_projects.values()
                        if item.topic_id == request.topic_id
                    ),
                    None,
                )
            )
            if existing is not None:
                service.creator_projects[existing.id] = existing
                return _creator_project_payload(service, existing)
            pack = _evidence_for(service, request.topic_id)
            opportunity = (
                service.content_opportunity_repository.get_by_source(request.topic_id)
                if service.content_opportunity_repository is not None
                else next(
                    (item for item in service.opportunities.values() if item.source_id == request.topic_id),
                    None,
                )
            ) or build_opportunity(topic.candidate, pack, service.account_profile)
            if opportunity.blockers:
                opportunity = opportunity.model_copy(update={"status": "blocked"})
            elif opportunity.status == "new":
                opportunity = opportunity.model_copy(
                    update={"status": "adopted", "decided_at": datetime.now(UTC)}
                )
            service.persist_opportunity(opportunity)
            now = datetime.now(UTC)
            project = CreatorProject(
                id=str(uuid4()),
                topic_id=request.topic_id,
                task_id=topic.task_id,
                opportunity=opportunity,
                profile_version=service.account_profile.version,
                stage="evidence_ready" if pack.ready_for_draft else "research",
                created_at=now,
                updated_at=now,
            )
            service.persist_creator_project(project)
            return _creator_project_payload(service, project)

    @router.get("/creation/hot-articles")
    def list_creation_hot_articles(
        limit: int = Query(12, ge=1, le=30),
        window: str = Query("7d", pattern="^(24h|7d)$"),
    ) -> dict[str, object]:
        """Expose current AIHOT high-score items as creation inspirations.

        ``score`` is the platform's distribution/quality score. It is never
        presented as an unavailable WeChat read-count or third-party metric.
        """
        service = service_provider()
        cutoff = datetime.now(UTC) - (
            timedelta(hours=24) if window == "24h" else timedelta(days=7)
        )
        rows = service.list_distribution_items(cutoff, limit=1000)
        rows.sort(
            key=lambda row: (
                row[1].selected,
                row[1].distribution_score,
                row[0].published_at,
            ),
            reverse=True,
        )
        return {
            "schemaVersion": 1,
            "metricLabel": "AIHOT 分发质量分",
            "metricDisclosure": "该分数综合来源、时效、讨论信号与内容质量，不等同于微信阅读量。",
            "items": [
                {
                    "articleId": article.id,
                    "title": article.title,
                    "url": article.source_url,
                    "publisher": article.source_name,
                    "category": public_category_slug(article.category),
                    "publishedAt": article.published_at.isoformat(),
                    "score": round(decision.distribution_score, 2),
                    "selected": decision.selected,
                    "scoreConfidence": decision.score_confidence.value,
                }
                for article, decision in rows[:limit]
            ],
            "count": min(limit, len(rows)),
        }

    @router.post(
        "/creation/inspiration/preview",
    )
    def preview_inspiration_source(
        request: InspirationPreviewRequest,
    ) -> dict[str, object]:
        """Parse one inspiration article without creating durable workflow data."""
        service = service_provider()
        stored_articles = [
            article
            for article, _ in service.list_all_distribution_items(limit=10000)
        ]
        try:
            primary = _primary_inspiration_from_request(request, stored_articles)
        except InspirationImportError as exc:
            raise AppError(422, exc.code, str(exc)) from exc
        return {
            "schemaVersion": 1,
            "source": _inspiration_source_payload(primary),
            "policy": "正文只用于主题、抽象结构和最终相似度检查；事实仍需独立来源确认。",
        }

    @router.post(
        "/creation/projects/from-inspiration",
        status_code=status.HTTP_201_CREATED,
    )
    def create_project_from_inspiration(
        request: InspirationProjectCreateRequest,
    ) -> dict[str, object]:
        """Create a research project from a hot article or user URL."""
        service = service_provider()
        stored_articles = [
            article
            for article, _ in service.list_all_distribution_items(limit=10000)
        ]
        try:
            primary = _primary_inspiration_from_request(request, stored_articles)
            explicit_supporting = [
                fetch_inspiration_source(
                    url,
                    role="supporting",
                    source_kind="supporting_url",
                    known_article=_known_article_for_url(url, stored_articles),
                )
                for url in request.supporting_urls
                if _canonical_article_url(url) != _canonical_article_url(request.primary_url)
            ]
        except InspirationImportError as exc:
            raise AppError(422, exc.code, str(exc)) from exc

        auto_articles = related_stored_articles(primary, stored_articles, limit=5)
        supporting = _deduplicate_inspiration_sources(
            [*explicit_supporting, *[source_from_stored_article(item) for item in auto_articles]],
            primary_url=primary.url,
        )
        task_id = str(uuid4())
        topic_id = f"{task_id}:inspiration"
        candidate, related, pack = build_inspiration_research(
            topic_id=topic_id,
            primary=primary,
            supporting=supporting,
        )
        task = TaskRecord(
            id=task_id,
            status=TaskStatus.REVIEW_READY,
            status_history=[TaskStatus.QUEUED, TaskStatus.COLLECTING, TaskStatus.RANKING, TaskStatus.REVIEW_READY],
            mode="inspiration",
            collected_article_count=len(related),
            window_days=7,
            source_counts={source.publisher: 1 for source in [primary, *supporting]},
            topic_ids=[topic_id],
            result=PipelineResult(
                saved_article_count=0,
                candidates=[candidate],
                related_articles_by_topic={candidate.id: related},
                articles=related,
                scores={candidate.id: candidate.heat_score},
                selected_article_ids=[],
            ),
        )
        opportunity = build_opportunity(candidate, pack, service.account_profile)
        opportunity = opportunity.model_copy(
            update={
                "status": "adopted" if pack.ready_for_draft else "blocked",
                "decided_at": datetime.now(UTC),
            }
        )
        now = datetime.now(UTC)
        project = CreatorProject(
            id=str(uuid4()), topic_id=topic_id, task_id=task_id,
            origin="inspiration", inspiration_sources=[primary, *supporting],
            opportunity=opportunity, profile_version=service.account_profile.version,
            stage="evidence_ready" if pack.ready_for_draft else "research",
            created_at=now, updated_at=now,
        )
        with service.lock:
            service.tasks[task.id] = task
            service.topics[topic_id] = TopicRecord(
                task_id=task.id, candidate=candidate, related_articles=related
            )
            service.evidence[topic_id] = pack
            service._persist_task(task)
            service._persist_evidence(topic_id, task.id, pack)
            service.persist_opportunity(opportunity)
            service.persist_creator_project(project)
        return _creator_project_payload(service, project)

    @router.get("/creation/projects")
    def list_creator_projects() -> dict[str, object]:
        service = service_provider()
        with service.lock:
            projects = sorted(
                service.creator_projects.values(),
                key=lambda item: (item.updated_at, item.id),
                reverse=True,
            )
            return {
                "schemaVersion": 1,
                "items": [
                    _creator_project_payload(service, project)
                    for project in projects
                ],
                "count": len(projects),
            }

    @router.get("/creation/projects/{project_id}")
    def get_creator_project(project_id: str) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            project = _creator_project_or_404(service, project_id)
            return _creator_project_payload(service, project)

    @router.get("/creation/projects/{project_id}/evidence")
    def get_creator_project_evidence(project_id: str) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _evidence_for

            project = _creator_project_or_404(service, project_id)
            return _evidence_for(service, project.topic_id).model_dump(mode="json")

    @router.get("/creation/projects/{project_id}/opportunity")
    def get_creator_project_opportunity(project_id: str) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            project = _creator_project_or_404(service, project_id)
            if project.opportunity is None:
                raise not_found()
            return _opportunity_payload(project.opportunity)

    @router.post("/creation/projects/{project_id}/author-inputs")
    def save_author_input(
        project_id: str,
        request: AuthorInputRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            project = _creator_project_or_404(service, project_id)
            if request.project_version != project.version:
                raise AppError(409, "PROJECT_VERSION_CONFLICT", "项目已更新，请刷新后再保存作者材料。")
            now = datetime.now(UTC)
            input_id = request.id or str(uuid4())
            item = request.to_domain(input_id=input_id).model_copy(
                update={"created_at": now, "updated_at": now}
            )
            inputs = [existing for existing in project.author_inputs if existing.id != input_id]
            inputs.append(item)
            project = project.model_copy(
                update={
                    "author_inputs": inputs,
                    "version": project.version + 1,
                    "updated_at": now,
                }
            )
            service.persist_creator_project(project)
            return _creator_project_payload(service, project)

    @router.post("/creation/projects/{project_id}/brief")
    def save_creator_project_brief(
        project_id: str,
        request: ContentBriefUpdateRequest | None = None,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _evidence_for

            project = _creator_project_or_404(service, project_id)
            pack = _evidence_for(service, project.topic_id)
            if request is None:
                brief = build_content_brief(pack)
            else:
                if request.version != project.version:
                    raise AppError(
                        409,
                        "PROJECT_VERSION_CONFLICT",
                        "创作项目已经被更新，请刷新后再保存。",
                    )
                brief = request.to_domain().model_copy(
                    update={"version": project.version + 1}
                )
                brief = validate_content_brief(brief, pack)
            next_version = max(project.version + 1, brief.version)
            project = project.model_copy(
                update={
                    "brief": brief.model_copy(update={"version": next_version}),
                    "stage": "brief_ready" if brief.ready_for_draft else "research",
                    "version": next_version,
                    "updated_at": datetime.now(UTC),
                }
            )
            service.persist_creator_project(project)
            return _creator_project_payload(service, project)

    @router.post(
        "/creation/projects/{project_id}/drafts",
        status_code=status.HTTP_201_CREATED,
    )
    def create_creator_project_draft(
        project_id: str,
        profile_request: WritingProfileRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _evidence_for, _draft_payload

            project = _creator_project_or_404(service, project_id)
            pack = _evidence_for(service, project.topic_id)
            writing_profile = profile_request.to_domain()
            brief = project.brief
            if brief is None or not brief.ready_for_draft:
                raise AppError(
                    409,
                    "BRIEF_NOT_READY",
                    "Content Brief 尚未通过证据门，不能生成正式研究稿。",
                )
            if hasattr(service.provider, "generate_with_audit"):
                generation = service.provider.generate_with_audit(
                    pack,
                    writing_profile,
                    brief=brief,
                    project_id=project.id,
                )
                document = generation.document
            else:
                document = service.provider.generate(
                    pack,
                    writing_profile,
                    brief=brief,
                )
                generation = None
            author_section, author_input_ids = confirmed_author_section(project.author_inputs)
            if author_section:
                document = document.model_copy(
                    update={"body": f"{document.body}\n\n{author_section}"}
                )
            issues = check_quality(
                document,
                pack,
                profile=service.account_profile,
                allow_confirmed_personal_experience=bool(author_input_ids),
                llm_client=service._llm_client,
                llm_model=(
                    service._llm_config.model
                    if service._llm_config is not None
                    else "deepseek-chat"
                ),
            )
            draft_id = str(uuid4())
            similarity_reports: list[SimilarityReport] = []
            if project.origin == "inspiration":
                similarity_reports, originality_issues = evaluate_originality(
                    document,
                    project.inspiration_sources,
                    draft_id=draft_id,
                    draft_version=1,
                )
                issues.extend(originality_issues)
            record = DraftRecord(
                id=draft_id,
                topic_id=project.topic_id,
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
            service._persist_draft(record, project.task_id)
            draft_version = build_draft_version(
                draft_id=record.id,
                version=1,
                parent_version=None,
                generation_method=(generation.method if generation is not None else "rule"),
                document=document,
                selected_title=document.title,
                profile_version=service.account_profile.version,
                evidence=pack,
                author_input_ids=author_input_ids,
                model=(generation.model if generation is not None else None),
                change_summary="按已批准 Content Brief 生成首版研究稿",
                prompt_version_id=(generation.prompt_version_id if generation is not None else None),
                llm_call_ids=(generation.call_ids if generation is not None else []),
                generation_prompt_hash=(generation.prompt_hash if generation is not None else None),
            )
            quality_report = build_quality_report(
                draft_id=record.id,
                draft_version=1,
                profile_version=service.account_profile.version,
                issues=issues,
            )
            project = project.model_copy(
                update={
                    "current_draft_id": record.id,
                    "draft_versions": [*project.draft_versions, draft_version],
                    "quality_reports": [*project.quality_reports, quality_report],
                    "similarity_reports": [*project.similarity_reports, *similarity_reports],
                    "human_approved_draft_version": None,
                    "stage": (
                        "review_blocked"
                        if any(issue.blocking for issue in issues)
                        else "review_passed"
                    ),
                    "version": project.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            service.persist_creator_project(project)
            return {
                "project": _creator_project_payload(service, project),
                "draft": _draft_payload(record),
            }

    @router.put("/creation/drafts/{draft_id}")
    def update_creator_draft(
        draft_id: str,
        request: DraftUpdateRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _draft_payload

            record = service.drafts.get(draft_id)
            if record is None:
                raise not_found()
            if request.version is not None and request.version != record.version:
                raise AppError(409, "DRAFT_VERSION_CONFLICT", "草稿已更新，请刷新后重新保存。")
            project = next(
                (
                    item
                    for item in service.creator_projects.values()
                    if item.current_draft_id == draft_id
                ),
                None,
            )
            titles = [request.selected_title] + [
                title
                for title in record.document.titles
                if title != request.selected_title
            ]
            document = record.document.model_copy(
                update={
                    "titles": titles[:5],
                    "body": request.body,
                    "stage": "research",
                }
            )
            record.document = document
            record.selected_title = request.selected_title
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
                profile=service.account_profile,
                allow_confirmed_personal_experience=bool(
                    project is not None
                    and any(item.confirmed for item in project.author_inputs)
                ),
                allow_human_reviewed_edits=True,
            )
            similarity_reports: list[SimilarityReport] = []
            if project is not None and project.origin == "inspiration":
                similarity_reports, originality_issues = evaluate_originality(
                    document,
                    project.inspiration_sources,
                    draft_id=record.id,
                    draft_version=record.version,
                )
                record.quality_issues.extend(originality_issues)
            record.versions.append(document.model_copy(deep=True))
            if project is not None:
                author_input_ids = [item.id for item in project.author_inputs if item.confirmed]
                draft_version = build_draft_version(
                    draft_id=record.id,
                    version=record.version,
                    parent_version=record.version - 1,
                    generation_method="author_edit",
                    document=document,
                    selected_title=request.selected_title,
                    profile_version=project.profile_version,
                    evidence=record.evidence,
                    author_input_ids=author_input_ids,
                    change_summary="作者编辑标题或正文",
                )
                quality_report = build_quality_report(
                    draft_id=record.id,
                    draft_version=record.version,
                    profile_version=project.profile_version,
                    issues=record.quality_issues,
                )
                project = project.model_copy(
                    update={
                        "draft_versions": [*project.draft_versions, draft_version],
                        "quality_reports": [*project.quality_reports, quality_report],
                        "similarity_reports": [*project.similarity_reports, *similarity_reports],
                        "human_approved_draft_version": None,
                        "stage": (
                            "review_blocked"
                            if any(issue.blocking for issue in record.quality_issues)
                            else "review_passed"
                        ),
                        "version": project.version + 1,
                        "updated_at": datetime.now(UTC),
                    }
                )
                service.persist_creator_project(project)
                task_id = project.task_id
            else:
                task_id = service.topics[record.topic_id].task_id
            service._persist_draft(record, task_id)
            return _draft_payload(record)

    @router.get("/creation/drafts/{draft_id}")
    def get_creator_draft(draft_id: str) -> dict[str, object]:
        """Return the current editable draft and its latest quality report."""
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _draft_payload

            record = service.drafts.get(draft_id)
            if record is None:
                raise not_found()
            project = next(
                (
                    item
                    for item in service.creator_projects.values()
                    if item.current_draft_id == draft_id
                ),
                None,
            )
            return _draft_payload(record)

    @router.post("/creation/drafts/{draft_id}/polish")
    def polish_creator_draft(
        draft_id: str,
        request: DraftPolishRequest,
    ) -> dict[str, object]:
        """Run evidence-bound, originality-gated pre-publication polishing."""
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _draft_payload

            record = service.drafts.get(draft_id)
            if record is None:
                raise not_found()
            if request.version != record.version:
                raise AppError(409, "DRAFT_VERSION_CONFLICT", "草稿已更新，请刷新后重新优化。")
            project = next(
                (
                    item
                    for item in service.creator_projects.values()
                    if item.current_draft_id == draft_id
                ),
                None,
            )
            if project is None:
                raise AppError(409, "CREATION_PROJECT_REQUIRED", "当前草稿未关联内容生产项目。")
            if not hasattr(service.provider, "polish_with_audit"):
                raise AppError(
                    503,
                    "AI_POLISH_UNAVAILABLE",
                    "当前未启用 AI 写作模型，无法执行深度润色；仍可手动编辑并运行质量检查。",
                    retryable=True,
                )
            try:
                generation = service.provider.polish_with_audit(
                    record.evidence,
                    request.to_domain(),
                    record.document,
                    brief=project.brief,
                    project_id=project.id,
                    draft_id=record.id,
                )
            except Exception as error:
                raise AppError(
                    503,
                    "AI_POLISH_FAILED",
                    "AI 深度润色暂时失败，原稿未被修改，请稍后重试。",
                    retryable=True,
                ) from error

            document = generation.document
            record.document = document
            record.selected_title = document.title
            record.version += 1
            record.trusted_body = document.body
            record.trusted_titles = tuple(document.titles)
            record.quality_issues = check_quality(
                document,
                record.evidence,
                llm_client=service._llm_client,
                llm_model=(
                    service._llm_config.model
                    if service._llm_config is not None
                    else "deepseek-chat"
                ),
                profile=service.account_profile,
                allow_confirmed_personal_experience=any(
                    item.confirmed for item in project.author_inputs
                ),
            )
            similarity_reports: list[SimilarityReport] = []
            if project.origin == "inspiration":
                similarity_reports, originality_issues = evaluate_originality(
                    document,
                    project.inspiration_sources,
                    draft_id=record.id,
                    draft_version=record.version,
                )
                record.quality_issues.extend(originality_issues)
            record.versions.append(document.model_copy(deep=True))
            author_input_ids = [item.id for item in project.author_inputs if item.confirmed]
            draft_version = build_draft_version(
                draft_id=record.id,
                version=record.version,
                parent_version=record.version - 1,
                generation_method=generation.method,
                document=document,
                selected_title=document.title,
                profile_version=project.profile_version,
                evidence=record.evidence,
                author_input_ids=author_input_ids,
                model=generation.model,
                change_summary="发表前去 AI 味与阅读节奏优化",
                prompt_version_id=generation.prompt_version_id,
                llm_call_ids=generation.call_ids,
                generation_prompt_hash=generation.prompt_hash,
            )
            quality_report = build_quality_report(
                draft_id=record.id,
                draft_version=record.version,
                profile_version=project.profile_version,
                issues=record.quality_issues,
            )
            project = project.model_copy(
                update={
                    "draft_versions": [*project.draft_versions, draft_version],
                    "quality_reports": [*project.quality_reports, quality_report],
                    "similarity_reports": [*project.similarity_reports, *similarity_reports],
                    "human_approved_draft_version": None,
                    "stage": (
                        "review_blocked"
                        if any(issue.blocking for issue in record.quality_issues)
                        else "review_passed"
                    ),
                    "version": project.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            service.persist_creator_project(project)
            service._persist_draft(record, project.task_id)
            return {
                "project": _creator_project_payload(service, project),
                "draft": _draft_payload(record),
            }

    @router.post("/creation/drafts/{draft_id}/review")
    def review_creator_draft(draft_id: str) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import _draft_payload

            record = service.drafts.get(draft_id)
            if record is None:
                raise not_found()
            project = next(
                (
                    item
                    for item in service.creator_projects.values()
                    if item.current_draft_id == draft_id
                ),
                None,
            )
            record.quality_issues = check_quality(
                record.document,
                record.evidence,
                trusted_body=record.trusted_body,
                trusted_titles=record.trusted_titles,
                llm_client=service._llm_client,
                llm_model=(
                    service._llm_config.model
                    if service._llm_config is not None
                    else "deepseek-chat"
                ),
                profile=service.account_profile,
                allow_confirmed_personal_experience=bool(
                    project is not None
                    and any(item.confirmed for item in project.author_inputs)
                ),
                allow_human_reviewed_edits=True,
            )
            if project is not None:
                similarity_reports: list[SimilarityReport] = []
                if project.origin == "inspiration":
                    similarity_reports, originality_issues = evaluate_originality(
                        record.document,
                        project.inspiration_sources,
                        draft_id=record.id,
                        draft_version=record.version,
                    )
                    record.quality_issues.extend(originality_issues)
                quality_report = build_quality_report(
                    draft_id=record.id,
                    draft_version=record.version,
                    profile_version=project.profile_version,
                    issues=record.quality_issues,
                )
                project = project.model_copy(
                    update={
                        "quality_reports": [*project.quality_reports, quality_report],
                        "similarity_reports": [*project.similarity_reports, *similarity_reports],
                        "stage": (
                            "review_blocked"
                            if any(issue.blocking for issue in record.quality_issues)
                            else "review_passed"
                        ),
                        "version": project.version + 1,
                        "updated_at": datetime.now(UTC),
                    }
                )
                service.persist_creator_project(project)
                service._persist_draft(record, project.task_id)
            return _draft_payload(record)

    @router.post("/creation/drafts/{draft_id}/approve")
    def approve_creator_draft(
        draft_id: str,
        request: DraftApprovalRequest,
    ) -> dict[str, object]:
        service = service_provider()
        with service.lock:
            record = service.drafts.get(draft_id)
            if record is None:
                raise not_found()
            if request.version != record.version:
                raise AppError(409, "DRAFT_VERSION_CONFLICT", "草稿已更新，请刷新后重新确认。")
            if any(issue.blocking for issue in record.quality_issues):
                raise AppError(409, "QUALITY_BLOCKED", "草稿仍有质量阻断项，不能确认定稿。")
            project = next(
                (item for item in service.creator_projects.values() if item.current_draft_id == draft_id),
                None,
            )
            if project is None:
                raise not_found()
            final_similarity_reports: list[SimilarityReport] = []
            if project.origin == "inspiration":
                final_similarity_reports, originality_issues = evaluate_originality(
                    record.document,
                    project.inspiration_sources,
                    draft_id=record.id,
                    draft_version=record.version + 1,
                )
                if any(issue.blocking for issue in originality_issues):
                    record.quality_issues.extend(originality_issues)
                    service._persist_draft(record, project.task_id)
                    raise AppError(
                        409,
                        "ORIGINALITY_BLOCKED",
                        "当前草稿未通过与主灵感文章不超过 10% 的原创度硬门。",
                    )
            latest_report = next(
                (
                    report
                    for report in reversed(project.quality_reports)
                    if report.draft_version == record.version and report.passed
                ),
                None,
            )
            if latest_report is None:
                latest_report = build_quality_report(
                    draft_id=draft_id,
                    draft_version=record.version,
                    profile_version=project.profile_version,
                    issues=record.quality_issues,
                )
            record.version += 1
            record.document = record.document.model_copy(update={"stage": "review_ready"})
            record.versions.append(record.document.model_copy(deep=True))
            service._persist_draft(record, project.task_id)
            final_version = build_draft_version(
                draft_id=draft_id,
                version=record.version,
                parent_version=record.version - 1,
                generation_method="finalize",
                document=record.document,
                selected_title=record.selected_title,
                profile_version=project.profile_version,
                evidence=record.evidence,
                author_input_ids=[item.id for item in project.author_inputs if item.confirmed],
                change_summary="作者完成人工事实核对并确认定稿",
            )
            latest_report = build_quality_report(
                draft_id=draft_id,
                draft_version=record.version,
                profile_version=project.profile_version,
                issues=record.quality_issues,
                human_confirmed=True,
            )
            reports = [*project.quality_reports, latest_report]
            project = project.model_copy(
                update={
                    "quality_reports": reports,
                    "similarity_reports": [*project.similarity_reports, *final_similarity_reports],
                    "draft_versions": [*project.draft_versions, final_version],
                    "human_approved_draft_version": record.version,
                    "stage": "review_passed",
                    "version": project.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            service.persist_creator_project(project)
            return _creator_project_payload(service, project)

    @router.get("/creation/drafts/{draft_id}/export")
    def export_creator_draft(
        draft_id: str,
        format: str = Query("markdown", pattern="^(markdown|html|text|wechat_html)$"),
    ) -> PlainTextResponse:
        """Export only a quality-approved draft for WeChat editing."""
        service = service_provider()
        with service.lock:
            from wechat_agent.api.routes import (
                _export_html,
                _export_markdown,
                _export_text,
            )

            record = service.drafts.get(draft_id)
            if record is None:
                raise not_found()
            if any(issue.blocking for issue in record.quality_issues):
                raise AppError(
                    409,
                    "QUALITY_BLOCKED",
                    "草稿仍有质量阻断项，修复后才能导出。",
                )
            project = next(
                (item for item in service.creator_projects.values() if item.current_draft_id == draft_id),
                None,
            )
            if project is None or project.human_approved_draft_version != record.version:
                raise AppError(
                    409,
                    "HUMAN_APPROVAL_REQUIRED",
                    "当前草稿版本尚未经过人工最终确认，不能导出正式稿。",
                )
            if project.origin == "inspiration":
                current_reports = [
                    report for report in project.similarity_reports
                    if report.draft_id == draft_id
                    and report.draft_version == record.version
                    and any(source.id == report.source_id and source.role == "primary" for source in project.inspiration_sources)
                ]
                if not current_reports or any(not report.passed for report in current_reports):
                    raise AppError(
                        409,
                        "ORIGINALITY_BLOCKED",
                        "当前定稿缺少已通过的 10% 原创相似度报告，不能导出。",
                    )
            if format == "wechat_html":
                content = export_wechat_html(record.document, record.evidence)
                media_type = "text/html; charset=utf-8"
            elif format == "html":
                content = _export_html(record.document, record.evidence)
                media_type = "text/html; charset=utf-8"
            elif format == "text":
                content = _export_text(record.document)
                media_type = "text/plain; charset=utf-8"
            else:
                content = _export_markdown(record.document, record.evidence)
                media_type = "text/markdown; charset=utf-8"
            if project is not None:
                latest_report = next(
                    report for report in reversed(project.quality_reports)
                    if report.draft_version == record.version and report.human_confirmed
                )
                export_record = ExportRecord(
                    id=str(uuid4()),
                    draft_id=draft_id,
                    draft_version=record.version,
                    format=format,
                    quality_report_id=latest_report.id,
                    content_sha256=sha256(content.encode("utf-8")).hexdigest(),
                )
                service.persist_creator_project(
                    project.model_copy(
                        update={
                            "stage": "exported",
                            "export_records": [*project.export_records, export_record],
                            "version": project.version + 1,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
            return PlainTextResponse(content=content, media_type=media_type)

    # ── Items (articles feed) ─────────────────────────────────────────

    @router.get(
        "/items",
        response_model=ItemsResponse,
        responses={
            304: {"description": "Not modified"},
            400: {"model": ProblemDetail, "description": "Invalid cursor"},
            422: {"model": ProblemDetail, "description": "Invalid query"},
        },
    )
    def list_items(
        request: Request,
        mode: str = Query("selected", pattern="^(selected|all)$"),
        window: str = Query("7d", pattern="^(24h|7d)$"),
        category: str | None = Query(None, pattern="^(ai-models|ai-products|industry|paper|tip)$"),
        q: str | None = Query(None, min_length=2, max_length=200),
        by: str = Query("timeline", pattern="^(timeline|published)$"),
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None, max_length=512),
    ) -> JSONResponse:
        """List pre-assessed AI items from the persistent distribution store."""
        service = service_provider()
        cursor_state = _decode_cursor(cursor) if cursor else None
        request_time = datetime.now(UTC)
        anchor = (
            cursor_state.anchor
            if cursor_state is not None
            else request_time.replace(second=0, microsecond=0)
        )
        if anchor > request_time + timedelta(minutes=5):
            raise invalid_cursor()
        cutoff = anchor - (
            timedelta(hours=24) if window == "24h" else timedelta(days=7)
        )
        rows = _get_item_rows(service, since=cutoff)

        # Apply filters
        if mode == "selected":
            rows = [row for row in rows if row.decision.selected]

        if category:
            rows = [
                row
                for row in rows
                if public_category_slug(row.article.category) == category
            ]

        if q:
            q_lower = q.casefold()
            rows = [
                row for row in rows
                if q_lower in row.article.title.casefold()
                or q_lower in row.article.summary.casefold()
            ]

        strategy_versions = sorted(
            {row.decision.strategy_version for row in rows}
        )
        cursor_signature = _cursor_signature(
            mode=mode,
            window=window,
            category=category,
            q=q,
            by=by,
            strategy_versions=strategy_versions,
        )
        if (
            cursor_state is not None
            and cursor_state.signature != cursor_signature
        ):
            raise invalid_cursor()

        rows.sort(
            key=lambda row: (
                _item_sort_at(row.article, by=by, anchor=anchor),
                row.article.id,
            ),
            reverse=True,
        )
        if cursor_state is not None:
            rows = [
                row
                for row in rows
                if (
                    _item_sort_at(row.article, by=by, anchor=anchor),
                    row.article.id,
                )
                < (cursor_state.sort_at, cursor_state.item_id)
            ]
        page_candidates = rows[: limit + 1]
        has_more = len(page_candidates) > limit
        page_rows = page_candidates[:limit]
        next_cursor = (
            _encode_cursor(
                anchor=anchor,
                sort_at=_item_sort_at(
                    page_rows[-1].article,
                    by=by,
                    anchor=anchor,
                ),
                item_id=page_rows[-1].article.id,
                signature=cursor_signature,
            )
            if has_more and page_rows
            else None
        )

        payload = {
            "schemaVersion": 1,
            "query": {
                "mode": mode,
                "category": category,
                "q": q,
                "window": window,
                "by": by,
                "ordering": (
                    "timelineDesc" if by == "timeline" else "publishedDesc"
                ),
                "anchorAt": anchor.isoformat(),
            },
            "items": [
                _public_item_payload(row, anchor=anchor)
                for row in page_rows
            ],
            "page": {
                "count": len(page_rows),
                "hasMore": has_more,
                "nextCursor": next_cursor,
                "cursorVersion": 1,
            },
        }
        body = json.dumps(payload, ensure_ascii=False, default=str)
        etag = _etag_for(body)
        if _check_etag(request, etag):
            return _not_modified(
                etag,
                "public, s-maxage=60",
                result_count=len(page_rows),
            )
        return JSONResponse(
            content=json.loads(body),
            headers={
                "ETag": etag,
                "Cache-Control": "public, s-maxage=60",
                "X-Result-Count": str(len(page_rows)),
            },
        )

    # ── Hot topics ────────────────────────────────────────────────────

    @router.get(
        "/hot-topics",
        response_model=HotTopicsResponse,
        responses={304: {"description": "Not modified"}},
    )
    def hot_topics(request: Request) -> JSONResponse:
        """Get current hot topics from persisted distribution assessments."""
        service = service_provider()
        publisher_groups = (
            {
                source.id: source.publisher_group
                for source in service.source_repository.list_all()
            }
            if service.source_repository is not None
            else {}
        )
        topics = _build_hot_topics(
            _get_item_rows(
                service,
                since=datetime.now(UTC) - timedelta(hours=24),
            ),
            publisher_groups=publisher_groups,
        )

        payload = {
            "schemaVersion": 1,
            "count": len(topics),
            "items": topics[:10],
        }
        body = json.dumps(payload, ensure_ascii=False, default=str)
        etag = _etag_for(body)
        if _check_etag(request, etag):
            return _not_modified(
                etag,
                "public, s-maxage=300",
                result_count=len(payload["items"]),
            )
        return JSONResponse(
            content=json.loads(body),
            headers={
                "ETag": etag,
                "Cache-Control": "public, s-maxage=300",
                "X-Result-Count": str(len(payload["items"])),
            },
        )

    # ── Daily reports ─────────────────────────────────────────────────

    @router.get(
        "/dailies/latest",
        response_model=DailyReportResponse,
        responses={
            304: {"description": "Not modified"},
            404: {"model": ProblemDetail, "description": "No daily report"},
        },
    )
    def latest_daily(request: Request) -> JSONResponse:
        """Get the latest daily report."""
        service = service_provider()
        report = generate_daily_report(service)
        body = json.dumps(report, ensure_ascii=False, default=str)
        etag = _etag_for(body)
        if _check_etag(request, etag):
            return _not_modified(
                etag,
                "public, s-maxage=300",
                result_count=int(report.get("itemCount", 0)),
            )
        return JSONResponse(
            content=json.loads(body),
            headers={
                "ETag": etag,
                "Cache-Control": "public, s-maxage=300",
                "X-Result-Count": str(report.get("itemCount", 0)),
            },
        )

    @router.get(
        "/dailies/{date}",
        response_model=DailyReportResponse,
        responses={
            304: {"description": "Not modified"},
            404: {"model": ProblemDetail, "description": "Daily not found"},
        },
    )
    def daily_by_date(date: str, request: Request) -> JSONResponse:
        """Get daily report for a specific date (YYYY-MM-DD)."""
        service = service_provider()
        report = generate_daily_for_date(service, date)
        if report is None:
            raise not_found("无效的日期格式，请使用 YYYY-MM-DD。")
        body = json.dumps(report, ensure_ascii=False, default=str)
        etag = _etag_for(body)
        if _check_etag(request, etag):
            return _not_modified(
                etag,
                "public, s-maxage=3600",
                result_count=int(report.get("itemCount", 0)),
            )
        return JSONResponse(
            content=json.loads(body),
            headers={
                "ETag": etag,
                "Cache-Control": "public, s-maxage=3600",
                "X-Result-Count": str(report.get("itemCount", 0)),
            },
        )

    @router.post("/dailies/{date_str}/rebuild")
    def rebuild_daily(date_str: str) -> dict[str, object]:
        """Explicitly rebuild a date; unchanged content stays idempotent."""
        try:
            report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise not_found("无效的日期格式，请使用 YYYY-MM-DD。") from None
        return materialize_daily_report(
            service_provider(),
            report_date,
            rebuild=True,
        )

    @router.get("/dailies", response_model=DailyIndexResponse)
    def dailies_index(limit: int = Query(7, ge=1, le=30)) -> JSONResponse:
        """List latest persisted report revision for each available date."""
        service = service_provider()
        reports = list_daily_reports(service, limit=limit)
        if not reports:
            reports = ensure_recent_daily_reports(service, limit=limit)
        items = [
            {
                "date": report.date.isoformat(),
                "revision": report.revision,
                "status": report.status.value,
                "generatedAt": report.generated_at.isoformat(),
                "itemCount": report.item_count,
                "sourceCount": report.source_count,
                "contentHash": report.content_hash,
            }
            for report in reports[:limit]
        ]
        return JSONResponse(
            content={"schemaVersion": 1, "count": len(items), "items": items},
            headers={"X-Result-Count": str(len(items))},
        )

    # ── Durable selected-set synchronization ──────────────────────────

    @router.get(
        "/selected/snapshot",
        response_model=SelectedSnapshotResponse,
        responses={
            304: {"description": "Not modified"},
            409: {
                "model": ProblemDetail,
                "description": "Snapshot page expired; restart the snapshot",
            },
            422: {"model": ProblemDetail, "description": "Invalid query"},
        },
    )
    def selected_snapshot(
        request: Request,
        fields: str = Query("default", pattern="^(default|minimal)$"),
        limit: int = Query(500, ge=1, le=1000),
        page: str | None = Query(None, max_length=512),
    ) -> JSONResponse:
        """Return one page from a frozen complete selected-set snapshot."""
        service = service_provider()
        repository = service.selected_sync_repository
        if repository is None:
            raise temporarily_unavailable(
                "精选同步存储暂不可用。"
            )
        try:
            with service.lock:
                if page is None:
                    repository.reconcile(_selected_sync_items(service))
                result = repository.snapshot(
                    fields=fields,  # type: ignore[arg-type]
                    limit=limit,
                    page=page,
                )
        except SnapshotRequiredError as error:
            raise snapshot_required(
                "快照翻页游标已失效，请丢弃未完成镜像并重新下载。"
            ) from error
        payload = {
            "schemaVersion": 1,
            "fields": result.fields,
            "items": result.items,
            "cursor": result.cursor,
            "hasMore": result.has_more,
            "nextPage": result.next_page,
        }
        body = json.dumps(payload, ensure_ascii=False, default=str)
        etag = _etag_for(body)
        if _check_etag(request, etag):
            return _not_modified(
                etag,
                "public, s-maxage=300",
                result_count=len(result.items),
            )
        return JSONResponse(
            content=json.loads(body),
            headers={
                "ETag": etag,
                "Cache-Control": "public, s-maxage=300",
                "X-Result-Count": str(len(result.items)),
            },
        )

    @router.get(
        "/selected/changes",
        response_model=SelectedChangesResponse,
        responses={
            304: {"description": "Not modified"},
            409: {
                "model": ProblemDetail,
                "description": "Cursor expired; rebuild from snapshot",
            },
            422: {"model": ProblemDetail, "description": "Invalid query"},
        },
    )
    def selected_changes(
        request: Request,
        cursor: str = Query(..., min_length=1, max_length=512),
        limit: int = Query(100, ge=1, le=1000),
    ) -> JSONResponse:
        """Return ordered idempotent upsert/remove changes after a watermark."""
        service = service_provider()
        repository = service.selected_sync_repository
        if repository is None:
            raise temporarily_unavailable(
                "精选同步存储暂不可用。"
            )
        try:
            with service.lock:
                repository.reconcile(_selected_sync_items(service))
                result = repository.changes(cursor=cursor, limit=limit)
        except SnapshotRequiredError as error:
            raise snapshot_required() from error
        changes = [
            (
                {"op": "upsert", "id": change.id, "item": change.item}
                if change.op == "upsert"
                else {"op": "remove", "id": change.id}
            )
            for change in result.changes
        ]
        payload = {
            "schemaVersion": 1,
            "fields": result.fields,
            "changes": changes,
            "cursor": result.cursor,
            "hasMore": result.has_more,
        }
        body = json.dumps(payload, ensure_ascii=False, default=str)
        etag = _etag_for(body)
        if _check_etag(request, etag):
            return _not_modified(
                etag,
                "public, s-maxage=60",
                result_count=len(changes),
            )
        return JSONResponse(
            content=json.loads(body),
            headers={
                "ETag": etag,
                "Cache-Control": "public, s-maxage=60",
                "X-Result-Count": str(len(changes)),
            },
        )

    # ── Health / Info ─────────────────────────────────────────────────

    @router.get(
        "/fingerprint",
        response_model=FingerprintResponse,
        responses={304: {"description": "Not modified"}},
    )
    def fingerprint(request: Request) -> JSONResponse:
        """Lightweight data fingerprint for polling."""
        service = service_provider()
        rows = _get_item_rows(
            service,
            since=datetime.now(UTC) - timedelta(days=7),
        )
        latest_scored_at = max(
            (row.decision.scored_at.isoformat() for row in rows),
            default="none",
        )
        fingerprint_str = f"items={len(rows)};latest={latest_scored_at}"
        etag = _etag_for(fingerprint_str)
        if _check_etag(request, etag):
            return _not_modified(
                etag,
                "public, s-maxage=60",
                result_count=len(rows),
            )
        return JSONResponse(
            content={
                "schemaVersion": 1,
                "fingerprint": etag,
                "itemCount": len(rows),
                "latestScoredAt": None if latest_scored_at == "none" else latest_scored_at,
            },
            headers={
                "ETag": etag,
                "Cache-Control": "public, s-maxage=60",
                "X-Result-Count": str(len(rows)),
            },
        )

    return router


# ── Helpers ───────────────────────────────────────────────────────────

def _source_health_label(source: Source) -> str:
    if not source.enabled:
        return "disabled"
    if source.last_checked_at is None:
        return "unknown"
    if (
        source.circuit_state is CircuitState.OPEN
        or source.consecutive_failures >= 3
    ):
        return "failing"
    if (
        source.consecutive_failures > 0
        or (
            source.success_rate_7d is not None
            and source.success_rate_7d < 0.9
        )
    ):
        return "degraded"
    return "healthy"


def _source_payload(source: Source) -> dict[str, object]:
    return {
        "id": source.id,
        "name": source.name,
        "homepage": source.homepage,
        "feedUrl": source.feed_url,
        "kind": source.kind.value,
        "tier": source.tier.value,
        "publisherGroup": source.publisher_group,
        "categories": [category.value for category in source.categories],
        "evidenceEligible": source.evidence_eligible,
        "signalOnly": source.signal_only,
        "enabled": source.enabled,
        "parserVersion": source.parser_version,
        "scheduleMinutes": source.schedule_minutes,
        "health": _source_health_label(source),
        "circuitState": source.circuit_state.value,
        "consecutiveFailures": source.consecutive_failures,
        "successRate7d": source.success_rate_7d,
        "lastSuccessAt": (
            source.last_success_at.isoformat()
            if source.last_success_at is not None
            else None
        ),
        "lastCheckedAt": (
            source.last_checked_at.isoformat()
            if source.last_checked_at is not None
            else None
        ),
        "nextRunAt": (
            source.next_run_at.isoformat()
            if source.next_run_at is not None
            else None
        ),
        "lastErrorCode": source.last_error_code,
        "inFlight": (
            source.lease_expires_at is not None
            and source.lease_expires_at > datetime.now(UTC)
        ),
        "leaseExpiresAt": (
            source.lease_expires_at.isoformat()
            if source.lease_expires_at is not None
            else None
        ),
    }


def _source_run_payload(run: SourceRun) -> dict[str, object]:
    return {
        "id": run.id,
        "sourceId": run.source_id,
        "taskId": run.task_id,
        "windowDays": run.window_days,
        "startedAt": run.started_at.isoformat(),
        "finishedAt": run.finished_at.isoformat(),
        "status": run.status.value,
        "stage": run.stage,
        "httpStatus": run.http_status,
        "fetchedCount": run.fetched_count,
        "storedCount": run.stored_count,
        "skippedCount": run.skipped_count,
        "errorCode": run.error_code,
        "errorSummary": run.error_summary,
        "reliabilityExcludedReason": run.reliability_excluded_reason,
        "retryAfterSeconds": run.retry_after_seconds,
        "durationMs": run.duration_ms,
    }


def _creator_project_or_404(
    service: ContentApiService,
    project_id: str,
) -> CreatorProject:
    project = service.creator_projects.get(project_id)
    if project is None and service.creator_project_repository is not None:
        project = service.creator_project_repository.get(project_id)
        if project is not None:
            service.creator_projects[project.id] = project
    if project is None:
        raise not_found()
    return project


def _creator_project_payload(
    service: ContentApiService,
    project: CreatorProject,
) -> dict[str, object]:
    topic = service.topics.get(project.topic_id)
    draft = (
        service.drafts.get(project.current_draft_id)
        if project.current_draft_id is not None
        else None
    )
    blocking_count = (
        sum(issue.blocking for issue in draft.quality_issues)
        if draft is not None
        else None
    )
    return {
        "schemaVersion": 1,
        "id": project.id,
        "topicId": project.topic_id,
        "taskId": project.task_id,
        "origin": project.origin,
        "profileVersion": project.profile_version,
        "topicTitle": topic.candidate.title if topic is not None else "",
        "category": (
            public_category_slug(topic.candidate.category)
            if topic is not None
            else None
        ),
        "stage": project.stage,
        "version": project.version,
        "brief": (
            _content_brief_payload(project.brief)
            if project.brief is not None
            else None
        ),
        "opportunity": (
            _opportunity_payload(project.opportunity)
            if project.opportunity is not None
            else None
        ),
        "authorInputs": [
            {
                "id": item.id,
                "kind": item.kind,
                "content": item.content,
                "confirmed": item.confirmed,
                "sourceUrls": item.source_urls,
                "updatedAt": item.updated_at.isoformat(),
            }
            for item in project.author_inputs
        ],
        "inspirationSources": [
            _inspiration_source_payload(item)
            for item in project.inspiration_sources
        ],
        "similarityReports": [
            {
                "id": item.id,
                "draftId": item.draft_id,
                "draftVersion": item.draft_version,
                "sourceId": item.source_id,
                "sourceUrl": item.source_url,
                "threshold": item.threshold,
                "score": item.score,
                "ngramOverlap": item.ngram_overlap,
                "longestMatchRatio": item.longest_match_ratio,
                "matchedFragments": item.matched_fragments,
                "passed": item.passed,
                "algorithm": item.algorithm,
                "checkedAt": item.checked_at.isoformat(),
            }
            for item in project.similarity_reports
        ],
        "currentDraftId": project.current_draft_id,
        "humanApprovedDraftVersion": project.human_approved_draft_version,
        "draftVersions": [
            {
                "id": item.id,
                "version": item.version,
                "parentVersion": item.parent_version,
                "generationMethod": item.generation_method,
                "model": item.model,
                "promptHash": item.prompt_hash,
                "promptVersionId": item.prompt_version_id,
                "llmCallIds": item.llm_call_ids,
                "profileVersion": item.profile_version,
                "citedClaimIds": item.cited_claim_ids,
                "authorInputIds": item.author_input_ids,
                "changeSummary": item.change_summary,
                "createdAt": item.created_at.isoformat(),
            }
            for item in project.draft_versions
        ],
        "qualityReports": [
            {
                "id": item.id,
                "draftVersion": item.draft_version,
                "profileVersion": item.profile_version,
                "passed": item.passed,
                "humanConfirmed": item.human_confirmed,
                "issueCount": len(item.issues),
                "createdAt": item.created_at.isoformat(),
            }
            for item in project.quality_reports
        ],
        "exportRecords": [
            {
                "id": item.id,
                "draftVersion": item.draft_version,
                "format": item.format,
                "qualityReportId": item.quality_report_id,
                "contentSha256": item.content_sha256,
                "exportedAt": item.exported_at.isoformat(),
            }
            for item in project.export_records
        ],
        "quality": (
            {
                "blockingIssueCount": blocking_count,
                "canExport": blocking_count == 0,
            }
            if blocking_count is not None
            else None
        ),
        "createdAt": project.created_at.isoformat(),
        "updatedAt": project.updated_at.isoformat(),
    }


def _canonical_article_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ("", value.strip())
    return ((parsed.hostname or "").casefold(), parsed.path.rstrip("/") or "/")


def _primary_inspiration_from_request(
    request: InspirationPreviewRequest,
    stored_articles: list[ArticleRecord],
) -> InspirationSource:
    known_article = _known_article_for_url(request.primary_url, stored_articles)
    if request.primary_text:
        return source_from_manual_text(
            request.primary_url,
            request.primary_text,
            title=request.primary_title,
            role="primary",
            source_kind=request.source_kind,
            known_article=known_article,
        )
    return fetch_inspiration_source(
        request.primary_url,
        role="primary",
        source_kind=request.source_kind,
        known_article=known_article,
    )


def _inspiration_source_payload(item: InspirationSource) -> dict[str, object]:
    return {
        "id": item.id,
        "role": item.role,
        "sourceKind": item.source_kind,
        "url": item.url,
        "title": item.title,
        "publisher": item.publisher,
        "publisherIdentity": item.publisher_identity,
        "contentSha256": item.content_sha256,
        "contentCharacters": len(item.content_text),
        "sourceType": item.source_type.value,
        "sourceTypeVerified": item.source_type_verified,
        "publishedAt": item.published_at.isoformat() if item.published_at else None,
        "importedAt": item.imported_at.isoformat(),
        "structure": {
            "headingCount": item.structure.heading_count,
            "paragraphCount": item.structure.paragraph_count,
            "averageParagraphCharacters": item.structure.average_paragraph_characters,
            "shortParagraphRatio": item.structure.short_paragraph_ratio,
            "listItemCount": item.structure.list_item_count,
            "questionCount": item.structure.question_count,
            "openingApproach": item.structure.opening_approach,
            "reusableBlueprint": item.structure.reusable_blueprint,
        },
    }


def _known_article_for_url(value: str, articles: list[ArticleRecord]) -> ArticleRecord | None:
    target = _canonical_article_url(value)
    return next(
        (item for item in articles if _canonical_article_url(item.source_url) == target),
        None,
    )


def _deduplicate_inspiration_sources(sources, *, primary_url: str):
    result = []
    seen_urls = {_canonical_article_url(primary_url)}
    seen_publishers = set()
    for source in sources:
        key = _canonical_article_url(source.url)
        if key in seen_urls or source.publisher_identity in seen_publishers:
            continue
        seen_urls.add(key)
        seen_publishers.add(source.publisher_identity)
        result.append(source)
    return result


def _account_profile_payload(profile: AccountProfile) -> dict[str, object]:
    data = profile.model_dump(mode="json", by_alias=True)
    return {
        "schemaVersion": 1,
        "id": data["id"],
        "version": data["version"],
        "accountName": data["account_name"],
        "positioning": data["positioning"],
        "targetAudience": data["target_audience"],
        "contentPillars": data["content_pillars"],
        "excludedTopics": data["excluded_topics"],
        "toneKeywords": data["tone_keywords"],
        "targetCharacters": data["target_characters"],
        "paragraphLimit": data["paragraph_limit"],
        "titlePreferences": data["title_preferences"],
        "forbiddenPhrases": data["forbidden_phrases"],
        "acceptedSourceTiers": data["accepted_source_tiers"],
        "citationStyle": data["citation_style"],
        "riskTolerance": data["risk_tolerance"],
        "verifiedExperienceInventory": data["verified_experience_inventory"],
        "fixedViewpoints": data["fixed_viewpoints"],
        "effectiveAt": profile.effective_at.isoformat(),
    }


def _prompt_payload(prompt) -> dict[str, object]:
    return {
        "id": prompt.id,
        "stage": prompt.stage,
        "version": prompt.version,
        "templateHash": prompt.template_hash,
        "outputSchema": prompt.output_schema,
        "createdAt": prompt.created_at.isoformat(),
    }


def _llm_call_payload(call) -> dict[str, object]:
    return {
        "id": call.id,
        "operationId": call.operation_id,
        "projectId": call.project_id,
        "draftId": call.draft_id,
        "stage": call.stage,
        "status": call.status,
        "provider": call.provider,
        "model": call.model,
        "promptVersionId": call.prompt_version_id,
        "promptHash": call.prompt_hash,
        "evidenceClaimIds": call.evidence_claim_ids,
        "inputTokens": call.input_tokens,
        "outputTokens": call.output_tokens,
        "estimatedCost": call.estimated_cost,
        "durationMs": round(call.duration_ms, 2),
        "retryCount": call.retry_count,
        "errorCode": call.error_code,
        "fallbackReason": call.fallback_reason,
        "createdAt": call.created_at.isoformat(),
    }


def _creation_replay_payload(report) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": report.id,
        "requestedMode": report.requested_mode,
        "actualMode": report.actual_mode,
        "profileVersion": report.profile_version,
        "promptVersionIds": report.prompt_version_ids,
        "sampleCount": report.sample_count,
        "passedCount": report.passed_count,
        "passRate": report.pass_rate,
        "sourceCoverageRate": report.source_coverage_rate,
        "unsupportedNumberCount": report.unsupported_number_count,
        "fabricatedExperienceCount": report.fabricated_experience_count,
        "profileMismatchCount": report.profile_mismatch_count,
        "medianDurationMs": round(report.median_duration_ms, 2),
        "items": [
            {
                "topicId": item.topic_id,
                "title": item.title,
                "generationMethod": item.generation_method,
                "passed": item.passed,
                "blockingIssueCodes": item.blocking_issue_codes,
                "citedSourceCount": item.cited_source_count,
                "evidenceClaimCount": item.evidence_claim_count,
                "visibleCharacters": item.visible_characters,
                "durationMs": round(item.duration_ms, 2),
            }
            for item in report.items
        ],
        "createdAt": report.created_at.isoformat(),
    }


def _opportunity_payload(opportunity) -> dict[str, object]:
    scores = opportunity.scores
    return {
        "schemaVersion": 1,
        "id": opportunity.id,
        "sourceKind": opportunity.source_kind,
        "sourceId": opportunity.source_id,
        "title": opportunity.title,
        "category": opportunity.category,
        "creationScore": opportunity.creation_score,
        "scores": {
            "accountFit": scores.account_fit,
            "audienceValue": scores.audience_value,
            "differentiation": scores.differentiation,
            "eventImpact": scores.event_impact,
            "evidenceCompleteness": scores.evidence_completeness,
            "timeliness": scores.timeliness,
            "productionEfficiency": scores.production_efficiency,
        },
        "recommendedAngle": opportunity.recommended_angle,
        "readerBenefit": opportunity.reader_benefit,
        "timeWindowHours": opportunity.time_window_hours,
        "risks": opportunity.risks,
        "blockers": opportunity.blockers,
        "status": opportunity.status,
        "ignoreReason": opportunity.ignore_reason,
        "createdAt": opportunity.created_at.isoformat(),
        "decidedAt": opportunity.decided_at.isoformat() if opportunity.decided_at else None,
    }


def _refresh_opportunities(service: ContentApiService):
    ranked_topics = sorted(
        service.topics.values(),
        key=lambda item: (item.candidate.heat_score.value, item.candidate.id),
        reverse=True,
    )
    active_source_ids: set[str] = set()
    for topic in ranked_topics[:10]:
        active_source_ids.add(topic.candidate.id)
        existing = next(
            (item for item in service.opportunities.values() if item.source_id == topic.candidate.id),
            None,
        )
        try:
            from wechat_agent.api.routes import _evidence_for

            pack = _evidence_for(service, topic.candidate.id)
        except Exception:
            continue
        refreshed = build_opportunity(topic.candidate, pack, service.account_profile)
        if existing is not None:
            status = existing.status
            if status in {"new", "blocked"}:
                status = "blocked" if refreshed.blockers else "new"
            refreshed = refreshed.model_copy(
                update={
                    "status": status,
                    "ignore_reason": existing.ignore_reason,
                    "created_at": existing.created_at,
                    "decided_at": existing.decided_at,
                }
            )
        service.persist_opportunity(refreshed)
    return sorted(
        (
            item
            for item in service.opportunities.values()
            if item.source_id in active_source_ids
        ),
        key=lambda item: (item.creation_score, item.created_at),
        reverse=True,
    )[:10]


def _content_brief_payload(brief) -> dict[str, object]:
    return {
        "version": brief.version,
        "coreThesis": brief.core_thesis,
        "audienceBenefit": brief.audience_benefit,
        "angle": brief.angle,
        "differentiation": brief.differentiation,
        "sections": [
            {
                "id": section.id,
                "heading": section.heading,
                "intent": section.intent,
                "claimIds": section.claim_ids,
            }
            for section in brief.sections
        ],
        "requiredSourceUrls": brief.required_source_urls,
        "risks": brief.risks,
        "authorInputs": brief.author_inputs,
        "readyForDraft": brief.ready_for_draft,
        "blockingReasons": brief.blocking_reasons,
        "updatedAt": brief.updated_at.isoformat(),
    }


def _v1_task_payload(task: TaskRecord) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": task.id,
        "status": task.status.value,
        "statusHistory": [s.value for s in task.status_history],
        "mode": task.mode,
        "collectedArticleCount": task.collected_article_count,
        "windowDays": task.window_days,
        "expandedCategories": task.expanded_categories,
        "collectionWarnings": task.collection_warnings,
        "sourceCounts": task.source_counts,
    }


@dataclass(frozen=True)
class DistributionRow:
    article: ArticleRecord
    decision: CurationDecision


def _public_item_payload(
    row: DistributionRow,
    *,
    anchor: datetime,
) -> dict[str, object]:
    article = row.article
    decision = row.decision
    return {
        "id": article.id,
        "title": article.title,
        "originalTitle": None,
        "summary": article.summary,
        "source": {
            "name": article.source_name,
            "tier": _public_tier(article.tier),
            "type": article.source_type.value,
        },
        "links": {
            "aihot": article.source_url,
            "original": article.source_url,
        },
        "publishedAt": article.published_at.isoformat(),
        "discoveredAt": article.collected_at.isoformat(),
        "timelineAt": _item_sort_at(
            article,
            by="timeline",
            anchor=anchor,
        ).isoformat(),
        "category": public_category_slug(article.category),
        "score": decision.distribution_score,
        "selected": decision.selected,
        "reason": decision.reason,
        "assessment": {
            "components": decision.components,
            "threshold": decision.threshold,
            "confidence": decision.score_confidence.value,
            "strategyVersion": decision.strategy_version,
            "scoredAt": decision.scored_at.isoformat(),
        },
        "attribution": {"name": "AI 信息小屋"},
    }


def _selected_sync_items(service: ContentApiService) -> list[SelectedSyncItem]:
    """Build both representations from the complete durable selected set."""
    anchor = datetime.now(UTC)
    rows = [
        DistributionRow(article=article, decision=decision)
        for article, decision in service.list_all_distribution_items(
            limit=100000
        )
        if decision.eligible and decision.selected
    ]
    unique: dict[str, DistributionRow] = {}
    for row in rows:
        unique.setdefault(row.article.id, row)
    result: list[SelectedSyncItem] = []
    for item_id in sorted(unique):
        payload = _public_item_payload(unique[item_id], anchor=anchor)
        minimal = {
            key: value
            for key, value in payload.items()
            if key != "summary"
        }
        minimal["links"] = {
            "aihot": payload["links"]["aihot"]  # type: ignore[index]
        }
        result.append(
            SelectedSyncItem(
                id=item_id,
                default=payload,
                minimal=minimal,
            )
        )
    return result


def _get_item_rows(
    service: ContentApiService,
    *,
    since: datetime | None = None,
    limit: int = 2000,
) -> list[DistributionRow]:
    """Read Article + CurationDecision; task state is deliberately irrelevant."""
    cutoff = since or (datetime.now(UTC) - timedelta(days=30))
    rows = [
        DistributionRow(article=article, decision=decision)
        for article, decision in service.list_distribution_items(cutoff, limit=limit)
        if decision.eligible
    ]
    rows.sort(
        key=lambda row: (row.article.published_at, row.article.collected_at),
        reverse=True,
    )
    seen: set[str] = set()
    unique: list[DistributionRow] = []
    for row in rows:
        if row.article.id in seen:
            continue
        seen.add(row.article.id)
        unique.append(row)
    return unique


def _public_tier(tier: str | None) -> str | None:
    return {"t1": "T1", "t1_5": "T1.5", "t2": "T2"}.get(tier or "")


def _build_hot_topics(
    rows: list[DistributionRow],
    *,
    publisher_groups: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Conservative title aggregation gated by independent publishers."""
    publisher_groups = publisher_groups or {}
    groups: dict[str, list[DistributionRow]] = {}
    for row in rows:
        if not row.decision.selected:
            continue
        key = _normalized_title_key(row.article.title)
        if not key:
            continue
        groups.setdefault(key, []).append(row)

    topics: list[dict[str, object]] = []
    for grouped in groups.values():
        ranked = sorted(
            grouped,
            key=lambda row: (
                row.decision.selected,
                row.decision.distribution_score,
                row.article.published_at,
            ),
            reverse=True,
        )
        lead = ranked[0]
        source_names = list(dict.fromkeys(row.article.source_name for row in ranked))
        source_ids = {
            row.article.source_id
            or row.article.source_identity
            or f"name:{row.article.source_name.casefold()}"
            for row in ranked
        }
        independent_groups = {
            publisher_groups.get(
                row.article.source_id or "",
                row.article.source_identity
                or row.article.source_id
                or f"name:{row.article.source_name.casefold()}",
            )
            for row in ranked
        }
        if len(independent_groups) < 2:
            continue
        topics.append({
            "title": lead.article.title,
            "category": public_category_slug(lead.article.category),
            "summary": lead.article.summary,
            "score": lead.decision.distribution_score,
            "sourceCount": len(source_ids),
            "signalCount": len(independent_groups),
            "publisherGroupCount": len(independent_groups),
            "reportCount": len(grouped),
            "qualification": "multi_publisher",
            "sourceNames": source_names,
            "latestAt": max(row.article.published_at for row in grouped).isoformat(),
        })
    topics.sort(
        key=lambda topic: (
            topic["signalCount"],
            topic["sourceCount"],
            topic["reportCount"],
            topic["score"],
            topic["latestAt"],
        ),
        reverse=True,
    )
    return topics


def _normalized_title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return "".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


@dataclass(frozen=True)
class ItemCursor:
    anchor: datetime
    sort_at: datetime
    item_id: str
    signature: str


def _item_sort_at(
    article: ArticleRecord,
    *,
    by: str,
    anchor: datetime,
) -> datetime:
    if by == "published":
        return article.published_at
    return (
        article.collected_at
        if article.published_at >= anchor - timedelta(hours=72)
        else article.published_at
    )


def _cursor_signature(
    *,
    mode: str,
    window: str,
    category: str | None,
    q: str | None,
    by: str,
    strategy_versions: list[str],
) -> str:
    material = json.dumps(
        {
            "mode": mode,
            "window": window,
            "category": category,
            "q": q.casefold().strip() if q else None,
            "by": by,
            "strategyVersions": strategy_versions,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(material.encode("utf-8")).hexdigest()[:24]


def _encode_cursor(
    *,
    anchor: datetime,
    sort_at: datetime,
    item_id: str,
    signature: str,
) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "anchor": anchor.isoformat(),
            "at": sort_at.isoformat(),
            "id": item_id,
            "sig": signature,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> ItemCursor:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            padded.encode(),
            altchars=b"-_",
            validate=True,
        ).decode()
        payload = json.loads(raw)
        if payload.get("v") != 1:
            raise ValueError("unsupported cursor version")
        anchor = datetime.fromisoformat(payload["anchor"])
        sort_at = datetime.fromisoformat(payload["at"])
        item_id = payload["id"]
        signature = payload["sig"]
        if (
            anchor.tzinfo is None
            or sort_at.tzinfo is None
            or not isinstance(item_id, str)
            or not item_id
            or not isinstance(signature, str)
            or len(signature) != 24
        ):
            raise ValueError("invalid cursor payload")
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ):
        raise invalid_cursor() from None
    return ItemCursor(
        anchor=anchor.astimezone(UTC),
        sort_at=sort_at.astimezone(UTC),
        item_id=item_id,
        signature=signature,
    )
