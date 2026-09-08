import type {
  DailyReportResponse as GeneratedDailyReport,
  DailySection as GeneratedDailySection,
  HotTopic as GeneratedHotTopic,
  HotTopicsResponse as GeneratedHotTopicsResponse,
  ItemsResponse as GeneratedItemsResponse,
  PublicItem,
  PublicSource,
} from './api-v1.generated'

export type { ProblemDetail, PublicV1Operations } from './api-v1.generated'

// ===== 主题 =====
export type ThemeMode = 'dark' | 'light' | 'auto'
export type ThemeActual = 'dark' | 'light'

// ===== 信源等级 =====
export type SourceTier = NonNullable<PublicSource['tier']>

// ===== 分类 =====
export type Category = NonNullable<PublicItem['category']>

// ===== 任务状态 =====
export type TaskStatus = 'queued' | 'collecting' | 'pre_screening' | 'ranking' | 'review_ready' | 'drafting' | 'completed' | 'failed'

// ===== 公开分发类型（由 OpenAPI v1 生成） =====
export type TimelineItem = PublicItem

// ===== 热点话题 =====
export type HotTopic = GeneratedHotTopic

// ===== 任务（AI 创作） =====
export interface Task {
  id: string
  status: TaskStatus
  status_history: string[]
  mode: 'demo' | 'live'
  collected_article_count: number
  window_days: number
  expanded_categories: string[]
  collection_warnings: string[]
  source_counts: Record<string, number>
}

// ===== 选题 =====
export interface TopicCandidate {
  id: string
  title: string
  category: Category | 'tool' | 'news'
  heat_score: {
    value: number
    confidence: 'measured' | 'estimated'
    measured_likes?: number | null
    components: Record<string, number>
  }
  summary: string
  recommendation_reason: string
}

// ===== 证据包 =====
export interface EvidencePack {
  topic_id: string
  topic_title: string
  category: Category | 'tool' | 'news'
  facts: EvidenceFact[]
  source_urls: string[]
  conflicts: EvidenceConflict[]
  unverified_claims: string[]
  suggested_angle: string
  ready_for_draft: boolean
}

export interface EvidenceFact {
  claim_id?: string
  claim: string
  source_name?: string
  source_url?: string | null
  source_urls: string[]
}

export interface EvidenceConflict {
  subject: string
  claims: string[]
  source_urls: string[]
}

// ===== 草稿 =====
export interface Draft {
  id: string
  topic_id: string
  version: number
  selected_title: string
  titles: string[]
  body: string
  cited_source_urls: string[]
  stage: 'research' | 'review_ready'
  available_versions: number[]
  quality: QualityReport
}

export interface QualityReport {
  issues: QualityIssue[]
  blocking_issue_count: number
  can_review: boolean
  can_export: boolean
}

export interface QualityIssue {
  code: string
  message: string
  blocking: boolean
  location?: string | null
  evidence_claim_ids?: string[]
  fix_suggestion?: string | null
}

// ===== API 响应 =====
export interface PaginatedResponse<T> {
  items: T[]
  shortage?: Record<string, number>
}

// ===== 内容生产项目 =====
export type CreationStage =
  | 'opportunity'
  | 'research'
  | 'evidence_ready'
  | 'brief_ready'
  | 'drafting'
  | 'review_blocked'
  | 'review_passed'
  | 'exported'

export interface ContentBriefSection {
  id: string
  heading: string
  intent: string
  claimIds: string[]
}

export interface ContentBrief {
  version: number
  coreThesis: string
  audienceBenefit: string
  angle: string
  differentiation: string
  sections: ContentBriefSection[]
  requiredSourceUrls: string[]
  risks: string[]
  authorInputs: string[]
  readyForDraft: boolean
  blockingReasons: string[]
  updatedAt: string
}

export interface CreatorProject {
  schemaVersion: number
  id: string
  topicId: string
  taskId: string
  origin: 'topic' | 'inspiration'
  profileVersion: number
  topicTitle: string
  category: Category | null
  stage: CreationStage
  version: number
  brief: ContentBrief | null
  opportunity: ContentOpportunity | null
  authorInputs: AuthorInput[]
  inspirationSources: InspirationSourceSummary[]
  similarityReports: SimilarityReportSummary[]
  currentDraftId: string | null
  humanApprovedDraftVersion: number | null
  draftVersions: DraftVersionSummary[]
  qualityReports: QualityReportSummary[]
  exportRecords: ExportRecordSummary[]
  quality: {
    blockingIssueCount: number
    canExport: boolean
  } | null
  createdAt: string
  updatedAt: string
}

export interface InspirationStructureSummary {
  headingCount: number
  paragraphCount: number
  averageParagraphCharacters: number
  shortParagraphRatio: number
  listItemCount: number
  questionCount: number
  openingApproach: string
  reusableBlueprint: string[]
}

