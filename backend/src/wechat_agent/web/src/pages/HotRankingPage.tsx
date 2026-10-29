import { useEffect, useState } from 'react'
import type { HotTopic, HotTopicsResponse } from '../types'
import { apiGet } from '../utils/api'

interface RankingTopic {
  id: string
  title: string
  url: string | null
  sourceNames: string[]
  sourceCount: number
  signalCount: number
  reportCount: number
  latestAt: string
  score: number | null
}

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

  const rankingTopics = buildRankingTopics(topics)

  return (
    <div className="ranking-page">
      <header className="ranking-hero">
        <span className="page-eyebrow">HOT RADAR</span>
        <h1>AI 热点榜</h1>
        <p>统计最近 24 小时全部有效资讯，呈现被最多报道与独立信源共同覆盖的 AI 核心话题。</p>
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
          <span>{rankingTopics.length} 个事件</span>
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

        {!loading && rankingTopics.map((topic, index) => (
          <article className="ranking-row" key={topic.id}>
            <span className={`ranking-index${index < 3 ? ` is-top-${index + 1}` : ''}`}>
              {String(index + 1).padStart(2, '0')}
            </span>
            <div className="ranking-story">
              <div className="ranking-title-line">
                <h3>
                  {topic.url ? (
                    <a href={topic.url} target={isExternalUrl(topic.url) ? '_blank' : undefined} rel="noreferrer">
                      {topic.title}
                    </a>
                  ) : topic.title}
                </h3>
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

        {!loading && !error && rankingTopics.length === 0 && (
          <div className="page-empty-state ranking-empty">
            <p className="page-empty-state-text">最近 24 小时还没有可统计的热点资讯。</p>
          </div>
        )}
      </section>

      <p className="ranking-note">
        热点统计不要求通过精选门槛；热度值等于相关报道数量，独立信源数单独统计，质量评分只用于选择代表标题和同热度排序。
      </p>
    </div>
  )
}

function heatValue(topic: RankingTopic): number {
  return topic.reportCount
}

function statusFor(topic: RankingTopic): string | null {
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

function MiniTrend({ topic, index }: { topic: RankingTopic; index: number }) {
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

function buildRankingTopics(topics: HotTopic[]): RankingTopic[] {
  return topics.map((topic, index) => ({
    id: `topic-${index}-${topic.title}`,
    title: topic.title,
    url: topic.links?.story ?? null,
    sourceNames: topic.sourceNames,
    sourceCount: topic.sourceCount,
    signalCount: topic.signalCount,
    reportCount: topic.reportCount,
    latestAt: topic.latestAt,
    score: topic.score ?? null,
  }))
}

function isExternalUrl(value: string): boolean {
  return /^https?:\/\//i.test(value)
}
