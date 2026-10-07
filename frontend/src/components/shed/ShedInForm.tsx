import { useEffect, useState } from 'react'
import { listDefectTypes } from '../../api/defectTypes'
import { createShedIn, asShedInErrorDetail } from '../../api/shedVisits'
import { friendlyErrorMessage } from '../../api/client'
import { familyForVariant, SCHEDULE_OPTIONS } from '../../lib/schedule'
import { nowLocalDateTimeValue } from '../../lib/localDateTime'
import { LocomotiveSelect } from './LocomotiveSelect'
import { LogBookBookingRow } from './LogBookBookingRow'
import type { EquipmentSelection } from '../equipment/EquipmentSelector'
import { ButtonLabel } from '../ui/States'
import type {
  ArrivalCondition,
  DefectType,
  Locomotive,
  ScheduleVariant,
  ShedInRequest,
} from '../../types'

interface BookingDraft {
  key: string
  equipmentSelection: EquipmentSelection | null
  defectTypeId: number | null
  remarks: string
}

let nextKey = 1
function newBookingDraft(): BookingDraft {
  return { key: `booking-${nextKey++}`, equipmentSelection: null, defectTypeId: null, remarks: '' }
}

const ARRIVAL_CONDITIONS: ArrivalCondition[] = ['WORKING', 'DEAD']

