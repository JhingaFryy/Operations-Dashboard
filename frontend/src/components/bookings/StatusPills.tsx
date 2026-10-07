import type { StatusCounts } from '../../lib/grouping'

/** The status tally shown on a collapsed group header, so the shape of a locomotive's or an
 * equipment's work is readable without expanding it.
 *
 * Shared by the Admin Booking Pool and the Section Dashboard. It was a private function in the
 * Booking Pool page until the Section Dashboard adopted the same hierarchy; extracted rather
 * than copied, so the two surfaces cannot drift on what a count means or how it is labelled.
 *
 * Purely presentational - it renders the tally it is handed and computes nothing.
 */
export function StatusPills({ counts, label }: { counts: StatusCounts; label: string }) {
  return (
    <span className="collapsible-group-counts" aria-label={`Booking states for ${label}`}>
      <CountPill tone="open" value={counts.OPEN} word="Open" />
      <CountPill tone="active" value={counts.IN_PROGRESS} word="In progress" />
      <CountPill tone="done" value={counts.ATTENDED} word="Attended" />
      <CountPill tone="attention" value={counts.REOPENED} word="Reopened" />
    </span>
  )
}

function CountPill({ tone, value, word }: { tone: string; value: number; word: string }) {
  return (
    <span className={`count-pill count-pill-${tone}${value === 0 ? ' count-pill-zero' : ''}`}>
      <span className="count-pill-value">{value}</span>
      <span className="count-pill-word">{word}</span>
    </span>
  )
}
