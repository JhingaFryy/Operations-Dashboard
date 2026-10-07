import { formatDateTime, formatSchedule } from '../../lib/format'
import { StatusBadge, VisitStatusBadge } from '../ui/StatusBadge'
import type { StatusMeta } from '../../lib/status'

/** Persistent identity header for a single shed visit. Keeps the locomotive
 * — not the page name — as the dominant element, with schedule, arrival and
 * visit status as compact supporting metadata. */
export function LocoContextHeader({
  locoNumber,
  scheduleFamily,
  scheduleVariant,
  arrivalAt,
  status,
  shedOutReadiness,
}: {
  locoNumber: string
  scheduleFamily: string | null
  scheduleVariant: string | null
  arrivalAt: string
  status: string
  /** Optional compact glance at Shed Out readiness/blocking state, computed
   * by the caller from data it has already loaded (stage status + shed-out
   * eligibility). Purely a mirror of that state for the hero — never a
   * second source of truth; the Shed Out panel below remains authoritative. */
  shedOutReadiness?: StatusMeta
}) {
  return (
    <header className="loco-context-header">
      <div className="loco-context-identity">
        {/* Kept as one heading string ("Loco 39126 — IA") so the page
            still has a single, searchable h1. */}
        <h1>
          <span className="loco-context-label">Loco</span>{' '}
          <span className="loco-number loco-number-xl">{locoNumber}</span>
          <span className="loco-context-schedule">
            {' '}
            — {formatSchedule(scheduleFamily, scheduleVariant)}
          </span>
        </h1>
      </div>

      <dl className="loco-context-meta">
        <div>
          <dt>Arrival</dt>
          <dd>{formatDateTime(arrivalAt)}</dd>
        </div>
        <div>
          <dt>Visit status</dt>
          <dd>
            <VisitStatusBadge status={status} size="small" />
          </dd>
        </div>
        {shedOutReadiness && (
          <div>
            <dt>Shed Out</dt>
            <dd>
              <StatusBadge meta={shedOutReadiness} size="small" />
            </dd>
          </div>
        )}
      </dl>
    </header>
  )
}
