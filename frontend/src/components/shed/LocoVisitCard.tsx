import { Link } from 'react-router-dom'
import { formatDateTimeShort } from '../../lib/format'
import { formatDuration } from '../../lib/localDateTime'
import type { CurrentShedVisit, OperationalPhase, ShedVisitAction } from '../../types'

/** One locomotive currently in the shed, as a dense full-width row.
 *
 * The loco number dominates - it is what every operator searches by - followed by the raw
 * schedule (IA / IOH) and the operational phase.
 *
 * Phase, its label and the offered actions all come from the server
 * (app/services/shed_visit_phase.py); nothing here re-derives them from timestamps. That is what
 * keeps an invalid action from ever appearing next to a locomotive, and what stops this card and
 * the backend disagreeing about what a loco is doing.
 *
 * Only fields GET /api/shed-visits/current actually returns are shown.
 */

const PHASE_CLASS: Record<OperationalPhase, string> = {
  SPARE: 'phase-chip-spare',
  SCHEDULE_IN_PROGRESS: 'phase-chip-in-progress',
  INSPECTION_COMPLETED: 'phase-chip-inspection-complete',
  READY: 'phase-chip-ready',
  SHED_OUT: 'phase-chip-shed-out',
}

const ACTION_LABEL: Record<ShedVisitAction, string> = {
  START_SCHEDULE: 'Start Schedule',
  COMPLETE_SCHEDULE: 'Complete Schedule',
  MARK_READY: 'Mark Ready',
  SHED_OUT: 'Shed Out',
}

/** The one timing that matters in each phase - the card stays scannable rather than showing all four. */
function primaryTiming(visit: CurrentShedVisit): { label: string; value: string; running: boolean } | null {
  const t = visit.timings
  switch (visit.operational_phase) {
    case 'SPARE':
      return { label: 'Waiting', value: formatDuration(t.waiting_seconds), running: t.waiting_running }
    case 'SCHEDULE_IN_PROGRESS':
      return { label: 'Elapsed', value: formatDuration(t.schedule_seconds), running: t.schedule_running }
    case 'INSPECTION_COMPLETED':
      return { label: 'Inspection Duration', value: formatDuration(t.schedule_seconds), running: false }
    case 'READY':
      return { label: 'Schedule Duration', value: formatDuration(t.schedule_seconds), running: false }
    default:
      return { label: 'Total Shed Time', value: formatDuration(t.total_seconds), running: false }
  }
}

/** The timestamp that marks when the current phase began. */
function phaseTimestamp(visit: CurrentShedVisit): { label: string; value: string } {
  switch (visit.operational_phase) {
    case 'SCHEDULE_IN_PROGRESS':
      return { label: 'Started', value: formatDateTimeShort(visit.schedule_started_at) }
    case 'INSPECTION_COMPLETED':
      return { label: 'Inspected', value: formatDateTimeShort(visit.inspection_completed_at ?? null) }
    case 'READY':
      return { label: 'Ready', value: formatDateTimeShort(visit.ready_at) }
    case 'SHED_OUT':
      return { label: 'Departed', value: formatDateTimeShort(visit.departed_at) }
    default:
      return { label: 'Arrived', value: formatDateTimeShort(visit.arrival_at) }
  }
}

export function LocoVisitCard({
  visit,
  onAction,
  busy = false,
}: {
  visit: CurrentShedVisit
  onAction?: (visit: CurrentShedVisit, action: ShedVisitAction) => void
  busy?: boolean
}) {
  const pending = visit.pending_booking_count
  const attentionClass = pending > 0 ? ' loco-visit-card-attention' : ''
  const timing = primaryTiming(visit)
  const stamp = phaseTimestamp(visit)

  return (
    <article className={`loco-visit-card${attentionClass}`}>
      <div className="loco-visit-card-identity">
        <div className="loco-visit-card-identity-top">
          <span className="loco-number">{visit.loco_number}</span>
          {/* Raw schedule kept separate from the phase label, so "IA" stays readable as itself. */}
          <span className="loco-visit-card-schedule">{visit.schedule_variant ?? '—'}</span>
        </div>
        <span className={`phase-chip ${PHASE_CLASS[visit.operational_phase]}`}>
          {visit.display_label}
        </span>
      </div>

      <div className="loco-visit-card-fact">
        <span className="loco-visit-card-fact-label">{stamp.label}</span>
        <span className="loco-visit-card-fact-value">{stamp.value}</span>
      </div>

      {timing && (
        <div className="loco-visit-card-fact">
          <span className="loco-visit-card-fact-label">{timing.label}</span>
          <span className={`loco-visit-card-fact-value${timing.running ? ' timing-running' : ''}`}>
            {timing.value}
          </span>
        </div>
      )}

      <div className="loco-visit-card-fact">
        <span className="loco-visit-card-fact-label">Bookings</span>
        <span className="loco-visit-card-fact-value">
          {pending > 0 ? (
            <span className="loco-visit-card-pending">
              {pending} pending of {visit.booking_total}
            </span>
          ) : (
            <span>{visit.booking_total} · all attended</span>
          )}
        </span>
      </div>

      <div className="loco-visit-card-actions">
        {/* Rendered straight from the server's available_actions - an action that is not valid
            for this phase is never offered. */}
        {onAction &&
          visit.available_actions.map((action) => (
            <button
              key={action}
              type="button"
              className={`btn btn-small ${action === 'SHED_OUT' ? 'btn-secondary' : 'btn-primary'}`}
              disabled={busy}
              onClick={() => onAction(visit, action)}
            >
              {ACTION_LABEL[action]}
            </button>
          ))}
        {visit.schedule_family === 'MINOR' && (
          <Link className="btn btn-secondary btn-small" to={`/shed-visits/${visit.id}/workflow`}>
            Open workflow
          </Link>
        )}
      </div>
    </article>
  )
}
