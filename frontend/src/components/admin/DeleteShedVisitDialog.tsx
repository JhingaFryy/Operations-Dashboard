import { useEffect, useState } from 'react'
import { deleteShedVisit, previewVisitDeletion } from '../../api/adminDeletion'
import { friendlyErrorMessage } from '../../api/client'
import { ButtonLabel } from '../ui/States'
import type { DeletionEventDetail, VisitDeletionPreview } from '../../types'

/** Permanently delete one shed visit and everything it owns. ADMIN ONLY, and irreversible.
 *
 * THREE GATES, EACH GUARDING A DIFFERENT MISTAKE:
 *   - The PREVIEW guards acting on the wrong visit, or on a bigger visit than expected. It is
 *     fetched before anything is typed, and the counts come from the same server-side computation
 *     the deletion itself runs - not an estimate of it.
 *   - The CONFIRMATION PHRASE guards a mis-click. It names this locomotive and schedule, so a
 *     muscle-memory "yes" on the wrong row cannot pass. The server still identifies the target by
 *     visit id; the phrase is a human check and never an identifier.
 *   - The PASSWORD guards an unattended desk or a borrowed session. A valid token proves someone
 *     logged in; it does not prove who is at the keyboard now.
 *
 * THE PASSWORD IS NEVER PERSISTED OR LOGGED HERE. It lives in component state for the life of this
 * dialog, is sent in the request BODY (never a URL, which would reach access logs and history), and
 * is cleared on every outcome - success, failure, or cancel.
 *
 * NOTHING IS DISABLED TO MAKE THIS FEEL SAFER. Deletion works on signed and approved records
 * deliberately, because a mistaken entry can be in any state; what signing changes is that every
 * signature is recorded in the deletion ledger first. The dialog says so rather than refusing.
 */
export function DeleteShedVisitDialog({
  visitId,
  onDeleted,
  onCancel,
}: {
  visitId: number
  onDeleted: (event: DeletionEventDetail) => void
  onCancel: () => void
}) {
  const [preview, setPreview] = useState<VisitDeletionPreview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [password, setPassword] = useState('')

  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    previewVisitDeletion(visitId)
      .then((result) => {
        if (!cancelled) setPreview(result)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(friendlyErrorMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [visitId])

  const trimmedReason = reason.trim()
  const expected = preview?.required_confirmation ?? ''
  const confirmationMatches =
    expected.length > 0 && confirmation.trim().toUpperCase() === expected.toUpperCase()
  // Every gate must be satisfied, and the preview must have LOADED - without it there is no
  // confirmation phrase to check against and no idea what is about to be destroyed.
  const canDelete =
    !deleting && preview !== null && trimmedReason.length >= 10 && confirmationMatches &&
    password.length > 0

  const handleDelete = async () => {
    if (!canDelete) return
    setDeleting(true)
    setError(null)
    try {
      const event = await deleteShedVisit(visitId, {
        password,
        reason: trimmedReason,
        confirmation: confirmation.trim(),
      })
      setPassword('')
      onDeleted(event)
    } catch (err) {
      setError(friendlyErrorMessage(err))
      // Cleared on failure too: a wrong password must be retyped, not resubmitted by a second click.
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
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Delete shed visit">
      <div className="modal modal-danger delete-visit-dialog">
        <h2 className="danger-heading">Delete Shed Visit Permanently</h2>

        {loadError ? (
          <div className="form-error" role="alert">
            Could not load what this deletion would destroy, so it cannot proceed safely. {loadError}
          </div>
        ) : preview === null ? (
          <p role="status">Checking what this visit contains…</p>
        ) : (
          <>
            <dl className="delete-visit-summary">
              <dt>Locomotive</dt>
              <dd>{preview.visit.loco_number ?? '—'}</dd>
              <dt>Visit</dt>
              <dd>#{preview.visit.id}</dd>
              <dt>Schedule</dt>
              <dd>
                {[preview.visit.schedule_family, preview.visit.schedule_variant]
                  .filter(Boolean)
                  .join(' / ') || '—'}
              </dd>
              <dt>Arrival</dt>
              <dd>
                {preview.visit.arrival_at
                  ? new Date(preview.visit.arrival_at).toLocaleString()
                  : '—'}
              </dd>
              <dt>Status</dt>
              <dd>{preview.visit.status ?? '—'}</dd>
            </dl>

            <div className="delete-visit-counts">
              <h3>This will permanently delete {preview.total_rows} record(s):</h3>
              {Object.keys(preview.counts).length === 0 ? (
                <p>Only the shed visit itself.</p>
              ) : (
                <ul>
                  {Object.entries(preview.counts)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([entity, count]) => (
                      <li key={entity}>
                        <strong>{count}</strong> {entity.replace(/_/g, ' ')}
                      </li>
                    ))}
                </ul>
              )}
            </div>

            {preview.warnings.length > 0 && (
              <ul className="delete-visit-warnings" role="alert">
                {preview.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}

            {preview.files.some((f) => f.shared) && (
              <p className="form-hint">
                Some referenced files are also used by records that are not being deleted. Those
                files are left on disk.
              </p>
            )}

            <p className="delete-visit-irreversible">
              This cannot be undone. A permanent record of the deletion — who, when, why, and a
              snapshot of every destroyed row — is written to the Deletion History.
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
              <span className="field-label">
                To confirm, type <code>{expected}</code>
              </span>
              <input
                type="text"
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                disabled={deleting}
                autoComplete="off"
              />
            </label>

            <label className="field">
              <span className="field-label">Your own account password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={deleting}
                // Never offered to a password manager: this is a re-authentication challenge, not a
                // login, and a saved value would defeat the point of asking.
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
            <ButtonLabel busy={deleting} label="Delete Visit Permanently" busyLabel="Deleting…" />
          </button>
        </div>
      </div>
    </div>
  )
}
