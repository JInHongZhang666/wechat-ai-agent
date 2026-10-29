import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DailyIndexResponse } from '../types/api-v1.generated'
import type { DailyReport, DailySection } from '../types'
import { apiGet } from '../utils/api'

type ReportPeriod = 'daily' | 'weekly' | 'monthly'

interface PeriodGroup {
  key: string
  label: string
  items: DailyIndexResponse['items']
}

const PERIOD_META: Record<ReportPeriod, { title: string; highlight: string; code: string; cadence: string }> = {
  daily: { title: 'AI 信息小屋日报', highlight: '今日看点', code: 'DAILY', cadence: '每早八时' },
  weekly: { title: 'AI 信息小屋周报', highlight: '本周看点', code: 'WEEKLY', cadence: '每周回顾' },
  monthly: { title: 'AI 信息小屋月报', highlight: '本月看点', code: 'MONTHLY', cadence: '每月回顾' },
}

export default function DailyPage() {
  const [index, setIndex] = useState<DailyIndexResponse['items']>([])
  const [report, setReport] = useState<DailyReport | null>(null)
  const [period, setPeriod] = useState<ReportPeriod>('daily')
  const [selectedArchiveKey, setSelectedArchiveKey] = useState<string | null>(null)
  const [selectedPeriodLabel, setSelectedPeriodLabel] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const reportCache = useRef(new Map<string, DailyReport>())
  const requestId = useRef(0)

  const getDailyReport = useCallback(async (date: string): Promise<DailyReport> => {
    const cached = reportCache.current.get(date)
    if (cached) return cached
    const nextReport = await apiGet<DailyReport>(`/v1/dailies/${date}`)
    reportCache.current.set(date, nextReport)
    return nextReport
  }, [])

  const loadReport = useCallback(async (date?: string) => {
    const currentRequest = ++requestId.current
    setLoading(true)
    setError(null)
    try {
      const nextReport = date
        ? await getDailyReport(date)
        : await apiGet<DailyReport>('/v1/dailies/latest')
      if (currentRequest !== requestId.current) return
      reportCache.current.set(nextReport.date, nextReport)
      setReport(nextReport)
      setSelectedArchiveKey(nextReport.date)
      setSelectedPeriodLabel(null)
    } catch (reason) {
      if (currentRequest !== requestId.current) return
      setError(reason instanceof Error ? reason.message : '日报加载失败')
    } finally {
      if (currentRequest === requestId.current) setLoading(false)
    }
  }, [getDailyReport])

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
        reportCache.current.set(latest.date, latest)
        setSelectedArchiveKey(latest.date)
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setError(reason instanceof Error ? reason.message : '日报加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

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

  const weeklyGroups = useMemo(
    () => buildPeriodGroups(index, 'weekly'),
    [index],
  )

  const monthlyGroups = useMemo(
    () => buildPeriodGroups(index, 'monthly'),
    [index],
  )

  const loadPeriodReport = useCallback(async (group: PeriodGroup, targetPeriod: Exclude<ReportPeriod, 'daily'>) => {
    const currentRequest = ++requestId.current
    setLoading(true)
    setError(null)
    try {
      const reports = await Promise.all(group.items.map(item => getDailyReport(item.date)))
      if (currentRequest !== requestId.current) return
      setReport(mergeDailyReports(reports, group, targetPeriod))
      setSelectedArchiveKey(group.key)
      setSelectedPeriodLabel(group.label)
    } catch (reason) {
      if (currentRequest !== requestId.current) return
      setError(reason instanceof Error ? reason.message : `${targetPeriod === 'weekly' ? '周报' : '月报'}加载失败`)
    } finally {
      if (currentRequest === requestId.current) setLoading(false)
    }
  }, [getDailyReport])

  const changePeriod = useCallback((nextPeriod: ReportPeriod) => {
    if (nextPeriod === period) return
    setPeriod(nextPeriod)
    if (nextPeriod === 'daily') {
      void loadReport(index[0]?.date)
      return
    }
    const firstGroup = nextPeriod === 'weekly' ? weeklyGroups[0] : monthlyGroups[0]
    if (firstGroup) void loadPeriodReport(firstGroup, nextPeriod)
  }, [index, loadPeriodReport, loadReport, monthlyGroups, period, weeklyGroups])

  const retryCurrentView = useCallback(() => {
    if (period === 'daily') {
      void loadReport(selectedArchiveKey || index[0]?.date)
      return
    }
    const groups = period === 'weekly' ? weeklyGroups : monthlyGroups
    const group = groups.find(candidate => candidate.key === selectedArchiveKey) || groups[0]
    if (group) void loadPeriodReport(group, period)
  }, [index, loadPeriodReport, loadReport, monthlyGroups, period, selectedArchiveKey, weeklyGroups])

  const periodGroups = period === 'weekly' ? weeklyGroups : monthlyGroups
  const periodMeta = PERIOD_META[period]
  const reportDateLabel = period === 'daily' && report
    ? formatFullDate(report.date)
    : selectedPeriodLabel

  return (
    <div className="daily-layout">
      <aside className="daily-archive" aria-label="日报归档">
        <div className="daily-period-tabs" aria-label="日报周期">
          {(['daily', 'weekly', 'monthly'] as const).map(value => (
            <button
              className={period === value ? 'is-active' : ''}
              key={value}
              type="button"
              aria-pressed={period === value}
              onClick={() => changePeriod(value)}
            >
              {value === 'daily' ? '日报' : value === 'weekly' ? '周报' : '月报'}
            </button>
          ))}
        </div>

        <div className="daily-archive-scroll">
          {period === 'daily'
            ? monthGroups.map(([month, items]) => (
              <section className="daily-month" key={month}>
                <div className="daily-month-head">
                  <strong>{formatMonth(month)}</strong>
                  <span>{items.length}</span>
                </div>
                {items.map(item => (
                  <button
                    type="button"
                    key={item.date}
                    className={`daily-archive-item${selectedArchiveKey === item.date ? ' is-active' : ''}`}
                    onClick={() => void loadReport(item.date)}
                  >
                    <strong>{Number(item.date.slice(8, 10))} 日</strong>
                    <span>{item.itemCount} 条精选 · {item.sourceCount} 个信源</span>
                  </button>
                ))}
              </section>
            ))
            : (
              <section className="daily-month daily-period-archive">
                <div className="daily-month-head">
                  <strong>{period === 'weekly' ? '自然周归档' : '自然月归档'}</strong>
                  <span>{periodGroups.length}</span>
                </div>
                {periodGroups.map(group => (
                <button
                  type="button"
                  key={group.key}
                  className={`daily-archive-item${selectedArchiveKey === group.key ? ' is-active' : ''}`}
                  onClick={() => void loadPeriodReport(group, period)}
                >
                  <strong>{group.label}</strong>
                  <span>{sumItems(group.items)} 条精选 · {group.items.length} 期日报</span>
                </button>
                ))}
              </section>
            )}
        </div>
      </aside>

      <article className="daily-report">
        {error && (
          <div className="feed-error" role="alert">
            <span>加载失败：{error}</span>
            <button className="btn" type="button" onClick={retryCurrentView}>
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
              <span className="daily-volume">VOL.{report.date.split('-').join('.')} · {report.itemCount} STORIES · AI CABIN {periodMeta.code}</span>
              <h1>{periodMeta.title}</h1>
              <p>{reportDateLabel} · {periodMeta.code} · {periodMeta.cadence}</p>
            </header>

            <section className="daily-highlights">
              <div className="daily-highlights-head">
                <h2>{periodMeta.highlight}</h2>
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

function buildPeriodGroups(
  items: DailyIndexResponse['items'],
  period: Exclude<ReportPeriod, 'daily'>,
): PeriodGroup[] {
  const groups = new Map<string, PeriodGroup>()
  for (const item of items) {
    const key = period === 'weekly' ? weekStart(item.date) : item.date.slice(0, 7)
    const groupKey = `${period}:${key}`
    const current = groups.get(groupKey) || {
      key: groupKey,
      label: period === 'weekly' ? formatWeekRange(key) : formatMonth(key),
      items: [],
    }
    current.items.push(item)
    groups.set(groupKey, current)
  }
  return Array.from(groups.values())
}

function weekStart(value: string): string {
  const date = new Date(`${value}T00:00:00Z`)
  const day = date.getUTCDay() || 7
  date.setUTCDate(date.getUTCDate() - day + 1)
  return date.toISOString().slice(0, 10)
}

function formatWeekRange(startValue: string): string {
  const start = new Date(`${startValue}T00:00:00Z`)
  const end = new Date(start)
  end.setUTCDate(end.getUTCDate() + 6)
  const startLabel = `${start.getUTCMonth() + 1} 月 ${start.getUTCDate()} 日`
  const endLabel = `${end.getUTCMonth() + 1} 月 ${end.getUTCDate()} 日`
  return `${start.getUTCFullYear()} 年 ${startLabel}—${endLabel}`
}

function sumItems(items: DailyIndexResponse['items']): number {
  return items.reduce((total, item) => total + item.itemCount, 0)
}

function mergeDailyReports(
  reports: DailyReport[],
  group: PeriodGroup,
  period: Exclude<ReportPeriod, 'daily'>,
): DailyReport {
  const sortedReports = [...reports].sort((left, right) => right.date.localeCompare(left.date))
  const latest = sortedReports[0]
  if (!latest) throw new Error('所选周期暂无可聚合的日报')

  const sectionMap = new Map<DailySection['slug'], DailySection>()
  const sectionItemIds = new Map<DailySection['slug'], Set<string>>()
  for (const daily of sortedReports) {
    for (const section of daily.sections) {
      if (!sectionMap.has(section.slug)) {
        sectionMap.set(section.slug, { ...section, count: 0, items: [] })
        sectionItemIds.set(section.slug, new Set())
      }
      const target = sectionMap.get(section.slug)!
      const ids = sectionItemIds.get(section.slug)!
      for (const item of section.items) {
        if (ids.has(item.id)) continue
        ids.add(item.id)
        target.items.push(item)
      }
      target.count = target.items.length
    }
  }

  const flashMap = new Map<string, DailyReport['flashes'][number]>()
  for (const daily of sortedReports) {
    for (const flash of daily.flashes) {
      if (!flashMap.has(flash.links.original)) flashMap.set(flash.links.original, flash)
    }
  }

  const sections = Array.from(sectionMap.values())
  const sourceNames = new Set(sections.flatMap(section => section.items.map(item => item.source.name)))
  const itemCount = sections.reduce((total, section) => total + section.items.length, 0)

  return {
    ...latest,
    lead: `${group.label}共汇总 ${group.items.length} 期日报，保留去重后的重点资讯。`,
    sections,
    flashes: Array.from(flashMap.values()),
    itemCount,
    sourceCount: sourceNames.size,
    contentHash: `${period}:${group.key}:${sortedReports.map(daily => daily.contentHash).join(':')}`,
  }
}
