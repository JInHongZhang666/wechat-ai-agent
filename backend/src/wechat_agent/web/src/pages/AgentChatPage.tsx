import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import Button from '../components/Button'
import { CreatorPageHeader } from '../components/CreatorPageScaffold'
import { useAppearance } from '../hooks/useAppearance'
import { apiGet, apiPost } from '../utils/api'

type ChatRole = 'user' | 'assistant'

interface ChatMessage {
  role: ChatRole
  content: string
}

interface AgentStatus {
  enabled: boolean
  model_configured: boolean
  conversations_supported: boolean
  tools: string[]
}

interface AgentRun {
  status: 'completed' | 'needs_input' | 'failed' | 'needs_evidence' | 'stopped'
  error_code: string | null
  answer: string | null
  tool_calls: number
}

interface ConversationResponse {
  conversation_id: string
  status: AgentRun['status']
  messages: ChatMessage[]
  last_run: AgentRun
}

const STARTERS = [
  '找最近7天 OpenAI 的产品更新',
  '检索最近30天国内大模型的重要发布',
  '研究最近7天 AI Agent 领域值得关注的事件',
]
const DEFAULT_AVATAR = '/assets/user-avatar-default.jpg'
const LINGXI_AVATAR = '/assets/lingxi-ai-avatar.webp'

const INLINE_PATTERN = /(\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s)\]}>。，！？、]+)/g
const MARKDOWN_LINK_PATTERN = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m4 4 16 8-16 8 3-8-3-8Z" />
      <path d="M7 12h13" />
    </svg>
  )
}

function renderInline(content: string): ReactNode {
  return content.split(INLINE_PATTERN).map((part, index) => {
    const markdownLink = part.match(MARKDOWN_LINK_PATTERN)
    if (markdownLink) {
      return <a key={`${part}-${index}`} href={markdownLink[2]} target="_blank" rel="noreferrer">{markdownLink[1]}</a>
    }
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>
    }
    if (part.match(/^https?:\/\//)) {
      return <a key={`${part}-${index}`} href={part} target="_blank" rel="noreferrer">{part}</a>
    }
    return <Fragment key={index}>{part}</Fragment>
  })
}

function renderContent(content: string) {
  return content.split('\n').map((line, index) => {
    if (!line.trim()) return <span className="agent-content-line is-empty" key={index} aria-hidden="true" />
    const heading = line.match(/^#{1,3}\s+(.+)/)
    const numbered = line.match(/^(\d+)\.\s+(.+)/)
    if (heading) return <strong className="agent-content-line is-heading" key={index}>{renderInline(heading[1])}</strong>
    if (numbered) return <span className="agent-content-line is-numbered" key={index}><i>{numbered[1]}.</i><span>{renderInline(numbered[2])}</span></span>
    return <span className="agent-content-line" key={index}>{renderInline(line)}</span>
  })
}

export default function AgentChatPage() {
  const { avatar } = useAppearance()
  const [status, setStatus] = useState<AgentStatus | null>(null)
  const [conversationId, setConversationId] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    apiGet<AgentStatus>('/v1/agent/status')
      .then(value => { if (mountedRef.current) setStatus(value) })
      .catch(reason => {
        if (mountedRef.current) setError(reason instanceof Error ? reason.message : '无法读取 Agent 状态')
      })
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  const available = Boolean(status?.enabled && status?.model_configured && status?.conversations_supported)

  const send = async (draft = input) => {
    const message = draft.trim()
    if (!message || busy || !available) return
    setBusy(true)
    setError('')
    setInput('')
    setMessages(current => [...current, { role: 'user', content: message }])
    try {
      const path = conversationId
        ? `/v1/agent/conversations/${conversationId}/messages`
        : '/v1/agent/conversations'
      const response = await apiPost<ConversationResponse>(path, conversationId
        ? { message, refresh_sources: false }
        : { message, mode: 'query', refresh_sources: false })
      if (!mountedRef.current) return
      setConversationId(response.conversation_id)
      setMessages(response.messages)
      if (response.last_run.status === 'failed') {
        setError(`本次运行未完成：${response.last_run.error_code || '未知错误'}`)
      }
    } catch (reason) {
      if (!mountedRef.current) return
      setMessages(current => current.filter((_, index) => index !== current.length - 1))
      setInput(message)
      setError(reason instanceof Error ? reason.message : '发送失败，请稍后重试')
    } finally {
      if (mountedRef.current) setBusy(false)
    }
  }

  const reset = () => {
    if (busy) return
    setConversationId('')
    setMessages([])
    setInput('')
    setError('')
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send()
    }
  }

  return (
    <div className="creator-tool-page agent-chat-page">
      <CreatorPageHeader
        title="灵犀探讯"
        description="输入想了解的方向，立即搜索、补证并整理有来源的 AI 资讯。"
        actions={<Button onClick={reset} disabled={busy || messages.length === 0}>新对话</Button>}
      />

      <section className="agent-chat-shell" aria-label="灵犀探讯对话">
        <div className="agent-chat-scroll" ref={scrollRef} aria-live="polite">
          {messages.length === 0 ? (
            <div className="agent-chat-welcome">
              <div className="agent-chat-welcome-icon"><img src={LINGXI_AVATAR} alt="灵犀探讯" /></div>
              <h2>今天想追踪什么？</h2>
              <p>说一个大概方向即可，我会先按合理范围直接搜索；后续可以继续缩小范围或更正条件。</p>
              <div className="agent-starter-list">
                {STARTERS.map(starter => (
                  <button key={starter} type="button" onClick={() => void send(starter)} disabled={!available || busy}>
                    <span>{starter}</span><i aria-hidden="true">→</i>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="agent-message-list">
              {messages.map((message, index) => (
                <article className={`agent-message is-${message.role}`} key={`${message.role}-${index}`}>
                  <div className="agent-message-avatar" aria-hidden="true">
                    <img src={message.role === 'assistant' ? LINGXI_AVATAR : (avatar?.url ?? DEFAULT_AVATAR)} alt="" />
                  </div>
                  <div className="agent-message-body">
                    <span>{message.role === 'assistant' ? '灵犀探讯' : '你'}</span>
                    <div className="agent-message-content">{renderContent(message.content)}</div>
                  </div>
                </article>
              ))}
              {busy ? (
                <article className="agent-message is-assistant is-loading">
                  <div className="agent-message-avatar"><img src={LINGXI_AVATAR} alt="" /></div>
                  <div className="agent-message-body">
                    <span>灵犀探讯</span>
                    <div className="agent-message-content"><i /><i /><i /> <em>正在搜索并整理资讯…</em></div>
                  </div>
                </article>
              ) : null}
            </div>
          )}
        </div>

        <footer className="agent-composer">
          {error ? <p className="agent-chat-error" role="alert">{error}</p> : null}
          <div className="agent-composer-box">
            <textarea
              value={input}
              onChange={event => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={available ? '输入想查找的资讯，例如：最近7天 OpenAI 有哪些产品更新？' : '请先启用 Agent 并配置模型'}
              rows={2}
              disabled={!available || busy}
              aria-label="资讯需求"
            />
            <button type="button" className="agent-send-button" onClick={() => void send()} disabled={!input.trim() || !available || busy} aria-label="发送">
              <SendIcon />
            </button>
          </div>
          <small>Enter 发送，Shift + Enter 换行 · 回答仅基于项目已采集的公开资讯</small>
        </footer>
      </section>
    </div>
  )
}
