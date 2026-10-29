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

interface FeaturedHotTopic {
  id: string
  title: string
  url: string | null
  sourceNames: string[]
  sourceCount: number
  heat: number
  latestAt: string
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
          .catch(() => ({ schemaVersion: 1 as const, count: 0, items: [] }))
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

  const featuredHotTopics = buildFeaturedHotTopics(hotTopics)

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

      {showHotTopics && (
        <section className="hot-topics" aria-labelledby="current-hot-topics-title">
          <div className="hot-topics-head">
            <h2 className="hot-topics-title" id="current-hot-topics-title">
              <span className="hot-topics-live-dot" aria-hidden="true" />
              当前热点
            </h2>
            <Link className="hot-topics-more" to="/hot">完整榜单 →</Link>
          </div>
          <ol className="hot-topics-list">
            {loading && Array.from({ length: 5 }, (_, index) => (
              <li key={index} className="hot-topics-row hot-topics-row-loading" aria-hidden="true">
                <span className="skeleton hot-topics-rank-skeleton" />
                <span className="skeleton hot-topics-title-skeleton" />
                <span className="skeleton hot-topics-meta-skeleton" />
              </li>
            ))}
            {!loading && featuredHotTopics.map((topic, index) => (
              <li key={topic.id} className="hot-topics-row">
                <span className={`hot-topics-rank${index < 3 ? ` hot-topics-rank-${index + 1}` : ''}`}>
                  {index + 1}
                </span>
                {topic.url ? (
                  <a
                    className="hot-topics-link"
                    href={topic.url}
                    target={isExternalUrl(topic.url) ? '_blank' : undefined}
                    rel={isExternalUrl(topic.url) ? 'noreferrer' : undefined}
                  >
                    {topic.title}
                  </a>
                ) : (
                  <Link className="hot-topics-link" to="/hot">{topic.title}</Link>
                )}
                <div className="hot-topics-sources" aria-label={`${topic.sourceCount} 个信源`}>
                  <div className="hot-topics-source-stack" aria-hidden="true">
                    {topic.sourceNames.slice(0, 4).map((sourceName, sourceIndex) => (
                      <span
                        key={`${sourceName}-${sourceIndex}`}
                        className={`hot-topics-source-badge hot-topics-source-badge-${(index + sourceIndex) % 5}`}
                        title={sourceName}
                      >
                        {sourceInitial(sourceName)}
                      </span>
                    ))}
                  </div>
                  <span className="hot-topics-source-count">{topic.sourceCount} 源</span>
                </div>
                <span className="hot-topics-meta">
                  <strong>{topic.heat}</strong>
                  <span>热度</span>
                </span>
                <span className={`hot-topics-trend hot-topics-trend-${trendFor(topic)}`} aria-label={trendLabel(topic)}>
                  {trendIcon(topic)}
                </span>
              </li>
            ))}
            {!loading && featuredHotTopics.length === 0 && (
              <li className="hot-topics-empty">近 24 小时暂时没有可展示的热点资讯</li>
            )}
          </ol>
          <p className="hot-topics-disclosure">统计最近 24 小时全部有效资讯，无需通过精选门槛；热度为相关报道数量，旁边为独立信源数。</p>
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

function buildFeaturedHotTopics(hotTopics: HotTopic[]): FeaturedHotTopic[] {
  return hotTopics.slice(0, 5).map((topic, index) => ({
    id: `topic-${index}-${topic.title}`,
    title: topic.title,
    url: topic.links?.story ?? null,
    sourceNames: topic.sourceNames,
    sourceCount: topic.sourceCount,
    heat: topic.reportCount,
    latestAt: topic.latestAt,
  }))
}

function sourceInitial(sourceName: string): string {
  const cleaned = sourceName.replace(/^[^：:]+[：:]\s*/, '').trim()
  const latin = cleaned.match(/[A-Za-z0-9]/)?.[0]
  return (latin ?? cleaned.slice(0, 1) ?? 'AI').toUpperCase()
}

function ageInHours(value: string): number {
  return Math.max(0, (Date.now() - new Date(value).getTime()) / 3_600_000)
}

function trendFor(topic: FeaturedHotTopic): 'new' | 'rising' | 'steady' {
  if (ageInHours(topic.latestAt) <= 6) return 'new'
  if (topic.sourceCount >= 2) return 'rising'
  return 'steady'
}

function trendIcon(topic: FeaturedHotTopic): string {
  const trend = trendFor(topic)
  if (trend === 'new') return '新'
  if (trend === 'rising') return '↗'
  return '—'
}

function trendLabel(topic: FeaturedHotTopic): string {
  const trend = trendFor(topic)
  if (trend === 'new') return '最新热点'
  if (trend === 'rising') return '热度上升'
  return '热度平稳'
}

function isExternalUrl(value: string): boolean {
  return /^https?:\/\//i.test(value)
}
