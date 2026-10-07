import { formatDateTimeShort } from '../../lib/format'
import { ChecksheetProgressBadge, ChecksheetStatusBadge, Tag } from '../ui/StatusBadge'
import type { ChecksheetRequirementProgressItem } from '../../types'

/** One required (or optional) checksheet on a stage, with its live BL-DCMS
 * evidence beneath it.
 *
 * Two distinct statuses are shown deliberately and are never conflated:
 *   - the requirement's progress state (NOT_STARTED / IN_PROGRESS /
 *     SATISFIED / REJECTED), computed by the backend under its own
 *     submission-satisfying rule; and
 *   - each matching checksheet's own BL-DCMS record status
 *     (DRAFT / SUBMITTED / UNDER_REVIEW / APPROVED / REJECTED) plus its
 *     signature state, shown verbatim as evidence.
 *
 * Internal checksheet ids are not displayed — the template name, section and
 * equipment are what identify a row to an operator.
 */
export function ChecksheetProgressRow({ item }: { item: ChecksheetRequirementProgressItem }) {
  const rejected = item.progress_state === 'REJECTED'
  return (
    <li className={`checksheet-progress-row${rejected ? ' checksheet-progress-row-rejected' : ''}`}>
      <div className="checksheet-progress-row-header">
        <strong className="checksheet-progress-template">{item.template_name_snapshot}</strong>
        <Tag tone={item.is_required ? 'info' : 'neutral'}>{item.is_required ? 'Required' : 'Optional'}</Tag>
        <ChecksheetProgressBadge state={item.progress_state} size="small" />
      </div>

      <p className="checksheet-progress-row-context">
        {item.section ?? '—'} · {item.equipment ?? '—'}
      </p>

      {item.matching_checksheets.length > 0 && (
        <ul className="checksheet-progress-evidence">
          {item.matching_checksheets.map((c) => (
            <li key={c.checksheet_id}>
              <ChecksheetStatusBadge status={c.status} size="small" />
              {c.signed && <span className="checksheet-evidence-note">(signed)</span>}
              {c.signing_timestamp && (
                <span className="checksheet-evidence-note">{formatDateTimeShort(c.signing_timestamp)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}