export interface InspirationSourceSummary {
  id: string
  role: 'primary' | 'supporting'
  sourceKind: 'hot' | 'user_url' | 'supporting_url'
  url: string
  title: string
  publisher: string
  publisherIdentity: string
  contentSha256: string
  contentCharacters: number
  sourceType: 'official' | 'primary' | 'secondary'
  sourceTypeVerified: boolean
  publishedAt: string | null
  importedAt: string
  structure: InspirationStructureSummary
}

export interface InspirationPreview {
  schemaVersion: number
  source: InspirationSourceSummary
  policy: string
}

export interface SimilarityReportSummary {
  id: string
  draftId: string
  draftVersion: number
  sourceId: string
  sourceUrl: string
  threshold: number
  score: number
  ngramOverlap: number
  longestMatchRatio: number
  matchedFragments: string[]
  passed: boolean
  algorithm: string
  checkedAt: string
}

export interface CreationHotArticle {
  articleId: string
  title: string
  url: string
  publisher: string
  category: Category
  publishedAt: string
  score: number
  selected: boolean
  scoreConfidence: 'measured' | 'estimated'
}

export interface CreationHotArticleList {
  schemaVersion: number
  metricLabel: string
  metricDisclosure: string
  items: CreationHotArticle[]
  count: number
}

export interface AccountProfile {
  schemaVersion: number
  id: string
  version: number
  accountName: string
  positioning: string
  targetAudience: string
  contentPillars: string[]
  excludedTopics: string[]
  toneKeywords: string[]
  targetCharacters: number
  paragraphLimit: number
  titlePreferences: string[]
  forbiddenPhrases: string[]
  acceptedSourceTiers: string[]
  citationStyle: string
  riskTolerance: 'low' | 'medium' | 'high'
  verifiedExperienceInventory: string[]
  fixedViewpoints: string[]
  effectiveAt: string
}

export interface OpportunityScores {
  accountFit: number
  audienceValue: number
  differentiation: number
  eventImpact: number
  evidenceCompleteness: number
  timeliness: number
  productionEfficiency: number
}

export interface ContentOpportunity {
  schemaVersion: number
  id: string
  sourceKind: 'topic' | 'item' | 'story'
  sourceId: string
  title: string
  category: string
  creationScore: number
  scores: OpportunityScores
  recommendedAngle: string
  readerBenefit: string
  timeWindowHours: number
  risks: string[]
  blockers: string[]
  status: 'new' | 'adopted' | 'ignored' | 'blocked'
  ignoreReason: string | null
  createdAt: string
  decidedAt: string | null
}

export interface OpportunityList {
  schemaVersion: number
  items: ContentOpportunity[]
  count: number
}

export interface AuthorInput {
  id: string
  kind: 'experience' | 'opinion' | 'example' | 'data'
  content: string
  confirmed: boolean
  sourceUrls: string[]
  updatedAt: string
}

export interface DraftVersionSummary {
  id: string
  version: number
  parentVersion: number | null
  generationMethod: 'ai' | 'rule' | 'author_edit' | 'finalize'
  model: string | null
  promptHash: string
  promptVersionId: string | null
  llmCallIds: string[]
  profileVersion: number
  citedClaimIds: string[]
  authorInputIds: string[]
  changeSummary: string
  createdAt: string
}

export interface QualityReportSummary {
  id: string
  draftVersion: number
  profileVersion: number
  passed: boolean
  humanConfirmed: boolean
  issueCount: number
  createdAt: string
}

export interface ExportRecordSummary {
  id: string
  draftVersion: number
  format: 'markdown' | 'html' | 'text' | 'wechat_html'
  qualityReportId: string
  contentSha256: string
  exportedAt: string
}

export interface CreatorProjectList {
  schemaVersion: number
  items: CreatorProject[]
  count: number
}

export interface CreatorDraftResult {
  project: CreatorProject
  draft: Draft
}

export interface PromptVersionSummary {
  id: string
  stage: 'probe' | 'outline' | 'section' | 'editor' | 'semantic' | 'fallback'
  version: number
  templateHash: string
  outputSchema: string
  createdAt: string
}

export interface CreationReplayReport {
  schemaVersion: number
  id: string
  requestedMode: 'rule' | 'current'
  actualMode: 'ai' | 'rule' | 'mixed'
  profileVersion: number
  promptVersionIds: string[]
  sampleCount: number
  passedCount: number
  passRate: number
  sourceCoverageRate: number
  unsupportedNumberCount: number
  fabricatedExperienceCount: number
  profileMismatchCount: number
  medianDurationMs: number
  createdAt: string
}

export interface CreationRuntime {
  schemaVersion: number
  configured: boolean
  verified: boolean
  healthState: 'unconfigured' | 'untested' | 'healthy' | 'degraded'
  activeMode: 'ai' | 'rule'
  provider: string
  model: string | null
  distributionProcessing: {
    enabled: boolean
    available: boolean
    state: 'unconfigured' | 'healthy' | 'degraded'
    callsToday: number
    dailyCallLimit: number
    reason: string | null
    openedAt: string | null
  }
  generationStages: string[]
  promptVersions: PromptVersionSummary[]
  recentCalls: { count: number; successful: number; fallbacks: number; errors: number; modelAttempts: number }
  lastAttemptAt: string | null
  lastSuccessAt: string | null
  lastError: { code: string; message: string; stage: string; createdAt: string } | null
  latestReplay: CreationReplayReport | null
}

