import { useCallback, useEffect, useState } from 'react'
import { addSection, getBookingDetail, getBookingHistory } from '../../api/bookings'
import { friendlyErrorMessage } from '../../api/client'
import { formatDateTime as formatISTDateTime, formatSchedule } from '../../lib/format'
import type { BookingDetail, BookingHistoryEvent, Section } from '../../types'
import { ButtonLabel } from '../ui/States'

function formatDateTime(iso: string | null): string | null {
  return iso ? formatISTDateTime(iso) : null
}

export function BookingDetailPanel({
  bookingId,
  canAddSection,
  sections,
  onSectionAdded,
}: {
  bookingId: number
  canAddSection: boolean
  sections: Section[]
  onSectionAdded?: () => void
}) {
  const [detail, setDetail] = useState<BookingDetail | null>(null)
  const [history, setHistory] = useState<BookingHistoryEvent[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [showAddForm, setShowAddForm] = useState(false)
  const [addSectionId, setAddSectionId] = useState<string>('')
  const [addReason, setAddReason] = useState('')
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([getBookingDetail(bookingId), getBookingHistory(bookingId)])
      .then(([d, h]) => {
        setDetail(d)
        setHistory(h)
      })
      .catch((err) => setError(friendlyErrorMessage(err)))
      .finally(() => setLoading(false))
  }, [bookingId])

  useEffect(() => {
    load()
  }, [load])

  if (loading) return <div role="status">Loading booking detail…</div>
  if (error)
    return (
      <div className="form-error" role="alert">
        {error}
      </div>
    )
  if (!detail) return null

  const assignedSectionIds = new Set(detail.assignments.map((a) => a.section_id))
  const availableSections = sections.filter((s) => !assignedSectionIds.has(s.id))

  function handleAddSection(e: React.FormEvent) {
    e.preventDefault()
    if (!addSectionId || !addReason.trim()) return
    setAdding(true)
    setAddError(null)
    addSection(bookingId, Number(addSectionId), addReason.trim())
      .then(() => {
        setShowAddForm(false)
        setAddSectionId('')
        setAddReason('')
        load()
        onSectionAdded?.()
      })
      .catch((err) => setAddError(friendlyErrorMessage(err)))
      .finally(() => setAdding(false))
  }

  return (
    <div className="booking-detail-panel">
      <dl className="booking-detail-meta">
        <div>
          <dt>Locomotive</dt>
          <dd>{detail.shed_visit.loco_number}</dd>
        </div>
        <div>
          <dt>Schedule</dt>
          <dd>{formatSchedule(detail.shed_visit.schedule_family, detail.shed_visit.schedule_variant)}</dd>
        </div>
        <div>
          <dt>Booking Source</dt>
          <dd>{detail.booking_source}</dd>
        </div>
        <div>
          <dt>Booking Status</dt>
          <dd>
            <span className="badge badge-neutral">{detail.status}</span>
          </dd>
        </div>
      </dl>

      {detail.equipment_path.length > 0 && (
        <p className="booking-detail-path">{detail.equipment_path.map((p) => p.name).join(' → ')}</p>
      )}

      <p className="booking-detail-description">{detail.description}</p>

      <h4>Section Assignments</h4>
      <table className="booking-detail-assignments-table">
        <thead>
          <tr>
            <th>Section</th>
            <th>Status</th>
            <th>Source</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {detail.assignments.map((a) => (
            <tr key={a.id}>
              <td>{a.section_code}</td>
              <td>
                <span className="badge badge-neutral">{a.status}</span>
              </td>
              <td>{a.assignment_source}</td>
              <td>
                {a.started_by_name && `Started by ${a.started_by_name}`}
                {a.attended_by_name && ` · Attended by ${a.attended_by_name}`}
                {a.attendance_remarks && ` — ${a.attendance_remarks}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {canAddSection && (
        <div className="booking-detail-add-section">
          {!showAddForm ? (
            <button
              type="button"
              className="btn btn-secondary btn-small"
              disabled={availableSections.length === 0}
              onClick={() => setShowAddForm(true)}
            >
              + Add Section
            </button>
          ) : (
            <form onSubmit={handleAddSection} className="booking-detail-add-section-form">
              <p className="field-hint">
                Adds another responsible section to this booking. Existing section assignments are
                not removed.
              </p>
              <label className="field-label" htmlFor={`add-section-select-${bookingId}`}>
                Section
              </label>
              <select
                id={`add-section-select-${bookingId}`}
                value={addSectionId}
                onChange={(e) => setAddSectionId(e.target.value)}
              >
                <option value="">-- Select section --</option>
                {availableSections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.code})
                  </option>
                ))}
              </select>

              <label className="field-label" htmlFor={`add-section-reason-${bookingId}`}>
                Reason
              </label>
              <textarea
                id={`add-section-reason-${bookingId}`}
                rows={2}
                value={addReason}
                onChange={(e) => setAddReason(e.target.value)}
              />

              {addError && (
                <div className="form-error" role="alert">
                  {addError}
                </div>
              )}

              <div className="booking-detail-add-section-actions">
                <button
                  type="submit"
                  className="btn btn-primary btn-small"
                  disabled={adding || !addSectionId || !addReason.trim()}
                  aria-busy={adding}
                >
                  <ButtonLabel busy={adding} label="Confirm Add Section" busyLabel="Adding…" />
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-small"
                  disabled={adding}
                  onClick={() => {
                    setShowAddForm(false)
                    setAddError(null)
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      <h4>History</h4>
      {history && history.length === 0 && <p className="field-hint">No events yet.</p>}
      {history && history.length > 0 && (
        <ul className="booking-detail-history">
          {history.map((e) => (
            <li key={e.id}>
              <span className="badge badge-neutral">{e.event_type}</span>{' '}
              <span>{formatDateTime(e.created_at)}</span>
              {e.actor_name && <span> · {e.actor_name}</span>}
              {e.to_section_code && <span> · {e.to_section_code}</span>}
              {e.remarks && <p className="booking-detail-history-remarks">{e.remarks}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
