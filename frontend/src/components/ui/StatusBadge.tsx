import type { ReactNode } from 'react'
import {
  ASSIGNMENT_STATUS_META,
  CHECKSHEET_PROGRESS_META,
  CHECKSHEET_STATUS_META,
  STAGE_STATUS_META,
  VISIT_STATUS_META,
  statusMeta,
  type StatusMeta,
  type StatusTone,
} from '../../lib/status'

/** One badge component for every status in the app.
 *
 * Status is never colour-only: the glyph (aria-hidden, decorative) and the
 * text label always travel together, so the badge stays readable in
 * greyscale and to a screen reader. */
export function StatusBadge({
  meta,
  size = 'default',
  title,
}: {
  meta: StatusMeta
  size?: 'default' | 'small'
  title?: string
}) {
  return (
    <span
      className={`status-badge status-badge-${meta.tone}${size === 'small' ? ' status-badge-small' : ''}`}
      title={title}
    >
      <span className="status-badge-glyph" aria-hidden="true">
        {meta.glyph}
      </span>
      <span className="status-badge-text">{meta.label}</span>
    </span>
  )
}

/** Plain non-status pill (equipment, source, stage, "Required"/"Optional"). */
export function Tag({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: StatusTone
}) {
  return <span className={`tag tag-${tone}`}>{children}</span>
}

/** OPEN / IN_PROGRESS / ATTENDED / REOPENED — bookings and section
 * assignments share this vocabulary server-side, so they share it here. */
export function BookingStatusBadge({
  status,
  size,
}: {
  status: string
  size?: 'default' | 'small'
}) {
  return <StatusBadge meta={statusMeta(ASSIGNMENT_STATUS_META, status)} size={size} />
}

/** BL-DCMS record status: DRAFT / SUBMITTED / UNDER_REVIEW / REJECTED / APPROVED. */
export function ChecksheetStatusBadge({ status, size }: { status: string; size?: 'default' | 'small' }) {
  return <StatusBadge meta={statusMeta(CHECKSHEET_STATUS_META, status)} size={size} />
}

/** Per-requirement correlation state from /checksheet-requirement-progress. */
export function ChecksheetProgressBadge({ state, size }: { state: string; size?: 'default' | 'small' }) {
  return <StatusBadge meta={statusMeta(CHECKSHEET_PROGRESS_META, state)} size={size} />
}

export function StageStatusBadge({ status, size }: { status: string; size?: 'default' | 'small' }) {
  return <StatusBadge meta={statusMeta(STAGE_STATUS_META, status)} size={size} />
}

export function VisitStatusBadge({ status, size }: { status: string; size?: 'default' | 'small' }) {
  return <StatusBadge meta={statusMeta(VISIT_STATUS_META, status)} size={size} />
}
