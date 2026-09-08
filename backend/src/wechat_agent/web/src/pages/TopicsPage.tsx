import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { ItemsResponse, TimelineItem } from '../types'
import { apiGet } from '../utils/api'

interface TopicDefinition {
  name: string
  query: string
  description: string
  keywords: string[]
}

interface TopicGroup {
  title: string
  subtitle: string
  topics: TopicDefinition[]
}

const TOPIC_GROUPS: TopicGroup[] = [
  {
    title: '公司与模型',
    subtitle: '按厂商与模型系追踪：谁发布了什么，又赢了哪一局',
    topics: [
      topic('OpenAI / ChatGPT', 'OpenAI', 'GPT、ChatGPT、Sora 与 OpenAI 公司动态的持续追踪。', ['openai', 'chatgpt', 'gpt-', 'sora']),
      topic('Anthropic / Claude', 'Claude', 'Claude 系列模型、Claude Code、安全研究与公司进展。', ['anthropic', 'claude']),
      topic('Google / Gemini', 'Gemini', 'Google 与 DeepMind 的 Gemini、Veo 及研究产品动态。', ['google', 'deepmind', 'gemini', 'veo']),
      topic('DeepSeek', 'DeepSeek', 'DeepSeek 的模型发布、开源权重与技术报告。', ['deepseek']),
      topic('通义千问 Qwen', 'Qwen', '阿里通义千问 Qwen 系列的开放发布与能力迭代。', ['qwen', '通义', '千问']),
      topic('Kimi / 月之暗面', 'Kimi', 'Kimi 系列模型与产品演进的重点动态。', ['kimi', '月之暗面', 'moonshot']),
      topic('MiniMax', 'MiniMax', 'MiniMax 的模型、语音、多模态与产品发布。', ['minimax']),
      topic('智谱 GLM', 'GLM', '智谱 GLM 系列与开源模型、智能体能力进展。', ['智谱', 'glm']),
      topic('xAI / Grok', 'Grok', 'xAI 与 Grok 模型、产品和基础设施动态。', ['xai', 'x.ai', 'grok']),
      topic('Meta / Llama', 'Llama', 'Meta 的 Llama 开源模型与 AI 产品生态。', ['meta', 'llama']),
      topic('Microsoft / Copilot', 'Copilot', '微软 Copilot、Azure AI 与企业 AI 布局。', ['microsoft', 'copilot', 'azure ai']),
      topic('NVIDIA 英伟达', 'NVIDIA', 'GPU、CUDA、推理基础设施与 AI 生态。', ['nvidia', '英伟达', 'cuda']),
      topic('Hugging Face', 'Hugging Face', '开源社区、模型库、数据集和研究动态。', ['hugging face', 'huggingface']),
      topic('Cursor', 'Cursor', 'AI 编程产品 Cursor 的能力迭代与开发工作流。', ['cursor']),
    ],
  },
  {
    title: '技术方向',
    subtitle: '按技术领域深挖：Agent、多模态、推理与生成式能力',
    topics: [
      topic('Agent 智能体', 'Agent', '自主规划、调用工具与完成多步任务的智能体技术。', ['agent', '智能体']),
      topic('AI 编码', 'AI 编程', '编程助手、代码模型、Vibe Coding 与开发工作流。', ['coding', 'code', '编程', '代码']),
      topic('推理能力', '推理模型', '思维链、数学与逻辑推理能力的突破和评测。', ['reasoning', '推理', '思维链']),
      topic('多模态', '多模态', '视觉理解、图文混合、语音与视频输入输出。', ['multimodal', '多模态', '视觉', '语音']),
      topic('图像生成', '图像生成', '文生图模型、图像编辑能力与创作工具生态。', ['image generation', '图像生成', '文生图', '图像编辑']),
      topic('AI 视频', 'AI 视频', '文生视频、视频理解、剪辑与影视制作工具。', ['video', '视频生成', '文生视频', '视频理解']),
      topic('开源模型', '开源模型', '权重开放、开源协议、社区模型与部署方案。', ['open source', 'open-source', '开源', '权重']),
      topic('RAG / 知识库', 'RAG', '检索增强生成、知识库与企业数据接入方案。', ['rag', 'retrieval', '检索增强', '知识库']),
      topic('AI 基础设施', 'AI 基础设施', '训练、推理、算力、芯片与开发者平台。', ['inference', 'training', 'gpu', '推理部署', '训练', '算力']),
    ],
  },
  {
    title: '内容形态',
    subtitle: '按信息价值快速进入模型、产品、研究与行业内容',
    topics: [
      topic('模型发布 / 更新', '模型发布', '模型版本、能力升级、权重与 API 变更。', ['模型发布', 'model release', '模型更新', 'api']),
      topic('产品发布 / 更新', '产品发布', 'AI 产品、功能与开发工具的重要发布。', ['产品发布', 'product', 'launch', '功能更新']),
      topic('论文研究', '论文', '值得关注的新研究、基准、评测与技术发现。', ['paper', 'research', '论文', '研究']),
      topic('行业动态', 'AI 行业', '公司、监管、融资、市场与产业变化。', ['industry', '公司', '监管', '融资', '市场']),
      topic('技巧与观点', 'AI 技巧', '可操作的方法、教程、实践经验与高质量判断。', ['教程', '技巧', '观点', 'guide', 'tutorial']),
    ],
  },
]

