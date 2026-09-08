import type { TimelineItem as TimelineItemType } from '../types'
import Chip from './Chip'

interface TimelineItemProps {
  item: TimelineItemType
  index: number
  timeBasis?: 'timeline' | 'published'
}

const CATEGORY_LABELS: Record<string, string> = {
  'ai-models': '模型发布',
  'ai-products': '产品发布',
  'industry': '行业动态',
  'paper': '论文',
  'tip': '技巧观点',
}

export default function TimelineItem({ item, index, timeBasis = 'timeline' }: TimelineItemProps) {
  const time = formatTime(
    timeBasis === 'published'
      ? item.publishedAt || item.discoveredAt
      : item.timelineAt || item.discoveredAt,
  )

  return (
    <div className="timeline-item anim-feed" style={{ animationDelay: `${index * 50}ms` }} data-item-id={item.id}>
      {/* Time Column */}
      <div className="timeline-time">{time}</div>

      {/* Rail + Dot */}
      <div className="timeline-rail">
        <div className="timeline-dot" />
      </div>

      {/* Card */}
      <div className="timeline-card">
        {/* Head: source + score + badge */}
        <div className="timeline-card-head">
          <div className="timeline-head-left">
            <span className="timeline-source">{item.source.name}</span>
            {item.source.tier && (
              <Chip label={item.source.tier} variant={item.source.tier === 'T1' ? 't1' : 't2'} />
            )}
          </div>
          <div className="timeline-head-right">
            {item.score != null && (
              <span className="timeline-score">
                {item.score}
              </span>
            )}
            {item.category && (
              <Chip label={CATEGORY_LABELS[item.category] || item.category} variant="cyan" />
            )}
            {item.selected && (
              <span className="timeline-selected-badge">精选</span>
            )}
          </div>
        </div>

        {/* Title */}
        <a
          className="timeline-title timeline-title-link"
          href={item.links.original}
          target="_blank"
          rel="noreferrer"
        >
          {item.title}
        </a>

        {/* Summary (nullable per AIHOT spec) */}
        {item.summary && (
          <div className="timeline-summary">{item.summary}</div>
        )}

        {item.reason && (
          <div className="timeline-reason" title={item.reason}>
            推荐依据：{item.reason}
          </div>
        )}

      </div>
    </div>
  )
}

/** Format ISO timestamp to HH:MM */
function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return '--:--'
  }
}
