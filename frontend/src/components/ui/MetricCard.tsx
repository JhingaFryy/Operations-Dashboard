import type { ReactNode } from 'react'

/** A single operational count.
 *
 * Only ever fed values the API actually returns, or a literal tally of rows
 * the page has already loaded. There is no placeholder/estimate path: if a
 * number isn't available, the card isn't rendered at all. */
export function MetricCard({
  label,
  value,
  hint,
  tone = 'neutral',
  footer,
  className,
  valueClassName,
}: {
  label: string
  value: number | string
  hint?: string
  tone?: 'neutral' | 'attention'
  footer?: ReactNode
  /** Extra class on the card root — lets a page keep an established
   * container hook while still using the shared component. */
  className?: string
  /** Extra class on the value element, for the same reason. */
  valueClassName?: string
}) {
  return (
    <div className={`metric-card metric-card-${tone}${className ? ` ${className}` : ''}`}>
      <span className="metric-card-label">{label}</span>
      <span className={`metric-card-value${valueClassName ? ` ${valueClassName}` : ''}`}>{value}</span>
      {hint && <span className="metric-card-hint">{hint}</span>}
      {footer}
    </div>
  )
}
