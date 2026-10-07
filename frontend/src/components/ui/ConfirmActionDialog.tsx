import type { ReactNode } from 'react'

/** Inline confirmation step for a state-changing action.
 *
 * Rendered in place (not portalled) so it stays inside the row/card it
 * belongs to — the confirmation and the thing being confirmed are never
 * separated, and keyboard focus order stays natural.
 *
 * `reason` turns it into a reason-capturing confirmation: the confirm button
 * stays disabled until the operator has typed something, which is exactly
 * what the backend requires for attend/reopen remarks.
 */
export function ConfirmActionDialog({
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  tone = 'primary',
  busy = false,
  disabled = false,
  reason,
  children,
  onConfirm,
  onCancel,
}: {
  title: string
  description?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  tone?: 'primary' | 'danger'
  busy?: boolean
  disabled?: boolean
  reason?: {
    id: string
    label: string
    value: string
    onChange: (value: string) => void
    /** When true the confirm button stays disabled until non-blank. */
    required?: boolean
    rows?: number
  }
  children?: ReactNode
  onConfirm: () => void
  onCancel: () => void
}) {
  const reasonBlocked = Boolean(reason?.required) && !reason?.value.trim()
  return (
    <div className="confirm-action" role="group" aria-label={title}>
      <p className="confirm-action-title">{title}</p>
      {description && <p className="confirm-action-description">{description}</p>}

      {reason && (
        <div className="confirm-action-field">
          <label className="field-label" htmlFor={reason.id}>
            {reason.label}
          </label>
          <textarea
            id={reason.id}
            rows={reason.rows ?? 2}
            value={reason.value}
            onChange={(e) => reason.onChange(e.target.value)}
          />
        </div>
      )}

      {children}

      <div className="confirm-action-buttons">
        <button
          type="button"
          className={`btn btn-small ${tone === 'danger' ? 'btn-danger' : 'btn-primary'}`}
          disabled={busy || disabled || reasonBlocked}
          aria-busy={busy}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
        <button type="button" className="btn btn-secondary btn-small" disabled={busy} onClick={onCancel}>
          {cancelLabel}
        </button>
      </div>
    </div>
  )
}
