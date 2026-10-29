import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Button from '../components/Button'
import {
  CreatorEmptyState,
  CreatorPageHeader,
  CreatorSectionTitle,
  CreatorStatus,
} from '../components/CreatorPageScaffold'
import { useAppearance } from '../hooks/useAppearance'

type ImportMode = 'file' | 'url'
type TaskStatus = 'queued' | 'downloading' | 'extracting_captions' | 'extracting_audio' | 'transcribing' | 'completed' | 'no_captions' | 'failed'
type ExportFormat = 'text' | 'srt' | 'vtt'
type ResultView = 'original' | 'refined' | 'qa'

interface QaMessage {
  role: 'user' | 'assistant'
  content: string
}

interface VideoRuntimeStatus {
  enabled: boolean
  provider: string
  providerReady: boolean
  providerMessage: string
  ocrProvider: string
  ocrReady: boolean
  ocrMessage: string
  model: string
  ffmpegReady: boolean
  ytDlpReady: boolean
  cookieFileReady?: boolean
  cookieBrowser?: string | null
  urlImportEnabled: boolean
  maxFileMb: number
  maxDurationMinutes: number
}

interface VideoSegment {
  id: number
  start: number
  end: number
  text: string
}

interface VideoTask {
  id: string
  status: TaskStatus
  progress: number
  sourceType: string
  sourceName: string
  sourceUrl?: string | null
  duration?: number | null
  language?: string | null
  transcriptSource?: 'screen_ocr' | 'speech' | null
  transcript: string
  refinedTranscript: string
  refinedModel?: string | null
  segments: VideoSegment[]
  error?: string | null
}

const DEFAULT_AVATAR = '/assets/user-avatar-default.jpg'
const LINGXI_AVATAR = '/assets/lingxi-ai-avatar.webp'
const VIDEO_SESSION_KEY = 'ai-cabin.video-analysis.session.v1'

interface SavedVideoSession {
  taskId: string
  mode: ImportMode
  sourceUrl: string
  resultView: ResultView
  qaMessages: QaMessage[]
}

function readSavedVideoSession(): SavedVideoSession | null {
  try {
    const raw = window.localStorage.getItem(VIDEO_SESSION_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<SavedVideoSession>
    if (typeof value.taskId !== 'string' || !value.taskId.startsWith('video_')) return null
    const qaMessages = Array.isArray(value.qaMessages)
      ? value.qaMessages.filter((message): message is QaMessage => (
        Boolean(message)
        && (message.role === 'user' || message.role === 'assistant')
        && typeof message.content === 'string'
      )).slice(-24)
      : []
    return {
      taskId: value.taskId,
      mode: value.mode === 'url' ? 'url' : 'file',
      sourceUrl: typeof value.sourceUrl === 'string' ? value.sourceUrl : '',
      resultView: value.resultView === 'refined' || value.resultView === 'qa' ? value.resultView : 'original',
      qaMessages,
    }
  } catch {
    return null
  }
}

function saveVideoSession(session: SavedVideoSession) {
  try {
    window.localStorage.setItem(VIDEO_SESSION_KEY, JSON.stringify(session))
  } catch {
    // Storage may be unavailable in private mode; the active page still works.
  }
}

function clearVideoSession() {
  try {
    window.localStorage.removeItem(VIDEO_SESSION_KEY)
  } catch {
    // Ignore unavailable browser storage.
  }
}

function ExpandIcon({ expanded }: { expanded: boolean }) {
  return expanded ? (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 3v6H3M15 3v6h6M9 21v-6H3M15 21v-6h6" />
      <path d="m9 9-6-6M15 9l6-6M9 15l-6 6M15 15l6 6" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 3H3v6M15 3h6v6M9 21H3v-6M15 21h6v-6" />
      <path d="m3 3 6 6M21 3l-6 6M3 21l6-6M21 21l-6-6" />
    </svg>
  )
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m4 4 16 8-16 8 3-8-3-8Z" />
      <path d="M7 12h13" />
    </svg>
  )
}

const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: '等待处理',
  downloading: '获取视频',
  extracting_captions: '识别画面字幕',
  extracting_audio: '提取音频',
  transcribing: '语音转写',
  completed: '解析完成',
  no_captions: '未检测到画面字幕',
  failed: '解析失败',
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return '--:--'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remaining = Math.floor(seconds % 60)
  return hours > 0
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
}

