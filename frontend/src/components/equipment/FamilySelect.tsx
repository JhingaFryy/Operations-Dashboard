import type { EquipmentFamily } from '../../types'

export function FamilySelect({
  families,
  value,
  onChange,
  loading,
  id = 'equipment-family',
}: {
  families: EquipmentFamily[]
  value: string | null
  onChange: (code: string | null) => void
  loading: boolean
  /** Override when more than one FamilySelect can be on the page at once
   * (e.g. one per Shed In booking row) — ids must stay unique. */
  id?: string
}) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        Locomotive Family
      </label>
      {loading ? (
        <div className="field-loading" role="status">
          Loading families…
        </div>
      ) : (
        <select id={id} value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">-- Select family --</option>
          {families.map((f) => (
            <option key={f.id} value={f.code}>
              {f.name} ({f.code})
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
