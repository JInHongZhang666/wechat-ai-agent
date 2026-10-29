import { useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import {
  CreatorEmptyState,
  CreatorPageHeader,
  CreatorSectionTitle,
} from '../components/CreatorPageScaffold'

interface PromptItem {
  id: string
  name: string
  category: string
  description: string
  content: string
  favorite: boolean
  builtIn: boolean
}

const STORAGE_KEY = 'ai-cabin.prompt-library.v1'
const BUILT_IN_PROMPTS: PromptItem[] = [
  {
    id: 'wechat-article',
    name: '公众号文章策划助手',
    category: '写作创作',
    description: '从主题、读者收益和证据边界出发，生成可执行的文章策划。',
    content: '请围绕「{{主题}}」为「{{目标读者}}」设计一篇公众号文章策划。\n\n要求：\n1. 先给出中心论点与读者收益；\n2. 所有事实结论标注需要核验的来源；\n3. 给出原创角度与分段提纲；\n4. 不虚构亲测、采访或个人经历。',
    favorite: true,
    builtIn: true,
  },
  {
    id: 'fact-check',
    name: '信息核验清单',
    category: '内容审校',
    description: '检查数字、时间、主体和因果结论是否有可靠证据。',
    content: '请审校以下内容：{{正文}}\n\n逐项列出：可核验事实、当前证据、证据缺口、风险等级和修改建议。不要把推测写成事实。',
    favorite: false,
    builtIn: true,
  },
  {
    id: 'title-options',
    name: '克制型标题方案',
    category: '写作创作',
    description: '生成清楚、有信息量且避免夸张承诺的标题。',
    content: '根据文章核心内容「{{核心内容}}」生成 8 个公众号标题。标题要具体、可理解，不使用“震惊”“颠覆”“所有人”等夸张表达。',
    favorite: false,
    builtIn: true,
  },
  {
    id: 'video-highlights',
    name: '视频观点提取',
    category: '视频解析',
    description: '从带时间戳字幕中提取观点、证据和可引用片段。',
    content: '阅读以下带时间戳字幕：{{字幕}}\n\n输出核心观点、证据片段、对应时间、行动建议和仍需核验的说法。',
    favorite: false,
    builtIn: true,
  },
]

function loadPrompts() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') as PromptItem[] | null
    return Array.isArray(saved) && saved.length ? saved : BUILT_IN_PROMPTS
  } catch {
    return BUILT_IN_PROMPTS
  }
}

