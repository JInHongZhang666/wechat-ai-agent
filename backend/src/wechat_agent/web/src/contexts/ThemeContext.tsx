import { createContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import type { ThemeMode, ThemeActual } from '../types'

const STORAGE_KEY = 'ai-cabin-theme'

function getSystemTheme(): ThemeActual {
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function resolveTheme(mode: ThemeMode): ThemeActual {
  return mode === 'auto' ? getSystemTheme() : mode
}

export interface ThemeContextValue {
  mode: ThemeMode
  actual: ThemeActual
  setMode: (mode: ThemeMode) => void
}

export const ThemeContext = createContext<ThemeContextValue>({
  mode: 'auto',
  actual: 'dark',
  setMode: () => {},
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved === 'dark' || saved === 'light' || saved === 'auto') return saved
    } catch { /* storage disabled */ }
    return 'auto'
  })

  const actual = resolveTheme(mode)

  const setMode = useCallback((newMode: ThemeMode) => {
    document.documentElement.classList.add('theme-no-transition')
    setModeState(newMode)
    try {
      localStorage.setItem(STORAGE_KEY, newMode)
    } catch { /* storage disabled */ }
    requestAnimationFrame(() => {
      document.documentElement.classList.remove('theme-no-transition')
    })
  }, [])

  useEffect(() => {
    const root = document.documentElement
    root.setAttribute('data-theme', actual)
    root.setAttribute('data-theme-mode', mode)
    const meta = document.getElementById('theme-color-dynamic')
    if (meta) {
      meta.setAttribute('content', actual === 'light' ? '#f4f5f6' : '#10151c')
    }
  }, [actual, mode])

  useEffect(() => {
    if (mode !== 'auto') return
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const handler = () => {
      const newActual = getSystemTheme()
      document.documentElement.setAttribute('data-theme', newActual)
      const meta = document.getElementById('theme-color-dynamic')
      if (meta) {
        meta.setAttribute('content', newActual === 'light' ? '#f4f5f6' : '#10151c')
      }
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [mode])

  return (
    <ThemeContext.Provider value={{ mode, actual, setMode }}>
      {children}
    </ThemeContext.Provider>
  )
}
