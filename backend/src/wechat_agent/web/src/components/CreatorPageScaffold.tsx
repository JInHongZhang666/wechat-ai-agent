import type { ReactNode } from 'react'

export function CreatorPageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description: string
  actions?: ReactNode
}) {
  return (
    <header className="creator-page-head">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="creator-page-actions">{actions}</div> : null}
    </header>
  )
}

export function CreatorSectionTitle({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="creator-section-title">
      <div>
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {action}
    </div>
  )
}

export function CreatorEmptyState({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children?: ReactNode
}) {
  return (
    <div className="creator-empty-state">
      <span className="creator-empty-mark" aria-hidden="true" />
      <strong>{title}</strong>
      <p>{description}</p>
      {children}
    </div>
  )
}

export function CreatorStatus({
  tone = 'neutral',
  children,
}: {
  tone?: 'neutral' | 'success' | 'warning' | 'accent'
  children: ReactNode
}) {
  return <span className={`creator-status is-${tone}`}>{children}</span>
}
