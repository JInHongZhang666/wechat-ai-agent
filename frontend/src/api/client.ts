/** API client with v1 endpoint support, ETag caching, and Problem JSON error handling. */

import type {
  TaskPayload,
  TopicsResponse,
  EvidencePack,
  DraftPayload,
  ApiErrorBody,
} from '../types/api';
import type {
  ProblemDetail,
  SelectedChangesResponse,
  SelectedSnapshotResponse,
} from '../types/api-v1.generated';

// ── ETag cache ────────────────────────────────────────────────────────

interface CacheEntry {
  etag: string;
  value: unknown;
}

const etagCache = new Map<string, CacheEntry>();

function getCached(url: string): CacheEntry | undefined {
  return etagCache.get(url);
}

function setCached(url: string, etag: string | null, value: unknown) {
  if (etag) {
    etagCache.set(url, { etag, value });
  }
}

// ── Error types ───────────────────────────────────────────────────────

class ApiClientError extends Error {
  code: string;
  retryable: boolean;
  requestId?: string;
  constructor(message: string, code: string, retryable: boolean, requestId?: string) {
    super(message);
    this.code = code;
    this.retryable = retryable;
    this.requestId = requestId;
  }
}

// ── Core request ──────────────────────────────────────────────────────

async function request<T>(
  path: string,
  options: RequestInit = {},
  useEtag = true,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };

  // Attach ETag if cached
  if (useEtag && (options.method === undefined || options.method === 'GET')) {
    const cached = getCached(path);
    if (cached) {
      headers['If-None-Match'] = cached.etag;
    }
  }

  const res = await fetch(path, { ...options, headers });

  // 304 Not Modified — return cached
  if (res.status === 304) {
    const cached = getCached(path);
    if (cached) return cached.value as T;
    const freshRes = await fetch(path, options);
    return handleResponse<T>(freshRes, path);
  }

  return handleResponse<T>(res, path);
}

async function handleResponse<T>(res: Response, path: string): Promise<T> {
  const data = await res.json();
  if (!res.ok) {
    const err = (data as ApiErrorBody)?.error;
    const problem = data as Partial<ProblemDetail>;
    throw new ApiClientError(
      problem.detail || err?.message || '请求失败',
      problem.code || err?.code || 'UNKNOWN',
      problem.retryable ?? err?.retryable ?? false,
      problem.requestId,
    );
  }
  setCached(path, res.headers.get('ETag'), data);
  return data as T;
}

// ── v1 API ────────────────────────────────────────────────────────────

export const api = {
  // ---- v1 items (AIHOT-compatible) ----
  getItems: (params?: {
    mode?: 'selected' | 'all';
    window?: '24h' | '7d';
    category?: string;
    q?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.mode) qs.set('mode', params.mode);
    if (params?.window) qs.set('window', params.window);
    if (params?.category) qs.set('category', params.category);
    if (params?.q) qs.set('q', params.q);
    if (params?.limit) qs.set('limit', String(params.limit));
    const query = qs.toString();
    return request<any>(`/api/v1/items${query ? `?${query}` : ''}`);
  },

  getHotTopics: () => request<any>('/api/v1/hot-topics'),

  // ---- v1 tasks ----
  createTask: (mode: 'demo' | 'live') =>
    request<TaskPayload>(`/api/v1/tasks/${mode}`, { method: 'POST', body: '{}' }),

  getTask: (taskId: string) => request<TaskPayload>(`/api/v1/tasks/${taskId}`),

  getTopics: (taskId: string) =>
    request<TopicsResponse>(`/api/v1/tasks/${taskId}/topics`),

  // ---- v1 evidence & drafts ----
  getEvidence: (topicId: string) =>
    request<EvidencePack>(`/api/v1/topics/${encodeURIComponent(topicId)}/evidence`),

  getV1Draft: (topicId: string) =>
    request<{ has_draft: boolean; evidence?: EvidencePack }>(
      `/api/v1/topics/${encodeURIComponent(topicId)}/draft`,
    ),

  // ---- Legacy endpoints (still used for draft creation/editing) ----
  createDraft: (topicId: string, profile?: Record<string, unknown>) =>
    request<DraftPayload>(`/api/topics/${encodeURIComponent(topicId)}/draft`, {
      method: 'POST',
      body: JSON.stringify(profile || {}),
    }),

  updateDraft: (draftId: string, selected_title: string, body: string) =>
    request<DraftPayload>(`/api/drafts/${draftId}`, {
      method: 'PUT',
      body: JSON.stringify({ selected_title, body }),
    }),

  exportDraft: async (draftId: string, format: string = 'markdown') => {
    const res = await fetch(`/api/drafts/${draftId}/export?format=${format}`);
    if (!res.ok) throw new Error('导出失败');
    return res.text();
  },

  // ---- Fingerprint (low-traffic polling) ----
  getFingerprint: () => request<any>('/api/v1/fingerprint'),

  // ---- Durable selected mirror (not used for ordinary feed browsing) ----
  getSelectedSnapshot: (params?: {
    fields?: 'default' | 'minimal';
    limit?: number;
    page?: string;
  }) => {
    const qs = new URLSearchParams();
    qs.set('fields', params?.fields || 'minimal');
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.page) qs.set('page', params.page);
    return request<SelectedSnapshotResponse>(
      `/api/v1/selected/snapshot?${qs.toString()}`,
    );
  },

  getSelectedChanges: (cursor: string, limit = 100) => {
    const qs = new URLSearchParams({ cursor, limit: String(limit) });
    return request<SelectedChangesResponse>(
      `/api/v1/selected/changes?${qs.toString()}`,
    );
  },
};

export { ApiClientError };