export default function PromptLibraryPage() {
  const [prompts, setPrompts] = useState<PromptItem[]>(loadPrompts)
  const [selectedId, setSelectedId] = useState(prompts[0]?.id || '')
  const [category, setCategory] = useState('全部提示词')
  const [search, setSearch] = useState('')
  const [editor, setEditor] = useState<PromptItem | null>(prompts[0] || null)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prompts))
  }, [prompts])

  const categories = useMemo(() => {
    const counts = new Map<string, number>()
    prompts.forEach(prompt => counts.set(prompt.category, (counts.get(prompt.category) || 0) + 1))
    return [...counts.entries()]
  }, [prompts])

  const filtered = useMemo(() => prompts.filter(prompt => {
    if (category === '我的收藏' && !prompt.favorite) return false
    if (category !== '全部提示词' && category !== '我的收藏' && prompt.category !== category) return false
    const needle = search.trim().toLowerCase()
    return !needle || `${prompt.name} ${prompt.description} ${prompt.content}`.toLowerCase().includes(needle)
  }), [prompts, category, search])

  const selectPrompt = (prompt: PromptItem) => {
    setSelectedId(prompt.id)
    setEditor({ ...prompt })
    setNotice('')
  }

  const createPrompt = () => {
    const prompt: PromptItem = {
      id: crypto.randomUUID(),
      name: '未命名提示词',
      category: '自定义',
      description: '',
      content: '请在这里编写提示词，并使用 {{变量名}} 标记可替换内容。',
      favorite: false,
      builtIn: false,
    }
    setPrompts(current => [prompt, ...current])
    setCategory('全部提示词')
    selectPrompt(prompt)
  }

  const savePrompt = () => {
    if (!editor) return
    const normalized = { ...editor, name: editor.name.trim() || '未命名提示词' }
    setPrompts(current => current.map(prompt => prompt.id === normalized.id ? normalized : prompt))
    setEditor(normalized)
    setNotice('提示词已保存到本机。')
  }

  const duplicatePrompt = () => {
    if (!editor) return
    const copy = { ...editor, id: crypto.randomUUID(), name: `${editor.name} · 副本`, builtIn: false }
    setPrompts(current => [copy, ...current])
    selectPrompt(copy)
  }

  const copyPrompt = async () => {
    if (!editor) return
    await navigator.clipboard.writeText(editor.content)
    setNotice('提示词内容已复制。')
  }

  const toggleFavorite = (id: string) => {
    setPrompts(current => current.map(prompt => prompt.id === id ? { ...prompt, favorite: !prompt.favorite } : prompt))
    setEditor(current => current?.id === id ? { ...current, favorite: !current.favorite } : current)
  }

  return (
    <div className="creator-tool-page prompt-library-page">
      <CreatorPageHeader
        title="提示词词库"
        description="收藏、检索和维护可复用提示词；自定义内容仅保存在当前浏览器。"
        actions={<Button variant="primary" onClick={createPrompt}>新建提示词</Button>}
      />

      {notice ? <p className="creator-inline-notice">{notice}</p> : null}

      <div className="prompt-library-grid">
        <aside className="creator-surface prompt-category-rail">
          <CreatorSectionTitle title="分类" />
          <button type="button" className={category === '全部提示词' ? 'is-active' : ''} onClick={() => setCategory('全部提示词')}><span>全部提示词</span><strong>{prompts.length}</strong></button>
          <button type="button" className={category === '我的收藏' ? 'is-active' : ''} onClick={() => setCategory('我的收藏')}><span>我的收藏</span><strong>{prompts.filter(prompt => prompt.favorite).length}</strong></button>
          <div className="prompt-category-divider" />
          {categories.map(([name, count]) => <button type="button" className={category === name ? 'is-active' : ''} onClick={() => setCategory(name)} key={name}><span>{name}</span><strong>{count}</strong></button>)}
          <div className="prompt-storage-note"><strong>本地词库</strong><span>内容不会自动上传到服务器。</span></div>
        </aside>

        <section className="creator-surface prompt-list-panel">
          <div className="prompt-list-toolbar">
            <input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索提示词名称、用途或内容" />
            <span>{filtered.length} 条结果</span>
          </div>
          <div className="prompt-list-head"><span>提示词名称</span><span>分类</span><span>收藏</span></div>
          <div className="prompt-list-scroll">
            {filtered.length === 0 ? <CreatorEmptyState title="没有匹配的提示词" description="换个关键词或分类继续查找。" /> : filtered.map(prompt => (
              <button type="button" className={`prompt-list-row${selectedId === prompt.id ? ' is-selected' : ''}`} onClick={() => selectPrompt(prompt)} key={prompt.id}>
                <div><strong>{prompt.name}</strong><span>{prompt.description || '暂无用途说明'}</span></div>
                <span>{prompt.category}</span>
                <i
                  role="button"
                  tabIndex={0}
                  aria-label={prompt.favorite ? '取消收藏' : '收藏'}
                  className={prompt.favorite ? 'is-favorite' : ''}
                  onClick={event => { event.stopPropagation(); toggleFavorite(prompt.id) }}
                  onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleFavorite(prompt.id) } }}
                >{prompt.favorite ? '已收藏' : '收藏'}</i>
              </button>
            ))}
          </div>
        </section>

        <aside className="creator-surface prompt-editor-panel">
          <CreatorSectionTitle title="提示词详情" action={editor?.builtIn ? <span className="prompt-built-in">内置模板</span> : null} />
          {!editor ? <CreatorEmptyState title="选择提示词" description="可在这里查看或编辑提示词。" /> : (
            <div className="prompt-editor-form">
              <label><span>名称</span><input value={editor.name} onChange={event => setEditor(current => current ? { ...current, name: event.target.value } : current)} /></label>
              <label><span>分类</span><input value={editor.category} onChange={event => setEditor(current => current ? { ...current, category: event.target.value } : current)} /></label>
              <label><span>用途说明</span><textarea rows={3} value={editor.description} onChange={event => setEditor(current => current ? { ...current, description: event.target.value } : current)} placeholder="说明这个提示词适合解决什么问题" /></label>
              <label className="prompt-body-field"><span>提示词内容</span><textarea value={editor.content} onChange={event => setEditor(current => current ? { ...current, content: event.target.value } : current)} /></label>
              <div className="prompt-variable-hint"><strong>变量写法</strong><span>使用 {'{{变量名}}'} 标记每次使用时需要替换的内容。</span></div>
              <div className="prompt-editor-actions">
                <Button variant="primary" onClick={savePrompt}>保存</Button>
                <Button onClick={copyPrompt}>复制内容</Button>
                <Button onClick={duplicatePrompt}>创建副本</Button>
                <Button onClick={() => toggleFavorite(editor.id)}>{editor.favorite ? '取消收藏' : '加入收藏'}</Button>
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}
