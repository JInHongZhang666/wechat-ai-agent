import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import type {
  Category,
  SelectionReviewFilter,
  SelectionReviewItem,
  SelectionReviewPage,
  SelectionReviewReason,
  SelectionReviewStatus,
} from '../types'
import { apiGet, apiPost, apiPut } from '../utils/api'

const CATEGORIES: Array<{ value: Category; label: string }> = [
  { value: 'ai-models', label: '模型发布' },
  { value: 'ai-products', label: '产品发布' },
  { value: 'industry', label: '行业动态' },
  { value: 'paper', label: '论文' },
  { value: 'tip', label: '技巧观点' },
]

const KEEP_REASONS: Array<{ value: SelectionReviewReason; label: string }> = [
  { value: 'timely_firsthand', label: '及时且接近一手信息' },
  { value: 'high_information_gain', label: '信息增量高' },
  { value: 'wechat_value', label: '对公众号读者有明确价值' },
  { value: 'other_keep', label: '其他保留原因' },
]

const REMOVE_REASONS: Array<{ value: SelectionReviewReason; label: string }> = [
  { value: 'low_information_gain', label: '信息增量低' },
  { value: 'weak_source', label: '来源可信度或一手性不足' },
  { value: 'outdated', label: '信息已过时' },
  { value: 'too_niche', label: '过于小众，读者价值有限' },
  { value: 'duplicate_or_followup', label: '重复报道或低增量跟进' },
  { value: 'other_remove', label: '其他移除原因' },
]

function reasonLabel(reason: SelectionReviewReason) {
  return [...KEEP_REASONS, ...REMOVE_REASONS].find(item => item.value === reason)?.label || reason
}

