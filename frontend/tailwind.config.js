/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // ── Brand colors (existing) ──
        ink: '#14231f',
        muted: '#66756f',
        paper: '#f5f1e8',
        surface: '#fffdf8',
        line: '#d9d5ca',
        green: { DEFAULT: '#0f6b53', soft: '#dff0e7' },
        orange: '#e47636',
        red: '#a94036',

        // ── Design Token mapped colors ──
        // Dark theme tokens as Tailwind colors
        'bg-0': 'var(--bg-0, #0f1117)',
        'bg-1': 'var(--bg-1, #161822)',
        'bg-2': 'var(--bg-2, #1c1f2e)',
        'surface-0': 'var(--surface-0, #161822)',
        'surface-1': 'var(--surface-1, #1c1f2e)',
        'surface-2': 'var(--surface-2, #222639)',
        'surface-card': 'var(--surface-card, #1a1d2b)',
        'text-0': 'var(--text-0, #f1f5f9)',
        'text-1': 'var(--text-1, #cbd5e1)',
        'text-2': 'var(--text-2, #94a3b8)',
        'border-soft': 'var(--border-soft, #1e2235)',
        'border-emphasis': 'var(--border-emphasis, rgba(59,130,246,0.125))',

        // Accent colors
        'accent-amber': 'var(--accent-amber, #f59e0b)',
        'accent-cyan': 'var(--accent-cyan, #06b6d4)',
        'accent-emerald': 'var(--accent-emerald, #10b981)',
        'accent-rose': 'var(--accent-rose, #f43f5e)',
        'theme-accent': 'var(--theme-accent, #3b82f6)',
      },
      fontFamily: {
        sans: [
          '"Microsoft YaHei UI"',
          '"PingFang SC"',
          'system-ui',
          'sans-serif',
        ],
        mono: ['"SF Mono"', '"Fira Code"', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: '0 18px 50px rgba(38, 55, 48, .09)',
        sm: '0 10px 30px rgba(38, 55, 48, .05)',
        brand: '0 10px 22px rgba(15, 107, 83, .24)',
        'tl-card': 'var(--shadow-card)',
        'tl-card-hover': 'var(--shadow-card-hover)',
      },
      borderRadius: {
        'tl-sm': 'var(--radius-sm)',
        'tl': 'var(--radius)',
        'tl-lg': 'var(--radius-lg)',
      },
      animation: {
        'fade-in': 'fadeIn var(--dur-enter, 350ms) ease-out',
        'slide-in': 'slideIn var(--dur-enter, 350ms) ease-out',
        skeleton: 'skel 1.5s infinite linear',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideIn: {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        skel: {
          '0%': { backgroundPosition: '-200px 0' },
          '100%': { backgroundPosition: 'calc(200px + 100%) 0' },
        },
      },
    },
  },
  plugins: [],
};
