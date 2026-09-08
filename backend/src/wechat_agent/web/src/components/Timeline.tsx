import type { TimelineItem as TimelineItemType } from '../types'
import TimelineItemComponent from './TimelineItem'

interface TimelineProps {
  items: TimelineItemType[]
  loading?: boolean
  timeBasis?: 'timeline' | 'published'
}

/** Group items by date and render as timeline days */
export default function Timeline({ items, loading, timeBasis = 'timeline' }: TimelineProps) {
  if (loading) {
    return (
      <div className="timeline">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="skeleton-card" aria-hidden="true">
            <div className="skeleton skeleton-line skeleton-line-sm" />
            <div className="skeleton skeleton-line skeleton-line-lg" />
            <div className="skeleton skeleton-line skeleton-line-md" />
          </div>
        ))}
      </div>
    )
  }

  const days = groupByDate(items, timeBasis)

  return (
    <div className="timeline">
      {days.map(day => (
        <div key={day.date} className="timeline-day">
          <div className="timeline-day-head">
            <div className="timeline-date">{day.label}</div>
            <div />
            <div />
          </div>
          <div className="timeline-day-items">
            {day.items.map((item, i) => (
              <TimelineItemComponent key={item.id} item={item} index={i} timeBasis={timeBasis} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

interface DayGroup {
  date: string
  label: string
  items: TimelineItemType[]
}

function groupByDate(
  items: TimelineItemType[],
  timeBasis: 'timeline' | 'published',
): DayGroup[] {
  const map = new Map<string, TimelineItemType[]>()
  for (const item of items) {
    const timestamp = timeBasis === 'published'
      ? item.publishedAt || item.discoveredAt
      : item.timelineAt || item.discoveredAt
    const d = timestamp.slice(0, 10)
    if (!map.has(d)) map.set(d, [])
    map.get(d)!.push(item)
  }
  return Array.from(map.entries()).map(([date, items]) => ({
    date,
    label: formatDateLabel(date),
    items,
  }))
}

function formatDateLabel(dateStr: string): string {
  try {
    const d = new Date(dateStr)
    const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
    return `${d.getMonth() + 1}月${d.getDate()}日 ${weekdays[d.getDay()]}`
  } catch {
    return dateStr
  }
}
