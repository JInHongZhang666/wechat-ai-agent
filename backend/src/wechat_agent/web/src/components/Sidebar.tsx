import { NavLink, Link, useLocation } from 'react-router-dom'
import ThemeToggle from './ThemeToggle'

interface NavItem {
  label: string
  path: string
  icon: string
  hash?: string
}

interface NavGroup {
  label: string
  items: NavItem[]
}

const NEWS_GROUPS: NavGroup[] = [
  {
    label: '内容',
    items: [
      { label: '精选', path: '/', icon: '⌁' },
      { label: '全部 AI 动态', path: '/all', icon: '≡' },
      { label: '热点榜', path: '/hot', icon: '◔' },
      { label: 'AI 日报', path: '/daily', icon: '▤' },
      { label: '主题', path: '/topics', icon: '▦' },
    ],
  },
]

const CREATE_ITEMS: NavItem[] = [
  { label: '工作台', path: '/create/drafts', icon: '▦' },
  { label: '视频解析', path: '/create/drafts', hash: '#video-parser', icon: '◉' },
  { label: '会员中心', path: '/create/drafts', hash: '#membership', icon: '♙' },
  { label: '公众号', path: '/create/drafts', hash: '#wechat-account', icon: '☻' },
  { label: '提示词词库', path: '/create/drafts', hash: '#prompt-library', icon: '♧' },
  { label: '设置', path: '/create/drafts', hash: '#writing-settings', icon: '⚙' },
]

const OPS_ITEMS: NavItem[] = [
  { label: '人工标注', path: '/ops/evaluation', icon: '◎' },
]

export default function Sidebar() {
  const location = useLocation()

  return (
    <aside className="sidebar">
      <Link to="/" className="sidebar-brand">
        <span className="sidebar-brand-en">AI CABIN</span>
        <span className="sidebar-brand-cn">信息小屋</span>
      </Link>

      <nav className="side-nav">
        {NEWS_GROUPS.map(group => (
          <div key={group.label}>
            <div className="side-group">{group.label}</div>
            {group.items.map(item => (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === '/'}
                className={({ isActive }) =>
                  `side-link${isActive ? ' side-link-active' : ''}`
                }
              >
                <span className="side-icon">{item.icon}</span>
                <span className="side-label">{item.label}</span>
              </NavLink>
            ))}
          </div>
        ))}

        <div className="side-group">内容创作</div>
        {CREATE_ITEMS.map(item => {
          const isActive = location.pathname === item.path
            && (item.hash ? location.hash === item.hash : !location.hash)
          return (
          <Link
            key={`${item.path}${item.hash || ''}`}
            to={`${item.path}${item.hash || ''}`}
            className={`side-link${isActive ? ' side-link-active' : ''}`}
          >
            <span className="side-icon">{item.icon}</span>
            <span className="side-label">{item.label}</span>
          </Link>
          )
        })}


        <div className="side-group">系统</div>
        {OPS_ITEMS.map(item => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              `side-link${isActive ? ' side-link-active' : ''}`
            }
          >
            <span className="side-icon">{item.icon}</span>
            <span className="side-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <ThemeToggle />

      <div className="sidebar-footer">
        <span className="sidebar-filing">© AI 信息小屋</span>
      </div>
    </aside>
  )
}
