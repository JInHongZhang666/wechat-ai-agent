import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import type {
  BlindSelectionReviewItem,
  BlindSelectionReviewPage,
  Category,
  DoubleReviewStatus,
  ReviewParticipant,
  SelectionReviewFilter,
  SelectionReviewReason,
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

const ALL_REASONS = [...KEEP_REASONS, ...REMOVE_REASONS]

function reasonLabel(reason: SelectionReviewReason) {
  return ALL_REASONS.find(item => item.value === reason)?.label || reason
}

export default function SecondSelectionReviewPanel() {
  const [status, setStatus] = useState<DoubleReviewStatus | null>(null)
  const [page, setPage] = useState<BlindSelectionReviewPage | null>(null)
  const [reviewer, setReviewer] = useState('')
  const [filter, setFilter] = useState<SelectionReviewFilter>('pending')
  const [category, setCategory] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [selected, setSelected] = useState<boolean | null>(null)
  const [reasonCode, setReasonCode] = useState<SelectionReviewReason | ''>('')
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const activeItem = page?.items[activeIndex] || null

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const nextStatus = await apiGet<DoubleReviewStatus>(
        '/ops/evaluation/reviews/selection-audit-v1/double-review/status',
      )
      setStatus(nextStatus)
      if (!nextStatus.second_reviewer) {
        setPage(null)
        setActiveIndex(0)
        return
      }
      const params = new URLSearchParams({ status: filter, limit: '100' })
      if (category) params.set('category', category)
      const nextPage = await apiGet<BlindSelectionReviewPage>(
        `/ops/evaluation/reviews/selection-audit-v1/second-review/items?${params.toString()}`,
      )
      setPage(nextPage)
      setActiveIndex(0)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '第二人盲审加载失败')
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
      return
    }
    setSelected(null)
    setReasonCode('')
    setNotes('')
  }, [activeItem?.article.id, activeItem?.review])

  const reasons = selected === true
    ? KEEP_REASONS
    : selected === false
      ? REMOVE_REASONS
      : []
  const completion = Math.round((status?.completion_rate || 0) * 100)
  const cannotSave = useMemo(
    () => selected === null || !reasonCode || !status?.second_reviewer || Boolean(activeItem?.review),
    [activeItem?.review, reasonCode, selected, status?.second_reviewer],
  )

  const claim = async () => {
    if (!reviewer.trim()) {
      setError('请输入真实姓名或长期使用的稳定代号。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await apiPost<ReviewParticipant>(
        '/ops/evaluation/reviews/selection-audit-v1/second-reviewer/claim',
        { reviewer: reviewer.trim(), claimed_at: new Date().toISOString() },
      )
      setNotice('第二复核人已绑定；该身份不可更换，请独立完成全部样本。')
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '第二复核人绑定失败')
    } finally {
      setSaving(false)
    }
  }

  const chooseDecision = (value: boolean) => {
    setSelected(value)
    setReasonCode('')
  }

  const save = async () => {
    if (!activeItem || cannotSave || selected === null || !reasonCode || !status?.second_reviewer) {
      setError('请先独立判断是否精选，并选择匹配的主要原因。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await apiPut<BlindSelectionReviewItem>(
        `/ops/evaluation/reviews/selection-audit-v1/second-review/items/${encodeURIComponent(activeItem.article.id)}`,
        {
          selected,
          reason_code: reasonCode,
          notes: notes.trim(),
          reviewer: status.second_reviewer,
          reviewed_at: new Date().toISOString(),
          annotation_method: 'human',
        },
      )
      setNotice(`第 ${activeItem.position} 条盲审已保存；答案不可覆盖，也不会直接修改 Gold。`)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '第二人盲审保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (!loading && status && !status.second_reviewer) {
    return (
      <section className="evaluation-claim-card" aria-label="绑定第二复核人">
        <span className="evaluation-lifecycle-badge">等待第二复核人</span>
        <h2>开始第二人盲审</h2>
        <p>你将看不到首轮标签、第一复核人的答案、当前 Gold 和策略建议，只依据来源内容独立判断。</p>
        <div className="evaluation-blind-warning" role="note">
          必须由另一位真人完成；姓名或稳定代号一经绑定不能更换，也不能与初始标注人或第一复核人相同。
        </div>
        <label className="evaluation-field">
          <span>第二复核人</span>
          <input
            className="field"
            value={reviewer}
            onChange={event => setReviewer(event.target.value)}
            placeholder="真实姓名或稳定代号"
            autoComplete="off"
          />
        </label>
        {error && <div className="feed-error" role="alert"><span>{error}</span></div>}
        <Button variant="primary" onClick={claim} disabled={saving || !reviewer.trim()}>
          {saving ? '绑定中…' : '确认身份并开始盲审'}
        </Button>
      </section>
    )
  }

  return (
    <>
      <section className="evaluation-progress" aria-label="第二人盲审进度">
        <div className="evaluation-progress-head">
          <strong>{status?.second_completed_count || 0} / {status?.total_count || 40}</strong>
          <span>{completion}% · 还需 {status?.second_pending_count ?? 40} 条</span>
        </div>
        <div className="evaluation-progress-track"><span style={{ width: `${completion}%` }} /></div>
        <div className="evaluation-progress-meta">
          <span className="evaluation-lifecycle-badge">{status?.lifecycle || '加载中'}</span>
          <span>复核人：{status?.second_reviewer || '未绑定'}</span>
          <span>提交不会直接修改 Gold</span>
        </div>
      </section>

      <div className="evaluation-blind-warning" role="note">
        当前为真正盲审：页面不会显示首轮判断、第一复核答案、当前 Gold、分发得分或策略建议。
      </div>

      <div className="evaluation-toolbar evaluation-review-toolbar">
        <select className="field" value={filter} onChange={event => setFilter(event.target.value as SelectionReviewFilter)}>
          <option value="pending">待盲审</option>
          <option value="completed">已盲审</option>
          <option value="all">全部</option>
        </select>
        <select className="field" value={category} onChange={event => setCategory(event.target.value)}>
          <option value="">全部分类</option>
          {CATEGORIES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <Button onClick={load} disabled={loading}>刷新</Button>
      </div>

      {error && <div className="feed-error" role="alert"><span>{error}</span></div>}
      {notice && <div className="evaluation-notice" role="status">{notice}</div>}

      {!loading && !activeItem && !error && (
        <div className="page-empty-state">
          <div className="page-empty-state-icon">✓</div>
          <p className="page-empty-state-text">当前筛选下没有待处理盲审。</p>
        </div>
      )}

      {activeItem && (
        <div className="evaluation-layout">
          <aside className="evaluation-list" aria-label="第二人盲审列表">
            <div className="evaluation-list-head">共 {page?.total || 0} 条</div>
            {page?.items.map((item, index) => (
              <button
                type="button"
                key={item.article.id}
                className={`evaluation-list-item${index === activeIndex ? ' is-active' : ''}`}
                onClick={() => {
                  setActiveIndex(index)
                  setNotice(null)
                }}
              >
                <span>#{item.position} · {item.article.category}</span>
                <strong>{item.article.title}</strong>
                <small>{item.review ? '已完成盲审' : '待独立判断'}</small>
              </button>
            ))}
          </aside>

          <section className="evaluation-editor">
            <article className="evaluation-source-card">
              <div className="evaluation-source-meta">
                <span>#{activeItem.position}</span>
                <span>{activeItem.article.source_name}</span>
                <span>{activeItem.article.tier || '未分级'}</span>
                <span>{activeItem.article.category}</span>
              </div>
              <h2>{activeItem.article.title}</h2>
              <p>{activeItem.article.summary || '该来源没有提供摘要，请打开原文后独立判断。'}</p>
              <a href={activeItem.article.source_url} target="_blank" rel="noreferrer">打开原始来源 →</a>
            </article>

            {activeItem.review ? (
              <section className="evaluation-review-result" aria-label="已完成盲审">
                <strong>你的结论：{activeItem.review.selected ? '保留精选' : '移出精选'}</strong>
                <span>原因：{reasonLabel(activeItem.review.reason_code)}</span>
                <span>时间：{new Date(activeItem.review.reviewed_at).toLocaleString()}</span>
                {activeItem.review.notes && <p>{activeItem.review.notes}</p>}
              </section>
            ) : (
              <>
                <section className="evaluation-form" aria-label="第二人盲审表单">
                  <fieldset className="evaluation-choice evaluation-field-wide">
                    <legend>仅根据来源内容，这条真正应该进入精选吗？</legend>
                    <div className="evaluation-choice-buttons">
                      <button type="button" className={selected === true ? 'is-active' : ''} aria-pressed={selected === true} onClick={() => chooseDecision(true)}>是，保留精选</button>
                      <button type="button" className={selected === false ? 'is-active is-no' : ''} aria-pressed={selected === false} onClick={() => chooseDecision(false)}>否，移出精选</button>
                    </div>
                  </fieldset>
                  <label className="evaluation-field evaluation-field-wide">
                    <span>主要原因</span>
                    <select className="field" value={reasonCode} onChange={event => setReasonCode(event.target.value as SelectionReviewReason)} disabled={selected === null}>
                      <option value="">{selected === null ? '请先作出独立判断' : '请选择一个主要原因'}</option>
                      {reasons.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </label>
                  <label className="evaluation-field evaluation-field-wide">
                    <span>说明（可选）</span>
                    <textarea className="field" rows={3} value={notes} onChange={event => setNotes(event.target.value)} placeholder="记录边界情况和判断依据" />
                  </label>
                </section>
                <div className="evaluation-save-row">
                  <span>{cannotSave ? '请完成判断和主要原因' : '保存后不可覆盖；答案不会直接修改 Gold'}</span>
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
