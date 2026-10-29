import { useState, useCallback, type CSSProperties } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import { useAppearance } from '../hooks/useAppearance'
import type { AmbientEffect } from '../contexts/AppearanceContext'

type ParticleStyle = CSSProperties & Record<'--particle-x' | '--particle-delay' | '--particle-duration' | '--particle-drift' | '--particle-size', string>

const DEFAULT_BACKGROUND_VIDEO = '/assets/columbina-wallpaper-05-hq.mp4'
const DEFAULT_BACKGROUND_POSTER = '/assets/columbina-wallpaper-05-hq-poster.jpg'

const PETAL_PARTICLES = Array.from({ length: 18 }, (_, index) => ({
  x: `${(index * 37 + 11) % 100}%`,
  delay: `${-((index * 1.3) % 12).toFixed(1)}s`,
  duration: `${9 + (index % 6) * 1.4}s`,
  drift: `${-42 + (index % 7) * 14}px`,
  size: `${7 + (index % 4) * 2}px`,
}))

const RAIN_PARTICLES = Array.from({ length: 34 }, (_, index) => ({
  x: `${(index * 29 + 7) % 100}%`,
  delay: `${-((index * 0.23) % 2.2).toFixed(2)}s`,
  duration: `${0.8 + (index % 5) * 0.12}s`,
  drift: `${-18 - (index % 4) * 5}px`,
  size: `${18 + (index % 5) * 5}px`,
}))

export default function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { background, effect, backgroundFit, backgroundPositionX, backgroundPositionY } = useAppearance()

  const closeSidebar = useCallback(() => setSidebarOpen(false), [])

  return (
    <div className={`app-shell${background ? ' has-custom-background' : ''}`}>
      <div className="app-ambient-background" aria-hidden="true">
        {background ? (
          <img
            className="app-ambient-background-media is-custom-image"
            src={background.url}
            alt=""
            style={{
              objectFit: backgroundFit === 'original' ? 'none' : backgroundFit,
              objectPosition: `${backgroundPositionX}% ${backgroundPositionY}%`,
            }}
          />
        ) : (
          <video
            className="app-ambient-background-media"
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
            poster={DEFAULT_BACKGROUND_POSTER}
            disablePictureInPicture
            disableRemotePlayback
            tabIndex={-1}
          >
            <source src={DEFAULT_BACKGROUND_VIDEO} type="video/mp4" />
          </video>
        )}
        <AmbientEffectLayer effect={effect} />
      </div>

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

function AmbientEffectLayer({ effect }: { effect: AmbientEffect }) {
  if (effect === 'none') return null
  const particles = effect === 'petals' ? PETAL_PARTICLES : RAIN_PARTICLES
  return (
    <div className={`app-ambient-effect is-${effect}`}>
      {particles.map((particle, index) => (
        <i
          key={index}
          style={{
            '--particle-x': particle.x,
            '--particle-delay': particle.delay,
            '--particle-duration': particle.duration,
            '--particle-drift': particle.drift,
            '--particle-size': particle.size,
          } as ParticleStyle}
        />
      ))}
    </div>
  )
}
