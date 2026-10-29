import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import Button from '../components/Button'
import {
  CreatorEmptyState,
  CreatorPageHeader,
  CreatorSectionTitle,
  CreatorStatus,
} from '../components/CreatorPageScaffold'
import { apiGet } from '../utils/api'
import type { CreatorProject, CreatorProjectList } from '../types'

type QueueFilter = 'all' | 'ready' | 'exported'

const STAGE_NAMES: Record<CreatorProject['stage'], string> = {
  opportunity: '待研究',
  research: '研究中',
  evidence_ready: '证据就绪',
  brief_ready: '策划就绪',
  drafting: '写作中',
  review_blocked: '需要修改',
  review_passed: '待导出',
  exported: '已导出',
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

export default function WechatPublishingPage() {
  const [projects, setProjects] = useState<CreatorProject[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [filter, setFilter] = useState<QueueFilter>('all')
  const [search, setSearch] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let cancelled = false
    apiGet<CreatorProjectList>('/v1/creation/projects')
      .then(response => {
        if (cancelled) return
        setProjects(response.items)
        setSelectedId(current => current || response.items[0]?.id || '')
      })
      .catch(reason => {
        if (!cancelled) setNotice(reason instanceof Error ? reason.message : '无法读取本地草稿')
      })
    return () => { cancelled = true }
  }, [])

  const filtered = useMemo(() => projects.filter(project => {
    if (filter === 'ready' && !project.quality?.canExport) return false
    if (filter === 'exported' && project.exportRecords.length === 0) return false
    return project.topicTitle.toLowerCase().includes(search.trim().toLowerCase())
  }), [projects, filter, search])

  const selected = projects.find(project => project.id === selectedId) || filtered[0] || null

  const copyTitle = async () => {
    if (!selected) return
    await navigator.clipboard.writeText(selected.topicTitle)
    setNotice('标题已复制。')
  }

  const nextDays = useMemo(() => Array.from({ length: 5 }, (_, index) => {
    const date = new Date()
    date.setDate(date.getDate() + index)
    return {
      key: date.toISOString().slice(0, 10),
      weekday: new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(date),
      date: new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(date),
    }
  }), [])

  return (
    <div className="creator-tool-page wechat-page">
      <CreatorPageHeader
        title="公众号"
        description="管理本地草稿、质量状态与发布准备；连接公众号前不会伪装成已发布。"
        actions={<Link className="btn btn-primary" to="/create/drafts">新建文章</Link>}
      />

      <section className="creator-surface wechat-account-band">
        <div className="wechat-account-mark" aria-hidden="true">公众号</div>
        <div>
          <span>账号连接</span>
          <h2>AI 信息小屋</h2>
          <p>当前为本地导出模式，尚未获得微信公众号平台授权。</p>
        </div>
        <CreatorStatus tone="warning">未连接</CreatorStatus>
        <Button disabled>连接公众号</Button>
      </section>

      {notice ? <p className="creator-inline-notice">{notice}</p> : null}

      <div className="wechat-publishing-grid">
        <section className="creator-surface wechat-queue">
          <CreatorSectionTitle title="内容草稿" description="来源于真实创作项目，点击行查看准备状态。" />
          <div className="wechat-queue-tools">
            <div className="creator-mode-tabs" role="tablist" aria-label="草稿筛选">
              <button type="button" className={filter === 'all' ? 'is-active' : ''} onClick={() => setFilter('all')}>全部 {projects.length}</button>
              <button type="button" className={filter === 'ready' ? 'is-active' : ''} onClick={() => setFilter('ready')}>质量通过</button>
              <button type="button" className={filter === 'exported' ? 'is-active' : ''} onClick={() => setFilter('exported')}>已导出</button>
            </div>
            <input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索草稿标题" />
          </div>
          <div className="creator-data-table wechat-draft-table">
            <div className="creator-data-row creator-data-head"><span>标题</span><span>阶段</span><span>质量</span><span>更新时间</span></div>
            {filtered.length === 0 ? <p className="creator-table-empty">没有符合条件的草稿。</p> : filtered.map(project => (
              <button type="button" className={`creator-data-row${selected?.id === project.id ? ' is-selected' : ''}`} key={project.id} onClick={() => setSelectedId(project.id)}>
                <strong>{project.topicTitle}</strong>
                <span>{STAGE_NAMES[project.stage]}</span>
                <span>{project.quality?.canExport ? '可导出' : project.quality ? `${project.quality.blockingIssueCount} 个阻断` : '待检查'}</span>
                <span>{formatDate(project.updatedAt)}</span>
              </button>
            ))}
          </div>
        </section>

        <aside className="creator-surface wechat-calendar">
          <CreatorSectionTitle title="本周发布计划" description="排期服务尚未接入。" />
          <div className="wechat-calendar-days">
            {nextDays.map(day => <div key={day.key}><strong>{day.weekday}</strong><span>{day.date}</span><small>暂无排期</small></div>)}
          </div>
        </aside>

        <aside className="creator-surface wechat-inspector">
          <CreatorSectionTitle title="草稿详情" />
          {!selected ? <CreatorEmptyState title="选择一篇草稿" description="草稿的质量与导出状态会显示在这里。" /> : (
            <>
              <div className="wechat-cover-placeholder" aria-hidden="true"><span>AI CABIN</span></div>
              <h3>{selected.topicTitle}</h3>
              <dl>
                <div><dt>当前阶段</dt><dd>{STAGE_NAMES[selected.stage]}</dd></div>
                <div><dt>质量状态</dt><dd>{selected.quality?.canExport ? '质量门通过' : '尚不可导出'}</dd></div>
                <div><dt>草稿版本</dt><dd>{selected.draftVersions.length} 个</dd></div>
                <div><dt>导出记录</dt><dd>{selected.exportRecords.length} 次</dd></div>
                <div><dt>最后更新</dt><dd>{formatDate(selected.updatedAt)}</dd></div>
              </dl>
              <div className="wechat-inspector-actions">
                <Link className="btn btn-primary" to={`/create/drafts?project=${selected.id}`}>打开草稿</Link>
                <Button onClick={copyTitle}>复制标题</Button>
                <Button disabled>安排发布</Button>
              </div>
              <small className="creator-control-note">直接发布需要先完成微信公众平台授权。</small>
            </>
          )}
        </aside>
      </div>
    </div>
  )
}
