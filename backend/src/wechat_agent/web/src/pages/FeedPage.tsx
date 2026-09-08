import { FormEvent, useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import CategoryTabs from '../components/CategoryTabs'
import Timeline from '../components/Timeline'
import type {
  Category,
  HotTopic,
  HotTopicsResponse,
  ItemsResponse,
  TimelineItem,
} from '../types'
import { apiGet } from '../utils/api'

interface FeedPageProps {
  mode: 'selected' | 'all'
  title: string
  subtitle: string
  category?: Category
  searchable?: boolean
  showHotTopics?: boolean
  showCategories?: boolean
}

export default function FeedPage({
  mode,
  title,
  subtitle,
  category,
  searchable = false,
  showHotTopics = false,
  showCategories = false,
}: FeedPageProps) {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialQuery = searchParams.get('q')?.trim() || ''
  const initialWindow = searchParams.get('window') === '24h' ? '24h' : '7d'
  const [items, setItems] = useState<TimelineItem[]>([])
  const [hotTopics, setHotTopics] = useState<HotTopic[]>([])
  const [windowValue, setWindowValue] = useState<'24h' | '7d'>(initialWindow)
  const [searchInput, setSearchInput] = useState(initialQuery)
  const [query, setQuery] = useState(initialQuery.length >= 2 ? initialQuery : '')
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)

  useEffect(() => {
    const nextQuery = searchParams.get('q')?.trim() || ''
    const nextWindow = searchParams.get('window') === '24h' ? '24h' : '7d'
    setSearchInput(nextQuery)
    setQuery(nextQuery.length >= 2 ? nextQuery : '')
    setWindowValue(nextWindow)
  }, [searchParams])

  const buildItemsPath = useCallback((cursor?: string | null) => {
    const params = new URLSearchParams({
      mode,
      window: windowValue,
      by: 'timeline',
      limit: '30',
    })
    if (category) params.set('category', category)
    if (query.length >= 2) params.set('q', query)
    if (cursor) params.set('cursor', cursor)
    return `/v1/items?${params.toString()}`
  }, [category, mode, query, windowValue])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)

    const feedRequest = apiGet<ItemsResponse>(buildItemsPath(), {
      signal: controller.signal,
    })
    const hotRequest = showHotTopics
      ? apiGet<HotTopicsResponse>('/v1/hot-topics', { signal: controller.signal })
      : Promise.resolve<HotTopicsResponse>({ schemaVersion: 1, count: 0, items: [] })

    Promise.all([feedRequest, hotRequest])
      .then(([feed, hot]) => {
        setItems(feed.items)
        setNextCursor(feed.page.nextCursor)
        setHotTopics(hot.items)
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setError(reason instanceof Error ? reason.message : '资讯加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [buildItemsPath, retryKey, showHotTopics])

  const submitSearch = (event: FormEvent) => {
    event.preventDefault()
    const nextQuery = searchInput.trim().length >= 2 ? searchInput.trim() : ''
    setQuery(nextQuery)
    const next = new URLSearchParams(searchParams)
    if (nextQuery) next.set('q', nextQuery)
    else next.delete('q')
    setSearchParams(next, { replace: true })
  }

  const clearSearch = () => {
    setSearchInput('')
    setQuery('')
    const next = new URLSearchParams(searchParams)
    next.delete('q')
    setSearchParams(next, { replace: true })
  }

  const chooseWindow = (value: '24h' | '7d') => {
    setWindowValue(value)
    const next = new URLSearchParams(searchParams)
    if (value === '24h') next.set('window', value)
    else next.delete('window')
    setSearchParams(next, { replace: true })
  }

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    setError(null)
    try {
      const response = await apiGet<ItemsResponse>(buildItemsPath(nextCursor))
      setItems(current => [
        ...current,
        ...response.items.filter(item => !current.some(existing => existing.id === item.id)),
      ])
      setNextCursor(response.page.nextCursor)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '下一页加载失败')
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <>
      <div className="page-header-compact">
        <h1 className="page-title">{title}</h1>
        <p className="page-subtitle">{subtitle}</p>
      </div>

      <div className="feed-navigation">
        {showCategories ? <CategoryTabs /> : <div />}
        {searchable && (
          <form className="feed-search" onSubmit={submitSearch}>
            <input
              className="feed-search-input"
              type="search"
              value={searchInput}
              onChange={event => setSearchInput(event.target.value)}
              placeholder="搜索标题或摘要（至少 2 个字符）"
              aria-label="搜索资讯"
            />
            <button className="btn" type="submit">搜索</button>
          </form>
        )}
      </div>

      <div className="feed-toolbar">
        <div className="feed-result-label">
          <span>{query ? `“${query}”的检索结果` : mode === 'selected' ? '高质量信息流' : '完整信息流'}</span>
          {query && (
            <button className="feed-clear-search" type="button" onClick={clearSearch}>
              清除搜索
            </button>
          )}
        </div>
        <div className="feed-window" aria-label="时间范围">
          {(['24h', '7d'] as const).map(value => (
            <button
              key={value}
              type="button"
              className={`feed-window-button${windowValue === value ? ' is-active' : ''}`}
              onClick={() => chooseWindow(value)}
            >
              {value === '24h' ? '24 小时' : '7 天'}
            </button>
          ))}
        </div>
      </div>

      {showHotTopics && !loading && hotTopics.length > 0 && (
        <section className="hot-topics">
          <div className="hot-topics-head">
            <h2 className="hot-topics-title">当前热点</h2>
            <Link className="hot-topics-more" to="/hot">完整榜单 →</Link>
          </div>
          <ol className="hot-topics-list">
            {hotTopics.map((topic, index) => (
              <li key={`${topic.title}-${index}`} className="hot-topics-row">
                <span className={`hot-topics-rank${index < 3 ? ` hot-topics-rank-${index + 1}` : ''}`}>
                  {index + 1}
                </span>
                <span className="hot-topics-link">{topic.title}</span>
                <span className="hot-topics-meta">{heatValue(topic)} 热度</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {error && (
        <div className="feed-error" role="alert">
          <span>加载失败：{error}</span>
          <button className="btn" type="button" onClick={() => setRetryKey(value => value + 1)}>
            重试
          </button>
        </div>
      )}

      <Timeline items={items} loading={loading} />

      {!loading && !error && items.length === 0 && (
        <div className="page-empty-state">
          <div className="page-empty-state-icon">📭</div>
          <p className="page-empty-state-text">
            {mode === 'selected'
              ? '当前时间范围内还没有达到精选阈值的真实资讯。'
              : '当前时间范围内还没有完成评估的真实资讯。'}
          </p>
          <Link className="btn" to="/create/tasks">运行一次信息采集</Link>
        </div>
      )}

      {!loading && nextCursor && (
        <div className="feed-load-more">
          <button className="btn" type="button" disabled={loadingMore} onClick={loadMore}>
            {loadingMore ? '加载中…' : '加载更多'}
          </button>
        </div>
      )}
    </>
  )
}

function heatValue(topic: HotTopic): number {
  if (topic.score != null) return Math.round(topic.score)
  return topic.signalCount + topic.sourceCount * 4 + topic.reportCount * 2
}
