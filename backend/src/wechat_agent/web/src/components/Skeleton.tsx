interface SkeletonProps {
  lines?: number
  card?: boolean
}

export default function Skeleton({ lines = 3, card = false }: SkeletonProps) {
  if (card) {
    return (
      <div className="skeleton-card" aria-hidden="true">
        <div className="skeleton skeleton-line skeleton-line-sm" />
        <div className="skeleton skeleton-line skeleton-line-lg" />
        <div className="skeleton skeleton-line skeleton-line-md" />
      </div>
    )
  }
  return (
    <div aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className={`skeleton skeleton-line skeleton-line-${i === 0 ? 'sm' : i === lines - 1 ? 'md' : 'lg'}`} />
      ))}
    </div>
  )
}
