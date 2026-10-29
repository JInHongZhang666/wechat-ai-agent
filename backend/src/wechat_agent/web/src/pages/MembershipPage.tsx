import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CreatorPageHeader,
  CreatorSectionTitle,
  CreatorStatus,
} from '../components/CreatorPageScaffold'
import { apiGet } from '../utils/api'
import type { CreatorProject, CreatorProjectList } from '../types'

const STAGE_NAMES: Record<CreatorProject['stage'], string> = {
  opportunity: '待采用',
  research: '研究中',
  evidence_ready: '证据就绪',
  brief_ready: '策划就绪',
  drafting: '写作中',
  review_blocked: '审校阻断',
  review_passed: '审校通过',
  exported: '已导出',
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

export default function MembershipPage() {
  const [projects, setProjects] = useState<CreatorProject[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    apiGet<CreatorProjectList>('/v1/creation/projects')
      .then(response => {
        if (!cancelled) setProjects(response.items)
      })
      .catch(reason => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '无法读取本地创作数据')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const metrics = useMemo(() => ({
    projects: projects.length,
    drafts: projects.filter(project => project.currentDraftId).length,
    passed: projects.filter(project => project.quality?.canExport).length,
    exports: projects.reduce((sum, project) => sum + project.exportRecords.length, 0),
  }), [projects])

  const recent = useMemo(
    () => [...projects].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, 8),
    [projects],
  )

  return (
    <div className="creator-tool-page membership-page">
      <CreatorPageHeader
        title="会员中心"
        description="查看当前运行版本、本地资源用量和最近的创作活动。"
        actions={<Link className="btn btn-default" to="/create/settings">偏好设置</Link>}
      />

      <section className="creator-surface membership-identity-band">
        <div className="membership-avatar" aria-hidden="true">AI</div>
        <div className="membership-identity-copy">
          <span>当前账户</span>
          <h2>AI 信息小屋 · 本地工作区</h2>
          <p>数据保存在当前设备，未接入商业计费系统。</p>
        </div>
        <div className="membership-plan-state">
          <span>当前版本</span>
          <strong>本地专业版</strong>
          <CreatorStatus tone="success">运行中</CreatorStatus>
        </div>
      </section>

      <section className="creator-surface membership-usage">
        <CreatorSectionTitle title="资源使用情况" description="以下数字来自当前本地创作项目，不包含虚构额度。" />
        <div className="membership-usage-rails">
          <UsageRail label="研究项目" value={metrics.projects} helper="已创建" ratio={Math.min(metrics.projects / 20, 1)} />
          <UsageRail label="文章草稿" value={metrics.drafts} helper="已有版本" ratio={metrics.projects ? metrics.drafts / metrics.projects : 0} />
          <UsageRail label="质量通过" value={metrics.passed} helper="可进入定稿" ratio={metrics.drafts ? metrics.passed / metrics.drafts : 0} tone="success" />
          <UsageRail label="本地导出" value={metrics.exports} helper="累计记录" ratio={Math.min(metrics.exports / 20, 1)} tone="violet" />
        </div>
      </section>

      <div className="membership-main-grid">
        <section className="creator-surface membership-capabilities">
          <CreatorSectionTitle title="当前能力" description="真实可用能力与接入状态一目了然。" />
          <div className="membership-capability-row membership-capability-head">
            <span>能力</span><span>状态</span><span>说明</span>
          </div>
          <CapabilityRow name="多源研究与证据整理" state="已启用" description="项目级来源与证据链" />
          <CapabilityRow name="文章生成与质量门" state="已启用" description="支持原创度与事实检查" />
          <CapabilityRow name="公众号富文本导出" state="已启用" description="人工定稿后可导出" />
          <CapabilityRow name="视频自动转写" state="待接入" description="当前只支持本地预览" warning />
          <CapabilityRow name="微信直接发布" state="待授权" description="暂不声称已连接公众号" warning />
        </section>

        <aside className="creator-surface membership-local-note">
          <CreatorSectionTitle title="关于本地版" />
          <p>本项目当前没有订阅扣费、自动续费或云存储。会员中心用于汇总能力边界与本地使用状态。</p>
          <dl>
            <div><dt>数据位置</dt><dd>本机 SQLite 与浏览器存储</dd></div>
            <div><dt>模型调用</dt><dd>遵循项目运行时配置</dd></div>
            <div><dt>发布方式</dt><dd>人工确认后导出</dd></div>
          </dl>
        </aside>
      </div>

      <section className="creator-surface membership-history">
        <CreatorSectionTitle title="最近使用记录" description="按项目更新时间排序。" action={<Link to="/create/drafts">查看工作台</Link>} />
        {error ? <p className="creator-inline-notice is-error">{error}</p> : null}
        <div className="creator-data-table membership-history-table">
          <div className="creator-data-row creator-data-head"><span>更新时间</span><span>内容名称</span><span>阶段</span><span>导出</span></div>
          {loading ? <p className="creator-table-empty">正在读取本地记录…</p> : recent.length === 0 ? <p className="creator-table-empty">还没有创作项目。</p> : recent.map(project => (
            <Link className="creator-data-row" to={`/create/drafts?project=${project.id}`} key={project.id}>
              <span>{formatDate(project.updatedAt)}</span>
              <strong>{project.topicTitle}</strong>
              <span>{STAGE_NAMES[project.stage]}</span>
              <span>{project.exportRecords.length} 次</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  )
}

function UsageRail({ label, value, helper, ratio, tone = 'accent' }: { label: string; value: number; helper: string; ratio: number; tone?: 'accent' | 'success' | 'violet' }) {
  return (
    <div className="membership-usage-item">
      <div><span>{label}</span><strong>{value}</strong></div>
      <div className="membership-progress" aria-label={`${label} ${Math.round(ratio * 100)}%`}><i className={`is-${tone}`} style={{ width: `${Math.max(3, Math.round(ratio * 100))}%` }} /></div>
      <small>{helper}</small>
    </div>
  )
}

function CapabilityRow({ name, state, description, warning = false }: { name: string; state: string; description: string; warning?: boolean }) {
  return (
    <div className="membership-capability-row">
      <strong>{name}</strong>
      <CreatorStatus tone={warning ? 'warning' : 'success'}>{state}</CreatorStatus>
      <span>{description}</span>
    </div>
  )
}
