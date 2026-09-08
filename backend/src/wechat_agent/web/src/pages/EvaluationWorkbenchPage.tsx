import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import SelectionReviewPanel from './SelectionReviewPanel'
import SecondSelectionReviewPanel from './SecondSelectionReviewPanel'
import SelectionAdjudicationPanel from './SelectionAdjudicationPanel'
import type {
  Category,
  EvaluationItem,
  EvaluationPage,
  EvaluationStatus,
  EvaluationStatusFilter,
  GoldLabel,
} from '../types'
import { apiGet, apiPost, apiPut } from '../utils/api'

const CATEGORIES: Array<{ value: Category; label: string }> = [
  { value: 'ai-models', label: '模型发布' },
  { value: 'ai-products', label: '产品发布' },
  { value: 'industry', label: '行业动态' },
  { value: 'paper', label: '论文' },
  { value: 'tip', label: '技巧观点' },
]

type BooleanField =
  | 'relevant'
  | 'selected'
  | 'primary_source_correct'
  | 'creation_worthy'
  | 'evidence_sufficient'

interface DraftLabel {
  relevant: boolean | null
  category: Category | ''
  selected: boolean | null
  primary_source_correct: boolean | null
  story_key: string
  creation_worthy: boolean | null
  evidence_sufficient: boolean | null
  annotator: string
  data_license: string
  notes: string
}

const EMPTY_DRAFT: DraftLabel = {
  relevant: null,
  category: '',
  selected: null,
  primary_source_correct: null,
  story_key: '',
  creation_worthy: null,
  evidence_sufficient: null,
  annotator: '',
  data_license: 'public-source-metadata-only',
  notes: '',
}

function draftFromItem(item: EvaluationItem | null, defaults: DraftLabel): DraftLabel {
  if (!item?.label) {
    return {
      ...EMPTY_DRAFT,
      annotator: defaults.annotator,
      data_license: defaults.data_license,
    }
  }
  return {
    relevant: item.label.relevant,
    category: item.label.category,
    selected: item.label.selected,
    primary_source_correct: item.label.primary_source_correct,
    story_key: item.label.story_key || '',
    creation_worthy: item.label.creation_worthy,
    evidence_sufficient: item.label.evidence_sufficient,
    annotator: item.label.annotator,
    data_license: item.label.data_license,
    notes: item.label.notes,
  }
}

function BooleanChoice({
  label,
  value,
  onChange,
}: {
  label: string
  value: boolean | null
  onChange: (value: boolean) => void
}) {
  return (
    <fieldset className="evaluation-choice">
      <legend>{label}</legend>
      <div className="evaluation-choice-buttons">
        <button
          type="button"
          className={value === true ? 'is-active' : ''}
          aria-pressed={value === true}
          onClick={() => onChange(true)}
        >
          是
        </button>
        <button
          type="button"
          className={value === false ? 'is-active is-no' : ''}
          aria-pressed={value === false}
          onClick={() => onChange(false)}
        >
          否
        </button>
      </div>
    </fieldset>
  )
}

type EvaluationMode =
  | 'initial'
  | 'selection-review'
  | 'second-selection-review'
  | 'selection-adjudication'