export default function SelectionReviewPanel() {
  const [status, setStatus] = useState<SelectionReviewStatus | null>(null)
  const [page, setPage] = useState<SelectionReviewPage | null>(null)
  const [filter, setFilter] = useState<SelectionReviewFilter>('pending')
  const [category, setCategory] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [selected, setSelected] = useState<boolean | null>(null)
  const [reasonCode, setReasonCode] = useState<SelectionReviewReason | ''>('')
  const [notes, setNotes] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [needsSeed, setNeedsSeed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const activeItem = page?.items[activeIndex] || null

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ status: filter, limit: '100' })
      if (category) params.set('category', category)
      const [nextStatus, nextPage] = await Promise.all([
        apiGet<SelectionReviewStatus>('/ops/evaluation/reviews/selection-audit-v1/status'),
        apiGet<SelectionReviewPage>(`/ops/evaluation/reviews/selection-audit-v1/items?${params.toString()}`),
      ])
      setStatus(nextStatus)
      setPage(nextPage)
      setActiveIndex(0)
      setNeedsSeed(false)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '精选复核队列加载失败'
      setError(message)
      setNeedsSeed(message.includes('尚未初始化') || message.includes('NOT_FOUND'))
    } finally {
      setLoading(false)
    }
  }, [category, filter])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (activeItem?.review) {
      setSelected(activeItem.review.selected)
      setReasonCode(activeItem.review.reason_code)
      setNotes(activeItem.review.notes)
      setReviewer(current => current || activeItem.review?.reviewer || '')
      return
    }
    setSelected(null)
    setReasonCode('')
    setNotes('')
  }, [activeItem?.candidate.article.id, activeItem?.review])

  const reasons = selected === true ? KEEP_REASONS : selected === false ? REMOVE_REASONS : []
  const completion = Math.round((status?.completion_rate || 0) * 100)
  const cannotSave = useMemo(
    () => selected === null || !reasonCode || !reviewer.trim() || Boolean(activeItem?.review),
    [activeItem?.review, reasonCode, reviewer, selected],
  )

  const initialize = async () => {
    setLoading(true)
    setError(null)
    try {
      await apiPost<SelectionReviewStatus>('/ops/evaluation/reviews/selection-audit-v1/seed')
      setNotice('已冻结精选复核队列；重复初始化不会更换样本。')
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '精选复核队列初始化失败')
      setLoading(false)
    }
  }

  const chooseDecision = (value: boolean) => {
    setSelected(value)
    setReasonCode('')
  }

  const save = async () => {
    if (!activeItem || cannotSave || selected === null || !reasonCode) {
      setError('请独立判断是否精选，并填写匹配的原因和复核人。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await apiPut<SelectionReviewItem>(
        `/ops/evaluation/reviews/selection-audit-v1/items/${encodeURIComponent(activeItem.candidate.article.id)}`,
        {
          selected,
          reason_code: reasonCode,
          notes: notes.trim(),
          reviewer: reviewer.trim(),
          reviewed_at: new Date().toISOString(),
          annotation_method: 'human',
        },
      )
      setNotice(`已保存第 ${activeItem.position} 条独立复核，审计记录不可覆盖。`)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '精选复核保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <section className="evaluation-progress" aria-label="精选复核进度">
        <div className="evaluation-progress-head">
          <strong>{status?.completed_count || 0} / {status?.total_count || status?.target_count || 40}</strong>
          <span>{completion}% · 还需 {status?.pending_count ?? 40} 条</span>
        </div>
        <div className="evaluation-progress-track">
          <span style={{ width: `${completion}%` }} />
        </div>
        <div className="evaluation-progress-meta">
          <span>已改判 {status?.changed_count || 0} 条</span>
          <span>独立复核 {status?.independent_review_count || 0} 条</span>
        </div>
      </section>

      <div className="evaluation-rubric" role="note">
        <strong>复核口径：</strong>相关不等于精选；精选需同时考虑信息增量、来源可信、时效性和公众号读者价值。
        请先独立判断，策略建议默认折叠。
      </div>

      <div className="evaluation-toolbar evaluation-review-toolbar">
        <select className="field" value={filter} onChange={event => setFilter(event.target.value as SelectionReviewFilter)}>
          <option value="pending">待复核</option>
          <option value="completed">已复核</option>
          <option value="all">全部</option>
        </select>
        <select className="field" value={category} onChange={event => setCategory(event.target.value)}>
          <option value="">全部分类</option>
          {CATEGORIES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <Button onClick={load} disabled={loading}>刷新</Button>
      </div>

      {error && (
        <div className="feed-error" role="alert">
          <span>{error}</span>
          {needsSeed && <Button onClick={initialize} disabled={loading}>初始化复核队列</Button>}
        </div>
      )}
      {notice && <div className="evaluation-notice" role="status">{notice}</div>}

      {!loading && !activeItem && !error && (
        <div className="page-empty-state">
          <div className="page-empty-state-icon">✓</div>
          <p className="page-empty-state-text">当前筛选下没有待处理复核；可切换到“已复核”查看审计记录。</p>
        </div>
      )}

      {activeItem && (
        <div className="evaluation-layout">
          <aside className="evaluation-list" aria-label="精选复核列表">
            <div className="evaluation-list-head">共 {page?.total || 0} 条</div>
            {page?.items.map((item, index) => (
              <button
                type="button"
                key={item.candidate.article.id}
                className={`evaluation-list-item${index === activeIndex ? ' is-active' : ''}`}
                onClick={() => {
                  setActiveIndex(index)
                  setNotice(null)
                }}
              >
                <span>#{item.position} · {item.candidate.article.category}</span>
                <strong>{item.candidate.article.title}</strong>
                <small>{item.review ? `已复核 · ${item.review.reviewer}` : '待独立复核'}</small>
              </button>
            ))}
          </aside>

          <section className="evaluation-editor">
            <article className="evaluation-source-card">
              <div className="evaluation-source-meta">
                <span>#{activeItem.position}</span>
                <span>{activeItem.candidate.article.source_name}</span>
                <span>{activeItem.candidate.article.tier || '未分级'}</span>
              </div>
              <h2>{activeItem.candidate.article.title}</h2>
              <p>{activeItem.candidate.article.summary || '该来源没有提供摘要，请打开原文后独立判断。'}</p>
              <a href={activeItem.candidate.article.source_url} target="_blank" rel="noreferrer">
                打开原始来源 →
              </a>
            </article>

            <details className="evaluation-suggestion">
              <summary>查看策略建议（完成独立判断后再看）</summary>
              <div>
                初始精选：{activeItem.initial_label.selected ? '是' : '否'} ·
                策略精选：{activeItem.candidate.suggestion.selected ? '是' : '否'} ·
                得分：{activeItem.candidate.suggestion.distribution_score ?? '无'}
              </div>
              {activeItem.candidate.suggestion.reason && <p>{activeItem.candidate.suggestion.reason}</p>}
            </details>

            {activeItem.review ? (
              <section className="evaluation-review-result" aria-label="已完成复核">
                <strong>复核结论：{activeItem.review.selected ? '保留精选' : '移出精选'}</strong>
                <span>原因：{reasonLabel(activeItem.review.reason_code)}</span>
                <span>复核人：{activeItem.review.reviewer}</span>
                <span>复核时间：{new Date(activeItem.review.reviewed_at).toLocaleString()}</span>
                {activeItem.review.notes && <p>{activeItem.review.notes}</p>}
              </section>
            ) : (
              <>
                <section className="evaluation-form" aria-label="精选独立复核表单">
                  <fieldset className="evaluation-choice evaluation-field-wide">
                    <legend>这条内容真正应该进入精选吗？</legend>
                    <div className="evaluation-choice-buttons">
                      <button type="button" className={selected === true ? 'is-active' : ''} aria-pressed={selected === true} onClick={() => chooseDecision(true)}>
                        是，保留精选
                      </button>
                      <button type="button" className={selected === false ? 'is-active is-no' : ''} aria-pressed={selected === false} onClick={() => chooseDecision(false)}>
                        否，移出精选
                      </button>
                    </div>
                  </fieldset>

                  <label className="evaluation-field evaluation-field-wide">
                    <span>主要原因</span>
                    <select className="field" value={reasonCode} onChange={event => setReasonCode(event.target.value as SelectionReviewReason)} disabled={selected === null}>
                      <option value="">{selected === null ? '请先作出独立判断' : '请选择一个主要原因'}</option>
                      {reasons.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </label>
                  <label className="evaluation-field">
                    <span>复核人</span>
                    <input className="field" value={reviewer} onChange={event => setReviewer(event.target.value)} placeholder="姓名或稳定代号" />
                  </label>
                  <label className="evaluation-field evaluation-field-wide">
                    <span>复核说明（可选）</span>
                    <textarea className="field" rows={3} value={notes} onChange={event => setNotes(event.target.value)} placeholder="记录边界情况、判断依据或改判理由" />
                  </label>
                </section>

                <div className="evaluation-save-row">
                  <span>{cannotSave ? '请完成判断、主要原因和复核人' : '复核完整；保存后形成不可覆盖的审计记录'}</span>
                  <Button variant="primary" onClick={save} disabled={saving || cannotSave}>
                    {saving ? '保存中…' : '保存并处理下一条'}
                  </Button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </>
  )
}
