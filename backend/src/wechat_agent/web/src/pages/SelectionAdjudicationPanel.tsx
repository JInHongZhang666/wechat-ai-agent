import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import type {
  DoubleReviewStatus,
  SelectionAdjudicationDecision,
  SelectionConflictItem,
  SelectionConflictPage,
  SelectionReviewFilter,
  SelectionReviewReason,
} from '../types'
import { apiGet, apiPut } from '../utils/api'

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

function ReviewAnswer({
  label,
  decision,
}: {
  label: string
  decision: SelectionConflictItem['reviewer_a']
}) {
  return (
    <article className={`evaluation-conflict-answer${decision.selected ? ' is-keep' : ' is-remove'}`}>
      <span>{label}</span>
      <strong>{decision.selected ? '保留精选' : '移出精选'}</strong>
      <p>{reasonLabel(decision.reason_code)}</p>
      <small>{decision.reviewer} · {new Date(decision.reviewed_at).toLocaleString()}</small>
      {decision.notes && <blockquote>{decision.notes}</blockquote>}
    </article>
  )
}

export default function SelectionAdjudicationPanel() {
  const [status, setStatus] = useState<DoubleReviewStatus | null>(null)
  const [page, setPage] = useState<SelectionConflictPage | null>(null)
  const [filter, setFilter] = useState<SelectionReviewFilter>('pending')
  const [activeIndex, setActiveIndex] = useState(0)
  const [selected, setSelected] = useState<boolean | null>(null)
  const [reasonCode, setReasonCode] = useState<SelectionReviewReason | ''>('')
  const [adjudicator, setAdjudicator] = useState('')
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const activeItem = page?.items[activeIndex] || null
  const isLocked = status?.lifecycle === 'awaiting_second_reviewer'
    || status?.lifecycle === 'second_reviewing'

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const nextStatus = await apiGet<DoubleReviewStatus>(
        '/ops/evaluation/reviews/selection-audit-v1/double-review/status',
      )
      setStatus(nextStatus)
      if (nextStatus.lifecycle === 'awaiting_second_reviewer' || nextStatus.lifecycle === 'second_reviewing') {
        setPage(null)
        setActiveIndex(0)
        return
      }
      const params = new URLSearchParams({ status: filter, limit: '100' })
      const nextPage = await apiGet<SelectionConflictPage>(
        `/ops/evaluation/reviews/selection-audit-v1/adjudication/items?${params.toString()}`,
      )
      setPage(nextPage)
      setActiveIndex(0)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '冲突裁决加载失败')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (activeItem?.adjudication) {
      setSelected(activeItem.adjudication.selected)
      setReasonCode(activeItem.adjudication.reason_code)
      setAdjudicator(current => current || activeItem.adjudication?.adjudicator || '')
      setNotes(activeItem.adjudication.notes)
      return
    }
    setSelected(null)
    setReasonCode('')
    setNotes('')
  }, [activeItem?.article.id, activeItem?.adjudication])

  const reasons = selected === true
    ? KEEP_REASONS
    : selected === false
      ? REMOVE_REASONS
      : []
  const cannotSave = useMemo(
    () => selected === null || !reasonCode || !adjudicator.trim() || Boolean(activeItem?.adjudication),
    [activeItem?.adjudication, adjudicator, reasonCode, selected],
  )

  const chooseDecision = (value: boolean) => {
    setSelected(value)
    setReasonCode('')
  }

  const save = async () => {
    if (!activeItem || cannotSave || selected === null || !reasonCode) {
      setError('请重新作出最终选择，并填写匹配原因和裁决人。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const decision: SelectionAdjudicationDecision = {
        selected,
        reason_code: reasonCode,
        notes: notes.trim(),
        adjudicator: adjudicator.trim(),
        adjudicated_at: new Date().toISOString(),
        annotation_method: 'human',
      }
      await apiPut<SelectionConflictItem>(
        `/ops/evaluation/reviews/selection-audit-v1/adjudication/items/${encodeURIComponent(activeItem.article.id)}`,
        decision,
      )
      setNotice(`第 ${activeItem.position} 条冲突已裁决；结果和审计记录均不可覆盖。`)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '冲突裁决保存失败')
      await load()
    } finally {
      setSaving(false)
    }
  }

  if (!loading && status && isLocked) {
    const completion = Math.round(status.completion_rate * 100)
    return (
      <section className="evaluation-locked-card" aria-label="冲突裁决尚未开放">
        <span className="evaluation-lifecycle-badge">裁决区已锁定</span>
        <h2>先完成全部第二人盲审</h2>
        <p>为避免后续冲突影响第二复核人的独立判断，40 条全部提交前不会展示任何 A/B 分歧。</p>
        <div className="evaluation-progress-track"><span style={{ width: `${completion}%` }} /></div>
        <strong>{status.second_completed_count} / {status.total_count} · 还需 {status.second_pending_count} 条</strong>
        {error && <div className="feed-error" role="alert"><span>{error}</span></div>}
      </section>
    )
  }

  return (
    <>
      <section className="evaluation-progress" aria-label="冲突裁决进度">
        <div className="evaluation-progress-head">
          <strong>{status?.adjudicated_count || 0} / {status?.conflict_count || 0}</strong>
          <span>待裁决 {status?.pending_adjudication_count ?? 0} 条</span>
        </div>
        <div className="evaluation-progress-track">
          <span style={{ width: `${status?.conflict_count ? (status.adjudicated_count / status.conflict_count) * 100 : 100}%` }} />
        </div>
        <div className="evaluation-progress-meta">
          <span className="evaluation-lifecycle-badge">{status?.lifecycle || '加载中'}</span>
          <span>A/B 一致 {status?.consensus_count || 0} 条</span>
          <span>A/B 冲突 {status?.conflict_count || 0} 条</span>
        </div>
      </section>

      <div className="evaluation-rubric" role="note">
        <strong>裁决规则：</strong>这里只显示 A/B 真正分歧。请阅读来源和两份理由后重新作出最终判断，不提供批量采纳；第二复核人不能担任裁决人。
      </div>

      <div className="evaluation-toolbar evaluation-adjudication-toolbar">
        <select className="field" value={filter} onChange={event => setFilter(event.target.value as SelectionReviewFilter)}>
          <option value="pending">待裁决</option>
          <option value="completed">已裁决</option>
          <option value="all">全部</option>
        </select>
        <Button onClick={load} disabled={loading}>刷新</Button>
      </div>

      {error && <div className="feed-error" role="alert"><span>{error}</span></div>}
      {notice && <div className="evaluation-notice" role="status">{notice}</div>}

      {!loading && !activeItem && !error && (
        <div className="page-empty-state">
          <div className="page-empty-state-icon">✓</div>
          <p className="page-empty-state-text">
            {status?.lifecycle === 'complete'
              ? '全部冲突已裁决。现在可通过受门禁的 CLI 生成 Gold v1.2 和一致性报告。'
              : '当前筛选下没有待处理冲突。'}
          </p>
        </div>
      )}

      {activeItem && (
        <div className="evaluation-layout">
          <aside className="evaluation-list" aria-label="精选冲突列表">
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
                <small>{item.adjudication ? '已完成裁决' : 'A/B 意见冲突'}</small>
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
              <p>{activeItem.article.summary || '该来源没有提供摘要，请打开原文判断。'}</p>
              <a href={activeItem.article.source_url} target="_blank" rel="noreferrer">打开原始来源 →</a>
            </article>

            <div className="evaluation-conflict-grid" aria-label="两位复核人的答案">
              <ReviewAnswer label="Reviewer A" decision={activeItem.reviewer_a} />
              <ReviewAnswer label="Reviewer B" decision={activeItem.reviewer_b} />
            </div>

            {activeItem.adjudication ? (
              <section className="evaluation-review-result" aria-label="已完成裁决">
                <strong>最终结论：{activeItem.adjudication.selected ? '保留精选' : '移出精选'}</strong>
                <span>原因：{reasonLabel(activeItem.adjudication.reason_code)}</span>
                <span>裁决人：{activeItem.adjudication.adjudicator}</span>
                <span>时间：{new Date(activeItem.adjudication.adjudicated_at).toLocaleString()}</span>
                {activeItem.adjudication.notes && <p>{activeItem.adjudication.notes}</p>}
              </section>
            ) : (
              <>
                <section className="evaluation-form" aria-label="精选冲突裁决表单">
                  <fieldset className="evaluation-choice evaluation-field-wide">
                    <legend>你的最终判断是什么？</legend>
                    <div className="evaluation-choice-buttons">
                      <button type="button" className={selected === true ? 'is-active' : ''} aria-pressed={selected === true} onClick={() => chooseDecision(true)}>最终保留精选</button>
                      <button type="button" className={selected === false ? 'is-active is-no' : ''} aria-pressed={selected === false} onClick={() => chooseDecision(false)}>最终移出精选</button>
                    </div>
                  </fieldset>
                  <label className="evaluation-field evaluation-field-wide">
                    <span>最终主要原因</span>
                    <select className="field" value={reasonCode} onChange={event => setReasonCode(event.target.value as SelectionReviewReason)} disabled={selected === null}>
                      <option value="">{selected === null ? '请先重新作出最终判断' : '请选择一个主要原因'}</option>
                      {reasons.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </label>
                  <label className="evaluation-field">
                    <span>裁决人（不能是 Reviewer B）</span>
                    <input className="field" value={adjudicator} onChange={event => setAdjudicator(event.target.value)} placeholder="真实姓名或稳定代号" />
                  </label>
                  <label className="evaluation-field evaluation-field-wide">
                    <span>裁决说明（可选）</span>
                    <textarea className="field" rows={3} value={notes} onChange={event => setNotes(event.target.value)} placeholder="记录采用该结论的依据" />
                  </label>
                </section>
                <div className="evaluation-save-row">
                  <span>{cannotSave ? '请完成最终判断、原因和裁决人' : '保存后将更新 Gold 的 selected 与审计备注，且不可覆盖'}</span>
                  <Button variant="primary" onClick={save} disabled={saving || cannotSave}>
                    {saving ? '保存中…' : '保存最终裁决'}
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
