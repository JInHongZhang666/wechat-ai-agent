"""Domain values for the AIHOT selected-set synchronization protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


SelectedFieldMode = Literal["default", "minimal"]


@dataclass(frozen=True)
class SelectedSyncItem:
    """One selected item in both supported transfer representations."""

    id: str
    default: dict[str, object]
    minimal: dict[str, object]


@dataclass(frozen=True)
class SelectedChange:
    """One durable, ordered selected-set mutation."""

    op: Literal["upsert", "remove"]
    id: str
    item: dict[str, object] | None = None


@dataclass(frozen=True)
class SelectedSnapshotPage:
    fields: SelectedFieldMode
    items: list[dict[str, object]]
    cursor: str
    has_more: bool
    next_page: str | None


@dataclass(frozen=True)
class SelectedChangesPage:
    fields: SelectedFieldMode
    changes: list[SelectedChange]
    cursor: str
    has_more: bool


class SnapshotRequiredError(Exception):
    """The supplied cursor cannot be resumed from retained state."""


class SelectedSyncRepository(Protocol):
    """Persist the selected mirror, mutations, cursors, and frozen snapshots."""

    def reconcile(self, items: list[SelectedSyncItem]) -> int: ...

    def snapshot(
        self,
        *,
        fields: SelectedFieldMode,
        limit: int,
        page: str | None = None,
    ) -> SelectedSnapshotPage: ...

    def changes(self, *, cursor: str, limit: int) -> SelectedChangesPage: ...
