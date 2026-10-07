import { useEffect, useMemo, useState } from 'react'
import { createPlanningBooking } from '../../api/bookings'
import { listCurrentShedVisits } from '../../api/shedVisits'
import { listDefectTypes } from '../../api/defectTypes'
import { listSections } from '../../api/sections'
import { friendlyErrorMessage } from '../../api/client'
import { EquipmentSearchBox } from '../equipment/EquipmentSearchBox'
import { formatSchedule } from '../../lib/format'
import { isLockedSelection, routableDestinations } from '../../lib/bookingRouting'
import type {
  CurrentShedVisit,
  DefectType,
  EquipmentNodeSearchResult,
  Section,
} from '../../types'

/**
 * Raise a booking as a planner.
 *
 * WHAT THE PLANNER SUPPLIES is only what a booking genuinely needs: which visit, which equipment,
 * the defect type, the description, and optionally which additional sections are responsible.
 *
 * WHAT THEY CANNOT SUPPLY is everything the server owns - booking_source (fixed to MANUAL),
 * status, created_by and every timestamp. Those are not disabled fields; they are not fields at
 * all, so there is nothing to tamper with. Provenance comes from bookings.created_by, which is
 * how the pool later renders the "Added by PPIO" badge.
 *
 * SECTIONS ARE OPTIONAL OR REQUIRED DEPENDING ON THE EQUIPMENT, and the server is the one that
 * knows which: if Loco Master maps the equipment, the mapped sections are used and extras are
 * additional; if it does not, the planner's selection is the booking's whole routing, and
 * selecting none is refused with NO_SECTION_MAPPING. Rather than predict that here, the dialog
 * explains the rule and surfaces the server's refusal verbatim when it happens.
 */
export function CreatePlanningBookingDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => void
}) {
  const [visits, setVisits] = useState<CurrentShedVisit[]>([])
  const [defectTypes, setDefectTypes] = useState<DefectType[]>([])
  const [sections, setSections] = useState<Section[]>([])
  const [loading, setLoading] = useState(true)

  const [visitId, setVisitId] = useState<number | ''>('')
  const [equipment, setEquipment] = useState<EquipmentNodeSearchResult | null>(null)
  const [defectTypeId, setDefectTypeId] = useState<number | ''>('')
  const [description, setDescription] = useState('')
  const [selectedSections, setSelectedSections] = useState<number[]>([])
  const [reason, setReason] = useState('')

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([listCurrentShedVisits(), listDefectTypes(), listSections()])
      .then(([v, d, s]) => {
        if (cancelled) return
        setVisits(v)
        setDefectTypes(d)
        setSections(s)
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

  const visit = useMemo(
    () => visits.find((v) => v.id === visitId) ?? null,
    [visits, visitId],
  )

  // From the sections the API returned, minus planning ones - never a hardcoded list, so a
  // section added through normal section management appears here with no frontend change.
  const destinations = useMemo(() => routableDestinations(sections), [sections])

  // Equipment search is scoped to the chosen locomotive, exactly as the Shed In booking rows do,
  // so a result from another technology cannot be picked.
  const equipmentScope = visit ? { locoNumber: visit.loco_number } : null

  const toggleSection = (id: number) => {
    setSelectedSections((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  const canSubmit =
    !saving && !loading && visitId !== '' && equipment !== null && defectTypeId !== ''
    && description.trim().length > 0

  const submit = async () => {
    if (!canSubmit) return
    setSaving(true)
    setError(null)
    try {
      await createPlanningBooking({
        shed_visit_id: Number(visitId),
        equipment_node_id: equipment!.id,
        defect_type_id: Number(defectTypeId),
        description: description.trim(),
        additional_section_ids: selectedSections,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      })
      onCreated()
    } catch (err) {
      // Shown verbatim. The one the planner most needs to read is NO_SECTION_MAPPING: the
      // equipment has no mapping, so at least one responsible section has to be chosen.
      setError(friendlyErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="planning-booking-title">
        <h2 id="planning-booking-title">Create planning booking</h2>

        {error && (
          <div className="manage-sections-error" role="alert">
            {error}
          </div>
        )}

        {loading ? (
          <p role="status">Loading…</p>
        ) : (
          <>
            <div className="field">
              <label className="field-label" htmlFor="planning-visit">
                Locomotive / shed visit
              </label>
              <select
                id="planning-visit"
                value={visitId}
                onChange={(e) => {
                  setVisitId(e.target.value === '' ? '' : Number(e.target.value))
                  // The equipment search is scoped to the locomotive, so a visit change
                  // invalidates any equipment already chosen.
                  setEquipment(null)
                }}
                disabled={saving}
              >
                <option value="">Select a locomotive in shed…</option>
                {visits.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.loco_number} — {formatSchedule(v.schedule_family, v.schedule_variant)}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="planning-equipment">
                Equipment
              </label>
              {equipmentScope ? (
                equipment ? (
                  <div className="planning-equipment-chosen">
                    <span>{equipment.name}</span>
                    <button type="button" onClick={() => setEquipment(null)} disabled={saving}>
                      Change
                    </button>
                  </div>
                ) : (
                  <EquipmentSearchBox
                    id="planning-equipment"
                    scope={equipmentScope}
                    onSelectResult={setEquipment}
                  />
                )
              ) : (
                <p className="field-hint">Choose a locomotive first.</p>
              )}
            </div>

            <div className="field">
              <label className="field-label" htmlFor="planning-defect">
                Defect type
              </label>
              <select
                id="planning-defect"
                value={defectTypeId}
                onChange={(e) => setDefectTypeId(e.target.value === '' ? '' : Number(e.target.value))}
                disabled={saving}
              >
                <option value="">Select a defect type…</option>
                {defectTypes.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="planning-description">
                Description
              </label>
              <textarea
                id="planning-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                disabled={saving}
              />
            </div>

            <fieldset className="section-checklist">
              <legend>Responsible sections</legend>
              <p className="field-hint">
                The equipment’s mapped section is added automatically and always kept. Choose any
                additional sections that are also responsible. If the equipment has no mapping,
                at least one section must be chosen here.
              </p>
              {destinations.map((section) => (
                <label key={section.id} className="section-checklist-item">
                  <input
                    type="checkbox"
                    checked={selectedSections.includes(section.id)}
                    onChange={() => toggleSection(section.id)}
                    disabled={saving || isLockedSelection(section.id, [])}
                  />
                  <span>{section.code}</span>
                  {section.name && section.name !== section.code && (
                    <span className="section-checklist-name">{section.name}</span>
                  )}
                </label>
              ))}
            </fieldset>

            <div className="field">
              <label className="field-label" htmlFor="planning-reason">
                Reason (optional)
              </label>
              <input
                id="planning-reason"
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why this routing"
                disabled={saving}
              />
            </div>
          </>
        )}

        <div className="modal-actions">
          <button type="button" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={submit} disabled={!canSubmit}>
            {saving ? 'Creating…' : 'Create booking'}
          </button>
        </div>
      </div>
    </div>
  )
}