export default function TopicsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialWindow = searchParams.get('window') === '24h' ? '24h' : '7d'
  const initialQuery = searchParams.get('q')?.trim() || ''
  const [items, setItems] = useState<TimelineItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [query, setQuery] = useState(initialQuery)
  const [windowValue, setWindowValue] = useState<'24h' | '7d'>(initialWindow)
  const [activeOnly, setActiveOnly] = useState(searchParams.get('active') === '1')

  useEffect(() => {
    setQuery(searchParams.get('q')?.trim() || '')
    setWindowValue(searchParams.get('window') === '24h' ? '24h' : '7d')
    setActiveOnly(searchParams.get('active') === '1')
  }, [searchParams])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    apiGet<ItemsResponse>(`/v1/items?mode=selected&window=${windowValue}&by=timeline&limit=100`, {
      signal: controller.signal,
    })
      .then(response => setItems(response.items))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === 'AbortError') return
        setError(reason instanceof Error ? reason.message : '主题数据加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [retryKey, windowValue])

  const topicStats = useMemo(() => {
    const stats = new Map<string, { count: number; latest: TimelineItem | null }>()
    for (const group of TOPIC_GROUPS) {
      for (const definition of group.topics) {
        const matches = items.filter(item => matchesTopic(item, definition))
        stats.set(definition.name, {
          count: matches.length,
          latest: matches[0] || null,
        })
      }
    }
    return stats
  }, [items])

  const filteredGroups = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase()
    return TOPIC_GROUPS.map(group => ({
      ...group,
      topics: group.topics
        .filter(definition => matchesQuery(definition, normalizedQuery))
        .filter(definition => !activeOnly || (topicStats.get(definition.name)?.count || 0) > 0)
        .sort((left, right) => {
          const leftStats = topicStats.get(left.name)
          const rightStats = topicStats.get(right.name)
          return (rightStats?.count || 0) - (leftStats?.count || 0) || left.name.localeCompare(right.name)
        }),
    })).filter(group => group.topics.length > 0)
  }, [activeOnly, query, topicStats])

  const activeTopicCount = useMemo(
    () => Array.from(topicStats.values()).filter(stats => stats.count > 0).length,
    [topicStats],
  )
  const latestItem = items[0] || null
  const windowLabel = windowValue === '24h' ? '24 小时' : '7 日'

  const updateFilters = (next: { query?: string; windowValue?: '24h' | '7d'; activeOnly?: boolean }) => {
    const nextQuery = next.query ?? query
    const nextWindow = next.windowValue ?? windowValue
    const nextActiveOnly = next.activeOnly ?? activeOnly
    const params = new URLSearchParams()
    if (nextQuery.trim()) params.set('q', nextQuery.trim())
    if (nextWindow === '24h') params.set('window', nextWindow)
    if (nextActiveOnly) params.set('active', '1')
    setSearchParams(params, { replace: true })
  }

  return (
    <div className="topics-page">
      <header className="topics-hero">
        <div>
          <span className="page-eyebrow">TOPICS · 主题地图</span>
          <h1>按主题看 AI</h1>
          <p>公司与模型、技术方向、内容形态，由近期精选自动映射；先看哪里热，再进入完整信息流。</p>
        </div>
        <dl className="topic-hero-stats" aria-label="主题概览">
          <div>
            <dt>近 {windowLabel}精选</dt>
            <dd>{loading ? '...' : items.length}</dd>
          </div>
          <div>
            <dt>活跃主题</dt>
            <dd>{loading ? '...' : activeTopicCount}</dd>
          </div>
          <div>
            <dt>最后更新</dt>
            <dd>{relativeTime(itemTime(latestItem))}</dd>
          </div>
        </dl>
      </header>

      <div className="topics-toolbar">
        <label className="feed-search">
          <span className="sr-only">搜索主题</span>
          <input
            className="feed-search-input"
            value={query}
            onChange={event => updateFilters({ query: event.target.value })}
            placeholder="搜索公司、模型、技术方向..."
          />
        </label>
        <div className="feed-window" aria-label="主题时间范围">
          {(['24h', '7d'] as const).map(value => (
            <button
              key={value}
              type="button"
              className={`feed-window-button${windowValue === value ? ' is-active' : ''}`}
              onClick={() => updateFilters({ windowValue: value })}
            >
              {value === '24h' ? '24 小时' : '7 天'}
            </button>
          ))}
        </div>
        <label className="topics-toggle">
          <input
            checked={activeOnly}
            onChange={event => updateFilters({ activeOnly: event.target.checked })}
            type="checkbox"
          />
          <span>只看有更新</span>
        </label>
        <span className="topics-toolbar-meta">
          {loading ? '正在同步主题热度' : `${filteredGroups.reduce((sum, group) => sum + group.topics.length, 0)} 个主题可浏览`}
        </span>
      </div>

      {error && (
        <div className="feed-error" role="alert">
          <span>加载失败：{error}</span>
          <button className="btn" type="button" onClick={() => setRetryKey(value => value + 1)}>
            重试
          </button>
        </div>
      )}

      {filteredGroups.map(group => (
        <section className="topic-group" key={group.title}>
          <div className="topic-group-head">
            <h2>{group.title}</h2>
            <p>{group.subtitle}</p>
          </div>
          <div className="topic-grid">
            {group.topics.map(definition => (
              <TopicCard
                definition={definition}
                key={definition.name}
                loading={loading}
                stats={topicStats.get(definition.name) || { count: 0, latest: null }}
                windowValue={windowValue}
              />
            ))}
          </div>
        </section>
      ))}

      {!loading && !error && filteredGroups.length === 0 && (
        <div className="page-empty-state">
          <div className="page-empty-state-text">
            {activeOnly ? '当前范围内没有匹配且有更新的主题。' : '没有匹配的主题，换个关键词试试。'}
          </div>
        </div>
      )}
    </div>
  )
}

