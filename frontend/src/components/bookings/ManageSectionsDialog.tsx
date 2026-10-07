import { useEffect, useMemo, useState } from 'react'
import { addBookingSections } from '../../api/bookings'
import { listSections } from '../../api/sections'
import { friendlyErrorMessage } from '../../api/client'
import { bookingSourceLabel } from '../../lib/format'
import {
  additionPlan,
  canSubmitAddition,
  isLockedSelection,
  routableDestinations,
} from '../../lib/bookingRouting'
import type { BookingPoolItem, Section } from '../../types'

/**
 * ADD maintenance sections to a booking.
 *
 * ADDITIVE ONLY, and the UI says so rather than relying on the user to notice: a section the
 * booking already has is shown CHECKED and DISABLED. It cannot be unchecked, because the server
 * has no removal path at all - an unchecked box could only ever mislead. The submit button is
 * enabled only when at least one NEW section is selected.
 *
 * WHAT THIS DELIBERATELY CANNOT DO. Nothing here reads or writes the booking's description,
 * defect, source, equipment, locomotive, status, attendance or timestamps - the payload carries
 * section ids and an optional reason, and the server refuses anything else. A planning section is
 * never offered as a destination, because PPIO routes and is never routed to; nor is any section
 * whose status as a work destination is unestablished.
 */
export function ManageSectionsDialog({
  booking,
  onClose,
  onSaved,
}: {
  booking: BookingPoolItem
  onClose: () => void
  onSaved: () => void
}) {
  const currentIds = useMemo(
    () => (booking.routed_sections ?? []).map((s) => s.section_id),
    [booking.routed_sections],
  )

  const [sections, setSections] = useState<Section[]>([])
  // Seeded with what is already assigned, so those boxes render checked. They are locked, so
  // this never shrinks below currentIds.
  const [selected, setSelected] = useState<number[]>(currentIds)
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listSections()
      .then((rows) => {
        if (!cancelled) setSections(rows)
      })
      .catch((err) => {
        if (!cancelled) setError(friendlyErrorMessage(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Planning sections are filtered out here as well as refused by the server, so the checklist
  // never offers a destination that would be rejected on submit.
  const destinations = useMemo(() => routableDestinations(sections), [sections])
  const plan = useMemo(() => additionPlan(currentIds, selected), [currentIds, selected])

  const toggle = (id: number) => {
    // An already-assigned section is locked: refuse the toggle rather than rely on `disabled`
    // alone, so a programmatic or keyboard path cannot un-select it either.
    if (isLockedSelection(id, currentIds)) return
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  const submit = async () => {
    setSaving(true)
    setError(null)
    try {
      // Only the NEW sections are sent. The existing ones are not restated, so there is no way
      // for this request to be interpreted as a desired set.
      await addBookingSections(booking.id, plan.toAdd, reason.trim() || undefined)
      onSaved()
    } catch (err) {
      // A 409 here is the removal-safety rule: a section whose work has started cannot be
      // dropped. The server's own message names the section and why, so it is shown verbatim
      // rather than replaced with a generic failure.
      setError(friendlyErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const sectionCodeById = useMemo(
    () => new Map(sections.map((s) => [s.id, s.code])),
    [sections],
  )
  const codesFor = (ids: number[]) =>
    ids.map((id) => sectionCodeById.get(id) ?? String(id)).join(', ')

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="manage-sections-title">
        <h2 id="manage-sections-title">Add sections</h2>

        <dl className="manage-sections-meta">
          <div>
            <dt>Locomotive</dt>
            <dd>{booking.shed_visit.loco_number}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{bookingSourceLabel(booking.booking_source)}</dd>
          </div>
          <div>
            <dt>Equipment</dt>
            <dd>{booking.equipment_node_name ?? 'No equipment specified'}</dd>
          </div>
          <div>
            <dt>Defect</dt>
            <dd>{booking.description}</dd>
          </div>
        </dl>

        {error && (
          <div className="manage-sections-error" role="alert">
            {error}
          </div>
        )}

        {loading ? (
          <p role="status">Loading sections…</p>
        ) : (
          <fieldset className="section-checklist">
            <legend>Maintenance sections</legend>
            {destinations.length === 0 && <p>No maintenance sections are available.</p>}
            {destinations.map((section) => (
              <label key={section.id} className="section-checklist-item">
                <input
                  type="checkbox"
                  checked={selected.includes(section.id)}
                  onChange={() => toggle(section.id)}
                  /* Already assigned: checked and locked. PPIO cannot remove a section, so an
                     un-checkable box is the honest control. */
                  disabled={saving || isLockedSelection(section.id, currentIds)}
                />
                <span>{section.code}</span>
                {isLockedSelection(section.id, currentIds) && (
                  <span className="section-checklist-name">already assigned</span>
                )}
                {section.name && section.name !== section.code && (
                  <span className="section-checklist-name">{section.name}</span>
                )}
              </label>
            ))}
          </fieldset>
        )}

        <div className="field">
          <label className="field-label" htmlFor="manage-sections-reason">
            Reason (optional)
          </label>
          <input
            id="manage-sections-reason"
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why this routing"
            disabled={saving}
          />
        </div>

        {/* What submitting would do, stated before it is done - the same before/after pair the
            audit event records. */}
        <p className="manage-sections-summary" role="status">
          {plan.isNoOp
            ? 'Select at least one section that is not already assigned.'
            : `Adding ${codesFor(plan.toAdd)}. Existing sections are kept.`}
        </p>

        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={submit}
            /* An empty set is refused here as it is on the server: a booking routed to nothing
               would be invisible to every section. */
            /* Enabled only when at least one NEW section is selected. */
            disabled={saving || loading || !canSubmitAddition(currentIds, selected)}
          >
            {saving ? 'Adding…' : 'Add sections'}
          </button>
        </div>
      </div>
    </div>
  )
}
