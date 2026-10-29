"""FastAPI application factory and default ASGI application."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
from time import perf_counter
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from wechat_agent.api.errors import AppError, app_error_handler, problem_response
from wechat_agent.api.agent import create_agent_router
from wechat_agent.api.evaluation import create_evaluation_router
from wechat_agent.api.story_shadow import (
    StoryShadowResources,
    create_story_shadow_router,
)
from wechat_agent.api.routes import ApiError, ContentApiService, create_router
from wechat_agent.api.schemas import ErrorBody, ErrorResponse
from wechat_agent.api.feeds import create_feed_router
from wechat_agent.api.observability import (
    HttpObservation,
    HttpObservationRegistry,
)
from wechat_agent.api.public_openapi import build_public_v1_openapi
from wechat_agent.api.v1_routes import create_v1_router
from wechat_agent.api.video import create_video_router
from wechat_agent.services.scheduler import create_scheduler
from wechat_agent.services.daily import (
    REPORT_TIMEZONE,
    ensure_recent_daily_reports,
    materialize_daily_report,
)
from wechat_agent.collectors.base import LiveCollector
from wechat_agent.collectors.defaults import build_default_live_collector
from wechat_agent.collectors.registry import (
    apply_source_registry_state,
    sources_from_live_collector,
)
from wechat_agent.config import load_llm_config
from wechat_agent.services.pipeline import ContentPipeline
from wechat_agent.evaluation.workbench import EvaluationWorkbenchService
from wechat_agent.storage.mongo import (
    MongoAccountProfileRepository,
    MongoArticleRepository,
    MongoCurationDecisionRepository,
    MongoCreatorProjectRepository,
    MongoContentOpportunityRepository,
    MongoDraftRepository,
    MongoEvidenceRepository,
    MongoGenerationAuditRepository,
    MongoSelectedSyncRepository,
    MongoTaskRepository,
)
from wechat_agent.storage.sqlite import (
    SqliteAccountProfileRepository,
    SqliteArticleRepository,
    SqliteCurationDecisionRepository,
    SqliteCreatorProjectRepository,
    SqliteContentOpportunityRepository,
    SqliteDailyReportRepository,
    SqliteDraftRepository,
    SqliteEvidenceRepository,
    SqliteEvaluationRepository,
    SqliteGenerationAuditRepository,
    SqliteSelectedSyncRepository,
    SqliteSourceRepository,
    SqliteSourceRunRepository,
    SqliteTaskRepository,
)
from wechat_agent.storage.memory import InMemoryDailyReportRepository
from wechat_agent.storage.story_sqlite import SqliteStoryRepository
from wechat_agent.story.service import StoryShadowService
from wechat_agent.writing.provider import (
    built_in_prompt_versions,
    LlmDraftProvider,
    RuleBasedDraftProvider,
)
from wechat_agent.video import VideoAnalysisService, VideoConfig


def create_app(
    *,
    database_url: str | None = None,
    fixture_path: Path | None = None,
    live_collector: LiveCollector | None = None,
    enable_default_live_collector: bool = True,
    load_llm_from_environment: bool = True,
) -> FastAPI:
    """Create an app whose repository and in-memory stores are instance-local."""
    resolved_database_url = (
        database_url
        or os.getenv("WECHAT_AGENT_DATABASE_URL")
        or os.getenv("WECHAT_AGENT_MONGO_URI")
        or "sqlite:///:memory:"
    )
    resolved_fixture_path = fixture_path or (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "acceptance_articles.json"
    )
    scheduler = create_scheduler()
    video_service = VideoAnalysisService(VideoConfig.from_env())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Build repositories and apply schema migrations before accepting the
        # first request.  Keeping this work out of the request path both fails
        # fast on startup errors and preserves the public cold-request SLA.
        get_service()
        if enable_default_live_collector:
            def _bg_collect():
                svc = get_service()
                task = svc.create_scheduled_live_task()
                if task is None:
                    return None
                return {
                    "id": task.id,
                    "status": task.status.value,
                    "collected_article_count": task.collected_article_count,
                    "topic_ids": task.topic_ids,
                }

            scheduler.start(_bg_collect)
        daily_hour = min(
            23,
            max(0, int(os.getenv("WECHAT_AGENT_DAILY_HOUR", "8"))),
        )

        def _bg_daily():
            report_date = (
                datetime.now(REPORT_TIMEZONE).date() - timedelta(days=1)
            )
            return materialize_daily_report(
                get_service(),
                report_date,
                rebuild=True,
            )

        scheduler.start_daily(
            _bg_daily,
            hour=daily_hour,
            timezone="Asia/Shanghai",
        )
        try:
            yield
        finally:
            scheduler.stop()
            video_service.close()

    app = FastAPI(
        title="AI 信息小屋 · AI 内容智能体",
        version="1.0.0",
        description=(
            "Unified AI information distribution and content production platform. "
            "Multi-tier source grading, two-pass AI screening, "
            "five-dimension scoring, evidence-bound drafting, "
            "and quality gate enforcement. "
            "Set WECHAT_AGENT_LLM_API_KEY (or the legacy DEEPSEEK_API_KEY) "
            "to enable AI writing; "
            "WECHAT_AGENT_DATABASE_URL configures SQLite or MongoDB storage. "
            "uvicorn --workers 1 is required for a single-process deployment."
        ),
        lifespan=lifespan,
    )
    http_observations = HttpObservationRegistry()
    app.state.http_observations = http_observations

    @app.middleware("http")
    async def observe_http(request: Request, call_next):
        request_id = _request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        started_at = perf_counter()
        observed_at = datetime.now().astimezone()
        try:
            response = await call_next(request)
        except Exception:
            http_observations.record(
                HttpObservation(
                    observed_at=observed_at,
                    request_id=request_id,
                    method=request.method,
                    route=request.url.path,
                    status=500,
                    duration_ms=round((perf_counter() - started_at) * 1000, 3),
                    cache_status="BYPASS",
                    result_count=None,
                )
            )
            raise
        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        cache_status = (
            "HIT"
            if response.status_code == 304
            else ("MISS" if response.headers.get("ETag") else "BYPASS")
        )
        result_count_header = response.headers.get("X-Result-Count")
        result_count = (
            int(result_count_header)
            if result_count_header is not None
            else None
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Cache-Status"] = cache_status
        response.headers["Server-Timing"] = f'app;dur={duration_ms:.3f}'
        http_observations.record(
            HttpObservation(
                observed_at=observed_at,
                request_id=request_id,
                method=request.method,
                route=route_path,
                status=response.status_code,
                duration_ms=duration_ms,
                cache_status=cache_status,
                result_count=result_count,
            )
        )
        return response
    service: ContentApiService | None = None
    evaluation_service: EvaluationWorkbenchService | None = None
    story_repository: SqliteStoryRepository | None = None
    story_shadow_service: StoryShadowService | None = None
    service_init_lock = Lock()

    def get_service() -> ContentApiService:
        nonlocal service, evaluation_service, story_repository, story_shadow_service
        if service is not None:
            return service
        with service_init_lock:
            if service is not None:
                return service
            if resolved_database_url.startswith(("mongodb://", "mongodb+srv://")):
                article_repo = MongoArticleRepository(resolved_database_url)
                curation_repo = MongoCurationDecisionRepository(article_repo.db)
                selected_sync_repo = MongoSelectedSyncRepository(
                    article_repo.db,
                    max_changes=int(
                        os.getenv("WECHAT_AGENT_SELECTED_CHANGE_RETENTION", "10000")
                    ),
                    snapshot_ttl_seconds=int(
                        os.getenv("WECHAT_AGENT_SNAPSHOT_TTL_SECONDS", "900")
                    ),
                )
                task_repo = MongoTaskRepository(article_repo.db)
                evidence_repo = MongoEvidenceRepository(article_repo.db)
                draft_repo = MongoDraftRepository(article_repo.db)
                creator_project_repo = MongoCreatorProjectRepository(article_repo.db)
                account_profile_repo = MongoAccountProfileRepository(article_repo.db)
                content_opportunity_repo = MongoContentOpportunityRepository(article_repo.db)
                generation_audit_repo = MongoGenerationAuditRepository(article_repo.db)
                source_repo = None
                source_run_repo = None
                daily_report_repo = InMemoryDailyReportRepository()
                evaluation_repo = None
                story_repository = None
                story_shadow_service = None
            else:
                article_repo = SqliteArticleRepository(resolved_database_url)
                curation_repo = SqliteCurationDecisionRepository(article_repo.engine)
                selected_sync_repo = SqliteSelectedSyncRepository(
                    article_repo.engine,
                    max_changes=int(
                        os.getenv("WECHAT_AGENT_SELECTED_CHANGE_RETENTION", "10000")
                    ),
                    snapshot_ttl_seconds=int(
                        os.getenv("WECHAT_AGENT_SNAPSHOT_TTL_SECONDS", "900")
                    ),
                )
                task_repo = SqliteTaskRepository(article_repo.engine)
                evidence_repo = SqliteEvidenceRepository(article_repo.engine)
                draft_repo = SqliteDraftRepository(article_repo.engine)
                creator_project_repo = SqliteCreatorProjectRepository(article_repo.engine)
                account_profile_repo = SqliteAccountProfileRepository(article_repo.engine)
                content_opportunity_repo = SqliteContentOpportunityRepository(article_repo.engine)
                generation_audit_repo = SqliteGenerationAuditRepository(article_repo.engine)
                source_repo = SqliteSourceRepository(article_repo.engine)
                source_run_repo = SqliteSourceRunRepository(article_repo.engine)
                daily_report_repo = SqliteDailyReportRepository(article_repo.engine)
                evaluation_repo = SqliteEvaluationRepository(article_repo.engine)
                story_repository = SqliteStoryRepository(article_repo.engine)
                story_shadow_service = StoryShadowService(
                    article_repo,
                    curation_repo,
                    source_repo,
                    story_repository,
                )
            llm_config = (
                load_llm_config()
                if load_llm_from_environment
                else None
            )
            for prompt_version in built_in_prompt_versions():
                generation_audit_repo.save_prompt_version(prompt_version)
            draft_provider = (
                LlmDraftProvider(llm_config, generation_audit_repo)
                if llm_config is not None
                else RuleBasedDraftProvider(generation_audit_repo)
            )
            resolved_live_collector = (
                live_collector
                if live_collector is not None
                else (
                    build_default_live_collector()
                    if enable_default_live_collector
                    else None
                )
            )
            if source_repo is not None:
                source_repo.seed_many(
                    sources_from_live_collector(resolved_live_collector)
                )
                apply_source_registry_state(
                    resolved_live_collector,
                    source_repo,
                )
            initialized_service = ContentApiService(
                ContentPipeline(
                    article_repo,
                    curation_repository=curation_repo,
                    llm_config=llm_config,
                ),
                resolved_fixture_path,
                draft_provider=draft_provider,
                live_collector=resolved_live_collector,
                task_repo=task_repo,
                evidence_repo=evidence_repo,
                draft_repo=draft_repo,
                source_repo=source_repo,
                source_run_repo=source_run_repo,
                daily_report_repo=daily_report_repo,
                selected_sync_repo=selected_sync_repo,
                creator_project_repo=creator_project_repo,
                account_profile_repo=account_profile_repo,
                content_opportunity_repo=content_opportunity_repo,
                generation_audit_repo=generation_audit_repo,
                llm_config=llm_config,
                scheduler_running_provider=lambda: scheduler.running,
                story_shadow_service=story_shadow_service,
            )
            ensure_recent_daily_reports(initialized_service)
            if evaluation_repo is not None:
                evaluation_service = EvaluationWorkbenchService(
                    article_repo,
                    curation_repo,
                    evaluation_repo,
                    target_count=int(
                        os.getenv("WECHAT_AGENT_GOLD_TARGET", "200")
                    ),
                )
                evaluation_service.ensure_candidates()
            service = initialized_service
        return service

    def get_evaluation_service() -> EvaluationWorkbenchService | None:
        get_service()
        return evaluation_service

    def get_story_shadow_resources() -> StoryShadowResources | None:
        get_service()
        if story_repository is None or story_shadow_service is None:
            return None
        return StoryShadowResources(story_repository, story_shadow_service)

    app.include_router(create_router(get_service))
    app.include_router(create_agent_router(get_service))
    app.include_router(create_v1_router(get_service))
    app.include_router(create_feed_router(get_service))
    app.include_router(create_evaluation_router(get_evaluation_service))
    app.include_router(create_story_shadow_router(get_story_shadow_resources))
    app.include_router(
        create_video_router(
            lambda: video_service,
            lambda: get_service()._llm_config,
        )
    )

    # Register AppError handler (Problem JSON protocol)
    app.add_exception_handler(AppError, app_error_handler)

    web_source_dir = Path(__file__).resolve().parent / "web"
    web_dist_dir = web_source_dir / "dist"
    web_dir = web_dist_dir if (web_dist_dir / "index.html").is_file() else web_source_dir
    if (web_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> JSONResponse:
        import json
        path = web_dir / "manifest.webmanifest"
        content = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse(content, media_type="application/manifest+json")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        path = web_dir / "index.html"
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt() -> HTMLResponse:
        path = web_dir / "llms.txt"
        return HTMLResponse(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")

    @app.get("/.well-known/ai-plugin.json", include_in_schema=False)
    def ai_plugin() -> JSONResponse:
        import json as _json
        path = web_dir / "assets" / "ai-plugin.json"
        content = _json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse(content, media_type="application/json")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "1.0.0"}

    @app.get("/openapi-v1.json", include_in_schema=False)
    def public_openapi() -> JSONResponse:
        """Expose only the AIHOT-aligned anonymous read-only contract."""
        return JSONResponse(build_public_v1_openapi(app))

    @app.get("/api/ops/http-observability", include_in_schema=False)
    def http_observability(
        window_minutes: int = Query(60, ge=1, le=1440),
    ) -> dict[str, object]:
        """Return process-local diagnostics without expanding public v1."""
        return http_observations.snapshot(window_minutes)

    @app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def spa_fallback(full_path: str) -> HTMLResponse:
        """Allow direct refreshes on client-side application routes."""
        if (
            full_path.startswith(("api/", "assets/", "feed/", ".well-known/"))
            or full_path == "feed.xml"
            or "." in Path(full_path).name
        ):
            raise StarletteHTTPException(status_code=404)
        return HTMLResponse((web_dir / "index.html").read_text(encoding="utf-8"))

    @app.exception_handler(ApiError)
    async def handle_api_error(
        _request: Request, error: ApiError
    ) -> JSONResponse:
        return _error_response(
            error.status_code,
            error.code,
            error.message,
            error.retryable,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        if request.url.path.startswith("/api/v1/"):
            return problem_response(
                422,
                "INVALID_REQUEST",
                "请求参数不合法。",
                request_id=getattr(request.state, "request_id", None),
            )
        return _error_response(
            422,
            "INVALID_INPUT",
            "请求参数不合法。",
            False,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        code, message = {
            404: ("NOT_FOUND", "请求的资源不存在。"),
            405: ("METHOD_NOT_ALLOWED", "请求方法不受支持。"),
        }.get(
            error.status_code,
            ("HTTP_ERROR", "请求无法完成。"),
        )
        if request.url.path.startswith("/api/v1/"):
            return problem_response(
                error.status_code,
                code,
                message,
                request_id=getattr(request.state, "request_id", None),
            )
        return _error_response(
            error.status_code,
            code,
            message,
            False,
        )

    @app.exception_handler(Exception)
    async def handle_internal_error(
        _request: Request, _error: Exception
    ) -> JSONResponse:
        return _error_response(
            500,
            "INTERNAL_FAILURE",
            "服务器处理失败。",
            True,
        )

    return app


def _error_response(
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            retryable=retryable,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


app = create_app()


def _request_id(candidate: str | None) -> str:
    if candidate and 1 <= len(candidate) <= 80 and all(
        character.isalnum() or character in "-_.:"
        for character in candidate
    ):
        return candidate
    return f"req_{uuid4().hex[:12]}"