function TopicCard({
  definition,
  loading,
  stats,
  windowValue,
}: {
  definition: TopicDefinition
  loading: boolean
  stats: { count: number; latest: TimelineItem | null }
  windowValue: '24h' | '7d'
}) {
  return (
    <Link
      className={`topic-card${stats.count > 0 ? ' is-active' : ''}`}
      to={`/all?q=${encodeURIComponent(definition.query)}&window=${windowValue}`}
    >
      <div className="topic-card-head">
        <h3>{definition.name}</h3>
        <strong>{loading ? '...' : stats.count}</strong>
      </div>
      <p>{definition.description}</p>
      {stats.latest ? (
        <small>{relativeTime(itemTime(stats.latest))} · {stats.latest.title}</small>
      ) : (
        <small>近期暂无精选更新</small>
      )}
      <span>进入主题流 →</span>
    </Link>
  )
}

function topic(
  name: string,
  query: string,
  description: string,
  keywords: string[],
): TopicDefinition {
  return { name, query, description, keywords }
}

function matchesTopic(item: TimelineItem, definition: TopicDefinition): boolean {
  const haystack = `${item.title} ${item.summary || ''} ${item.source.name}`.toLocaleLowerCase()
  return definition.keywords.some(keyword => haystack.includes(keyword.toLocaleLowerCase()))
}

function matchesQuery(definition: TopicDefinition, normalizedQuery: string): boolean {
  if (!normalizedQuery) return true
  const haystack = `${definition.name} ${definition.query} ${definition.description} ${definition.keywords.join(' ')}`.toLocaleLowerCase()
  return haystack.includes(normalizedQuery)
}

function itemTime(item: TimelineItem | null): string | null {
  return item?.timelineAt || item?.discoveredAt || item?.publishedAt || null
}

function relativeTime(value: string | null): string {
  if (!value) return '--'
  const timestamp = new Date(value).getTime()
  if (!Number.isFinite(timestamp)) return '--'
  const diffHours = Math.max(0, (Date.now() - timestamp) / 3_600_000)
  if (diffHours < 1) return '刚刚'
  if (diffHours < 24) return `${Math.floor(diffHours)} 小时前`
  return `${Math.floor(diffHours / 24)} 天前`
}
