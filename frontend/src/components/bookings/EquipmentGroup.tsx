import type { ReactNode } from 'react'
import type { StatusCounts } from '../../lib/grouping'
import type { PathItem } from '../../types'

/** A group of bookings/assignments that share one piece of equipment.
 *
 * Equipment is the primary organising axis on both the Section Dashboard
 * (where the section is already fixed by the page) and the Admin Booking
 * Pool — never a flat chronological list. The per-group counts are literal
 * tallies of the rows already rendered inside the group; nothing is fetched
 * or derived beyond counting.
 *
 * `className` lets each page keep its own established container class.
 */
export function EquipmentGroup({
  label,
  path = [],
  count,
  counts,
  className,
  children,
}: {
  label: string
  path?: PathItem[]
  count: number
  counts?: StatusCounts
  className: string
  children: ReactNode
}) {
  return (
    <section className={`equipment-group ${className}`}>
      <div className="equipment-group-header">
        <h2>
          {path.length > 0 && (
            <span className="equipment-group-path booking-pool-group-path">
              {path.map((p) => p.name).join(' / ')} /{' '}
            </span>
          )}
          {label} <span className="equipment-group-count booking-pool-group-count">({count})</span>
        </h2>

        {counts && (
          <ul className="equipment-group-counts" aria-label={`Booking states for ${label}`}>
            <CountPill tone="open" value={counts.OPEN} word="Open" />
            <CountPill tone="active" value={counts.IN_PROGRESS} word="In progress" />
            <CountPill tone="done" value={counts.ATTENDED} word="Attended" />
            <CountPill tone="attention" value={counts.REOPENED} word="Reopened" />
          </ul>
        )}
      </div>

      <div className="equipment-group-body">{children}</div>
    </section>
  )
}

function CountPill({ tone, value, word }: { tone: string; value: number; word: string }) {
  return (
    <li className={`count-pill count-pill-${tone}${value === 0 ? ' count-pill-zero' : ''}`}>
      <span className="count-pill-value">{value}</span>
      <span className="count-pill-word">{word}</span>
    </li>
  )
}
