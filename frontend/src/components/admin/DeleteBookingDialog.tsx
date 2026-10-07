import { useEffect, useState } from 'react'
import { deleteBooking, previewBookingDeletion } from '../../api/adminDeletion'
import { friendlyErrorMessage } from '../../api/client'
import { ButtonLabel } from '../ui/States'
import type { BookingDeletionPreview, DeletionEventDetail } from '../../types'

/** Permanently delete ONE booking. ADMIN ONLY, and irreversible.
 *
 * NO CONFIRMATION PHRASE HERE, deliberately. A booking is a single row with a visible description,
 * and the preview shows that description - so there is no ambiguity about which one is being removed,
 * which is the mistake a typed phrase protects against on a visit. Asking for one anyway would train
 * people to type past it. The password and a recorded reason are still required.
 *
 * WHAT SURVIVES IS STATED, not left to be inferred. The visit, its checksheets and its other bookings
 * are untouched, and the dialog says so with the actual sibling count from the server.
 */
export function DeleteBookingDialog({
  bookingId,
  onDeleted,
  onCancel,
}: {
  bookingId: number
  onDeleted: (event: DeletionEventDetail) => void
  onCancel: () => void
}) {
  const [preview, setPreview] = useState<BookingDeletionPreview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [password, setPassword] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    previewBookingDeletion(bookingId)
      .then((result) => {
        if (!cancelled) setPreview(result)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(friendlyErrorMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [bookingId])

  const trimmedReason = reason.trim()
  const canDelete =
    !deleting && preview !== null && trimmedReason.length >= 10 && password.length > 0

  const handleDelete = async () => {
    if (!canDelete) return
    setDeleting(true)
    setError(null)
    try {
      const event = await deleteBooking(bookingId, { password, reason: trimmedReason })
      setPassword('')
      onDeleted(event)
    } catch (err) {
      setError(friendlyErrorMessage(err))
      setPassword('')
    } finally {
      setDeleting(false)
    }
  }

  const handleCancel = () => {
    setPassword('')
    onCancel()
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Delete booking">
      <div className="modal modal-danger">
        <h2 className="danger-heading">Delete Booking Permanently</h2>

        {loadError ? (
          <div className="form-error" role="alert">
            Could not load what this deletion would destroy, so it cannot proceed safely. {loadError}
          </div>
        ) : preview === null ? (
          <p role="status">Checking what this booking contains…</p>
        ) : (
          <>
            <dl className="delete-visit-summary">
              <dt>Booking</dt>
              <dd>#{preview.booking.id}</dd>
              <dt>Description</dt>
              <dd>{preview.booking.description ?? '—'}</dd>
              <dt>Status</dt>
              <dd>{preview.booking.status ?? '—'}</dd>
              <dt>Locomotive</dt>
              <dd>
                {preview.visit.loco_number ?? '—'}
                {preview.visit.schedule_variant ? ` · ${preview.visit.schedule_variant}` : ''}
              </dd>
            </dl>

            <div className="delete-visit-counts">
              <h3>This will permanently delete {preview.total_rows} record(s):</h3>
              <ul>
                {Object.entries(preview.counts)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([entity, count]) => (
                    <li key={entity}>
                      <strong>{count}</strong> {entity.replace(/_/g, ' ')}
                    </li>
                  ))}
              </ul>
            </div>

            {preview.warnings.length > 0 && (
              <ul className="delete-visit-warnings" role="alert">
                {preview.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}

            <p className="form-hint">
              The shed visit, its checksheets and its{' '}
              {preview.unaffected.other_bookings_on_this_visit} other booking(s) are not affected.
            </p>

            <p className="delete-visit-irreversible">
              This cannot be undone. A permanent record, including a snapshot of the deleted booking,
              is written to the Deletion History.
            </p>

            <label className="field">
              <span className="field-label">Why is this being deleted? (recorded permanently)</span>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                disabled={deleting}
                rows={2}
                maxLength={2000}
              />
              {trimmedReason.length > 0 && trimmedReason.length < 10 && (
                <span className="form-hint">Please give at least 10 characters.</span>
              )}
            </label>

            <label className="field">
              <span className="field-label">Your own account password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={deleting}
                autoComplete="off"
              />
            </label>

            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
          </>
        )}

        <div className="modal-actions">
          <button type="button" className="btn" onClick={handleCancel} disabled={deleting}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={handleDelete}
            disabled={!canDelete}
          >
            <ButtonLabel busy={deleting} label="Delete Booking Permanently" busyLabel="Deleting…" />
          </button>
        </div>
      </div>
    </div>
  )
}