async function readError(response: Response) {
  const payload = await response.json().catch(() => null) as { detail?: string; message?: string } | null
  return payload?.detail || payload?.message || `请求失败（HTTP ${response.status}）`
}

export default function VideoAnalysisPage() {
  const { avatar } = useAppearance()
  const [savedSession] = useState(readSavedVideoSession)
  const [mode, setMode] = useState<ImportMode>(savedSession?.mode ?? 'file')
  const [file, setFile] = useState<File | null>(null)
  const [objectUrl, setObjectUrl] = useState('')
  const [sourceUrl, setSourceUrl] = useState(savedSession?.sourceUrl ?? '')
  const [duration, setDuration] = useState(0)
  const [runtime, setRuntime] = useState<VideoRuntimeStatus | null>(null)
  const [task, setTask] = useState<VideoTask | null>(null)
  const [notice, setNotice] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [copied, setCopied] = useState(false)
  const [resultView, setResultView] = useState<ResultView>(savedSession?.resultView ?? 'original')
  const [refining, setRefining] = useState(false)
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [qaMessages, setQaMessages] = useState<QaMessage[]>(savedSession?.qaMessages ?? [])
  const [workbenchExpanded, setWorkbenchExpanded] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const qaScrollRef = useRef<HTMLDivElement>(null)
  const autoRefinementTaskRef = useRef('')

  const requestRefinement = useCallback(async (targetTask: VideoTask) => {
    if (!targetTask.transcript) return
    setRefining(true)
    setNotice('')
    try {
      const response = await fetch(`/api/v1/video/tasks/${targetTask.id}/refine`, { method: 'POST' })
      if (!response.ok) throw new Error(await readError(response))
      const payload = await response.json() as { refinedTranscript: string; model: string }
      setTask(current => current?.id === targetTask.id ? {
        ...current,
        refinedTranscript: payload.refinedTranscript,
        refinedModel: payload.model,
      } : current)
      setResultView('refined')
      setQaMessages([])
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '有效原稿筛选失败。')
    } finally {
      setRefining(false)
    }
  }, [])

  useEffect(() => {
    let active = true
    fetch('/api/v1/video/status')
      .then(async response => {
        if (!response.ok) throw new Error(await readError(response))
        return response.json() as Promise<VideoRuntimeStatus>
      })
      .then(value => { if (active) setRuntime(value) })
      .catch(error => { if (active) setNotice(error instanceof Error ? error.message : '无法读取转写服务状态。') })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    const restoreTask = async () => {
      let response = await fetch(
        savedSession?.taskId
          ? `/api/v1/video/tasks/${savedSession.taskId}`
          : '/api/v1/video/tasks/latest',
        { cache: 'no-store' },
      )
      if (!response.ok && savedSession?.taskId) {
        clearVideoSession()
        response = await fetch('/api/v1/video/tasks/latest', { cache: 'no-store' })
      }
      if (response.status === 404) return null
      if (!response.ok) throw new Error(await readError(response))
      return response.json() as Promise<VideoTask>
    }
    void restoreTask()
      .then(restoredTask => {
        if (!active || !restoredTask) return
        setTask(restoredTask)
        setMode(restoredTask.sourceType === 'url' ? 'url' : 'file')
        if (restoredTask.sourceUrl) setSourceUrl(restoredTask.sourceUrl)
        if (!savedSession && restoredTask.refinedTranscript) setResultView('refined')
      })
      .catch(error => {
        if (!active) return
        setNotice(error instanceof Error ? error.message : '无法恢复上次视频任务。')
      })
    return () => { active = false }
  }, [savedSession])

  useEffect(() => {
    if (!task?.id) return
    saveVideoSession({
      taskId: task.id,
      mode,
      sourceUrl,
      resultView,
      qaMessages,
    })
  }, [mode, qaMessages, resultView, sourceUrl, task?.id])

  useEffect(() => {
    if (!file) {
      setObjectUrl('')
      return
    }
    const nextUrl = URL.createObjectURL(file)
    setObjectUrl(nextUrl)
    return () => URL.revokeObjectURL(nextUrl)
  }, [file])

  useEffect(() => {
    if (!task || ['completed', 'no_captions', 'failed'].includes(task.status)) return
    let active = true
    const refresh = async () => {
      try {
        const response = await fetch(`/api/v1/video/tasks/${task.id}`, { cache: 'no-store' })
        if (!response.ok) throw new Error(await readError(response))
        const nextTask = await response.json() as VideoTask
        if (active) setTask(nextTask)
      } catch (error) {
        if (active) setNotice(error instanceof Error ? error.message : '任务状态更新失败。')
      }
    }
    const timer = window.setInterval(refresh, 1200)
    void refresh()
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [task?.id, task?.status])

  useEffect(() => {
    if (
      !task
      || task.status !== 'completed'
      || !task.transcript
      || task.refinedTranscript
      || autoRefinementTaskRef.current === task.id
    ) return
    autoRefinementTaskRef.current = task.id
    setResultView('refined')
    void requestRefinement(task)
  }, [requestRefinement, task?.id, task?.status, task?.transcript, task?.refinedTranscript])

  useEffect(() => {
    if (resultView !== 'qa') return
    qaScrollRef.current?.scrollTo({ top: qaScrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [asking, qaMessages, resultView])

  useEffect(() => {
    if (!workbenchExpanded) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setWorkbenchExpanded(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [workbenchExpanded])

  const openFilePicker = () => {
    const input = fileInputRef.current
    if (!input) return
    input.value = ''
    input.click()
  }

  const chooseFile = (nextFile: File | null) => {
    clearVideoSession()
    setFile(nextFile)
    setDuration(0)
    setTask(null)
    setNotice('')
    setCopied(false)
    setResultView('original')
    setQuestion('')
    setQaMessages([])
    if (nextFile) void submitFile(nextFile)
  }

  async function submitFile(selectedFile: File | null = file) {
    if (!selectedFile) {
      setNotice('请先选择本地视频。')
      return
    }
    setSubmitting(true)
    setNotice('')
    try {
      const form = new FormData()
      form.append('file', selectedFile)
      const response = await fetch('/api/v1/video/tasks/upload', { method: 'POST', body: form })
      if (!response.ok) throw new Error(await readError(response))
      setTask(await response.json() as VideoTask)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '视频上传失败。')
    } finally {
      setSubmitting(false)
    }
  }

  const submitUrl = async () => {
    const value = sourceUrl.trim()
    if (!/^https?:\/\//i.test(value)) {
      setNotice('请输入以 http:// 或 https:// 开头的公开视频链接。')
      return
    }
    setSubmitting(true)
    setNotice('')
    setTask(null)
    try {
      const response = await fetch('/api/v1/video/tasks/url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: value }),
      })
      if (!response.ok) throw new Error(await readError(response))
      setTask(await response.json() as VideoTask)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '视频链接提交失败。')
    } finally {
      setSubmitting(false)
    }
  }

  const seekTo = (seconds: number) => {
    const player = videoRef.current
    if (!player) return
    player.currentTime = seconds
    void player.play()
  }

  const copyTranscript = async () => {
    const content = resultView === 'refined' ? task?.refinedTranscript : task?.transcript
    if (!content) return
    await navigator.clipboard.writeText(content)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  const refineTranscript = async () => {
    if (!task?.transcript || refining) return
    await requestRefinement(task)
  }

  const askAboutTranscript = async () => {
    const value = question.trim()
    if (!task?.refinedTranscript || !value || asking) return
    const history = qaMessages.slice(-12)
    setAsking(true)
    setNotice('')
    setQuestion('')
    setQaMessages(current => [...current, { role: 'user', content: value }])
    try {
      const response = await fetch(`/api/v1/video/tasks/${task.id}/questions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: value, history }),
      })
      if (!response.ok) throw new Error(await readError(response))
      const payload = await response.json() as { answer: string }
      setQaMessages(current => [...current, { role: 'assistant', content: payload.answer }])
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '灵犀策划暂时不可用。')
    } finally {
      setAsking(false)
    }
  }

  const download = async (format: ExportFormat) => {
    if (!task) return
    const response = await fetch(`/api/v1/video/tasks/${task.id}/export?format=${format}`)
    if (!response.ok) {
      setNotice(await readError(response))
      return
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${file?.name || task.sourceName || 'video-transcript'}.${format === 'text' ? 'txt' : format}`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const effectiveDuration = task?.duration || duration
  const previewUrl = objectUrl || (task?.sourceType === 'upload' ? `/api/v1/video/tasks/${task.id}/media` : '')
  const hasTranscript = task?.status === 'completed' && task.segments.length > 0
  const isWorking = task && !['completed', 'no_captions', 'failed'].includes(task.status)
  const serviceReady = Boolean(runtime?.ocrReady || runtime?.providerReady)
  const transcriptMethod = task?.transcriptSource === 'screen_ocr'
    ? '画面字幕 OCR'
    : task?.transcriptSource === 'speech'
      ? '本地语音转写'
      : '等待解析'

  return (
    <div className="creator-tool-page video-analysis-page">
      <CreatorPageHeader
        title="视频解析"
        description="上传视频或粘贴公开视频链接，自动提取带时间轴的字幕与完整文案。"
        actions={<Link className="btn btn-default" to="/create/drafts">返回创作工作台</Link>}
      />

      <section className="creator-surface video-import-surface">
        <div className="video-runtime-row">
          <div className="creator-mode-tabs" role="tablist" aria-label="视频导入方式">
            <button type="button" className={mode === 'file' ? 'is-active' : ''} onClick={() => setMode('file')}>本地视频</button>
            <button type="button" className={mode === 'url' ? 'is-active' : ''} onClick={() => setMode('url')}>视频链接</button>
          </div>
          <CreatorStatus tone={serviceReady ? 'success' : 'neutral'}>
            {runtime ? (runtime.ocrReady ? runtime.ocrMessage : runtime.providerMessage) : '正在检查字幕识别服务'}
          </CreatorStatus>
        </div>

        {mode === 'file' ? (
          <div className="video-file-import">
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*,audio/*"
              onChange={event => chooseFile(event.target.files?.[0] || null)}
            />
            <div>
              <strong>{file?.name || (task?.sourceType === 'upload' ? task.sourceName : '选择一个本地视频开始')}</strong>
              <span>{file ? `${formatBytes(file.size)} · 选择后自动上传并提取字幕` : task?.sourceType === 'upload' ? '已恢复上次视频与解析结果' : `最大 ${runtime?.maxFileMb || 2048} MB，最长 ${runtime?.maxDurationMinutes || 180} 分钟`}</span>
            </div>
            <div className="video-import-actions">
              <Button onClick={openFilePicker}>{file ? '更换视频' : '选择视频'}</Button>
              <Button variant="primary" disabled={!file || submitting || Boolean(isWorking) || !serviceReady} onClick={() => void submitFile()}>
                {submitting ? '正在上传' : task?.status === 'completed' ? '重新解析' : '开始解析'}
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="video-url-import">
              <input value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} placeholder="粘贴公开视频链接" />
              <Button variant="primary" disabled={submitting || Boolean(isWorking) || runtime?.urlImportEnabled === false} onClick={submitUrl}>
                {submitting ? '正在提交' : '提取字幕文案'}
              </Button>
            </div>
            <p className="video-link-hint">
              普通公开视频可直接解析；抖音触发访问限制时会自动尝试 {runtime?.cookieBrowser === 'edge' ? 'Edge' : '浏览器'} Cookie。请先在浏览器打开该视频，并完全退出浏览器后重试。
            </p>
          </>
        )}

        {task ? (
          <div className={`video-task-progress ${task.status === 'failed' ? 'is-failed' : ''}`}>
            <div><strong>{STATUS_LABELS[task.status]}</strong><span>{task.progress}%</span></div>
            <div className="video-progress-track"><span style={{ width: `${task.progress}%` }} /></div>
            {task.error ? <p>{task.error}</p> : null}
          </div>
        ) : null}
        {notice ? <p className="creator-inline-notice">{notice}</p> : null}
        {runtime && !serviceReady ? (
          <p className="video-service-hint">画面字幕识别组件尚未就绪，请重新安装项目依赖后再解析。</p>
        ) : null}
      </section>

      <div className="video-analysis-grid">
        <section className="creator-surface video-preview-panel">
          <CreatorSectionTitle title="视频预览" description={file ? `${file.name} · ${formatDuration(effectiveDuration)}` : task?.sourceName || '尚未导入视频'} />
          {previewUrl ? (
            <video
              ref={videoRef}
              className="video-local-player"
              src={previewUrl}
              controls
              onLoadedMetadata={event => setDuration(event.currentTarget.duration)}
            />
          ) : (
            <CreatorEmptyState
              title={mode === 'url' && task ? '链接视频正在解析' : '等待视频'}
              description={mode === 'url' ? '链接模式完成后会返回字幕文案；本地视频可同步预览和定位。' : '选择本地视频后，可核对画面、声音和时长。'}
            >
              {mode === 'file' ? <Button onClick={openFilePicker}>选择本地视频</Button> : null}
            </CreatorEmptyState>
          )}
          <div className="video-source-meta">
            <div><span>处理方式</span><strong>{task?.sourceType === 'url' ? '公开视频链接' : transcriptMethod}</strong></div>
            <div><span>视频时长</span><strong>{formatDuration(effectiveDuration)}</strong></div>
            <div><span>隐私状态</span><strong>{task?.sourceType === 'url' ? '仅处理公开地址' : '本机服务处理'}</strong></div>
          </div>
        </section>

        <section className="creator-surface video-transcript-panel">
          <CreatorSectionTitle
            title="字幕与时间轴"
            description={hasTranscript ? `${task.segments.length} 段真实画面字幕 · 点击可定位` : '优先识别视频画面中真实出现的文字。'}
            action={<CreatorStatus tone={task?.status === 'completed' ? 'success' : 'neutral'}>{task ? STATUS_LABELS[task.status] : '待解析'}</CreatorStatus>}
          />
          {hasTranscript ? (
            <div className="video-segment-list">
              {task.segments.map(segment => (
                <button key={segment.id} type="button" onClick={() => seekTo(segment.start)} disabled={!previewUrl}>
                  <time>{formatDuration(segment.start)}</time>
                  <span>{segment.text}</span>
                </button>
              ))}
            </div>
          ) : (
            <CreatorEmptyState
              title={task?.status === 'failed' ? '本次解析未完成' : task?.status === 'no_captions' ? '未检测到画面字幕' : '暂无字幕内容'}
              description={task?.error || (task?.status === 'no_captions' ? '系统没有在采样画面中发现稳定字幕，不会用语音模型编造内容。' : '提交视频后，系统会逐帧识别真实画面文字并生成时间轴。')}
            />
          )}
          <div className="video-capability-list">
            <div><strong>画面字幕</strong><span>RapidOCR 识别视频中真实出现的文字</span></div>
            <div><strong>时间定位</strong><span>本地视频支持点击字幕跳转</span></div>
            <div><strong>标准导出</strong><span>支持 TXT、SRT 和 VTT</span></div>
          </div>
        </section>

        <aside className={`creator-surface video-insight-panel${workbenchExpanded ? ' is-expanded' : ''}`}>
          <CreatorSectionTitle
            title="内容工作台"
            description="保留原字稿，使用 DeepSeek 筛选有效原句，并由灵犀策划围绕稿件继续问答。"
            action={(
              <button
                type="button"
                className="video-workbench-expand"
                onClick={() => setWorkbenchExpanded(current => !current)}
                aria-label={workbenchExpanded ? '还原内容工作台' : '放大内容工作台'}
                title={workbenchExpanded ? '还原内容工作台（Esc）' : '放大内容工作台'}
              >
                <ExpandIcon expanded={workbenchExpanded} />
              </button>
            )}
          />
          <div className="video-result-switch" role="tablist" aria-label="解析结果类别">
            <button type="button" className={resultView === 'original' ? 'is-active' : ''} onClick={() => setResultView('original')}>原字稿</button>
            <button type="button" className={resultView === 'refined' ? 'is-active' : ''} onClick={() => setResultView('refined')}>有效原稿</button>
            <button type="button" className={resultView === 'qa' ? 'is-active' : ''} onClick={() => setResultView('qa')}>灵犀策划</button>
          </div>
          {resultView === 'original' ? (
            task?.status === 'completed' && task.transcript ? (
              <div className="video-transcript-copy">{task.transcript}</div>
            ) : (
              <CreatorEmptyState title="尚未生成原字稿" description="视频解析完成后，会在这里保留未经大模型改写的原始文字。" />
            )
          ) : resultView === 'refined' ? (
            task?.refinedTranscript ? (
              <div className="video-transcript-copy">{task.refinedTranscript}</div>
            ) : (
              <CreatorEmptyState title="尚未生成有效原稿" description="DeepSeek 只摘取有价值的原句，不总结、不改写，并删除重复、噪声和无关内容。">
                <Button variant="primary" disabled={!task?.transcript || refining} onClick={refineTranscript}>
                  {refining ? 'DeepSeek 正在筛选' : '筛选有效原句'}
                </Button>
              </CreatorEmptyState>
            )
          ) : task?.refinedTranscript ? (
            <div className="video-qa-workspace">
              <div className="video-qa-messages" ref={qaScrollRef} aria-live="polite">
                {qaMessages.length ? qaMessages.map((message, index) => (
                  <article key={`${message.role}-${index}`} className={`video-qa-message is-${message.role}`}>
                    <div className="video-qa-avatar" aria-hidden="true">
                      <img src={message.role === 'user' ? (avatar?.url ?? DEFAULT_AVATAR) : LINGXI_AVATAR} alt="" />
                    </div>
                    <div className="video-qa-bubble">
                      <strong>{message.role === 'user' ? '你' : '灵犀策划'}</strong>
                      <p>{message.content}</p>
                    </div>
                  </article>
                )) : (
                  <div className="video-qa-intro">
                    <span><img src={LINGXI_AVATAR} alt="" /></span>
                    <strong>灵犀策划已就绪</strong>
                    <p>可以询问核心观点、关键步骤、数字信息，回答只依据当前稿件。</p>
                  </div>
                )}
                {asking ? (
                  <div className="video-qa-thinking">
                    <span><img src={LINGXI_AVATAR} alt="" /></span>
                    <p><i /><i /><i />DeepSeek 正在阅读稿件…</p>
                  </div>
                ) : null}
              </div>
              <div className="video-qa-composer">
                <textarea
                  value={question}
                  onChange={event => setQuestion(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      void askAboutTranscript()
                    }
                  }}
                  placeholder="围绕有效原稿提问，例如：这段内容的核心观点是什么？"
                  rows={3}
                />
                <button
                  type="button"
                  className="video-qa-send"
                  disabled={!question.trim() || asking}
                  onClick={askAboutTranscript}
                  aria-label="发送问题"
                  title="发送问题"
                >
                  <SendIcon />
                </button>
              </div>
            </div>
          ) : (
            <CreatorEmptyState title="请先生成有效原稿" description="灵犀策划只读取有效原稿，不会脱离稿件自由发挥。">
              <Button variant="primary" disabled={!task?.transcript || refining} onClick={refineTranscript}>
                {refining ? 'DeepSeek 正在筛选' : '先筛选有效原句'}
              </Button>
            </CreatorEmptyState>
          )}
          {resultView !== 'qa' ? (
            <div className="video-export-actions">
              <Button disabled={resultView === 'refined' ? !task?.refinedTranscript : !task?.transcript} onClick={copyTranscript}>{copied ? '已复制' : resultView === 'refined' ? '复制有效原稿' : '复制原字稿'}</Button>
              {resultView === 'original' ? (
                <>
                  <Button disabled={!hasTranscript} onClick={() => download('text')}>TXT</Button>
                  <Button disabled={!hasTranscript} onClick={() => download('srt')}>SRT</Button>
                  <Button disabled={!hasTranscript} onClick={() => download('vtt')}>VTT</Button>
                </>
              ) : null}
            </div>
          ) : null}
          <small className="creator-control-note">
            {resultView === 'original' ? `结果来源：${transcriptMethod}` : resultView === 'refined' ? `筛选模型：${task?.refinedModel || 'DeepSeek'}` : '回答范围：仅限当前有效原稿'}
          </small>
        </aside>
      </div>
    </div>
  )
}
