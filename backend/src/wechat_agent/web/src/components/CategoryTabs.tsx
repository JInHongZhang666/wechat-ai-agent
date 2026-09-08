import { NavLink } from 'react-router-dom'

const CATEGORIES = [
  { label: '全部', path: '/' },
  { label: '模型', path: '/category/ai-models' },
  { label: '产品', path: '/category/ai-products' },
  { label: '行业', path: '/category/industry' },
  { label: '论文', path: '/category/paper' },
  { label: '教程', path: '/category/tip' },
] as const

export default function CategoryTabs() {
  return (
    <nav className="category-tabs" aria-label="精选分类">
      {CATEGORIES.map(item => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.path === '/'}
          className={({ isActive }) =>
            `category-tab${isActive ? ' is-active' : ''}`
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}
