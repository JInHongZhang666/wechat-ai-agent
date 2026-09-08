import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '../components/Button'
import Skeleton from '../components/Skeleton'
import { apiGet, apiPost, apiPut } from '../utils/api'
import type { AccountProfile, ContentOpportunity, CreationReplayReport, CreationRuntime, CreationRuntimeProbe, CreatorProject, OpportunityList } from '../types'

const LIST_FIELDS: Array<keyof Pick<AccountProfile,
  'contentPillars' | 'excludedTopics' | 'toneKeywords' | 'titlePreferences' |
  'forbiddenPhrases' | 'acceptedSourceTiers' | 'verifiedExperienceInventory' | 'fixedViewpoints'>> = [
  'contentPillars', 'excludedTopics', 'toneKeywords', 'titlePreferences',
  'forbiddenPhrases', 'acceptedSourceTiers', 'verifiedExperienceInventory', 'fixedViewpoints',
]

function csv(values: string[]) { return values.join('、') }
function split(value: string) { return value.split(/[、,，\n]/).map(item => item.trim()).filter(Boolean) }

export default function ContentOpportunitiesPage() {
  const navigate = useNavigate()
  const [profile, setProfile] = useState<AccountProfile | null>(null)
  const [edit, setEdit] = useState<AccountProfile | null>(null)
  const [items, setItems] = useState<ContentOpportunity[]>([])
  const [runtime, setRuntime] = useState<CreationRuntime | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    const [nextProfile, opportunities, nextRuntime] = await Promise.all([
      apiGet<AccountProfile>('/v1/creation/profile'),
      apiGet<OpportunityList>('/v1/creation/opportunities'),
      apiGet<CreationRuntime>('/v1/creation/runtime'),
    ])
    setProfile(nextProfile)
    setEdit(nextProfile)
    setItems(opportunities.items)
    setRuntime(nextRuntime)
  }

  useEffect(() => {
    load().catch(reason => setError(reason instanceof Error ? reason.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label); setError('')
    try { await action() } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${label}失败`)
    } finally { setBusy('') }
  }

  const saveProfile = () => {
    if (!profile || !edit) return
    void run('保存画像', async () => {
      const saved = await apiPut<AccountProfile>('/v1/creation/profile', {
        version: profile.version,
        accountName: edit.accountName,
        positioning: edit.positioning,
        targetAudience: edit.targetAudience,
        contentPillars: edit.contentPillars,
        excludedTopics: edit.excludedTopics,
        toneKeywords: edit.toneKeywords,
        targetCharacters: edit.targetCharacters,
        paragraphLimit: edit.paragraphLimit,
        titlePreferences: edit.titlePreferences,
        forbiddenPhrases: edit.forbiddenPhrases,
        acceptedSourceTiers: edit.acceptedSourceTiers,
        citationStyle: edit.citationStyle,
        riskTolerance: edit.riskTolerance,
        verifiedExperienceInventory: edit.verifiedExperienceInventory,
        fixedViewpoints: edit.fixedViewpoints,
      })
      setProfile(saved); setEdit(saved)
      const opportunities = await apiGet<OpportunityList>('/v1/creation/opportunities')
      setItems(opportunities.items)
    })
  }

  const adopt = (item: ContentOpportunity) => void run('采用机会', async () => {
    await apiPost(`/v1/creation/opportunities/${item.id}/decision`, { decision: 'adopted' })
    const project = await apiPost<CreatorProject>('/v1/creation/projects', { topicId: item.sourceId })
    navigate(`/create/drafts?project=${project.id}`)
  })

  const ignore = (item: ContentOpportunity) => void run('忽略机会', async () => {
    await apiPost(`/v1/creation/opportunities/${item.id}/decision`, {
      decision: 'ignored', reason: '当前不进入内容排期',
    })
    await load()
  })

  const runReplay = () => void run('质量回放', async () => {
    const report = await apiPost<CreationReplayReport>('/v1/creation/replays?mode=current&limit=20')
    const nextRuntime = await apiGet<CreationRuntime>('/v1/creation/runtime')
    setRuntime({ ...nextRuntime, latestReplay: report })
  })

  const probeRuntime = () => void run('验证模型', async () => {
    const probe = await apiPost<CreationRuntimeProbe>('/v1/creation/runtime/probe')
    const nextRuntime = await apiGet<CreationRuntime>('/v1/creation/runtime')
    setRuntime(nextRuntime)
    if (!probe.ok) throw new Error(probe.message)
  })

  if (loading || !edit) return <Skeleton card />

  return (
    <div className="opportunity-page">
      <header className="creation-hero">
        <span className="page-eyebrow">CONTENT OPPORTUNITIES</span>
        <h1>创作机会与账号画像</h1>
        <p>分发热度和创作价值分开计算。这里决定什么值得写、为什么值得写，以及文章必须遵守的账号边界。</p>
      </header>
      {error && <div className="creation-alert is-error">{error}</div>}

      <section className="creation-card generation-runtime-card">
        <div className="creation-card-head"><div><span>GENERATION</span><h3>生成引擎与质量基准</h3></div><div className="creation-card-actions"><Button onClick={probeRuntime} disabled={Boolean(busy) || !runtime?.configured}>验证模型</Button><Button onClick={runReplay} disabled={Boolean(busy) || !runtime?.verified}>运行 20 条 AI 影子回放</Button></div></div>
        <div className="runtime-grid">
          <div><span>当前状态</span><strong>{({ healthy: '真实 AI 已验证', degraded: 'AI 异常 · 自动回退', untested: 'AI 已配置 · 待验证', unconfigured: '安全规则回退' } as const)[runtime?.healthState || 'unconfigured']}</strong><small>{runtime?.configured ? runtime.model : '尚未配置模型密钥'}</small></div>
          <div><span>生成阶段</span><strong>{runtime?.generationStages.join(' → ') || 'outline → section → editor'}</strong><small>{runtime?.promptVersions.length || 0} 个 Prompt 版本已登记</small></div>
          <div><span>真实模型调用</span><strong>{runtime?.recentCalls.successful || 0} 成功 · {runtime?.recentCalls.errors || 0} 失败</strong><small>{runtime?.recentCalls.modelAttempts || 0} 次当前模型尝试 · {runtime?.recentCalls.fallbacks || 0} 次规则回退</small></div>
          <div><span>分发处理预算</span><strong>{runtime?.distributionProcessing.available ? '可调用' : runtime?.distributionProcessing.enabled ? '已熔断/限额' : '未启用'} · {runtime?.distributionProcessing.callsToday || 0}/{runtime?.distributionProcessing.dailyCallLimit || 0}</strong><small>{runtime?.distributionProcessing.reason || '增量去重后才会计入调用次数'}</small></div>
          <div><span>最近回放</span><strong>{runtime?.latestReplay ? `${runtime.latestReplay.actualMode.toUpperCase()} · ${Math.round(runtime.latestReplay.passRate * 100)}% 通过` : '尚未运行'}</strong><small>{runtime?.latestReplay ? `${runtime.latestReplay.sampleCount} 条 · 来源覆盖 ${Math.round(runtime.latestReplay.sourceCoverageRate * 100)}%` : '不会创建正式草稿'}</small></div>
        </div>
        {runtime?.lastError && <p className="runtime-note is-error">{runtime.lastError.message}（{runtime.lastError.code} · {runtime.lastError.stage}）</p>}
        {runtime?.healthState === 'untested' && <p className="runtime-note">模型已配置，但还没有成功调用证据。请先点击“验证模型”，通过后才能运行 AI 影子回放。</p>}
        {!runtime?.configured && <p className="runtime-note">当前不会伪装成 AI 成稿：系统明确使用确定性规则稿。安全配置 DeepSeek Key 并重启后，需要先完成真实连通性验证。</p>}
      </section>

      <section className="creation-card profile-card">
        <div className="creation-card-head"><div><span>PROFILE</span><h3>账号画像 v{profile?.version}</h3></div><Button variant="primary" onClick={saveProfile} disabled={Boolean(busy)}>保存新版本</Button></div>
        <div className="profile-grid">
          <label><span>账号名称</span><input value={edit.accountName} onChange={e => setEdit({ ...edit, accountName: e.target.value })} /></label>
          <label><span>风险偏好</span><select value={edit.riskTolerance} onChange={e => setEdit({ ...edit, riskTolerance: e.target.value as AccountProfile['riskTolerance'] })}><option value="low">低风险</option><option value="medium">中等</option><option value="high">较高</option></select></label>
          <label className="is-wide"><span>账号定位</span><textarea rows={2} value={edit.positioning} onChange={e => setEdit({ ...edit, positioning: e.target.value })} /></label>
          <label className="is-wide"><span>目标读者</span><textarea rows={2} value={edit.targetAudience} onChange={e => setEdit({ ...edit, targetAudience: e.target.value })} /></label>
          <label><span>目标字数</span><input type="number" value={edit.targetCharacters} onChange={e => setEdit({ ...edit, targetCharacters: Number(e.target.value) })} /></label>
          <label><span>段落上限</span><input type="number" value={edit.paragraphLimit} onChange={e => setEdit({ ...edit, paragraphLimit: Number(e.target.value) })} /></label>
          {LIST_FIELDS.map(field => <label key={field} className={field === 'verifiedExperienceInventory' || field === 'fixedViewpoints' ? 'is-wide' : ''}><span>{({ contentPillars: '内容支柱', excludedTopics: '不写范围', toneKeywords: '文风关键词', titlePreferences: '标题偏好', forbiddenPhrases: '禁用表达', acceptedSourceTiers: '可接受来源', verifiedExperienceInventory: '已验证真实经验', fixedViewpoints: '固定观点' } as Record<string, string>)[field]}</span><textarea rows={2} value={csv(edit[field])} onChange={e => setEdit({ ...edit, [field]: split(e.target.value) })} /></label>)}
          <label className="is-wide"><span>引用规范</span><textarea rows={2} value={edit.citationStyle} onChange={e => setEdit({ ...edit, citationStyle: e.target.value })} /></label>
        </div>
      </section>

      <section className="opportunity-section">
        <div className="opportunity-head"><div><span className="page-eyebrow">TOP 10</span><h2>当前创作机会</h2></div><span>双评分分离 · 最多 10 条</span></div>
        {items.length === 0 ? <div className="page-empty-state"><p>当前没有可评分选题，请先运行采集任务。</p></div> : <div className="opportunity-list">{items.map((item, index) => (
          <article key={item.id} className="opportunity-card">
            <div className="opportunity-rank">{String(index + 1).padStart(2, '0')}</div>
            <div className="opportunity-body"><div className="opportunity-title"><h3>{item.title}</h3><span>{item.status}</span></div><p>{item.recommendedAngle}</p><strong>读者收益：{item.readerBenefit}</strong><div className="opportunity-scores">{Object.entries(item.scores).map(([key, value]) => <span key={key}>{key} {Math.round(value)}</span>)}</div>{item.blockers.length > 0 && <div className="opportunity-blockers">{item.blockers.map(value => <span key={value}>{value}</span>)}</div>}</div>
            <div className="opportunity-action"><strong>{item.creationScore.toFixed(1)}</strong><span>创作分</span><Button variant="primary" onClick={() => adopt(item)} disabled={Boolean(busy) || item.blockers.length > 0}>采用</Button><Button onClick={() => ignore(item)} disabled={Boolean(busy)}>忽略</Button></div>
          </article>
        ))}</div>}
      </section>
    </div>
  )
}
