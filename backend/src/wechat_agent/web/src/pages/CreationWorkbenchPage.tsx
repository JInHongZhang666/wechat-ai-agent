import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import Button from '../components/Button'
import Skeleton from '../components/Skeleton'
import { apiExport, apiGet, apiPost, apiPut } from '../utils/api'
import type {
  ContentBrief,
  CreationHotArticle,
  CreationHotArticleList,
  CreationRuntime,
  CreationRuntimeProbe,
  CreatorDraftResult,
  CreatorProject,
  CreatorProjectList,
  Draft,
  InspirationPreview,
} from '../types'

type WorkbenchMode = 'drafts' | 'quality'
type SourceKind = 'hot' | 'user_url'
type LengthOption = 'short' | 'medium' | 'long'
type CreationMode = 'synthesized' | 'rewrite'
type RewriteGoal = 'new_article' | 'new_angle' | 'more_conversational' | 'more_actionable'
type WritingStyle = '专业理性' | '温暖治愈' | '犀利直接' | '轻松口语' | '故事感强' | '干货清单'

interface WriterSettings {
  length: LengthOption
  writingStyle: WritingStyle
  creationMode: CreationMode
  rewriteGoal: RewriteGoal
  promptVariant: 'aigc' | 'classic'
  referenceFocus: 'mixed' | 'structure' | 'tone' | 'opening'
  referenceLevel: 'low' | 'medium' | 'high'
  customInstruction: string
}

const SETTINGS_KEY = 'ai-cabin.writer-settings.v1'
const DRAFT_KEY_PREFIX = 'ai-cabin.writer-draft.'
const DEFAULT_SETTINGS: WriterSettings = {
  length: 'medium',
  writingStyle: '专业理性',
  creationMode: 'synthesized',
  rewriteGoal: 'new_article',
  promptVariant: 'aigc',
  referenceFocus: 'mixed',
  referenceLevel: 'medium',
  customInstruction: '',
}
const TARGET_CHARACTERS: Record<LengthOption, number> = { short: 650, medium: 800, long: 950 }

const STAGE_LABELS: Record<CreatorProject['stage'], string> = {
  opportunity: '待采用机会',
  research: '补充证据',
  evidence_ready: '证据已就绪',
  brief_ready: '策划已就绪',
  drafting: '写作中',
  review_blocked: '审校阻断',
  review_passed: '审校通过',
  exported: '已导出',
}

function loadWriterSettings(): WriterSettings {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}') }
  } catch {
    return DEFAULT_SETTINGS
  }
}

function downloadText(content: string, draftId: string, format: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `draft-${draftId}.${format === 'html' || format === 'wechat_html' ? 'html' : format === 'text' ? 'txt' : 'md'}`
  anchor.click()
  URL.revokeObjectURL(url)
}

function InlineMarkdown({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => (
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>
      : <span key={`${part}-${index}`}>{part}</span>
  ))}</>
}

