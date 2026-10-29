import { NavLink, Link, useLocation } from 'react-router-dom'
import ThemeToggle from './ThemeToggle'

interface NavItem {
  label: string
  path: string
  icon: string
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

const AGENT_ITEMS: NavItem[] = [
  { label: '灵犀探讯', path: '/agent', icon: '✦' },
]

const CREATE_ITEMS: NavItem[] = [
  { label: '工作台', path: '/create/drafts', icon: '▦' },
  { label: '视频解析', path: '/create/video', icon: '◉' },
  { label: '会员中心', path: '/create/membership', icon: '♙' },
  { label: '公众号', path: '/create/wechat', icon: '☻' },
  { label: '提示词词库', path: '/create/prompts', icon: '♧' },
  { label: '设置', path: '/create/settings', icon: '⚙' },
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

        <div className="side-group">智能助手</div>
        {AGENT_ITEMS.map(item => (
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

        <div className="side-group">内容创作</div>
        {CREATE_ITEMS.map(item => {
          const isActive = location.pathname === item.path
            || (item.path === '/create/settings' && location.pathname.startsWith(`${item.path}/`))
          return (
          <Link
            key={item.path}
            to={item.path}
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
