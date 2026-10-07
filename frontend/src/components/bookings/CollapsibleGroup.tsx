import type { ReactNode } from 'react'

/** One collapsed-by-default level of the Booking Pool hierarchy:
 * locomotive > booking source > equipment > bookings.
 *
 * COLLAPSED BY DEFAULT IS THE POINT. The pool routinely holds hundreds of bookings; rendering
 * them all at once is what made the page unusable. Children are passed as a function rather
 * than as elements so that a collapsed group renders NOTHING below its header - not hidden
 * rows, not display:none rows. The DOM cost of a collapsed group is its own header.
 *
 * Expansion state lives in the parent, keyed by a stable identity that CONTAINS its ancestors
 * (a locomotive number; a locomotive + stored source; a locomotive + source + equipment node id)
 * and never by array position, so re-sorting or re-filtering the list cannot transfer one group's
 * open state to another, and the same equipment node under two sources expands independently.
 */
export function CollapsibleGroup({
  id,
  expanded,
  onToggle,
  title,
  subtitle,
  count,
  countLabel,
  meta,
  level,
  children,
}: {
  /** Stable, identity-derived - used for aria wiring and as the React key by the caller. */
  id: string
  expanded: boolean
  onToggle: () => void
  title: ReactNode
  /** The equipment's hierarchy path, or the visit's schedule - what distinguishes namesakes. */
  subtitle?: ReactNode
  count: number
  countLabel: string
  /** Status pills or similar, shown on the header whether open or closed. */
  meta?: ReactNode
  /** Drives the heading's visual weight: a locomotive card, a source band inside it, an
   *  equipment panel inside that. Three distinct treatments, see index.css. */
  level: 'loco' | 'source' | 'equipment'
  children: () => ReactNode
}) {
  const panelId = `${id}-panel`
  return (
    <section className={`collapsible-group collapsible-group-${level}`}>
      <button
        type="button"
        className="collapsible-group-header"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={onToggle}
      >
        <span className="collapsible-group-chevron" aria-hidden="true">
          ▸
        </span>
        <span className="collapsible-group-heading">
          <span className="collapsible-group-title">{title}</span>
          {subtitle && <span className="collapsible-group-subtitle">{subtitle}</span>}
        </span>
        <span className="collapsible-group-count">
          {count} {countLabel}
          {count === 1 ? '' : 's'}
        </span>
        {meta}
      </button>

      {expanded && (
        <div className="collapsible-group-body" id={panelId}>
          {children()}
        </div>
      )}
    </section>
  )
}