function MarkdownPreview({ content }: { content: string }) {
  if (!content.trim()) return <div className="writer-empty-editor">文章内容会在这里生成</div>
  return (
    <article className="writer-markdown-preview">
      {content.split(/\n{2,}/).map((block, index) => {
        const value = block.trim()
        const heading = /^(#{1,4})\s+(.+)$/.exec(value)
        if (heading) {
          const Level = `h${Math.min(heading[1].length + 1, 4)}` as 'h2' | 'h3' | 'h4'
          return <Level key={index}><InlineMarkdown text={heading[2]} /></Level>
        }
        const lines = value.split('\n')
        if (lines.every(line => /^[-*]\s+/.test(line))) {
          return <ul key={index}>{lines.map(line => <li key={line}><InlineMarkdown text={line.replace(/^[-*]\s+/, '')} /></li>)}</ul>
        }
        return <p key={index}>{lines.map((line, lineIndex) => <span key={`${line}-${lineIndex}`}><InlineMarkdown text={line} />{lineIndex < lines.length - 1 && <br />}</span>)}</p>
      })}
    </article>
  )
}

export default function CreationWorkbenchPage({ mode }: { mode: WorkbenchMode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [projects, setProjects] = useState<CreatorProject[]>([])
  const [selectedId, setSelectedId] = useState(searchParams.get('project') || '')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [briefEdit, setBriefEdit] = useState<ContentBrief | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [authorKind, setAuthorKind] = useState<'experience' | 'opinion' | 'example' | 'data'>('opinion')
  const [authorContent, setAuthorContent] = useState('')
  const [authorConfirmed, setAuthorConfirmed] = useState(false)
  const [hotArticles, setHotArticles] = useState<CreationHotArticle[]>([])
  const [hotDisclosure, setHotDisclosure] = useState('')
  const [primaryUrl, setPrimaryUrl] = useState('')
  const [supportingUrls, setSupportingUrls] = useState('')
  const [sourceKind, setSourceKind] = useState<SourceKind>('hot')
  const [manualSourceTitle, setManualSourceTitle] = useState('')
  const [manualSourceText, setManualSourceText] = useState('')
  const [sourcePreview, setSourcePreview] = useState<InspirationPreview | null>(null)
  const [settings, setSettings] = useState<WriterSettings>(loadWriterSettings)
  const [runtime, setRuntime] = useState<CreationRuntime | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [settingsCollapsed, setSettingsCollapsed] = useState(false)
  const editorRef = useRef<HTMLTextAreaElement>(null)

  const selected = useMemo(
    () => projects.find(project => project.id === selectedId) || null,
    [projects, selectedId],
  )
  const primarySimilarity = useMemo(() => {
    if (!selected || !draft || selected.origin !== 'inspiration') return null
    const primary = selected.inspirationSources.find(source => source.role === 'primary')
    return [...selected.similarityReports].reverse().find(
      report => report.draftVersion === draft.version && report.sourceId === primary?.id,
    ) || null
  }, [selected, draft])
  const isDirty = Boolean(draft && (editTitle !== draft.selected_title || editBody !== draft.body))
  const visibleCharacters = editBody.replace(/[#*_`~\s]/g, '').length

  const updateSetting = <K extends keyof WriterSettings,>(key: K, value: WriterSettings[K]) => {
    setSettings(current => ({ ...current, [key]: value }))
  }

  const generationProfile = useCallback((version?: number) => ({
    ...(version ? { version } : {}),
    accountName: 'AI信息小屋',
    audience: '希望高效了解实用 AI、判断采用价值并获得行动建议的公众号读者',
    targetCharacters: TARGET_CHARACTERS[settings.length],
    paragraphLimit: settings.writingStyle === '轻松口语' ? 60 : 80,
    writingStyle: settings.writingStyle,
    creationMode: settings.creationMode,
    rewriteGoal: settings.rewriteGoal,
    promptVariant: settings.promptVariant,
    referenceFocus: settings.referenceFocus,
    referenceLevel: settings.referenceLevel,
    customInstruction: settings.customInstruction.trim(),
  }), [settings])

  const loadProjects = useCallback(async (preferId?: string) => {
    const response = await apiGet<CreatorProjectList>('/v1/creation/projects')
    setProjects(response.items)
    setSelectedId(current => {
      const desired = preferId || current
      if (desired && response.items.some(item => item.id === desired)) return desired
      const first = mode === 'quality'
        ? response.items.find(item => item.currentDraftId) || response.items[0]
        : response.items[0]
      return first?.id || ''
    })
  }, [mode])

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  }, [settings])

  const refreshRuntime = useCallback(async () => {
    const value = await apiGet<CreationRuntime>('/v1/creation/runtime')
    setRuntime(value)
  }, [])

  useEffect(() => {
    refreshRuntime().catch(() => { /* writing remains usable in safe rule mode */ })
  }, [refreshRuntime])

  useEffect(() => {
    if (loading || !location.hash) return
    setSettingsCollapsed(false)
    const timer = window.setTimeout(() => {
      const target = document.getElementById(location.hash.slice(1))
      if (target instanceof HTMLDetailsElement) target.open = true
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 80)
    return () => window.clearTimeout(timer)
  }, [loading, location.hash])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      loadProjects(),
      mode === 'drafts'
        ? apiGet<CreationHotArticleList>('/v1/creation/hot-articles?limit=8').then(value => {
          if (!cancelled) {
            setHotArticles(value.items)
            setHotDisclosure(value.metricDisclosure)
            if (!primaryUrl && value.items[0]) setPrimaryUrl(value.items[0].url)
          }
        })
        : Promise.resolve(),
    ])
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : '加载内容项目失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [loadProjects, mode])

  useEffect(() => {
    if (!selectedId) return
    const nextSearch = new URLSearchParams(location.search)
    if (nextSearch.get('project') === selectedId) return
    nextSearch.set('project', selectedId)
    navigate({
      pathname: location.pathname,
      search: `?${nextSearch.toString()}`,
      hash: location.hash,
    }, { replace: true })
  }, [location.hash, location.pathname, location.search, navigate, selectedId])

  useEffect(() => {
    setBriefEdit(selected?.brief || null)
    setDraft(null)
    setEditTitle('')
    setEditBody('')
    setPreviewing(false)
    if (!selected?.currentDraftId) return
    let cancelled = false
    apiGet<Draft>(`/v1/creation/drafts/${selected.currentDraftId}`)
      .then(value => {
        if (cancelled) return
        setDraft(value)
        let title = value.selected_title
        let body = value.body
        try {
          const local = JSON.parse(localStorage.getItem(`${DRAFT_KEY_PREFIX}${value.id}`) || '{}') as { version?: number; title?: string; body?: string }
          if (local.version === value.version && typeof local.title === 'string' && typeof local.body === 'string') {
            title = local.title
            body = local.body
          }
        } catch { /* ignore malformed local recovery data */ }
        setEditTitle(title)
        setEditBody(body)
      })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : '加载草稿失败') })
    return () => { cancelled = true }
  }, [selected?.id, selected?.currentDraftId, selected?.brief])

  useEffect(() => {
    if (!draft) return
    localStorage.setItem(`${DRAFT_KEY_PREFIX}${draft.id}`, JSON.stringify({
      version: draft.version,
      title: editTitle,
      body: editBody,
      savedAt: new Date().toISOString(),
    }))
  }, [draft, editTitle, editBody])

  const run = useCallback(async (label: string, action: () => Promise<void>) => {
    setBusy(label)
    setError('')
    setNotice('')
    try { await action() } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${label}失败`)
    } finally { setBusy('') }
  }, [])

  const probeRuntime = () => void run('验证模型', async () => {
    const probe = await apiPost<CreationRuntimeProbe>('/v1/creation/runtime/probe')
    await refreshRuntime()
    if (!probe.ok) throw new Error(probe.message)
    setNotice(`真实模型验证通过：${probe.call.model || '当前模型'}，现在可以进行 AI 分阶段生成。`)
  })

  const createInspirationProject = () => {
    if (!primaryUrl.trim()) return
    void run('导入并研究', async () => {
      const project = await apiPost<CreatorProject>('/v1/creation/projects/from-inspiration', {
        primaryUrl: primaryUrl.trim(),
        primaryTitle: manualSourceTitle.trim() || undefined,
        primaryText: manualSourceText.trim() || undefined,
        supportingUrls: supportingUrls.split(/[\n,，]/).map(value => value.trim()).filter(Boolean),
        sourceKind,
      })
      await loadProjects(project.id)
      const primary = project.inspirationSources.find(source => source.role === 'primary')
      if (primary) setSourcePreview({ schemaVersion: 1, source: primary, policy: '正文只用于主题、抽象结构和最终相似度检查；事实仍需独立来源确认。' })
      setNotice(project.stage === 'evidence_ready'
        ? '原文结构、独立资料和原创门禁已建立，请继续生成内容策划。'
        : '原文已解析，但独立资料仍不足；请先处理证据阻断项。')
    })
  }

  const previewInspiration = () => {
    if (!primaryUrl.trim()) return
    void run('解析来源', async () => {
      const preview = await apiPost<InspirationPreview>('/v1/creation/inspiration/preview', {
        primaryUrl: primaryUrl.trim(),
        primaryTitle: manualSourceTitle.trim() || undefined,
        primaryText: manualSourceText.trim() || undefined,
        sourceKind,
      })
      setSourcePreview(preview)
      setNotice('文章来源解析成功，请确认标题、发布者和正文长度后建立研究项目。')
    })
  }

  const generateBrief = () => {
    if (!selected) return
    void run('生成策划', async () => {
      const project = await apiPost<CreatorProject>(`/v1/creation/projects/${selected.id}/brief`)
      await loadProjects(project.id)
      setNotice(project.brief?.readyForDraft ? '内容策划已通过证据门，可以生成文章。' : '策划已生成，但仍需补充证据。')
    })
  }

  const saveBrief = () => {
    if (!selected || !briefEdit) return
    void run('保存策划', async () => {
      const project = await apiPost<CreatorProject>(`/v1/creation/projects/${selected.id}/brief`, {
        version: selected.version,
        coreThesis: briefEdit.coreThesis,
        audienceBenefit: briefEdit.audienceBenefit,
        angle: briefEdit.angle,
        differentiation: briefEdit.differentiation,
        sections: briefEdit.sections,
        requiredSourceUrls: briefEdit.requiredSourceUrls,
        risks: briefEdit.risks,
        authorInputs: briefEdit.authorInputs,
      })
      await loadProjects(project.id)
      setNotice(project.brief?.readyForDraft ? '策划已保存并通过证据门。' : '策划已保存，但仍有阻断项。')
    })
  }

  const createDraft = () => {
    if (!selected || (draft && !window.confirm('重新生成会创建一个全新的草稿版本，当前已保存版本仍保留在历史中。继续吗？'))) return
    void run(draft ? '重新生成' : '生成文章', async () => {
      const result = await apiPost<CreatorDraftResult>(`/v1/creation/projects/${selected.id}/drafts`, generationProfile())
      setDraft(result.draft)
      setEditTitle(result.draft.selected_title)
      setEditBody(result.draft.body)
      localStorage.removeItem(`${DRAFT_KEY_PREFIX}${result.draft.id}`)
      await loadProjects(result.project.id)
      await refreshRuntime()
      setNotice(result.project.draftVersions[result.project.draftVersions.length - 1]?.generationMethod === 'ai'
        ? '文章已由真实 AI 分阶段生成，并完成事实、来源、结构和原创度首轮检查。'
        : '模型调用未完成，已明确回退为安全规则稿；请先验证模型后再重新生成。')
    })
  }

  const saveDraft = () => {
    if (!draft) return
    void run('保存草稿', async () => {
      const updated = await apiPut<Draft>(`/v1/creation/drafts/${draft.id}`, {
        selectedTitle: editTitle.trim(), body: editBody, version: draft.version,
      })
      setDraft(updated)
      setEditTitle(updated.selected_title)
      setEditBody(updated.body)
      localStorage.removeItem(`${DRAFT_KEY_PREFIX}${updated.id}`)
      await loadProjects(selected?.id)
      setNotice('草稿已保存，全部质量检查已自动重跑。')
    })
  }

  const polishDraft = () => {
    if (!draft || !window.confirm('将基于当前稿件进行发表前深度润色，并重新运行事实与原创度检查。继续吗？')) return
    void run('去 AI 味', async () => {
      if (isDirty) throw new Error('请先保存当前修改，再执行深度润色。')
      const result = await apiPost<CreatorDraftResult>(`/v1/creation/drafts/${draft.id}/polish`, generationProfile(draft.version))
      setDraft(result.draft)
      setEditTitle(result.draft.selected_title)
      setEditBody(result.draft.body)
      localStorage.removeItem(`${DRAFT_KEY_PREFIX}${result.draft.id}`)
      await loadProjects(result.project.id)
      await refreshRuntime()
      setNotice('已完成去 AI 味、阅读节奏优化与全量质量复检。')
    })
  }

  const reviewDraft = () => {
    if (!draft) return
    void run('重新检查', async () => {
      if (isDirty) throw new Error('请先保存当前修改，再运行质量检查。')
      const reviewed = await apiPost<Draft>(`/v1/creation/drafts/${draft.id}/review`)
      setDraft(reviewed)
      await loadProjects(selected?.id)
      setNotice(reviewed.quality.can_export ? '检查通过，可以人工定稿。' : '检查完成，请按阻断项继续修复。')
    })
  }

  const saveAuthorInput = () => {
    if (!selected || !authorContent.trim()) return
    void run('保存作者材料', async () => {
      const project = await apiPost<CreatorProject>(`/v1/creation/projects/${selected.id}/author-inputs`, {
        projectVersion: selected.version,
        kind: authorKind,
        content: authorContent,
        confirmed: authorConfirmed,
        sourceUrls: [],
      })
      setAuthorContent('')
      setAuthorConfirmed(false)
      await loadProjects(project.id)
      setNotice('作者材料已保存；只有你确认真实的材料才会进入生成。')
    })
  }

  const approveDraft = () => {
    if (!draft || !selected) return
    void run('人工定稿', async () => {
      if (isDirty) throw new Error('请先保存当前修改，再确认定稿。')
      const project = await apiPost<CreatorProject>(`/v1/creation/drafts/${draft.id}/approve`, {
        version: draft.version,
        confirmation: '我已核对事实、作者材料和表达边界',
      })
      await loadProjects(project.id)
      const loaded = await apiGet<Draft>(`/v1/creation/drafts/${draft.id}`)
      setDraft(loaded)
      setEditTitle(loaded.selected_title)
      setEditBody(loaded.body)
      setNotice('当前版本已人工确认定稿，可以导出公众号富文本。')
    })
  }

  const exportDraft = (format: 'markdown' | 'text' | 'wechat_html') => {
    if (!draft) return
    void run('导出', async () => {
      const content = await apiExport(`/v1/creation/drafts/${draft.id}/export`, format)
      downloadText(content, draft.id, format)
      await loadProjects(selected?.id)
      setNotice('质量通过版本已导出。')
    })
  }

  const copyDraft = () => {
    const content = `${editTitle}\n\n${editBody}`.trim()
    navigator.clipboard.writeText(content)
      .then(() => setNotice('标题和正文已复制到剪贴板。'))
      .catch(() => setError('复制失败，请检查浏览器剪贴板权限。'))
  }

  const restoreDraft = () => {
    if (!draft || !window.confirm('恢复到服务器中最近保存的版本？未保存修改将被放弃。')) return
    setEditTitle(draft.selected_title)
    setEditBody(draft.body)
    localStorage.removeItem(`${DRAFT_KEY_PREFIX}${draft.id}`)
    setNotice('已恢复到最近保存版本。')
  }

  const clearDraft = () => {
    if (!draft || !window.confirm('只清空当前编辑器中的标题和正文？在点击保存前，服务器版本不会改变。')) return
    setEditTitle('')
    setEditBody('')
    setPreviewing(false)
    setNotice('当前编辑器已清空；如需撤销，请点击“恢复”。')
  }

  const applyFormat = (before: string, after = before, placeholder = '文字') => {
    const editor = editorRef.current
    if (!editor) return
    const start = editor.selectionStart
    const end = editor.selectionEnd
    const selectedText = editBody.slice(start, end) || placeholder
    const next = `${editBody.slice(0, start)}${before}${selectedText}${after}${editBody.slice(end)}`
    setEditBody(next)
    requestAnimationFrame(() => {
      editor.focus()
      editor.setSelectionRange(start + before.length, start + before.length + selectedText.length)
    })
  }

  const applyLinePrefix = (prefix: string) => {
    const editor = editorRef.current
    if (!editor) return
    const start = editor.selectionStart
    const end = editor.selectionEnd
    const from = editBody.lastIndexOf('\n', Math.max(0, start - 1)) + 1
    const toBreak = editBody.indexOf('\n', end)
    const to = toBreak === -1 ? editBody.length : toBreak
    const selectedLines = (editBody.slice(from, to) || '小标题').split('\n')
    const replacement = selectedLines.map(line => `${prefix}${line.replace(/^#{1,4}\s+|^[-*]\s+|^\d+\.\s+/, '')}`).join('\n')
    setEditBody(`${editBody.slice(0, from)}${replacement}${editBody.slice(to)}`)
    requestAnimationFrame(() => editor.focus())
  }

  const insertImage = () => {
    const url = window.prompt('粘贴图片 URL')
    if (url?.trim()) applyFormat('![', `](${url.trim()})`, '图片说明')
  }

  const updateBriefField = (field: 'coreThesis' | 'audienceBenefit' | 'angle' | 'differentiation', value: string) => {
    setBriefEdit(current => current ? { ...current, [field]: value } : current)
  }

  if (loading) return <Skeleton card />

  return (
    <div className="writer-page writer-workbench-theme" id="writing-workspace">
      <header className="writer-topbar">
        <div className="writer-header-identity">
          <h1>{mode === 'drafts' ? '文章创作' : '质量审校'}</h1>
          <span>本地工作台</span>
          <small>{selected ? selected.topicTitle : 'AI 信息小屋'}</small>
        </div>
        <div className="writer-top-actions">
          <Link className="btn btn-link" to="/create/tasks">文章管理</Link>
          {draft && <Link className="btn btn-link" to="/create/drafts#wechat-account">公众号发布</Link>}
          {mode === 'drafts' && <Button variant="primary" onClick={draft && isDirty ? saveDraft : createDraft} disabled={Boolean(busy) || (!draft && !selected?.brief?.readyForDraft) || Boolean(draft && isDirty && !editTitle.trim())}>{busy || (draft && isDirty ? '保存并检查' : draft ? '重新生成' : '生成文章')}</Button>}
        </div>
      </header>

      <div className={`writer-assistant-tip writer-runtime-${runtime?.healthState || 'unconfigured'}`}><span>✦</span><strong>{runtime?.healthState === 'healthy' ? '真实 AI 已验证' : runtime?.healthState === 'degraded' ? 'AI 异常 · 安全回退' : runtime?.healthState === 'untested' ? 'AI 已配置 · 待验证' : '安全规则创作'}</strong><p>{runtime?.lastError?.message || '系统会守住来源、事实和原创度边界，定稿前仍请核对关键结论。'}</p>{runtime?.configured && !runtime.verified && <Button onClick={probeRuntime} disabled={Boolean(busy)}>验证模型</Button>}</div>
      {error && <div className="creation-alert is-error">{error}</div>}
      {notice && <div className="creation-alert is-success">{notice}</div>}

      <div className={`writer-studio${settingsCollapsed ? ' is-settings-collapsed' : ''}`}>
        <aside className="writer-settings-panel">
          <div className="writer-panel-title"><div><strong>创作设置</strong><small>{selected ? STAGE_LABELS[selected.stage] : '新建任务'}</small></div><button type="button" onClick={() => setSettingsCollapsed(value => !value)}>{settingsCollapsed ? '展开' : '☷ 收起'}</button></div>

          <div className="writer-settings-scroll">

          {mode === 'drafts' && <details className="writer-setting-group writer-source-group" id="video-parser" open={!selected}>
            <summary><span>素材来源与研究</span><small>热点 / 链接 / 已有项目</small></summary>
            <div className="writer-setting-content">
              {projects.length > 0 && <label className="writer-field"><span>已有研究项目</span><select value={selectedId} onChange={event => setSelectedId(event.target.value)}>{projects.map(project => <option key={project.id} value={project.id}>{project.topicTitle}</option>)}</select></label>}
              <div className="writer-segmented"><button type="button" className={sourceKind === 'hot' ? 'is-active' : ''} onClick={() => { setSourceKind('hot'); setPrimaryUrl(hotArticles[0]?.url || ''); setManualSourceTitle(''); setManualSourceText(''); setSourcePreview(null) }}>AIHOT 热点</button><button type="button" className={sourceKind === 'user_url' ? 'is-active' : ''} onClick={() => { setSourceKind('user_url'); setPrimaryUrl(''); setManualSourceTitle(''); setManualSourceText(''); setSourcePreview(null) }}>文章链接</button></div>
              {sourceKind === 'hot' ? <label className="writer-field"><span>当前高分文章</span><select value={primaryUrl} onChange={event => { setPrimaryUrl(event.target.value); setSourcePreview(null) }}>{hotArticles.map(article => <option key={article.articleId} value={article.url}>{article.title} · {article.score.toFixed(1)}</option>)}</select><small>{hotDisclosure}</small></label> : <>
                <label className="writer-field"><span>主灵感文章链接</span><textarea rows={3} value={primaryUrl} onChange={event => { setPrimaryUrl(event.target.value); setSourcePreview(null) }} placeholder="粘贴公众号文章或公开网页链接" /></label>
                <details className="writer-manual-source">
                  <summary>网页无法解析？补充文章正文</summary>
                  <label className="writer-field"><span>文章标题（可选）</span><input value={manualSourceTitle} onChange={event => { setManualSourceTitle(event.target.value); setSourcePreview(null) }} placeholder="未填写时从正文或已收录文章中识别" /></label>
                  <label className="writer-field"><span>文章正文</span><textarea rows={7} maxLength={50000} value={manualSourceText} onChange={event => { setManualSourceText(event.target.value); setSourcePreview(null) }} placeholder="仅在网页抓取失败时粘贴；至少 120 个有效字符" /><small>{manualSourceText.length}/50000 · 只用于结构分析和原创度检查，不作为事实证据</small></label>
                </details>
              </>}
              <label className="writer-field"><span>补充资料链接（可选）</span><textarea rows={3} value={supportingUrls} onChange={event => setSupportingUrls(event.target.value)} placeholder="每行一个；系统也会自动匹配独立来源" /></label>
              <div className="writer-source-actions"><Button onClick={previewInspiration} disabled={Boolean(busy) || !primaryUrl.trim()}>解析预览</Button><Button variant="primary" onClick={createInspirationProject} disabled={Boolean(busy) || !primaryUrl.trim()}>建立研究项目</Button></div>
              {sourcePreview && <article className="writer-source-preview"><div><span>{sourcePreview.source.role === 'primary' ? '主灵感' : '资料'}</span><strong>{sourcePreview.source.title}</strong><small>{sourcePreview.source.publisher} · {sourcePreview.source.contentCharacters} 字</small></div><dl><div><dt>开篇</dt><dd>{sourcePreview.source.structure.openingApproach}</dd></div><div><dt>段落</dt><dd>{sourcePreview.source.structure.paragraphCount} 段</dd></div><div><dt>平均段长</dt><dd>{sourcePreview.source.structure.averageParagraphCharacters} 字</dd></div><div><dt>来源身份</dt><dd>{sourcePreview.source.sourceTypeVerified ? '已验证' : '待独立核验'}</dd></div></dl><p>{sourcePreview.policy}</p></article>}
              <p className="writer-policy">只借鉴主题、抽象结构和阅读节奏；相似度超过 10% 时禁止定稿和导出。</p>
            </div>
          </details>}

          {mode === 'quality' && projects.length > 0 && <label className="writer-field"><span>当前项目</span><select value={selectedId} onChange={event => setSelectedId(event.target.value)}>{projects.map(project => <option key={project.id} value={project.id}>{project.topicTitle}</option>)}</select></label>}

          {selected && <>
            <label className="writer-field"><span>创作模式</span><select value={settings.creationMode} onChange={event => updateSetting('creationMode', event.target.value as CreationMode)}><option value="synthesized">智能成文 · 多源合成</option><option value="rewrite">参考改写 · 原创重构</option></select></label>
            <label className="writer-field"><span>主题 <em>*</em></span><textarea rows={3} value={selected.topicTitle} readOnly /><small>主题来自已完成证据研究的项目，避免无来源直接成文。</small></label>
            <label className="writer-field" id="wechat-account"><span>公众号</span><select value="AI信息小屋" disabled><option>AI信息小屋 · 本地账号画像</option></select><small>正式发送前仍需完成公众号接口配置和人工定稿。</small></label>
            <label className="writer-field"><span>字数</span><select value={settings.length} onChange={event => updateSetting('length', event.target.value as LengthOption)}><option value="short">短文 · 650 字</option><option value="medium">中等 · 800 字</option><option value="long">长文 · 950 字</option></select></label>
            <label className="writer-field"><span>表达风格</span><select value={settings.writingStyle} onChange={event => updateSetting('writingStyle', event.target.value as WritingStyle)}><option>专业理性</option><option>温暖治愈</option><option>犀利直接</option><option>轻松口语</option><option>故事感强</option><option>干货清单</option></select></label>
            <label className="writer-field" id="prompt-library"><span>提示词</span><select value={settings.promptVariant} onChange={event => updateSetting('promptVariant', event.target.value as WriterSettings['promptVariant'])}><option value="aigc">通用模板 · 口语增强</option><option value="classic">经典模板 · 稳重克制</option></select><small>通用模板更口语化；经典模板更加稳重。两者都受证据与原创度硬门约束。</small></label>
            {settings.creationMode === 'rewrite' && <div className="writer-rewrite-fields"><label className="writer-field"><span>改写目标</span><select value={settings.rewriteGoal} onChange={event => updateSetting('rewriteGoal', event.target.value as RewriteGoal)}><option value="new_article">重写为新文章</option><option value="new_angle">换个切入角度</option><option value="more_conversational">更口语化</option><option value="more_actionable">更可执行</option></select></label><div className="writer-field-grid"><label className="writer-field"><span>参考重点</span><select value={settings.referenceFocus} onChange={event => updateSetting('referenceFocus', event.target.value as WriterSettings['referenceFocus'])}><option value="mixed">综合参考</option><option value="structure">重点参考结构</option><option value="tone">重点参考语气</option><option value="opening">重点参考开头方式</option></select></label><label className="writer-field"><span>参考强度</span><select value={settings.referenceLevel} onChange={event => updateSetting('referenceLevel', event.target.value as WriterSettings['referenceLevel'])}><option value="low">轻参考</option><option value="medium">中参考</option><option value="high">强参考</option></select></label></div></div>}
            <label className="writer-field" id="writing-settings"><span>补充写作要求</span><textarea rows={3} maxLength={1000} value={settings.customInstruction} onChange={event => updateSetting('customInstruction', event.target.value)} placeholder="例如：先给结论，再解释普通读者如何判断是否值得采用" /><small>{settings.customInstruction.length}/1000 · 不能覆盖事实和原创规则</small></label>

            {mode === 'drafts' && <details className="writer-setting-group" open={!selected.brief?.readyForDraft}>
              <summary><span>内容策划</span><small>{briefEdit?.readyForDraft ? '证据门已通过' : '待完成'}</small></summary>
              <div className="writer-setting-content">
                {!briefEdit ? <><p className="writer-helper">先把核验事实变成中心论点、读者收益和原创结构。</p><Button variant="primary" onClick={generateBrief} disabled={Boolean(busy)}>生成证据化策划</Button></> : <>
                  <label className="writer-field"><span>中心论点</span><textarea rows={3} value={briefEdit.coreThesis} onChange={event => updateBriefField('coreThesis', event.target.value)} /></label>
                  <label className="writer-field"><span>读者收益</span><textarea rows={3} value={briefEdit.audienceBenefit} onChange={event => updateBriefField('audienceBenefit', event.target.value)} /></label>
                  <label className="writer-field"><span>原创角度</span><textarea rows={3} value={briefEdit.angle} onChange={event => updateBriefField('angle', event.target.value)} /></label>
                  <label className="writer-field"><span>差异化</span><textarea rows={3} value={briefEdit.differentiation} onChange={event => updateBriefField('differentiation', event.target.value)} /></label>
                  <div className="writer-outline-list">{briefEdit.sections.map((section, index) => <label key={section.id}><span>{String(index + 1).padStart(2, '0')}</span><input value={section.heading} onChange={event => setBriefEdit(current => current ? { ...current, sections: current.sections.map(item => item.id === section.id ? { ...item, heading: event.target.value } : item) } : current)} /></label>)}</div>
                  {briefEdit.blockingReasons.map(reason => <p className="writer-blocker" key={reason}>{reason}</p>)}
                  <Button onClick={saveBrief} disabled={Boolean(busy)}>保存并校验策划</Button>
                </>}
              </div>
            </details>}

            {mode === 'drafts' && <details className="writer-setting-group">
              <summary><span>作者真实材料</span><small>{selected.authorInputs.filter(item => item.confirmed).length} 条已确认</small></summary>
              <div className="writer-setting-content">
                <label className="writer-field"><span>材料类型</span><select value={authorKind} onChange={event => setAuthorKind(event.target.value as typeof authorKind)}><option value="opinion">作者观点</option><option value="experience">真实经验</option><option value="example">真实案例</option><option value="data">作者数据</option></select></label>
                <label className="writer-field"><span>材料内容</span><textarea rows={4} value={authorContent} onChange={event => setAuthorContent(event.target.value)} placeholder="模型不会替你虚构亲测或个人经历" /></label>
                <label className="writer-check"><input type="checkbox" checked={authorConfirmed} onChange={event => setAuthorConfirmed(event.target.checked)} /><span>我确认材料真实，可用于文章</span></label>
                <Button onClick={saveAuthorInput} disabled={Boolean(busy) || !authorContent.trim()}>保存材料</Button>
              </div>
            </details>}

            <div className="writer-source-status" id="membership"><div><span>研究资料</span><strong>{selected.inspirationSources.length || selected.brief?.requiredSourceUrls.length || 0} 个来源</strong></div><div><span>本地专业能力</span><strong>{selected.quality?.blockingIssueCount ? `${selected.quality.blockingIssueCount} 个阻断` : '质量门已启用'}</strong></div></div>
          </>}
          </div>
        </aside>

        <main className="writer-editor-panel">
          <div className="writer-editor-head"><div><span>生成结果</span>{draft && <small>v{draft.version} · {isDirty ? '有未保存修改' : '已自动暂存'}</small>}</div>{draft && <div className={`writer-quality-pill ${draft.quality.can_export ? 'is-passed' : 'is-blocked'}`}>{draft.quality.can_export ? '质量通过' : `${draft.quality.blocking_issue_count} 个阻断`}</div>}</div>

          {!selected ? <div className="writer-empty-editor"><strong>从左侧开始一个创作任务</strong><span>选择 AIHOT 热点或粘贴文章链接，系统会先研究证据再生成原创内容。</span></div> : !draft ? <div className="writer-empty-editor"><strong>{selected.brief?.readyForDraft ? '创作设置已经就绪' : '先完成内容策划'}</strong><span>{selected.brief?.readyForDraft ? '确认左侧字数、风格和参考策略后，点击“生成文章”。' : '证据和策划通过后，才能进入正式写作。'}</span>{selected.brief?.readyForDraft && <Button variant="primary" onClick={createDraft} disabled={Boolean(busy)}>{busy || '生成文章'}</Button>}</div> : <>
            <div className="writer-editor-toolbar" role="toolbar" aria-label="编辑工具栏">
              <button type="button" onClick={() => applyLinePrefix('# ')} title="一级标题">H1</button><button type="button" onClick={() => applyLinePrefix('## ')} title="二级标题">H2</button><button type="button" onClick={() => applyLinePrefix('### ')} title="三级标题">H3</button><button type="button" onClick={() => applyLinePrefix('#### ')} title="四级标题">H4</button><i />
              <button type="button" onClick={() => applyFormat('**')} title="加粗"><b>B</b></button><button type="button" onClick={() => applyFormat('*')} title="斜体"><em>I</em></button><button type="button" onClick={() => applyFormat('<u>', '</u>')} title="下划线"><u>U</u></button><button type="button" onClick={() => applyFormat('> ', '', '引用内容')} title="引用">❞</button><i />
              <button type="button" onClick={() => applyLinePrefix('- ')} title="无序列表">• 列表</button><button type="button" onClick={() => applyLinePrefix('1. ')} title="有序列表">1. 列表</button><button type="button" onClick={insertImage} title="插入图片">▧ 图片</button>
            </div>

            <div className="writer-content-area">
              {previewing ? <div className="writer-preview-surface"><h1>{editTitle}</h1><MarkdownPreview content={editBody} /></div> : <div className="writer-edit-surface"><input className="writer-inline-title" value={editTitle} onChange={event => setEditTitle(event.target.value)} maxLength={80} aria-label="文章标题" placeholder="请输入文章标题" /><textarea ref={editorRef} value={editBody} onChange={event => setEditBody(event.target.value)} aria-label="正文编辑器" placeholder="文章内容会在这里生成" /></div>}
            </div>

            <div className="writer-document-meta">
              <span>{visibleCharacters} 字符</span><span>{draft.cited_source_urls.length} 个引用来源</span><span>{settings.writingStyle}</span>{primarySimilarity && <span className={primarySimilarity.passed ? 'is-safe' : 'is-risk'}>原文相似度 {(primarySimilarity.score * 100).toFixed(1)}%</span>}
              {draft.titles.length > 1 && <details className="writer-title-picker"><summary>备选标题</summary><div>{draft.titles.map((title, index) => <button type="button" className={editTitle === title ? 'is-active' : ''} key={title} onClick={() => setEditTitle(title)}><span>{index + 1}</span>{title}</button>)}</div></details>}
            </div>

            <div className="writer-editor-footer">
              <div><button type="button" onClick={() => setPreviewing(value => !value)}>{previewing ? '✎ 编辑' : '◉ 预览'}</button><button type="button" onClick={copyDraft}>▣ 复制</button><button type="button" onClick={clearDraft}>⌫ 清空</button><button type="button" onClick={polishDraft} disabled={Boolean(busy) || isDirty}>✦ 去 AI 味</button>{isDirty && <button type="button" onClick={restoreDraft}>↶ 恢复</button>}</div>
            </div>

            <details className="writer-quality-drawer">
              <summary><div><span>{draft.quality.can_export ? '✓' : '!'}</span><strong>{draft.quality.can_export ? '质量门已通过' : `${draft.quality.blocking_issue_count} 个问题阻止发布`}</strong></div><small>事实 · 来源 · 结构 · 表达 · 原创度</small></summary>
              <div className="writer-quality-content">
                {selected.origin === 'inspiration' && <div className={`writer-originality ${primarySimilarity?.passed ? 'is-passed' : 'is-blocked'}`}><div><span>原创相似度硬门</span><small>字符 12-gram + 最长连续片段，每版重算</small></div><strong>{primarySimilarity ? `${(primarySimilarity.score * 100).toFixed(1)}%` : '待检测'}<small>阈值 ≤ 10%</small></strong></div>}
                <div className="writer-issues">{draft.quality.issues.length === 0 ? <p>没有发现质量问题。</p> : draft.quality.issues.map(issue => <div className={issue.blocking ? 'is-blocking' : ''} key={`${issue.code}-${issue.message}`}><span>{issue.blocking ? '阻断' : '提示'}</span><p><strong>{issue.code}{issue.location ? ` · ${issue.location}` : ''}</strong>{issue.message}{issue.fix_suggestion && <small>{issue.fix_suggestion}</small>}</p></div>)}</div>
                {draft.cited_source_urls.length > 0 && <div className="writer-citations"><strong>引用来源</strong>{draft.cited_source_urls.map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer">来源 {index + 1} ↗</a>)}</div>}
                <div className="writer-publish-actions"><Button onClick={reviewDraft} disabled={Boolean(busy) || isDirty}>重新检查</Button><Button variant="primary" onClick={approveDraft} disabled={Boolean(busy) || isDirty || !draft.quality.can_export || selected.humanApprovedDraftVersion === draft.version}>{selected.humanApprovedDraftVersion === draft.version ? '当前版本已定稿' : '人工核对并定稿'}</Button><Button onClick={() => exportDraft('wechat_html')} disabled={Boolean(busy) || selected.humanApprovedDraftVersion !== draft.version}>导出公众号富文本</Button><Button onClick={() => exportDraft('markdown')} disabled={Boolean(busy) || selected.humanApprovedDraftVersion !== draft.version}>Markdown</Button><Button onClick={() => exportDraft('text')} disabled={Boolean(busy) || selected.humanApprovedDraftVersion !== draft.version}>纯文本</Button></div>
              </div>
            </details>
          </>}
        </main>
      </div>
      <footer className="writer-page-footer"><span>草稿自动暂存在本机，正式保存后会重跑全部质量门。</span><span>只有人工确认且质量通过的版本允许导出。</span></footer>
    </div>
  )
}
