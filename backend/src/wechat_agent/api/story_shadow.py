"""Internal operations API for the deterministic Story shadow engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from wechat_agent.api.errors import AppError
from wechat_agent.story.models import (
    PairPage,
    PairSuggestion,
    StoryDetail,
    StoryPage,
    StoryShadowRun,
    StoryShadowStatus,
    StoryStatus,
)
from wechat_agent.story.ports import StoryRepository
from wechat_agent.story.service import StoryShadowService


@dataclass(frozen=True)
class StoryShadowResources:
    repository: StoryRepository
    service: StoryShadowService


StoryShadowProvider = Callable[[], StoryShadowResources | None]


class StoryReplayRequest(BaseModel):
    days: int = Field(ge=1, le=7)


def create_story_shadow_router(provider: StoryShadowProvider) -> APIRouter:
    """Expose diagnostics without adding Story to the public v1 contract."""
    router = APIRouter(prefix="/api/ops/story-shadow")

    def resources() -> StoryShadowResources:
        resolved = provider()
        if resolved is None:
            raise AppError(
                503,
                "STORY_SHADOW_UNAVAILABLE",
                "Story 影子服务当前仅支持项目 SQLite 存储。",
            )
        return resolved

    @router.get("/status", response_model=StoryShadowStatus)
    def status() -> StoryShadowStatus:
        return resources().repository.status()

    @router.get("/stories", response_model=StoryPage)
    def list_stories(
        status: StoryStatus | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> StoryPage:
        return resources().repository.list_stories(
            status=status,
            offset=offset,
            limit=limit,
        )

    @router.get("/stories/{story_id}", response_model=StoryDetail)
    def story_detail(story_id: str) -> StoryDetail:
        detail = resources().repository.get_story(story_id)
        if detail is None:
            raise AppError(
                404,
                "STORY_NOT_FOUND",
                "指定 Story 不存在。",
            )
        return detail

    @router.get("/pairs", response_model=PairPage)
    def list_pairs(
        suggestion: PairSuggestion | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> PairPage:
        return resources().repository.list_pairs(
            suggestion=suggestion,
            offset=offset,
            limit=limit,
        )

    @router.post("/replay", response_model=StoryShadowRun)
    def replay(request: StoryReplayRequest) -> StoryShadowRun:
        from datetime import UTC, datetime

        return resources().service.replay(request.days, datetime.now(UTC))

    return router
