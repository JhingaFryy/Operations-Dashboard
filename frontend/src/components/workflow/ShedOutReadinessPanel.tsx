import { buildShedOutReadiness } from '../../lib/shedOutReadiness'
import type { ShedOutEligibility } from '../../types'

/** Presents the backend's Shed Out gate — it does not implement it.
 *
 * Every line comes from the typed `stage_blockers` / `booking_blockers`
 * arrays of GET /shed-visits/{id}/shed-out-eligibility; no raw backend
 * exception text is ever shown to the operator. Whether Shed Out is
 * permitted remains `eligibility.eligible`, and the POST itself is
 * re-validated server-side regardless of what this panel says.
 */
export function ShedOutReadinessPanel({ eligibility }: { eligibility: ShedOutEligibility }) {
  const readiness = buildShedOutReadiness(eligibility)

  return (
    <div className={`readiness-panel ${readiness.eligible ? 'readiness-panel-ready' : 'readiness-panel-blocked'}`}>
      <div className="readiness-headline">
        <span className="readiness-headline-glyph" aria-hidden="true">
          {readiness.eligible ? '✓' : '○'}
        </span>
        <div>
          <p className="readiness-headline-text">
            {readiness.eligible ? 'Ready for Shed Out' : 'Not yet eligible for Shed Out'}
          </p>
          {readiness.eligible ? (
            <p className="readiness-headline-sub">
              Every workflow stage is complete and every booking is attended in all responsible
              sections.
            </p>
          ) : (
            <ul className="readiness-reason-list">
              {readiness.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <ul className="shed-out-readiness-list">
        {readiness.stageLines.map((line) => (
          <li key={line.stageType} className={line.blocked ? 'readiness-item-blocked' : 'readiness-item-ok'}>
            <span className="readiness-item-glyph" aria-hidden="true">
              {line.blocked ? '✗' : '✓'}
            </span>
            <span className="readiness-item-text">
              {line.rawLabel} — {line.statusText}
            </span>
          </li>
        ))}

        {readiness.bookingLines.length === 0 ? (
          <li className="readiness-item-ok">
            <span className="readiness-item-glyph" aria-hidden="true">
              ✓
            </span>
            <span className="readiness-item-text">All bookings fully attended</span>
          </li>
        ) : (
          readiness.bookingLines.map((line) => (
            <li key={line.bookingId} className="readiness-item-blocked">
              <span className="readiness-item-glyph" aria-hidden="true">
                ✗
              </span>
              <span className="readiness-item-text">
                Booking #{line.bookingId} ({line.sourceLabel})
              </span>
              <span className="readiness-item-detail">
                {line.noSectionAssignment ? 'no section assignment' : line.pendingText}
              </span>
            </li>
          ))
        )}
      </ul>
    </div>
  )
}
