import { BookingEquipmentField } from '../equipment/BookingEquipmentField'
import type { EquipmentSelection } from '../equipment/EquipmentSelector'
import type { DefectType } from '../../types'

export function LogBookBookingRow({
  index,
  equipmentSelection,
  defectTypeId,
  remarks,
  defectTypes,
  defectTypesLoading,
  locoNumber,
  fieldError,
  onEquipmentChange,
  onDefectTypeChange,
  onRemarksChange,
  onRemove,
  titlePrefix = 'Booking',
  idNamespace = 'booking',
}: {
  index: number
  equipmentSelection: EquipmentSelection | null
  defectTypeId: number | null
  remarks: string
  defectTypes: DefectType[]
  defectTypesLoading: boolean
  /** The locomotive this booking belongs to. Its technology decides which equipment may be
   * chosen; there is no technology control on this form. */
  locoNumber: string | null
  fieldError?: string | null
  onEquipmentChange: (selection: EquipmentSelection | null) => void
  onDefectTypeChange: (id: number | null) => void
  onRemarksChange: (text: string) => void
  onRemove: () => void
  /** e.g. "Finding" to reuse this row for Test Before findings instead of
   * Shed In Log Book bookings — defaults to "Booking". */
  titlePrefix?: string
  /** Override when more than one instance of this row can be on the page
   * for a different feature at once — ids must stay unique. */
  idNamespace?: string
}) {
  const idPrefix = `${idNamespace}-${index}`

  return (
    <div className="panel log-book-booking-row">
      <div className="log-book-booking-row-header">
        <h3>
          {titlePrefix} #{index + 1}
        </h3>
        <button type="button" className="btn btn-secondary btn-small" onClick={onRemove}>
          Remove
        </button>
      </div>

      {fieldError && (
        <div className="form-error" role="alert">
          {fieldError}
        </div>
      )}

      {locoNumber ? (
        <BookingEquipmentField
          idPrefix={idPrefix}
          locoNumber={locoNumber}
          selection={equipmentSelection}
          onChange={onEquipmentChange}
        />
      ) : (
        <p className="field-hint">Select the locomotive first, then search for its equipment.</p>
      )}

      <div className="field">
        <label className="field-label" htmlFor={`${idPrefix}-defect-type`}>
          Defect Type
        </label>
        {defectTypesLoading ? (
          <div className="field-loading" role="status">
            Loading defect types…
          </div>
        ) : (
          <select
            id={`${idPrefix}-defect-type`}
            value={defectTypeId ?? ''}
            onChange={(e) => onDefectTypeChange(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">-- Select defect type --</option>
            {defectTypes.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="field">
        <label className="field-label" htmlFor={`${idPrefix}-remarks`}>
          Remarks
        </label>
        <textarea
          id={`${idPrefix}-remarks`}
          rows={2}
          value={remarks}
          onChange={(e) => onRemarksChange(e.target.value)}
          placeholder="Describe the defect…"
        />
      </div>
    </div>
  )
}
