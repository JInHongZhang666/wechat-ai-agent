import { useState, useCallback } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'

export default function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const closeSidebar = useCallback(() => setSidebarOpen(false), [])

  return (
    <div className="app-shell">
      {/* Desktop Sidebar */}
      <div className={`sidebar-wrapper${sidebarOpen ? ' is-open' : ''}`}>
        <Sidebar />
      </div>

      {/* Mobile Backdrop */}
      <div
        className={`sidebar-backdrop${sidebarOpen ? ' is-open' : ''}`}
        onClick={closeSidebar}
      />

      {/* Mobile Top Bar */}
      <div className="app-mobile-bar">
        <button
          className="app-hamburger"
          onClick={() => setSidebarOpen(prev => !prev)}
          aria-label="菜单"
          style={{
            width: 'var(--touch-target)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-0)',
            background: 'none',
            border: 'none',
            fontSize: '20px',
            cursor: 'pointer',
          }}
        >
          ☰
        </button>
        <div style={{
          textAlign: 'center',
          fontWeight: 700,
          fontSize: '15px',
          color: 'var(--text-0)',
        }}>
          AI 信息小屋
        </div>
        <div style={{ width: 'var(--touch-target)' }} />
      </div>

      {/* Main Content */}
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  )
}
