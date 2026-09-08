import { useParams } from 'react-router-dom'
import FeedPage from './FeedPage'
import type { Category } from '../types'

const CATEGORY_META: Record<Category, { title: string; subtitle: string }> = {
  'ai-models': { title: '模型发布', subtitle: '大模型发布、能力升级与重要技术变更' },
  'ai-products': { title: '产品发布', subtitle: 'AI 产品、工具与开发者能力更新' },
  industry: { title: '行业动态', subtitle: '产业、公司、政策与市场的重要变化' },
  paper: { title: '论文研究', subtitle: '值得关注的新研究、评测与技术发现' },
  tip: { title: '技巧观点', subtitle: '可操作的方法、经验与高质量观点' },
}

export default function CategoryPage() {
  const { slug } = useParams()
  const category = slug && slug in CATEGORY_META ? slug as Category : null

  if (!category) {
    return (
      <div className="page-empty-state">
        <div className="page-empty-state-icon">⚠️</div>
        <p className="page-empty-state-text">这个分类不存在。</p>
      </div>
    )
  }

  return (
    <FeedPage
      mode="selected"
      category={category}
      title={CATEGORY_META[category].title}
      subtitle={CATEGORY_META[category].subtitle}
      searchable
      showCategories
    />
  )
}
