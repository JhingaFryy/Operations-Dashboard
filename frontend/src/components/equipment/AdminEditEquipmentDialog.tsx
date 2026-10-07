import { useEffect, useState } from 'react'
import { adminUpdateNode, getMapping } from '../../api/equipment'
import { friendlyErrorMessage } from '../../api/client'
import { SectionCheckboxGroup } from './SectionCheckboxGroup'
import { ButtonLabel } from '../ui/States'
import type { EquipmentNode, PathItem, Section } from '../../types'

/** Edit EXISTING equipment from the Equipment Responsibility Mapping page. ADMIN ONLY.
 *
 * WHAT CAN BE CHANGED: the equipment's own name, its description, whether it is active, and
 * which sections are responsible for it.
 *
 * WHAT CANNOT, DELIBERATELY: where it sits in the hierarchy. The family and path are shown
 * read-only. Moving a node rewrites every descendant's path, changes which siblings its name
 * must be unique among, and changes how historical bookings render their equipment - that is a
 * separate feature with a separate blast radius, and the server does not accept a parent or
 * family here either, so hiding the control is not the only defence.
 *
 * THE MAPPING IS REPLACE-SET, so the dialog must know the COMPLETE current mapping before it
 * can safely save one. It therefore loads the mapping itself on open and refuses to save until
 * that has succeeded - saving a partially-loaded set would silently unmap sections the
 * administrator never saw. That is the single most damaging mistake this dialog could make.
 *
 * RENAMING IS NOT RE-IDENTIFYING. Bookings store equipment_node_id, so a rename keeps every
 * historical booking attached to this same node - they simply display the new name from now on.
 */
export function AdminEditEquipmentDialog({
  node,
  path,
  familyCode,
  sections,
  sectionsLoading,
  onDone,
  onCancel,
}: {
  node: EquipmentNode
  path: PathItem[]
  familyCode: string | null
  sections: Section[]
  sectionsLoading: boolean
  onDone: (result: { node: EquipmentNode; sectionCodes: string[] }) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(node.name)
  const [description, setDescription] = useState(node.description ?? '')
  const [isActive, setIsActive] = useState(node.is_active !== false)

  // The mapping the dialog opened with, and the set being edited. Both start empty and are
  // only usable once the load below succeeds - see `mappingLoaded`.
  const [originalCodes, setOriginalCodes] = useState<string[]>([])
  const [selectedCodes, setSelectedCodes] = useState<string[]>([])
  const [mappingLoaded, setMappingLoaded] = useState(false)
  const [mappingError, setMappingError] = useState<string | null>(null)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Set when the metadata saved but the mapping did not. The dialog stays open, because the
   * administrator's work is not finished and telling them it is would be false. */
  const [partial, setPartial] = useState(false)

  useEffect(() => {
    let cancelled = false
    setMappingLoaded(false)
    setMappingError(null)
    getMapping(node.id)
      .then((mapping) => {
        if (cancelled) return
        setOriginalCodes(mapping.section_codes)
        setSelectedCodes(mapping.section_codes)
        setMappingLoaded(true)
      })
      .catch((err) => {
        if (cancelled) return
        // Refused rather than defaulted to empty: an empty set would look like "no sections"
        // and saving it would unmap the equipment entirely.
        setMappingError(friendlyErrorMessage(err))
      })
    return () => {
      cancelled = true
    }
  }, [node.id])

  const toggle = (code: string, checked: boolean) =>
    setSelectedCodes((prev) =>
      checked ? [...new Set([...prev, code])] : prev.filter((c) => c !== code),
    )

  const trimmedName = name.trim()
  const nameChanged = trimmedName !== node.name
  const descriptionChanged = description.trim() !== (node.description ?? '').trim()
  const activeChanged = isActive !== (node.is_active !== false)
  const sameSet =
    selectedCodes.length === originalCodes.length &&
    selectedCodes.every((c) => originalCodes.includes(c))
  const mappingChanged = mappingLoaded && !sameSet

  const nothingToSave = !nameChanged && !descriptionChanged && !activeChanged && !mappingChanged
  const canSave =
    !saving && mappingLoaded && trimmedName.length > 0 && selectedCodes.length > 0 && !nothingToSave

  const handleSave = async () => {
    if (!canSave) return
    setSaving(true)
    setError(null)
    setPartial(false)
    try {
      const updated = await adminUpdateNode(node.id, {
        ...(nameChanged ? { name: trimmedName } : {}),
        // Only sent when it actually changed, so an untouched description is never rewritten.
        ...(descriptionChanged ? { description: description.trim() || null } : {}),
        ...(activeChanged ? { isActive } : {}),
        // The COMPLETE set, only ever from a mapping this dialog successfully loaded.
        ...(mappingChanged ? { sectionCodes: selectedCodes } : {}),
      })
      onDone({ node: updated, sectionCodes: updated.section_codes ?? selectedCodes })
    } catch (err) {
      const detail = (err as { body?: { detail?: { partial?: boolean } } })?.body?.detail
      if (detail && typeof detail === 'object' && detail.partial) setPartial(true)
      setError(friendlyErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Edit equipment">
      <div className="modal equipment-dialog">
        <h2>Edit Equipment</h2>

        {/* Read-only context. Shown because identity is (family, parent, name) - the
            administrator needs to see WHICH namesake they are editing - and not editable
            because moving equipment is out of scope here. */}
        <dl className="equipment-dialog-context">
          <dt>Family</dt>
          <dd>{familyCode ?? '—'}</dd>
          <dt>Location</dt>
          <dd>{path.length > 0 ? path.map((p) => p.name).join(' / ') : 'Top level'}</dd>
        </dl>

        <label className="field">
          <span className="field-label">Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={saving}
            maxLength={200}
          />
        </label>

        <label className="field">
          <span className="field-label">Description</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={saving}
            rows={2}
          />
        </label>

        <label className="field field-inline">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
            disabled={saving}
          />
          <span className="field-label">Active</span>
        </label>

        {mappingError ? (
          <div className="form-error" role="alert">
            Could not load this equipment’s current sections, so they cannot be edited safely.{' '}
            {mappingError}
          </div>
        ) : (
          <SectionCheckboxGroup
            idPrefix={`edit-equipment-${node.id}`}
            legend="Responsible sections"
            sections={sections}
            selected={selectedCodes}
            onToggle={toggle}
            disabled={saving || sectionsLoading || !mappingLoaded}
          />
        )}

        {mappingLoaded && selectedCodes.length === 0 && (
          <p className="form-hint">
            Equipment must stay mapped to at least one section, or its bookings cannot be routed.
          </p>
        )}

        {error && (
          <div className="form-error" role="alert">
            {partial
              ? 'The equipment details were saved, but its sections were not. Check the sections below and save again.'
              : error}
          </div>
        )}

        <div className="modal-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={handleSave} disabled={!canSave}>
            <ButtonLabel busy={saving} label="Save Changes" busyLabel="Saving…" />
          </button>
        </div>
      </div>
    </div>
  )
}
