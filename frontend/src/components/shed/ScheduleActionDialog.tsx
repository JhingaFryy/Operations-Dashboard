import type React from 'react'
import { useEffect, useState } from 'react'
import { localDateTimeToIso, nowLocalDateTimeValue } from '../../lib/localDateTime'
import { ButtonLabel } from '../ui/States'

export interface ScheduleActionDialogProps {
  title: string
  /** One sentence saying what confirming will record. */
  description: string
  label: string
  confirmLabel: string
  busy?: boolean
  error?: string | null
  /** Rendered in place of `error` when the failure has structure worth showing - today the
   * checksheet readiness blocker. */
  errorDetail?: React.ReactNode
  onConfirm: (isoTimestamp: string) => void
  onCancel: () => void
}

/**
 * The shared date/time confirmation used by Start Schedule, Complete Schedule and Shed Out.
 *
 * Same contract for all three (and matching the Shed In form's arrival field): the input is
 * pre-filled with the current local time so the common case is a single click, but stays fully
 * editable because these actions are routinely recorded after the fact. The value is converted
 * to an ISO instant exactly once, in localDateTimeToIso.
 */
export function ScheduleActionDialog({
  title,
  description,
  label,
  confirmLabel,
  busy = false,
  error = null,
  errorDetail = null,
  onConfirm,
  onCancel,
}: ScheduleActionDialogProps) {
  const [value, setValue] = useState(() => nowLocalDateTimeValue())
  const [localError, setLocalError] = useState<string | null>(null)

  useEffect(() => {
    // Re-seed to "now" each time the dialog is opened for a new action.
    setValue(nowLocalDateTimeValue())
    setLocalError(null)
  }, [title])

  const submit = () => {
    const iso = localDateTimeToIso(value)
    if (!iso) {
      setLocalError('Enter a valid date and time.')
      return
    }
    setLocalError(null)
    onConfirm(iso)
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="modal-title">{title}</h2>
        <p className="modal-description">{description}</p>

        <div className="field">
          <label className="field-label field-label-required" htmlFor="schedule-action-at">
            {label}
          </label>
          <input
            id="schedule-action-at"
            type="datetime-local"
            value={value}
            aria-required="true"
            onChange={(e) => setValue(e.target.value)}
          />
          <p className="field-hint">Defaults to now. Edit it if you are recording this later.</p>
        </div>

        {localError ? (
          <p className="form-error" role="alert">
            {localError}
          </p>
        ) : (
          errorDetail ?? (error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          ))
        )}

        <div className="modal-actions">
          <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy} aria-busy={busy}>
            <ButtonLabel busy={busy} label={confirmLabel} busyLabel="Saving…" />
          </button>
        </div>
      </div>
    </div>
  )
}
