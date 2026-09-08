interface ChipProps {
  label: string
  variant?: 'default' | 'strong' | 'ok' | 'bad' | 'cyan' | 'amber' | 't1' | 't2'
  className?: string
}

const VARIANT_CLASS: Record<string, string> = {
  strong: 'chip-strong', ok: 'chip-ok', bad: 'chip-bad',
  cyan: 'chip-cyan', amber: 'chip-amber',
  t1: 'chip-tier-t1', t2: 'chip-tier-t2',
}

export default function Chip({ label, variant = 'default', className = '' }: ChipProps) {
  const v = VARIANT_CLASS[variant] || ''
  return <span className={`chip ${v}${className ? ` ${className}` : ''}`}>{label}</span>
}