export default function EvaluationWorkbenchPage() {
  const [mode, setMode] = useState<EvaluationMode>('initial')
  const [status, setStatus] = useState<EvaluationStatus | null>(null)
  const [page, setPage] = useState<EvaluationPage | null>(null)
  const [filter, setFilter] = useState<EvaluationStatusFilter>('unlabelled')
  const [category, setCategory] = useState('')
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [draft, setDraft] = useState<DraftLabel>(EMPTY_DRAFT)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const activeItem = page?.items[activeIndex] || null
  const pageTitle = mode === 'initial'
    ? '🎯 R1 人工标注'
    : mode === 'selection-review'
      ? '🔎 精选独立复核'
      : mode === 'second-selection-review'
        ? '🕶️ 第二人盲审'
        : '⚖️ 精选冲突裁决'
  const pageSubtitle = mode === 'initial'
    ? '阅读原始来源后独立判断；右侧策略建议不是人工真值，也不会自动填入表单。'
    : mode === 'selection-review'
      ? '对冻结样本进行第一轮精选复核，形成不可覆盖的 Reviewer A 记录。'
      : mode === 'second-selection-review'
        ? '另一位真人在隔离答案的条件下完成 40 条独立判断，提交不会直接修改 Gold。'
        : '第二人完成全部样本后，只对 A/B 分歧逐条作出最终人工裁决。'

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ status: filter, limit: '100' })
      if (category) params.set('category', category)
      if (query.trim()) params.set('q', query.trim())
      const [nextStatus, nextPage] = await Promise.all([
        apiGet<EvaluationStatus>('/ops/evaluation/status'),
        apiGet<EvaluationPage>(`/ops/evaluation/candidates?${params.toString()}`),
      ])
      setStatus(nextStatus)
      setPage(nextPage)
      setActiveIndex(0)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '标注工作台加载失败')
    } finally {
      setLoading(false)
    }
  }, [category, filter, query])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    setDraft(current => draftFromItem(activeItem, current))
  }, [activeItem?.candidate.article.id])

  const completion = Math.round((status?.completion_rate || 0) * 100)
  const missingFields = useMemo(() => {
    const fields = [
      draft.relevant,
      draft.selected,
      draft.primary_source_correct,
      draft.creation_worthy,
      draft.evidence_sufficient,
    ]
    return fields.some(value => value === null)
      || !draft.category
      || !draft.annotator.trim()
      || !draft.data_license.trim()
  }, [draft])

  const setBoolean = (field: BooleanField, value: boolean) => {
    setDraft(current => {
      const next = { ...current, [field]: value }
      if (field === 'relevant' && !value) next.selected = false
      return next
    })
  }

  const save = async () => {
    if (!activeItem || missingFields) {
      setError('请完成所有必填判断，并填写标注人和数据许可。')
      return
    }
    if (draft.relevant === false && draft.selected === true) {
      setError('与 AI 无关的内容不能标记为精选。')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const label: GoldLabel = {
        relevant: draft.relevant as boolean,
        category: draft.category as Category,
        selected: draft.selected as boolean,
        primary_source_correct: draft.primary_source_correct as boolean,
        story_key: draft.story_key.trim() || null,
        creation_worthy: draft.creation_worthy as boolean,
        evidence_sufficient: draft.evidence_sufficient as boolean,
        annotation_method: 'human',
        data_license: draft.data_license.trim(),
        annotator: draft.annotator.trim(),
        annotated_at: new Date().toISOString(),
        notes: draft.notes.trim(),
      }
      await apiPut<EvaluationItem>(
        `/ops/evaluation/annotations/${encodeURIComponent(activeItem.candidate.article.id)}`,
        label,
      )
      setNotice(`已保存第 ${activeItem.position} 条人工标签。`)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '标签保存失败')
    } finally {
      setSaving(false)
    }
  }

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      await apiPost<EvaluationStatus>('/ops/evaluation/candidates/refresh')
      await load()
      setNotice('候选池已补充；已经冻结和标注的样本不会被替换。')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '候选池刷新失败')
      setLoading(false)
    }
  }

  return (
    <>
      <div className="page-header-compact evaluation-header">
        <div>
          <h1 className="page-title">{pageTitle}</h1>
          <p className="page-subtitle">{pageSubtitle}</p>
        </div>
        <div className="evaluation-header-actions">
          <a className="btn" href="/api/ops/evaluation/gold.jsonl">导出当前标签</a>
          {mode === 'initial' && <Button onClick={refresh} disabled={loading}>补充候选</Button>}
        </div>
      </div>

      <nav className="evaluation-mode-tabs" aria-label="标注工作模式">
        <button type="button" className={mode === 'initial' ? 'is-active' : ''} aria-pressed={mode === 'initial'} onClick={() => setMode('initial')}>
          首轮完整标注
        </button>
        <button type="button" className={mode === 'selection-review' ? 'is-active' : ''} aria-pressed={mode === 'selection-review'} onClick={() => setMode('selection-review')}>
          精选复核（40 条）
        </button>
        <button type="button" className={mode === 'second-selection-review' ? 'is-active' : ''} aria-pressed={mode === 'second-selection-review'} onClick={() => setMode('second-selection-review')}>
          第二人盲审
        </button>
        <button type="button" className={mode === 'selection-adjudication' ? 'is-active' : ''} aria-pressed={mode === 'selection-adjudication'} onClick={() => setMode('selection-adjudication')}>
          冲突裁决
        </button>
      </nav>

      {mode === 'selection-review'
        ? <SelectionReviewPanel />
        : mode === 'second-selection-review'
          ? <SecondSelectionReviewPanel />
          : mode === 'selection-adjudication'
            ? <SelectionAdjudicationPanel />
            : (
        <>

      <section className="evaluation-progress" aria-label="R1 人工标注进度">
        <div className="evaluation-progress-head">
          <strong>{status?.human_label_count || 0} / {status?.target_count || 200}</strong>
          <span>{completion}% · 还需 {status?.remaining_count ?? 200} 条</span>
        </div>
        <div className="evaluation-progress-track">
          <span style={{ width: `${completion}%` }} />
        </div>
        <div className="evaluation-progress-meta">
          候选池 {status?.candidate_count || 0} 条 · 完整标签 {status?.labelled_count || 0} 条
          {(status?.candidate_count || 0) < (status?.target_count || 200)
            ? ` · 候选还差 ${(status?.target_count || 200) - (status?.candidate_count || 0)} 条`
            : ''}
          {status?.ready_for_baseline ? ' · 已可生成 baseline' : ' · 尚未达到 R1 人工门槛'}
        </div>
      </section>

      <div className="evaluation-toolbar">
        <select className="field" value={filter} onChange={event => setFilter(event.target.value as EvaluationStatusFilter)}>
          <option value="unlabelled">未标注</option>
          <option value="labelled">已标注</option>
          <option value="all">全部</option>
        </select>
        <select className="field" value={category} onChange={event => setCategory(event.target.value)}>
          <option value="">全部分类</option>
          {CATEGORIES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <input
          className="field"
          value={query}
          onChange={event => setQuery(event.target.value)}
          placeholder="搜索候选标题"
          aria-label="搜索候选标题"
        />
        <Button onClick={load} disabled={loading}>刷新</Button>
      </div>

      {error && <div className="feed-error" role="alert"><span>{error}</span></div>}
      {notice && <div className="evaluation-notice" role="status">{notice}</div>}

      {!loading && !activeItem && !error && (
        <div className="page-empty-state">
          <div className="page-empty-state-icon">✅</div>
          <p className="page-empty-state-text">当前筛选条件下没有待处理候选。</p>
        </div>
      )}

      {activeItem && (
        <div className="evaluation-layout">
          <aside className="evaluation-list" aria-label="候选列表">
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
                <small>{item.label ? `已标注 · ${item.label.annotator}` : '未标注'}</small>
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
              <p>{activeItem.candidate.article.summary || '该来源没有提供摘要，请打开原文判断。'}</p>
              <a href={activeItem.candidate.article.source_url} target="_blank" rel="noreferrer">
                打开原始来源 ↗
              </a>
            </article>

            <details className="evaluation-suggestion">
              <summary>查看策略建议（仅供参考）</summary>
              <div>
                相关：{activeItem.candidate.suggestion.relevant ? '是' : '否'} ·
                分类：{activeItem.candidate.suggestion.category} ·
                精选：{activeItem.candidate.suggestion.selected ? '是' : '否'} ·
                得分：{activeItem.candidate.suggestion.distribution_score ?? '无'}
              </div>
              {activeItem.candidate.suggestion.reason && <p>{activeItem.candidate.suggestion.reason}</p>}
            </details>

            <section className="evaluation-form" aria-label="人工判断表单">
              <BooleanChoice label="与 AI 主题相关？" value={draft.relevant} onChange={value => setBoolean('relevant', value)} />
              <label className="evaluation-field">
                <span>五分类</span>
                <select className="field" value={draft.category} onChange={event => setDraft(current => ({ ...current, category: event.target.value as Category }))}>
                  <option value="">请选择</option>
                  {CATEGORIES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <BooleanChoice label="应该进入精选？" value={draft.selected} onChange={value => setBoolean('selected', value)} />
              <BooleanChoice label="当前是正确主来源？" value={draft.primary_source_correct} onChange={value => setBoolean('primary_source_correct', value)} />
              <BooleanChoice label="值得公众号创作？" value={draft.creation_worthy} onChange={value => setBoolean('creation_worthy', value)} />
              <BooleanChoice label="现有证据充分？" value={draft.evidence_sufficient} onChange={value => setBoolean('evidence_sufficient', value)} />

              <label className="evaluation-field evaluation-field-wide">
                <span>Story Key（同一事件填写相同值，可留空）</span>
                <input className="field" value={draft.story_key} onChange={event => setDraft(current => ({ ...current, story_key: event.target.value }))} placeholder="例如 openai-model-release-2026-08" />
              </label>
              <label className="evaluation-field">
                <span>标注人</span>
                <input className="field" value={draft.annotator} onChange={event => setDraft(current => ({ ...current, annotator: event.target.value }))} placeholder="姓名或稳定代号" />
              </label>
              <label className="evaluation-field">
                <span>数据许可</span>
                <input className="field" value={draft.data_license} onChange={event => setDraft(current => ({ ...current, data_license: event.target.value }))} />
              </label>
              <label className="evaluation-field evaluation-field-wide">
                <span>复核说明</span>
                <textarea className="field" rows={3} value={draft.notes} onChange={event => setDraft(current => ({ ...current, notes: event.target.value }))} placeholder="记录判断依据、冲突或边界情况" />
              </label>
            </section>

            <div className="evaluation-save-row">
              <span>{missingFields ? '还有必填判断未完成' : '标签完整，可安全保存'}</span>
              <Button variant="primary" onClick={save} disabled={saving || missingFields}>
                {saving ? '保存中…' : activeItem.label ? '更新标签' : '保存并处理下一条'}
              </Button>
            </div>
          </section>
        </div>
      )}
        </>
      )}
    </>
  )
}
