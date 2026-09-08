import { useEffect, useState } from 'react'
import type { HotTopic, HotTopicsResponse } from '../types'
import { apiGet } from '../utils/api'

export default function HotRankingPage() {
  const [topics, setTopics] = useState<HotTopic[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    apiGet<HotTopicsResponse>('/v1/hot-topics', { signal: controller.signal })
      .then(response => setTopics(response.items))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setError(reason instanceof Error ? reason.message : '热点榜加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [retryKey])

  return (
    <div className="ranking-page">
      <header className="ranking-hero">
        <span className="page-eyebrow">HOT RADAR</span>
        <h1>AI 热点榜</h1>
        <p>过去 24 小时最热的 AI 事件，按精选报道、信源数量与讨论信号实时排序。</p>
      </header>

      {error && (
        <div className="feed-error" role="alert">
          <span>加载失败：{error}</span>
          <button className="btn" type="button" onClick={() => setRetryKey(value => value + 1)}>
            重试
          </button>
        </div>
      )}

      <section className="ranking-board" aria-label="AI 热点排行">
        <div className="ranking-board-head">
          <div>
            <span className="page-eyebrow">NOW</span>
            <h2>当前热点</h2>
          </div>
          <span>{topics.length} 个事件</span>
        </div>

        {loading && Array.from({ length: 6 }, (_, index) => (
          <div className="ranking-row" key={index} aria-hidden="true">
            <span className="skeleton ranking-skeleton-rank" />
            <div className="ranking-story">
              <span className="skeleton skeleton-line skeleton-line-md" />
              <span className="skeleton skeleton-line skeleton-line-sm" />
            </div>
          </div>
        ))}

        {!loading && topics.map((topic, index) => (
          <article className="ranking-row" key={`${topic.title}-${index}`}>
            <span className={`ranking-index${index < 3 ? ` is-top-${index + 1}` : ''}`}>
              {String(index + 1).padStart(2, '0')}
            </span>
            <div className="ranking-story">
              <div className="ranking-title-line">
                <h3>{topic.title}</h3>
                {statusFor(topic) && <span className="ranking-status">{statusFor(topic)}</span>}
              </div>
              <p>{topic.sourceNames.slice(0, 3).join(' · ') || '多信源聚合'} · {relativeTime(topic.latestAt)}</p>
            </div>
            <MiniTrend topic={topic} index={index} />
            <div className="ranking-heat">
              <strong>{heatValue(topic)}</strong>
              <span>热度值</span>
            </div>
          </article>
        ))}

        {!loading && !error && topics.length === 0 && (
          <div className="page-empty-state ranking-empty">
            <p className="page-empty-state-text">最近 24 小时还没有形成多信源热点事件。</p>
          </div>
        )}
      </section>

      <p className="ranking-note">
        榜单热度由质量评分、独立信源数与报道信号综合计算；同一事件的关联报道合并统计。
      </p>
    </div>
  )
}

function heatValue(topic: HotTopic): number {
  if (topic.score != null) return Math.round(topic.score)
  return topic.signalCount + topic.sourceCount * 4 + topic.reportCount * 2
}

function statusFor(topic: HotTopic): string | null {
  const ageHours = (Date.now() - new Date(topic.latestAt).getTime()) / 3_600_000
  if (ageHours <= 6) return '新'
  if (topic.sourceCount >= 3) return '发酵中'
  return null
}

function relativeTime(value: string): string {
  const hours = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 3_600_000))
  if (hours < 1) return '刚刚'
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

function MiniTrend({ topic, index }: { topic: HotTopic; index: number }) {
  const heat = heatValue(topic)
  const points = [
    34 + ((heat + index * 7) % 9),
    31 - Math.min(13, topic.sourceCount * 3),
    17 + ((topic.signalCount + index) % 11),
    21 + ((topic.reportCount * 2 + index) % 13),
    16 + ((heat + index * 3) % 15),
  ]
  const path = points.map((y, pointIndex) => `${pointIndex * 27},${y}`).join(' ')
  return (
    <svg className="ranking-trend" viewBox="0 0 110 48" role="img" aria-label="热度走势">
      <polyline points={path} fill="none" stroke="currentColor" strokeWidth="2" />
      <circle cx="108" cy={points[points.length - 1]} r="2.5" fill="var(--surface-card)" stroke="currentColor" strokeWidth="2" />
    </svg>
  )
}
