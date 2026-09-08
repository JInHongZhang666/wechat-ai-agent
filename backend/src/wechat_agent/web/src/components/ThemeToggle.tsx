import { useTheme } from '../hooks/useTheme'
import type { ThemeMode } from '../types'

const OPTIONS: { mode: ThemeMode; label: string; icon: string }[] = [
  { mode: 'dark',  label: '深色', icon: '🌙' },
  { mode: 'auto',  label: '自动', icon: '🔄' },
  { mode: 'light', label: '浅色', icon: '☀️' },
]

export default function ThemeToggle() {
  const { mode, setMode } = useTheme()

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="主题切换">
      <div className="theme-toggle-thumb" data-pos={mode} />
      {OPTIONS.map(opt => (
        <button
          key={opt.mode}
          className={`theme-toggle-opt${mode === opt.mode ? ' theme-toggle-opt-active' : ''}`}
          role="radio"
          aria-checked={mode === opt.mode}
          aria-label={opt.label}
          onClick={() => setMode(opt.mode)}
        >
          {opt.icon}
        </button>
      ))}
    </div>
  )
}
