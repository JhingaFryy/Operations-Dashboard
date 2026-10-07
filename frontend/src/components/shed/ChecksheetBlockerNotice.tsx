import type { ChecksheetsIncompleteDetail } from '../../api/shedVisits'

const STAGE_LABEL: Record<string, string> = {
  TEST_BEFORE: 'Test Before',
  SCHEDULE_INSPECTION: 'Inspection',
  TEST_AFTER: 'Test After',
}

const REASON_LABEL: Record<string, string> = {
  MISSING: 'Not started',
  DRAFT: 'Draft',
  REJECTED: 'Rejected',
  UNDER_REVIEW: 'Under review',
}

/**
 * Why Ready - or Shed Out - was refused, in terms the shed can act on: how many required
 * checksheets are outstanding and which ones. Both gates return the same CHECKSHEETS_INCOMPLETE
 * payload, so this one notice serves both; only the backend's message names the action.
 *
 * There is deliberately no override, force or continue-anyway control here. Checksheets are
 * mandatory for both Minor and Major schedules and the rule is enforced by the backend for every
 * Ready-capable route and for Shed Out, so a button here could not bypass it and must not suggest
 * that it could.
 */
export function ChecksheetBlockerNotice({
  detail,
  limit,
}: {
  detail: ChecksheetsIncompleteDetail
  /** Show only the first few outstanding entries (the count above them is always exact). */
  limit?: number
}) {
  const entries = limit === undefined ? detail.outstanding_entries : detail.outstanding_entries.slice(0, limit)
  const hidden = detail.outstanding - entries.length

  return (
    <div className="form-error checksheet-blocker" role="alert">
      <p className="checksheet-blocker-message">{detail.message}</p>

      {detail.work_package_generated && detail.checksheets_readable && (
        <p className="checksheet-blocker-counts">
          {detail.satisfied} of {detail.total_required} required checksheets satisfied.
        </p>
      )}

      {entries.length > 0 && (
        <ul className="checksheet-blocker-list">
          {entries.map((entry, index) => (
            <li key={entry.requirement_id ?? index}>
              <span className="checksheet-blocker-name">
                {[entry.section, entry.label ?? entry.template_name].filter(Boolean).join(' · ')}
              </span>
              {entry.stage && (
                <span className="checksheet-blocker-stage">
                  {' '}
                  ({STAGE_LABEL[entry.stage] ?? entry.stage})
                </span>
              )}
              {entry.maintenance_type && (
                <span className="checksheet-blocker-stage"> ({entry.maintenance_type})</span>
              )}
              <span className="checksheet-blocker-reason">
                {' — '}
                {REASON_LABEL[entry.reason] ?? entry.reason}
              </span>
            </li>
          ))}
        </ul>
      )}

      {hidden > 0 && entries.length > 0 && (
        <p className="checksheet-blocker-counts">
          …and {hidden} more. See Pending Checksheets for the full list.
        </p>
      )}
    </div>
  )
}
