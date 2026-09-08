// ---- Task ----
export interface TaskPayload {
  id: string;
  status: string;
  status_history: string[];
  mode: string;
  collected_article_count: number;
  window_days: number;
  expanded_categories: string[];
  collection_warnings: string[];
  source_counts: Record<string, number>;
}

// ---- Topic ----
export interface TopicCandidate {
  id: string;
  title: string;
  category: 'tool' | 'news' | 'ai-models' | 'ai-products' | 'industry' | 'paper' | 'tip';
  heat_score: { value: number; confidence: string; measured_likes?: number };
  summary: string;
  recommendation_reason: string;
}

export interface TopicsResponse {
  items: TopicCandidate[];
  shortage: Record<string, number>;
}

// ---- Evidence ----
export interface EvidenceSource {
  source_name: string;
  source_identity: string;
  source_type: string;
  source_type_verified: boolean;
  provenance: string | null;
}

export interface EvidenceFact {
  claim: string;
  source_urls: string[];
  sources: Record<string, EvidenceSource>;
}

export interface EvidenceConflict {
  subject: string;
  claims: string[];
  source_urls: string[];
}

export interface EvidencePack {
  topic_id: string;
  topic_title: string;
  category: string;
  facts: EvidenceFact[];
  source_urls: string[];
  conflicts: EvidenceConflict[];
  unverified_claims: string[];
  suggested_angle: string;
  ready_for_draft: boolean;
}

// ---- Draft ----
export interface QualityIssue {
  code: string;
  message: string;
  blocking: boolean;
}

export interface QualityReport {
  issues: QualityIssue[];
  blocking_issue_count: number;
  can_review: boolean;
  can_export: boolean;
}

export interface DraftPayload {
  id: string;
  topic_id: string;
  version: number;
  available_versions: number[];
  selected_title: string;
  titles: string[];
  body: string;
  cited_source_urls: string[];
  stage: string;
  quality: QualityReport;
}

// ---- API Error ----
export interface ApiErrorBody {
  error: { code: string; message: string; retryable: boolean };
}