export interface CreationRuntimeProbe {
  schemaVersion: number
  ok: boolean
  healthState: 'healthy' | 'degraded'
  message: string
  call: {
    id: string
    stage: 'probe'
    status: 'success' | 'error'
    model: string | null
    durationMs: number
    errorCode: string | null
    createdAt: string
  }
}

// ===== 内部人工标注工作台（不属于公开 OpenAPI v1） =====
export type EvaluationStatusFilter = 'all' | 'labelled' | 'unlabelled'

export interface EvaluationSuggestion {
  relevant: boolean
  category: Category
  selected: boolean
  distribution_score: number | null
  strategy_version: string | null
  reason: string | null
}

export interface EvaluationArticle {
  id: string
  title: string
  summary: string
  source_name: string
  source_url: string
  source_identity: string | null
  published_at: string
  collected_at: string
  category: Category
  tier: string | null
}

export interface GoldLabel {
  relevant: boolean
  category: Category
  selected: boolean
  primary_source_correct: boolean
  story_key: string | null
  creation_worthy: boolean
  evidence_sufficient: boolean
  annotation_method: 'human' | 'synthetic'
  data_license: string
  annotator: string
  annotated_at: string
  notes: string
}

export interface EvaluationItem {
  position: number
  candidate: {
    schema_version: number
    evaluation_group: string
    article: EvaluationArticle
    suggestion: EvaluationSuggestion
  }
  label: GoldLabel | null
}

export interface EvaluationPage {
  items: EvaluationItem[]
  total: number
  offset: number
  limit: number
}

export interface EvaluationStatus {
  schema_version: number
  target_count: number
  candidate_count: number
  labelled_count: number
  human_label_count: number
  synthetic_label_count: number
  remaining_count: number
  completion_rate: number
  ready_for_baseline: boolean
}

export type SelectionReviewFilter = 'all' | 'pending' | 'completed'

export type SelectionReviewReason =
  | 'timely_firsthand'
  | 'high_information_gain'
  | 'wechat_value'
  | 'other_keep'
  | 'low_information_gain'
  | 'weak_source'
  | 'outdated'
  | 'too_niche'
  | 'duplicate_or_followup'
  | 'other_remove'

export interface SelectionReviewDecision {
  selected: boolean
  reason_code: SelectionReviewReason
  notes: string
  reviewer: string
  reviewed_at: string
  annotation_method: 'human'
}

export interface SelectionReviewItem {
  position: number
  reason: string
  candidate: EvaluationItem['candidate']
  initial_label: GoldLabel
  review: SelectionReviewDecision | null
}

export interface SelectionReviewPage {
  items: SelectionReviewItem[]
  total: number
  offset: number
  limit: number
}

export interface SelectionReviewStatus {
  schema_version: number
  review_set_id: string
  target_count: number
  total_count: number
  completed_count: number
  pending_count: number
  changed_count: number
  independent_review_count: number
  completion_rate: number
}

export type ReviewLifecycle =
  | 'awaiting_second_reviewer'
  | 'second_reviewing'
  | 'awaiting_adjudication'
  | 'complete'

export interface ReviewParticipant {
  review_set_id: string
  role: 'reviewer_a' | 'reviewer_b'
  reviewer: string
  claimed_at: string
}

export interface DoubleReviewStatus {
  schema_version: number
  review_set_id: string
  lifecycle: ReviewLifecycle
  total_count: number
  second_completed_count: number
  second_pending_count: number
  consensus_count: number
  conflict_count: number
  adjudicated_count: number
  pending_adjudication_count: number
  second_reviewer: string | null
  completion_rate: number
}

export interface BlindSelectionReviewItem {
  position: number
  article: EvaluationArticle
  review: SelectionReviewDecision | null
}

export interface BlindSelectionReviewPage {
  items: BlindSelectionReviewItem[]
  total: number
  offset: number
  limit: number
}

export interface SelectionAdjudicationDecision {
  selected: boolean
  reason_code: SelectionReviewReason
  notes: string
  adjudicator: string
  adjudicated_at: string
  annotation_method: 'human'
}

export interface SelectionConflictItem {
  position: number
  article: EvaluationArticle
  reviewer_a: SelectionReviewDecision
  reviewer_b: SelectionReviewDecision
  adjudication: SelectionAdjudicationDecision | null
}

export interface SelectionConflictPage {
  items: SelectionConflictItem[]
  total: number
  offset: number
  limit: number
}

export type ItemsResponse = GeneratedItemsResponse

export type HotTopicsResponse = GeneratedHotTopicsResponse

export type DailySection = GeneratedDailySection

export type DailyReport = GeneratedDailyReport

// ===== 导航 =====
export interface NavItem {
  label: string
  path: string
  icon: string
  group?: string
}
