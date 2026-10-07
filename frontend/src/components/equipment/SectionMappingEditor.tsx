import { useEffect, useState } from 'react'
import type { Section } from '../../types'
import { ButtonLabel } from '../ui/States'

interface Props {
  nodeId: number
  sections: Section[]
  sectionsLoading: boolean
  selectedCodes: string[]
  hasDirectMapping: boolean
  saving: boolean
  onSave: (codes: string[]) => void
  onClear: () => void
}

export function SectionMappingEditor({
  nodeId,
  sections,
  sectionsLoading,
  selectedCodes,
  hasDirectMapping,
  saving,
  onSave,
  onClear,
}: Props) {
  const [checked, setChecked] = useState<Set<string>>(new Set(selectedCodes))
  const [filter, setFilter] = useState('')
  const [confirmingClear, setConfirmingClear] = useState(false)

  // Re-sync local selection whenever the selected node changes or its
  // saved mapping is reloaded (e.g. right after a successful save).
  useEffect(() => {
    setChecked(new Set(selectedCodes))
    setConfirmingClear(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, selectedCodes.join(',')])

  function toggle(code: string) {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  const visibleSections = filter.trim()
    ? sections.filter(
        (s) =>
          s.code.toLowerCase().includes(filter.trim().toLowerCase()) ||
          s.name.toLowerCase().includes(filter.trim().toLowerCase()),
      )
    : sections

  return (
    <div className="panel section-mapping-editor">
      <h3>Mapped Sections</h3>

      {sectionsLoading ? (
        <div className="field-loading" role="status">Loading sections…</div>
      ) : sections.length === 0 ? (
        <p className="mapping-empty">No sections available.</p>
      ) : (
        <>
          {sections.length > 8 && (
            <input
              type="search"
              placeholder="Filter sections…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              aria-label="Filter sections"
              className="section-filter"
            />
          )}
          <ul className="section-checkbox-list">
            {visibleSections.map((s) => (
              <li key={s.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={checked.has(s.code)}
                    onChange={() => toggle(s.code)}
                  />
                  {s.name} ({s.code})
                </label>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="section-mapping-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={saving || sectionsLoading}
          aria-busy={saving}
          onClick={() => onSave(Array.from(checked))}
        >
          <ButtonLabel busy={saving} label="Save Mapping" busyLabel="Saving…" />
        </button>

        {!confirmingClear ? (
          <button
            type="button"
            className="btn btn-danger-outline"
            disabled={saving || !hasDirectMapping}
            onClick={() => setConfirmingClear(true)}
          >
            Clear Direct Mapping
          </button>
        ) : (
          <span className="confirm-clear">
            <span>Remove all direct mappings from this node?</span>
            <button
              type="button"
              className="btn btn-danger"
              disabled={saving}
              onClick={() => {
                setConfirmingClear(false)
                onClear()
              }}
            >
              Confirm Clear
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={saving}
              onClick={() => setConfirmingClear(false)}
            >
              Cancel
            </button>
          </span>
        )}
      </div>
      <p className="field-hint">
        Clearing this node's direct mapping does not remove responsibility — it allows the nearest
        mapped ancestor to take over instead.
      </p>
    </div>
  )
}
