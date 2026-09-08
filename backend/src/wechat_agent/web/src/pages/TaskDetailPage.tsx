import { useState, useEffect, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import Button from '../components/Button'
import Chip from '../components/Chip'
import Skeleton from '../components/Skeleton'
import { apiGet, apiPost, apiPut, apiExport } from '../utils/api'
import type { CreatorProject, Task, TopicCandidate, EvidencePack, Draft, PaginatedResponse } from '../types'

type WorkflowStep = 'topics' | 'evidence' | 'draft' | 'quality'

const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', collecting: '采集中', ranking: '评分中',
  review_ready: '待处理', drafting: '草稿中', completed: '已完成', failed: '失败',
}

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [task, setTask] = useState<Task | null>(null)
  const [topics, setTopics] = useState<TopicCandidate[]>([])
  const [selectedTopic, setSelectedTopic] = useState<TopicCandidate | null>(null)
  const [evidence, setEvidence] = useState<EvidencePack | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [step, setStep] = useState<WorkflowStep>('topics')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editBody, setEditBody] = useState('')
  const [editTitle, setEditTitle] = useState('')

  // Load task status
  useEffect(() => {
    if (!id) return
    let cancelled = false
    const load = async () => {
      try {
        const t = await apiGet<Task>(`/tasks/${id}`)
        if (!cancelled) setTask(t)
        if (t.status === 'review_ready' || t.status === 'completed' || t.status === 'drafting') {
          const resp = await apiGet<PaginatedResponse<TopicCandidate>>(`/tasks/${id}/topics`)
          if (!cancelled) setTopics(resp.items)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载任务失败')
      }
    }
    load()
    return () => { cancelled = true }
  }, [id])

  // Poll task status
  useEffect(() => {
    if (!task || !id) return
    if (['queued', 'collecting', 'ranking'].includes(task.status)) {
      const timer = setInterval(async () => {
        try {
          const t = await apiGet<Task>(`/tasks/${id}`)
          setTask(t)
          if (['review_ready', 'completed'].includes(t.status)) {
            clearInterval(timer)
            const resp = await apiGet<PaginatedResponse<TopicCandidate>>(`/tasks/${id}/topics`)
            setTopics(resp.items)
          }
          if (t.status === 'failed') clearInterval(timer)
        } catch { /* keep polling */ }
      }, 2000)
      return () => clearInterval(timer)
    }
  }, [task, id])

  const handleSelectTopic = useCallback(async (topic: TopicCandidate) => {
    setSelectedTopic(topic)
    setLoading(true)
    setStep('evidence')
    setDraft(null)
    try {
      const pack = await apiGet<EvidencePack>(`/topics/${topic.id}/evidence`)
      setEvidence(pack)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载证据包失败')
    }
    setLoading(false)
  }, [])

  const handleGenerateDraft = useCallback(async () => {
    if (!selectedTopic) return
    setLoading(true)
    setStep('draft')
    try {
      const d = await apiPost<Draft>(`/topics/${selectedTopic.id}/draft`)
      setDraft(d)
      setEditBody(d.body)
      setEditTitle(d.selected_title)
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成草稿失败')
    }
    setLoading(false)
  }, [selectedTopic])

  const handleStartCreation = useCallback(async () => {
    if (!selectedTopic) return
    setLoading(true)
    setError(null)
    try {
      const project = await apiPost<CreatorProject>('/v1/creation/projects', {
        topicId: selectedTopic.id,
      })
      navigate(`/create/drafts?project=${project.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建内容生产项目失败')
    } finally {
      setLoading(false)
    }
  }, [navigate, selectedTopic])

  const handleUpdateDraft = useCallback(async () => {
    if (!draft) return
    setLoading(true)
    try {
      const updated = await apiPut<Draft>(`/drafts/${draft.id}`, {
        selected_title: editTitle,
        body: editBody,
      })
      setDraft(updated)
      setStep('quality')
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新草稿失败')
    }
    setLoading(false)
  }, [draft, editTitle, editBody])

  const handleExport = useCallback(async (format: string) => {
    if (!draft) return
    try {
      const content = await apiExport(`/drafts/${draft.id}/export`, format)
      const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `draft-${draft.id}.${format === 'html' ? 'html' : 'md'}`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      alert('导出失败')
    }
  }, [draft])

  if (!id) return <div className="page-empty-state"><p>无效的任务 ID</p></div>
  if (error) return <div className="page-empty-state"><p>错误：{error}</p></div>

  const taskLoading = !task || ['queued', 'collecting', 'ranking'].includes(task.status)

  return (
    <>
      <div className="breadcrumb">
        <Link to="/create/tasks">任务列表</Link>
        <span>/</span>
        <span style={{ color: 'var(--text-0)' }}>任务详情</span>
      </div>

      <div className="page-header-compact">
        <h1 className="page-title" style={{ fontSize: '18px' }}>
          {task ? `${task.mode === 'demo' ? '🎭 Demo' : '🌐 Live'} · 任务 ${task.id.slice(0, 8)}` : '加载中...'}
        </h1>
        {task && (
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginTop: '8px' }}>
            <div className="task-status-bar">
              <div className={`task-status-bar-dot ${task.status}`} />
              <span className="task-status-bar-label">{STATUS_LABELS[task.status] || task.status}</span>
              {task.collected_article_count > 0 && (
                <span style={{ color: 'var(--text-2)' }}>
                  · {task.collected_article_count} 文章 · {task.window_days}天窗口
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Topics */}
      <section>
        <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-0)', marginBottom: '12px', fontFamily: 'var(--font-body)' }}>
          📝 选题列表 ({topics.length})
        </h3>

        {taskLoading && topics.length === 0 ? (
          <Skeleton card />
        ) : topics.length === 0 ? (
          <div className="page-empty-state" style={{ minHeight: '20vh' }}>
            <p className="page-empty-state-text">
              {task?.status === 'failed' ? '任务执行失败' : '等待任务完成...'}
            </p>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: '8px' }}>
            {topics.map(topic => (
              <button
                key={topic.id}
                type="button"
                onClick={() => handleSelectTopic(topic)}
                style={{
                  width: '100%', textAlign: 'left', padding: '12px 16px',
                  border: `1px solid ${selectedTopic?.id === topic.id ? 'var(--accent-cyan)' : 'var(--border)'}`,
                  borderRadius: 'var(--radius)', cursor: 'pointer',
                  background: selectedTopic?.id === topic.id ? 'var(--surface-0)' : 'var(--surface-card)',
                  transition: 'var(--theme-transition)',
                  fontFamily: 'var(--font-body)',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-0)' }}>
                    {topic.title}
                  </span>
                  <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                    <Chip label={topic.category} variant="cyan" />
                    <span style={{ fontSize: '12px', color: 'var(--text-2)', fontFamily: 'var(--font-mono)' }}>
                      {topic.heat_score.value.toFixed(1)}
                    </span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* Evidence Panel */}
      {step === 'evidence' && selectedTopic && (
        <section style={{
          marginTop: '16px', padding: '16px', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', background: 'var(--surface-card)',
        }}>
          <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-0)', marginBottom: '12px' }}>
            🔍 证据包：{selectedTopic.title}
          </h3>
          {loading ? <Skeleton /> : evidence ? (
            <>
              <div style={{ display: 'grid', gap: '8px' }}>
                {evidence.facts.map((fact, i) => (
                  <div key={i} style={{
                    padding: '10px 14px', borderRadius: 'var(--radius-sm)',
                    background: 'color-mix(in srgb, var(--accent-emerald) 6%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--accent-emerald) 20%, transparent)',
                  }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' }}>
                      <span className="status-dot status-dot-ok" />
                      <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-0)' }}>
                        已形成事实卡 · {fact.source_urls.length} 个来源
                      </span>
                    </div>
                    <p style={{ fontSize: '13px', color: 'var(--text-1)', margin: 0, lineHeight: 1.5 }}>
                      {fact.claim}
                    </p>
                  </div>
                ))}
                {evidence.conflicts.map((conflict, i) => (
                  <div key={`conflict-${i}`} style={{
                    padding: '10px 14px', borderRadius: 'var(--radius-sm)',
                    background: 'color-mix(in srgb, var(--accent-amber) 8%, transparent)',
                    border: '1px solid color-mix(in srgb, var(--accent-amber) 25%, transparent)',
                  }}>
                    <strong style={{ fontSize: '13px', color: 'var(--text-0)' }}>信息冲突：{conflict.subject}</strong>
                    <p style={{ fontSize: '13px', color: 'var(--text-1)', margin: '4px 0 0' }}>
                      {conflict.claims.join(' / ')}
                    </p>
                  </div>
                ))}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '12px' }}>
                <Button variant="primary" onClick={handleStartCreation} disabled={loading || !evidence.ready_for_draft}>
                  ✦ 进入策划与写作
                </Button>
                <Button onClick={handleGenerateDraft} disabled={loading}>
                  快速研究草稿（旧流程）
                </Button>
              </div>
            </>
          ) : null}
        </section>
      )}

      {/* Draft Panel */}
      {step === 'draft' && draft && (
        <section style={{
          marginTop: '16px', padding: '16px', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', background: 'var(--surface-card)',
        }}>
          <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-0)', marginBottom: '12px' }}>
            📄 研究草稿 v{draft.version}
          </h3>

          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '12px', color: 'var(--text-2)', display: 'block', marginBottom: '6px' }}>
              选择标题
            </label>
            <div style={{ display: 'grid', gap: '6px' }}>
              {draft.titles.map((t, i) => (
                <label key={i} style={{
                  display: 'flex', gap: '8px', alignItems: 'center', cursor: 'pointer',
                  padding: '8px 12px', borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${editTitle === t ? 'var(--accent-cyan)' : 'var(--border)'}`,
                  background: editTitle === t ? 'var(--surface-0)' : 'transparent',
                }}>
                  <input type="radio" name="title" value={t} checked={editTitle === t}
                    onChange={() => setEditTitle(t)}
                    style={{ accentColor: 'var(--accent-cyan)' }} />
                  <span style={{ fontSize: '13px', color: 'var(--text-0)' }}>{t}</span>
                </label>
              ))}
            </div>
          </div>

          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '12px', color: 'var(--text-2)', display: 'block', marginBottom: '6px' }}>
              正文
            </label>
            <textarea className="field" value={editBody} onChange={e => setEditBody(e.target.value)}
              rows={12} style={{ resize: 'vertical', fontFamily: 'var(--font-body)', lineHeight: 1.7 }} />
          </div>

          <div style={{ display: 'flex', gap: '8px' }}>
            <Button variant="primary" onClick={handleUpdateDraft} disabled={loading}>
              ✅ 提交审核
            </Button>
          </div>
        </section>
      )}

      {/* Quality Report */}
      {step === 'quality' && draft && (
        <section style={{
          marginTop: '16px', padding: '16px', border: '1px solid var(--border)',
          borderRadius: 'var(--radius)', background: 'var(--surface-card)',
        }}>
          <h3 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-0)', marginBottom: '12px' }}>
            ✅ 质量检查报告
          </h3>
          {!draft.quality.can_export ? (
            <div style={{
              padding: '10px 14px', borderRadius: 'var(--radius-sm)',
              background: 'color-mix(in srgb, var(--accent-rose) 8%, transparent)',
              border: '1px solid color-mix(in srgb, var(--accent-rose) 25%, transparent)',
              marginBottom: '12px',
            }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--accent-rose-fg)' }}>
                ⚠️ 存在阻断项，需修改后才能导出
              </span>
            </div>
          ) : (
            <div style={{
              padding: '10px 14px', borderRadius: 'var(--radius-sm)',
              background: 'color-mix(in srgb, var(--accent-emerald) 8%, transparent)',
              border: '1px solid color-mix(in srgb, var(--accent-emerald) 25%, transparent)',
              marginBottom: '12px',
            }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--accent-emerald-fg)' }}>
                ✅ 质量检查通过，可以导出
              </span>
            </div>
          )}
          <div style={{ display: 'grid', gap: '6px' }}>
            {draft.quality.issues.map((issue, i) => (
              <div key={i} style={{
                display: 'flex', gap: '8px', alignItems: 'center',
                padding: '6px 10px', borderRadius: 'var(--radius-sm)',
                background: issue.blocking ? 'color-mix(in srgb, var(--accent-rose) 5%, transparent)' : 'var(--surface-0)',
              }}>
                <span className={`status-dot ${issue.blocking ? 'status-dot-bad' : 'status-dot-ok'}`} />
                <span style={{ fontSize: '12px', color: 'var(--text-1)' }}>
                  [{issue.code}] {issue.message}
                </span>
                {issue.blocking && <Chip label="阻断" variant="bad" />}
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
            <Button onClick={() => handleExport('markdown')} disabled={!draft.quality.can_export}>📥 导出 Markdown</Button>
            <Button onClick={() => handleExport('html')} disabled={!draft.quality.can_export}>📥 导出 HTML</Button>
            <Button onClick={() => handleExport('text')} disabled={!draft.quality.can_export}>📥 导出纯文本</Button>
          </div>
        </section>
      )}
    </>
  )
}
