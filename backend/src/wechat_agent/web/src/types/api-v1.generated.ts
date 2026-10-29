// Generated from openapi-v1.json. Do not edit by hand.
// Run: python scripts/generate_public_contract.py

export interface Attribution {
  name: string
  url?: string | null
}

export interface CursorPage {
  count: number
  hasMore: boolean
  nextCursor: string | null
  cursorVersion: 1
}

export interface DailyFlash {
  title: string
  links: DailyFlashLinks
}

export interface DailyFlashLinks {
  aihot: string | null
  original: string
}

export interface DailyIndexItem {
  date: string
  revision: number
  status: "draft" | "published"
  generatedAt: string
  itemCount: number
  sourceCount: number
  contentHash: string
}

export interface DailyIndexResponse {
  schemaVersion: 1
  count: number
  items: Array<DailyIndexItem>
}

export interface DailyReportResponse {
  schemaVersion: 1
  date: string
  timezone: "Asia/Shanghai"
  revision: number
  status: "draft" | "published"
  lead: string
  sections: Array<DailySection>
  flashes: Array<DailyFlash>
  strategyVersion: string
  sourceSnapshotAt: string
  generatedAt: string
  publishedAt: string | null
  itemCount: number
  sourceCount: number
  contentHash: string
}

export interface DailySection {
  label: string
  slug: "ai-models" | "ai-products" | "industry" | "paper" | "tip"
  count: number
  items: Array<PublicItem>
}

export interface FingerprintResponse {
  schemaVersion: 1
  fingerprint: string
  itemCount: number
  latestScoredAt: string | null
}

export interface HotTopic {
  title: string
  sourceCount: number
  signalCount: number
  publisherGroupCount: number
  reportCount: number
  qualification: "multi_publisher" | "single_source"
  sourceNames: Array<string>
  latestAt: string
  category?: "ai-models" | "ai-products" | "industry" | "paper" | "tip" | null
  summary?: string | null
  score?: number | null
  links?: StoryLink | null
}

export interface HotTopicsResponse {
  schemaVersion: 1
  count: number
  items: Array<HotTopic>
}

export interface ItemAssessment {
  components: Record<string, number>
  threshold: number
  confidence: "measured" | "estimated"
  strategyVersion: string
  scoredAt: string
}

export interface ItemsQuery {
  mode: "selected" | "all"
  category: "ai-models" | "ai-products" | "industry" | "paper" | "tip" | null
  q: string | null
  window: "24h" | "7d"
  by: "timeline" | "published"
  ordering: "timelineDesc" | "publishedDesc"
  anchorAt: string
}

export interface ItemsResponse {
  schemaVersion: 1
  query: ItemsQuery
  items: Array<PublicItem>
  page: CursorPage
}

export interface ProblemDetail {
  type: string
  title: string
  status: number
  detail: string
  code: string
  requestId: string
  retryable: boolean
}

export interface PublicItem {
  id: string
  title: string
  originalTitle: string | null
  summary: string | null
  source: PublicSource
  links: PublicLinks
  publishedAt: string | null
  discoveredAt: string
  timelineAt: string
  category: "ai-models" | "ai-products" | "industry" | "paper" | "tip" | null
  score?: number | null
  selected: boolean
  reason?: string | null
  assessment?: ItemAssessment | null
  attribution?: Attribution | null
}

export interface PublicItemMinimal {
  id: string
  title: string
  originalTitle: string | null
  source: PublicSource
  links: PublicMinimalLinks
  publishedAt: string | null
  discoveredAt: string
  timelineAt: string
  category: "ai-models" | "ai-products" | "industry" | "paper" | "tip" | null
  score?: number | null
  selected: true
  reason?: string | null
  assessment?: ItemAssessment | null
  attribution?: Attribution | null
}

export interface PublicLinks {
  aihot: string
  original: string
}

export interface PublicMinimalLinks {
  aihot: string
}

export interface PublicSource {
  name: string
  tier?: "T1" | "T1.5" | "T2" | null
  type?: string | null
}

export interface SelectedChangesResponse {
  schemaVersion: 1
  fields: "default" | "minimal"
  changes: Array<SelectedUpsertChange | SelectedRemoveChange>
  cursor: string
  hasMore: boolean
}

export interface SelectedRemoveChange {
  op: "remove"
  id: string
}

export interface SelectedSnapshotResponse {
  schemaVersion: 1
  fields: "default" | "minimal"
  items: Array<PublicItem | PublicItemMinimal>
  cursor: string
  hasMore: boolean
  nextPage: string | null
}

export interface SelectedUpsertChange {
  op: "upsert"
  id: string
  item: PublicItem | PublicItemMinimal
}

export interface StoryLink {
  story: string
}

export interface PublicV1Operations {
  "GET /api/v1/dailies": DailyIndexResponse
  "GET /api/v1/dailies/latest": DailyReportResponse
  "GET /api/v1/dailies/{date}": DailyReportResponse
  "GET /api/v1/fingerprint": FingerprintResponse
  "GET /api/v1/hot-topics": HotTopicsResponse
  "GET /api/v1/items": ItemsResponse
  "GET /api/v1/selected/changes": SelectedChangesResponse
  "GET /api/v1/selected/snapshot": SelectedSnapshotResponse
}