export function ShedInForm({ onSuccess }: { onSuccess: (message: string) => void }) {
  const [defectTypes, setDefectTypes] = useState<DefectType[]>([])
  const [defectTypesLoading, setDefectTypesLoading] = useState(true)

  const [locomotive, setLocomotive] = useState<Locomotive | null>(null)

  function handleLocomotiveChange(next: Locomotive | null) {
    setLocomotive(next)
    if (next?.loco_number === locomotive?.loco_number) return
    // A different locomotive can be a different technology, so every equipment choice made for the
    // previous one is dropped rather than carried over - a stale node id must never be submitted
    // against a locomotive it does not belong to (the server would refuse it, and should never
    // have to). Defect type and remarks are technology-independent and are kept.
    setBookings((prev) => prev.map((b) => ({ ...b, equipmentSelection: null })))
  }
  const [scheduleVariant, setScheduleVariant] = useState<ScheduleVariant | ''>('')
  const [arrivalCondition, setArrivalCondition] = useState<ArrivalCondition | ''>('')
  // Pre-filled with the current local time so the common case is a single click, but fully
  // editable - arrivals are routinely recorded after the fact. Same contract as every other
  // shed lifecycle action (see lib/localDateTime.ts).
  const [arrivalAt, setArrivalAt] = useState(() => nowLocalDateTimeValue())
  const [bookings, setBookings] = useState<BookingDraft[]>([])

  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [bookingErrors, setBookingErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  useEffect(() => {
    listDefectTypes()
      .then(setDefectTypes)
      .catch(() => setDefectTypes([]))
      .finally(() => setDefectTypesLoading(false))
  }, [])

  function resetForm() {
    setLocomotive(null)
    setScheduleVariant('')
    setArrivalCondition('')
    setArrivalAt('')
    setBookings([])
    setValidationErrors([])
    setBookingErrors({})
  }

  function addBooking() {
    setBookings((prev) => [...prev, newBookingDraft()])
  }

  function removeBooking(key: string) {
    setBookings((prev) => prev.filter((b) => b.key !== key))
  }

  function updateBooking(key: string, patch: Partial<BookingDraft>) {
    setBookings((prev) => prev.map((b) => (b.key === key ? { ...b, ...patch } : b)))
  }

  function validate(): string[] {
    const errors: string[] = []
    if (!locomotive) errors.push('Select a locomotive.')
    if (!scheduleVariant) errors.push('Select a schedule (IA, IA0, IB, IC, IC0, IOH, or TOH).')
    if (!arrivalCondition) errors.push('Select the arrival condition.')
    if (!arrivalAt) errors.push('Enter the actual arrival date/time.')
    bookings.forEach((b, i) => {
      if (!b.equipmentSelection) errors.push(`Booking #${i + 1}: select equipment.`)
      if (!b.defectTypeId) errors.push(`Booking #${i + 1}: select a defect type.`)
      if (!b.remarks.trim()) errors.push(`Booking #${i + 1}: remarks cannot be blank.`)
    })
    return errors
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return

    setSubmitError(null)
    setBookingErrors({})

    const errors = validate()
    setValidationErrors(errors)
    if (errors.length > 0) return

    const variant = scheduleVariant as ScheduleVariant
    const payload: ShedInRequest = {
      loco_number: locomotive!.loco_number,
      schedule_family: familyForVariant(variant),
      schedule_variant: variant,
      arrival_condition: arrivalCondition as ArrivalCondition,
      arrival_at: new Date(arrivalAt).toISOString(),
      log_book_bookings: bookings.map((b) => ({
        equipment_node_id: b.equipmentSelection!.node.id,
        defect_type_id: b.defectTypeId!,
        remarks: b.remarks.trim(),
      })),
    }

    setSubmitting(true)
    createShedIn(payload)
      .then((result) => {
        const message =
          `Shed In recorded for ${result.loco_number}: ${result.bookings_created} booking(s), ` +
          `${result.stages_created} stage(s) created.`
        resetForm()
        onSuccess(message)
      })
      .catch((err) => {
        const detail = asShedInErrorDetail(err)
        if (detail?.booking_index != null && bookings[detail.booking_index]) {
          setBookingErrors({ [bookings[detail.booking_index].key]: detail.message })
        }
        setSubmitError(friendlyErrorMessage(err))
      })
      .finally(() => setSubmitting(false))
  }

  return (
    <form className="shed-in-form" onSubmit={handleSubmit}>
      <LocomotiveSelect value={locomotive} onChange={handleLocomotiveChange} />

      <div className="field">
        <label className="field-label field-label-required" htmlFor="schedule-variant">
          Schedule
        </label>
        <select
          id="schedule-variant"
          value={scheduleVariant}
          aria-required="true"
          aria-invalid={validationErrors.length > 0 && !scheduleVariant ? 'true' : undefined}
          onChange={(e) => setScheduleVariant(e.target.value as ScheduleVariant | '')}
        >
          <option value="">-- Select schedule --</option>
          {SCHEDULE_OPTIONS.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field-label field-label-required" htmlFor="arrival-condition">
          Arrival Condition
        </label>
        <select
          id="arrival-condition"
          value={arrivalCondition}
          aria-required="true"
          aria-invalid={validationErrors.length > 0 && !arrivalCondition ? 'true' : undefined}
          onChange={(e) => setArrivalCondition(e.target.value as ArrivalCondition | '')}
        >
          <option value="">-- Select condition --</option>
          {ARRIVAL_CONDITIONS.map((c) => (
            <option key={c} value={c}>
              {c === 'WORKING' ? 'Working' : 'Dead'}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field-label field-label-required" htmlFor="arrival-at">
          Actual Arrival Date/Time
        </label>
        <input
          id="arrival-at"
          type="datetime-local"
          value={arrivalAt}
          aria-required="true"
          aria-invalid={validationErrors.length > 0 && !arrivalAt ? 'true' : undefined}
          onChange={(e) => setArrivalAt(e.target.value)}
        />
        <p className="field-hint">Defaults to now. Edit it if you are recording this later.</p>
      </div>

      <h2>Log Book Bookings</h2>
      {bookings.length === 0 && <p className="field-hint">No Log Book bookings added yet.</p>}
      {bookings.map((b, i) => (
        <LogBookBookingRow
          key={b.key}
          index={i}
          equipmentSelection={b.equipmentSelection}
          defectTypeId={b.defectTypeId}
          remarks={b.remarks}
          defectTypes={defectTypes}
          defectTypesLoading={defectTypesLoading}
          locoNumber={locomotive?.loco_number ?? null}
          fieldError={bookingErrors[b.key]}
          onEquipmentChange={(selection) => updateBooking(b.key, { equipmentSelection: selection })}
          onDefectTypeChange={(id) => updateBooking(b.key, { defectTypeId: id })}
          onRemarksChange={(text) => updateBooking(b.key, { remarks: text })}
          onRemove={() => removeBooking(b.key)}
        />
      ))}

      <button type="button" className="btn btn-secondary" onClick={addBooking}>
        + Add Booking
      </button>

      {validationErrors.length > 0 && (
        <div className="form-error" role="alert" id="shed-in-validation-errors">
          <ul>
            {validationErrors.map((msg) => (
              <li key={msg}>{msg}</li>
            ))}
          </ul>
        </div>
      )}

      {submitError && (
        <div className="form-error" role="alert">
          {submitError}
        </div>
      )}

      <div className="shed-in-form-actions">
        <button
          type="submit"
          className="btn btn-primary"
          disabled={submitting}
          aria-busy={submitting}
          aria-describedby={validationErrors.length > 0 ? 'shed-in-validation-errors' : undefined}
        >
          <ButtonLabel busy={submitting} label="Shed In" busyLabel="Submitting…" />
        </button>
      </div>
    </form>
  )
}
