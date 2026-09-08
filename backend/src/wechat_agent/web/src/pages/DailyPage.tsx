import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DailyIndexResponse } from '../types/api-v1.generated'
import type { DailyReport } from '../types'
import { apiGet } from '../utils/api'

export default function DailyPage() {
  const [index, setIndex] = useState<DailyIndexResponse['items']>([])
  const [report, setReport] = useState<DailyReport | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)

  const loadReport = useCallback(async (date?: string) => {
    setLoading(true)
    setError(null)
    try {
      const nextReport = await apiGet<DailyReport>(date ? `/v1/dailies/${date}` : '/v1/dailies/latest')
      setReport(nextReport)
      setSelectedDate(nextReport.date)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '日报加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    Promise.all([
      apiGet<DailyIndexResponse>('/v1/dailies?limit=30', { signal: controller.signal }),
      apiGet<DailyReport>('/v1/dailies/latest', { signal: controller.signal }),
    ])
      .then(([dailyIndex, latest]) => {
        setIndex(dailyIndex.items)
        setReport(latest)
        setSelectedDate(latest.date)
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setError(reason instanceof Error ? reason.message : '日报加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [retryKey])

  const monthGroups = useMemo(() => {
    const groups = new Map<string, DailyIndexResponse['items']>()
    for (const item of index) {
      const month = item.date.slice(0, 7)
      const current = groups.get(month) || []
      current.push(item)
      groups.set(month, current)
    }
    return Array.from(groups.entries())
  }, [index])

  return (
    <div className="daily-layout">
      <aside className="daily-archive" aria-label="日报归档">
        <div className="daily-period-tabs" aria-label="日报周期">
          <button className="is-active" type="button">日报</button>
          <button type="button" disabled>周报</button>
          <button type="button" disabled>月报</button>
        </div>

        <div className="daily-archive-scroll">
          {monthGroups.map(([month, items]) => (
            <section className="daily-month" key={month}>
              <div className="daily-month-head">
                <strong>{formatMonth(month)}</strong>
                <span>{items.length}</span>
              </div>
              {items.map(item => (
                <button
                  type="button"
                  key={item.date}
                  className={`daily-archive-item${selectedDate === item.date ? ' is-active' : ''}`}
                  onClick={() => void loadReport(item.date)}
                >
                  <strong>{Number(item.date.slice(8, 10))} 日</strong>
                  <span>{item.itemCount} 条精选 · {item.sourceCount} 个信源</span>
                </button>
              ))}
            </section>
          ))}
        </div>
      </aside>

      <article className="daily-report">
        {error && (
          <div className="feed-error" role="alert">
            <span>加载失败：{error}</span>
            <button className="btn" type="button" onClick={() => setRetryKey(value => value + 1)}>
              重试
            </button>
          </div>
        )}

        {loading && (
          <div className="daily-report-loading">
            <span className="skeleton skeleton-line skeleton-line-sm" />
            <span className="skeleton skeleton-line skeleton-line-lg" />
            <span className="skeleton skeleton-line skeleton-line-md" />
          </div>
        )}

        {!loading && report && (
          <>
            <header className="daily-report-head">
              <span className="daily-volume">VOL.{report.date.split('-').join('.')} · {report.itemCount} STORIES · AI CABIN DAILY</span>
              <h1>AI 信息小屋日报</h1>
              <p>{formatFullDate(report.date)} · DAILY · 每早八时</p>
            </header>

            <section className="daily-highlights">
              <div className="daily-highlights-head">
                <h2>今日看点</h2>
                <span>{report.itemCount} 篇报道 · 约 {readingMinutes(report.itemCount)} 分钟</span>
              </div>
              {report.sections.map((section, indexInReport) => (
                <a className="daily-highlight-row" href={`#daily-${section.slug}`} key={section.slug}>
                  <span>{String(indexInReport + 1).padStart(2, '0')}</span>
                  <div>
                    <strong>{section.label}</strong>
                    <p>{section.items[0]?.title || '本栏目今日暂无头条'}</p>
                  </div>
                  <em>{section.count}</em>
                </a>
              ))}
            </section>

            {report.sections.map((section, sectionIndex) => (
              <section className="daily-editorial-section" id={`daily-${section.slug}`} key={section.slug}>
                <div className="daily-editorial-head">
                  <h2>
                    <span>{String(sectionIndex + 1).padStart(2, '0')}</span>
                    {section.label}
                  </h2>
                  <em>{section.count} 篇</em>
                </div>
                {section.items.map(item => (
                  <article className="daily-story" key={item.id}>
                    <a href={item.links.original} target="_blank" rel="noreferrer">{item.title}</a>
                    <div className="daily-story-meta">
                      {item.source.tier && <span>{item.source.tier}</span>}
                      <span>{item.source.name}</span>
                    </div>
                    {item.summary && <p>{item.summary}</p>}
                  </article>
                ))}
              </section>
            ))}

            {report.flashes.length > 0 && (
              <section className="daily-editorial-section">
                <div className="daily-editorial-head">
                  <h2><span>+</span> 快讯</h2>
                  <em>{report.flashes.length} 条</em>
                </div>
                <ul className="daily-flashes">
                  {report.flashes.map(flash => (
                    <li key={`${flash.title}-${flash.links.original}`}>
                      <a href={flash.links.original} target="_blank" rel="noreferrer">{flash.title}</a>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </article>
    </div>
  )
}

function formatMonth(value: string): string {
  const [year, month] = value.split('-')
  return `${year} 年 ${Number(month)} 月`
}

function formatFullDate(value: string): string {
  const date = new Date(`${value}T00:00:00+08:00`)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(date)
}

function readingMinutes(itemCount: number): number {
  return Math.max(1, Math.ceil(itemCount * 0.45))
}
