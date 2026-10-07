import { useState } from 'react'
import { bookingSourceLabel, formatDateTime, formatSchedule } from '../../lib/format'
import { allowedAssignmentActions } from '../../lib/transitions'
import { BookingStatusBadge, Tag } from '../ui/StatusBadge'
import { ConfirmActionDialog } from '../ui/ConfirmActionDialog'
import type { Section, SectionAssignment } from '../../types'
import { BookingDetailPanel } from './BookingDetailPanel'

/** One section assignment: the operational unit a Supervisor actually works.
 *
 * Action buttons are driven entirely by `allowedAssignmentActions`, which
 * mirrors the backend's transition table — a button for a forbidden
 * transition (OPEN → ATTENDED, REOPENED → ATTENDED, or ATTENDED → REOPENED
 * for a non-Admin) is never rendered, not even disabled. The backend
 * re-validates every transition regardless.
 */
export function AssignmentCard({
  assignment,
  busy,
  canReopen,
  canAddSection,
  sections,
  onStart,
  onAttend,
  onReopen,
  onSectionAdded,
}: {
  assignment: SectionAssignment
  busy: boolean
  canReopen: boolean
  canAddSection: boolean
  sections: Section[]
  onStart: () => void
  onAttend: (remarks: string) => void
  onReopen: (reason: string) => void
  onSectionAdded?: () => void
}) {
  const [attending, setAttending] = useState(false)
  const [reopening, setReopening] = useState(false)
  const [remarksDraft, setRemarksDraft] = useState('')
  const [showDetail, setShowDetail] = useState(false)

  const { booking } = assignment
  const actions = allowedAssignmentActions(assignment.status, { canReopen })
  const needsAttention = assignment.status === 'REOPENED'

  return (
    <div className={`assignment-card${needsAttention ? ' assignment-card-attention' : ''}`}>
      {/* Strong tier: loco number and current status lead the card. */}
      <div className="assignment-card-header">
        <div className="assignment-card-identity">
          <strong className="loco-number">{booking.shed_visit.loco_number}</strong>
        </div>
        <BookingStatusBadge status={booking.status} size="small" />
      </div>

      <p className="assignment-card-equipment">{booking.equipment_node_name ?? 'Unknown equipment'}</p>

      {/* Secondary tier: schedule and defect type as quiet pills. */}
      <div className="assignment-card-tags">
        <Tag>{formatSchedule(booking.shed_visit.schedule_family, booking.shed_visit.schedule_variant)}</Tag>
        {booking.defect_type && <Tag tone="info">{booking.defect_type.name}</Tag>}
      </div>

      <p className="assignment-card-description">{booking.description}</p>

      <dl className="assignment-card-meta">
        {/* Stated explicitly even though the card now sits inside a source group: a card read on
            its own - scrolled to, screen-read, or linked from elsewhere - should still say where
            its booking came from. Same shared label helper the group heading and the Admin
            Booking Pool's Source column use, so the three can never disagree. */}
        <div>
          <dt>Source</dt>
          <dd>{bookingSourceLabel(booking.booking_source)}</dd>
        </div>
        <div>
          <dt>Assigned</dt>
          <dd>{formatDateTime(assignment.assigned_at)}</dd>
        </div>
        {assignment.started_at && (
          <div>
            <dt>Started</dt>
            <dd>
              {formatDateTime(assignment.started_at)}
              {assignment.started_by_name ? ` · ${assignment.started_by_name}` : ''}
            </dd>
          </div>
        )}
        {assignment.attended_at && (
          <div>
            <dt>Attended</dt>
            <dd>
              {formatDateTime(assignment.attended_at)}
              {assignment.attended_by_name ? ` · ${assignment.attended_by_name}` : ''}
            </dd>
          </div>
        )}
        {assignment.attendance_remarks && (
          <div>
            <dt>Remarks</dt>
            <dd>{assignment.attendance_remarks}</dd>
          </div>
        )}
      </dl>

      {/* Which actions are offered comes entirely from
          `allowedAssignmentActions` above and is unchanged — only the button
          VARIANT differs per action: forward progression (Start Work / Mark
          Attended) is primary, Reopen is the warm attention treatment, and
          Details is the quiet secondary that sits apart from them. */}
      <div className="assignment-card-actions">
        {actions.includes('START') && !attending && (
          <button type="button" className="btn btn-primary btn-small" disabled={busy} aria-busy={busy} onClick={onStart}>
            Start Work
          </button>
        )}

        {actions.includes('ATTEND') && !attending && (
          <button
            type="button"
            className="btn btn-primary btn-small"
            disabled={busy}
            onClick={() => setAttending(true)}
          >
            Mark Attended
          </button>
        )}

        {actions.includes('REOPEN') && !reopening && (
          <button
            type="button"
            className="btn btn-attention btn-small"
            disabled={busy}
            onClick={() => setReopening(true)}
          >
            Reopen
          </button>
        )}

        <button
          type="button"
          className="btn btn-secondary btn-small assignment-card-details-btn"
          aria-expanded={showDetail}
          onClick={() => setShowDetail((v) => !v)}
        >
          {showDetail ? 'Hide Details' : 'Details'}
        </button>
      </div>

      {attending && (
        <ConfirmActionDialog
          title="Mark this assignment attended?"
          description="Recorded against your section only. Other responsible sections keep their own assignment."
          confirmLabel="Confirm Attend"
          busy={busy}
          reason={{
            id: `attend-remarks-${assignment.id}`,
            label: 'Attendance remarks',
            value: remarksDraft,
            onChange: setRemarksDraft,
            required: true,
          }}
          onConfirm={() => {
            onAttend(remarksDraft.trim())
            setAttending(false)
            setRemarksDraft('')
          }}
          onCancel={() => {
            setAttending(false)
            setRemarksDraft('')
          }}
        />
      )}

      {reopening && (
        <ConfirmActionDialog
          title="Reopen this attended assignment?"
          description="The assignment returns to Reopened and must be started and attended again."
          confirmLabel="Confirm Reopen"
          tone="danger"
          busy={busy}
          reason={{
            id: `reopen-reason-${assignment.id}`,
            label: 'Reopen reason',
            value: remarksDraft,
            onChange: setRemarksDraft,
            required: true,
          }}
          onConfirm={() => {
            onReopen(remarksDraft.trim())
            setReopening(false)
            setRemarksDraft('')
          }}
          onCancel={() => {
            setReopening(false)
            setRemarksDraft('')
          }}
        />
      )}

      {showDetail && (
        <BookingDetailPanel
          bookingId={assignment.booking_id}
          canAddSection={canAddSection}
          sections={sections}
          onSectionAdded={onSectionAdded}
        />
      )}
    </div>
  )
}
