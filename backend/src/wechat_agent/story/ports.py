"""Persistence boundary for the Story shadow engine."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from wechat_agent.story.models import (
    PairPage,
    PairSuggestion,
    StoryApplyResult,
    StoryDetail,
    StoryPage,
    StoryPersistenceBatch,
    StoryShadowRun,
    StoryShadowStatus,
    StoryStatus,
    StoryWindowState,
)


class StoryRepository(Protocol):
    def get_window(self, since: datetime) -> StoryWindowState: ...

    def apply_batch(self, batch: StoryPersistenceBatch) -> StoryApplyResult: ...

    def save_failed_run(self, run: StoryShadowRun) -> None: ...

    def get_story(self, story_id: str) -> StoryDetail | None: ...

    def list_stories(
        self,
        *,
        status: StoryStatus | None,
        offset: int,
        limit: int,
    ) -> StoryPage: ...

    def list_pairs(
        self,
        *,
        suggestion: PairSuggestion | None,
        offset: int,
        limit: int,
    ) -> PairPage: ...

    def latest_run(self) -> StoryShadowRun | None: ...

    def status(self) -> StoryShadowStatus: ...

    def list_runs(
        self,
        *,
        since: datetime,
        offset: int,
        limit: int,
    ) -> tuple[StoryShadowRun, ...]: ...

    def list_revisions(
        self,
        *,
        since: datetime,
        offset: int,
        limit: int,
    ) -> tuple: ...
