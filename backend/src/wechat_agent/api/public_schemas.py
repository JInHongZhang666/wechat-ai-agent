"""Stable public distribution schemas derived from the AIHOT v1 contract."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


CategorySlug = Literal[
    "ai-models",
    "ai-products",
    "industry",
    "paper",
    "tip",
]
SourceTierValue = Literal["T1", "T1.5", "T2"]


class PublicSource(BaseModel):
    name: str
    tier: SourceTierValue | None = None
    type: str | None = None


class PublicLinks(BaseModel):
    aihot: str
    original: str


class Attribution(BaseModel):
    name: str
    url: str | None = None


class ItemAssessment(BaseModel):
    components: dict[str, float]
    threshold: float
    confidence: Literal["measured", "estimated"]
    strategyVersion: str
    scoredAt: datetime


class PublicItem(BaseModel):
    id: str
    title: str
    originalTitle: str | None
    summary: str | None
    source: PublicSource
    links: PublicLinks
    publishedAt: datetime | None
    discoveredAt: datetime
    timelineAt: datetime
    category: CategorySlug | None
    score: float | None = Field(default=None, ge=0, le=100)
    selected: bool
    reason: str | None = None
    assessment: ItemAssessment | None = None
    attribution: Attribution | None = None


class PublicMinimalLinks(BaseModel):
    aihot: str


class PublicItemMinimal(BaseModel):
    """Snapshot representation without summaries or original-source links."""

    id: str
    title: str
    originalTitle: str | None
    source: PublicSource
    links: PublicMinimalLinks
    publishedAt: datetime | None
    discoveredAt: datetime
    timelineAt: datetime
    category: CategorySlug | None
    score: float | None = Field(default=None, ge=0, le=100)
    selected: Literal[True]
    reason: str | None = None
    assessment: ItemAssessment | None = None
    attribution: Attribution | None = None


class SelectedSnapshotResponse(BaseModel):
    schemaVersion: Literal[1]
    fields: Literal["default", "minimal"]
    items: list[PublicItem | PublicItemMinimal]
    cursor: str
    hasMore: bool
    nextPage: str | None


class SelectedUpsertChange(BaseModel):
    op: Literal["upsert"]
    id: str
    item: PublicItem | PublicItemMinimal


class SelectedRemoveChange(BaseModel):
    op: Literal["remove"]
    id: str


SelectedChangePayload = Annotated[
    SelectedUpsertChange | SelectedRemoveChange,
    Field(discriminator="op"),
]


class SelectedChangesResponse(BaseModel):
    schemaVersion: Literal[1]
    fields: Literal["default", "minimal"]
    changes: list[SelectedChangePayload]
    cursor: str
    hasMore: bool


class ItemsQuery(BaseModel):
    mode: Literal["selected", "all"]
    category: CategorySlug | None
    q: str | None
    window: Literal["24h", "7d"]
    by: Literal["timeline", "published"]
    ordering: Literal["timelineDesc", "publishedDesc"]
    anchorAt: datetime


class CursorPage(BaseModel):
    count: int = Field(ge=0)
    hasMore: bool
    nextCursor: str | None
    cursorVersion: Literal[1]


class ItemsResponse(BaseModel):
    schemaVersion: Literal[1]
    query: ItemsQuery
    items: list[PublicItem]
    page: CursorPage


class StoryLink(BaseModel):
    story: str


class HotTopic(BaseModel):
    title: str
    sourceCount: int = Field(ge=0)
    signalCount: int = Field(ge=0)
    publisherGroupCount: int = Field(ge=0)
    reportCount: int = Field(ge=0)
    qualification: Literal["multi_publisher", "single_source"]
    sourceNames: list[str]
    latestAt: datetime
    category: CategorySlug | None = None
    summary: str | None = None
    score: float | None = Field(default=None, ge=0, le=100)
    links: StoryLink | None = None


class HotTopicsResponse(BaseModel):
    schemaVersion: Literal[1]
    count: int = Field(ge=0)
    items: list[HotTopic]


class DailySection(BaseModel):
    label: str
    slug: CategorySlug
    count: int = Field(ge=0)
    items: list[PublicItem]


class DailyFlashLinks(BaseModel):
    aihot: str | None
    original: str


class DailyFlash(BaseModel):
    title: str
    links: DailyFlashLinks


class DailyReportResponse(BaseModel):
    schemaVersion: Literal[1]
    date: date
    timezone: Literal["Asia/Shanghai"]
    revision: int = Field(ge=1)
    status: Literal["draft", "published"]
    lead: str
    sections: list[DailySection]
    flashes: list[DailyFlash]
    strategyVersion: str
    sourceSnapshotAt: datetime
    generatedAt: datetime
    publishedAt: datetime | None
    itemCount: int = Field(ge=0)
    sourceCount: int = Field(ge=0)
    contentHash: str


class DailyIndexItem(BaseModel):
    date: date
    revision: int = Field(ge=1)
    status: Literal["draft", "published"]
    generatedAt: datetime
    itemCount: int = Field(ge=0)
    sourceCount: int = Field(ge=0)
    contentHash: str


class DailyIndexResponse(BaseModel):
    schemaVersion: Literal[1]
    count: int = Field(ge=0)
    items: list[DailyIndexItem]


class FingerprintResponse(BaseModel):
    schemaVersion: Literal[1]
    fingerprint: str
    itemCount: int = Field(ge=0)
    latestScoredAt: datetime | None
