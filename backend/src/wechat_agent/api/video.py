"""Private v1 endpoints for independently deployable video transcription."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import FileResponse, Response

from wechat_agent.api.errors import AppError
from wechat_agent.config import LlmConfig
from wechat_agent.video.insights import VideoInsightError, VideoInsightService
from wechat_agent.video.models import (
    VideoQuestionRequest,
    VideoQuestionResponse,
    VideoRefinementResponse,
    VideoTask,
    VideoTaskStatus,
    VideoUrlRequest,
)
from wechat_agent.video.service import VideoAnalysisService


VideoServiceProvider = Callable[[], VideoAnalysisService]
LlmConfigProvider = Callable[[], LlmConfig | None]


def create_video_router(
    service_provider: VideoServiceProvider,
    llm_config_provider: LlmConfigProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/video", tags=["video-analysis"])

    @router.get("/status")
    def video_runtime_status() -> dict[str, object]:
        return service_provider().runtime_status()

    @router.post(
        "/tasks/upload",
        status_code=status.HTTP_201_CREATED,
        response_model=VideoTask,
    )
    async def create_upload_task(
        file: UploadFile = File(...),
    ) -> VideoTask:
        return await service_provider().create_upload(file)

    @router.post(
        "/tasks/url",
        status_code=status.HTTP_201_CREATED,
        response_model=VideoTask,
    )
    def create_url_task(request: VideoUrlRequest) -> VideoTask:
        return service_provider().create_url(request.url)

    @router.get("/tasks/latest", response_model=VideoTask)
    def get_latest_video_task() -> VideoTask:
        return service_provider().latest_task()

    @router.get("/tasks/{task_id}", response_model=VideoTask)
    def get_video_task(task_id: str) -> VideoTask:
        return service_provider().get_task(task_id)

    @router.get("/tasks/{task_id}/media")
    def get_video_media(task_id: str) -> FileResponse:
        path, media_type = service_provider().source_media(task_id)
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.post(
        "/tasks/{task_id}/refine",
        response_model=VideoRefinementResponse,
    )
    def refine_video_transcript(task_id: str) -> VideoRefinementResponse:
        service = service_provider()
        task = service.get_task(task_id)
        if task.status is not VideoTaskStatus.COMPLETED or not task.transcript:
            raise AppError(409, "VIDEO_TRANSCRIPT_NOT_READY", "请先完成原字稿解析。")
        insight = _require_insight_service(llm_config_provider)
        try:
            refined = insight.refine(task.transcript)
            service.save_refinement(task_id, refined, insight.model)
            return VideoRefinementResponse(
                taskId=task_id,
                refinedTranscript=refined,
                model=insight.model,
            )
        except VideoInsightError as exc:
            raise _insight_error(exc) from None
        finally:
            insight.close()

    @router.post(
        "/tasks/{task_id}/questions",
        response_model=VideoQuestionResponse,
    )
    def ask_about_video(
        task_id: str,
        request: VideoQuestionRequest,
    ) -> VideoQuestionResponse:
        task = service_provider().get_task(task_id)
        if not task.refined_transcript:
            raise AppError(409, "VIDEO_REFINEMENT_REQUIRED", "请先生成有效原稿。")
        insight = _require_insight_service(llm_config_provider)
        try:
            answer = insight.answer(
                task.refined_transcript,
                request.question,
                request.history,
            )
            return VideoQuestionResponse(answer=answer, model=insight.model)
        except VideoInsightError as exc:
            raise _insight_error(exc) from None
        finally:
            insight.close()

    @router.get("/tasks/{task_id}/export")
    def export_video_task(
        task_id: str,
        format: str = Query("text", pattern="^(text|srt|vtt)$"),
    ) -> Response:
        body, media_type, filename = service_provider().export(task_id, format)
        encoded_name = quote(filename)
        return Response(
            content=body,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
            },
        )

    return router


def _require_insight_service(
    provider: LlmConfigProvider | None,
) -> VideoInsightService:
    config = provider() if provider is not None else None
    if config is None:
        raise AppError(
            503,
            "VIDEO_LLM_NOT_CONFIGURED",
            "DeepSeek 尚未配置，无法筛选有效原稿或进行稿件问答。",
        )
    return VideoInsightService(config)


def _insight_error(error: VideoInsightError) -> AppError:
    messages = {
        "LLM_TIMEOUT": "DeepSeek 响应超时，请稍后重试。",
        "LLM_INSUFFICIENT_BALANCE": "DeepSeek 账户余额不足，请充值后重试。",
        "VIDEO_TRANSCRIPT_EMPTY": "当前原字稿为空，无法整理。",
        "VIDEO_SELECTION_INVALID": "DeepSeek 没有按原句返回结果，请重新筛选。",
        "VIDEO_REFINEMENT_REQUIRED": "请先生成有效原稿。",
    }
    return AppError(
        502,
        error.code,
        messages.get(error.code, "DeepSeek 暂时无法完成处理，请稍后重试。"),
        retryable=error.code not in {"LLM_INSUFFICIENT_BALANCE"},
    )
