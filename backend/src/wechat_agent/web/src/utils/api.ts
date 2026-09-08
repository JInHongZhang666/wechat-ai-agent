const API_BASE = '/api'

interface CacheEntry {
  etag: string
  value: unknown
}

const responseCache = new Map<string, CacheEntry>()

export async function apiPost<T>(path: string, body: unknown = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText })) as {
      message?: string
      detail?: string
      error?: { message?: string }
    }
    throw new Error(err.error?.message || err.detail || err.message || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`
  const cached = responseCache.get(url)
  const headers = new Headers(init?.headers)
  if (cached) headers.set('If-None-Match', cached.etag)
  const res = await fetch(url, { ...init, headers })
  if (res.status === 304 && cached) return cached.value as T
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText })) as {
      message?: string
      detail?: string
      error?: { message?: string }
    }
    throw new Error(err.error?.message || err.detail || err.message || `HTTP ${res.status}`)
  }
  const value = await res.json() as T
  const etag = res.headers.get('ETag')
  if (etag) responseCache.set(url, { etag, value })
  return value
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: res.statusText })) as {
      message?: string
      detail?: string
      error?: { message?: string }
    }
    throw new Error(err.error?.message || err.detail || err.message || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function apiExport(path: string, format: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}?format=${format}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.text()
}
