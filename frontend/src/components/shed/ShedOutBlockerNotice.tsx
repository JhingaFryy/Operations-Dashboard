import type { ShedOutBlockedDetail } from '../../api/shedVisits'
import { ChecksheetBlockerNotice } from './ChecksheetBlockerNotice'

const STAGE_LABEL: Record<string, string> = {
  TEST_BEFORE: 'Test Before',
  SCHEDULE_INSPECTION: 'Inspection',
  TEST_AFTER: 'Test After',
}

const BOOKING_STATUS_LABEL: Record<string, string> = {
  OPEN: 'Open',
  IN_PROGRESS: 'In Progress',
  REOPENED: 'Reopened',
  NO_ASSIGNMENTS: 'No responsible section assigned',
}

const STAGE_STATUS_LABEL: Record<string, string> = {
  PENDING: 'Pending',
  IN_PROGRESS: 'In Progress',
  MISSING: 'Missing',
}

/** Backend vocabulary in words. Tolerates a missing value rather than crashing the dialog: an
 * unexpected blocker shape once took the whole workflow page down. */
function words(value: string | null | undefined, fallback = ''): string {
  return value ? value.replace(/_/g, ' ') : fallback
}

/** How many outstanding checksheets to list before summarising the rest. */
const CHECKSHEET_PREVIEW = 5

/**
 * Why Shed Out was refused - each gate in its own words, never a bare "not eligible".
 *
 *  - Stages (MINOR only): which workflow stage is not complete.
 *  - Bookings: which booking is still open, or has no responsible section.
 *  - Checksheets: the same notice Ready uses - how many required checksheets are outstanding, how
 *    many are satisfied, and the first few outstanding ones.
 *
 * Presentation only: the gates themselves are the backend's, re-evaluated on every Shed Out
 * request. There is no override, force or bypass control here, for any role.
 */
export function ShedOutBlockerNotice({ detail }: { detail: ShedOutBlockedDetail }) {
  const stages = detail.stage_blockers ?? []
  const bookings = detail.booking_blockers ?? []
  const checksheets = detail.checksheets ?? null
  const structured = stages.length > 0 || bookings.length > 0 || checksheets !== null

  return (
    <div className="shed-out-blockers">
      {/* Only when there is nothing structured to show - otherwise each panel below says it. */}
      {!structured && (
        <p className="form-error" role="alert">
          {detail.message}
        </p>
      )}

      {stages.length > 0 && (
        <div className="form-error" role="alert">
          <p className="checksheet-blocker-message">Shed Out blocked: workflow stages not complete.</p>
          <ul className="checksheet-blocker-list">
            {stages.map((b) => (
              <li key={b.stage_type}>
                {STAGE_LABEL[b.stage_type] ?? words(b.stage_type)}
                <span className="checksheet-blocker-reason">
                  {' — '}
                  {STAGE_STATUS_LABEL[b.status] ?? words(b.status)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {bookings.length > 0 && (
        <div className="form-error" role="alert">
          <p className="checksheet-blocker-message">
            Shed Out blocked: {bookings.length} {bookings.length === 1 ? 'booking needs' : 'bookings need'}{' '}
            attention.
          </p>
          <ul className="checksheet-blocker-list">
            {bookings.map((b) => (
              <li key={b.booking_id}>
                Booking #{b.booking_id} ({words(b.booking_source)})
                <span className="checksheet-blocker-reason">
                  {' — '}
                  {BOOKING_STATUS_LABEL[b.status] ?? words(b.status, 'Not yet attended')}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {checksheets && <ChecksheetBlockerNotice detail={checksheets} limit={CHECKSHEET_PREVIEW} />}
    </div>
  )
}
