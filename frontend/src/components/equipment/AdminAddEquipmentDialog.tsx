import { useEffect, useRef, useState } from 'react'
import { addNodeSections, adminCreateNode, matchNodesByName } from '../../api/equipment'
import { listSections } from '../../api/sections'
import { friendlyErrorMessage } from '../../api/client'
import { SectionCheckboxGroup } from './SectionCheckboxGroup'
import { ButtonLabel } from '../ui/States'
import type {
  EquipmentFamily,
  EquipmentNode,
  EquipmentNodeMatch,
  PathItem,
  Section,
} from '../../types'

export interface AdminAddEquipmentResult {
  node: EquipmentNode
  path: PathItem[]
  sectionCodes: string[]
  /** True when an existing node gained sections instead of a new node being created. */
  reused: boolean
}

const DEBOUNCE_MS = 350

/** One shared empty array. `setMatches([])` would hand React a fresh array identity on
 * every run of the lookup effect, which is a re-render for no change in meaning. */
const NO_MATCHES: EquipmentNodeMatch[] = []

/** Add equipment to the hierarchy from the Equipment Responsibility Mapping page, or give
 * equipment that is already there another responsible section.
 *
 * WHY THE PARENT IS ASKED FOR. This is administration of the master data, not a booking
 * shortcut, so the equipment's place in the tree is the administrator's decision. It is
 * also load-bearing twice over: identity is (family, parent, canonical name), so the
 * parent decides what counts as a duplicate; and bookings resolve their section from the
 * nearest mapped ancestor, so the parent decides routing when the node has no mapping of
 * its own. The dialog defaults to the branch already open on the page.
 *
 * WHY IT LOOKS FOR MATCHES AS YOU TYPE. Production holds 7,423 active nodes in which 1,129
 * names are reused under different parents ("Others" 637 times, "Split Pin" 149).
 * Somebody typing "Traction Motor" very often means one that already exists, and creating
 * a second node beside it would split one piece of equipment's bookings across two records
 * permanently.
 *
 * IT NEVER GUESSES WHICH ONE. Every namesake in the family is listed with its full path
 * and its current sections, and the administrator chooses; only an explicit "Create new
 * equipment instead" makes a new node. */
export function AdminAddEquipmentDialog({
  families,
  initialFamilyCode,
  parent,
  onDone,
  onCancel,
}: {
  families: EquipmentFamily[]
  initialFamilyCode: string | null
  /** The node currently selected on the page, offered as the parent. Null creates a root. */
  parent: { node: EquipmentNode; path: PathItem[] } | null
  onDone: (result: AdminAddEquipmentResult) => void
  onCancel: () => void
}) {
  const [familyCode, setFamilyCode] = useState(initialFamilyCode ?? '')
  const [underParent, setUnderParent] = useState(Boolean(parent))
  const [name, setName] = useState('')
  const [selectedSections, setSelectedSections] = useState<string[]>([])
  const [sections, setSections] = useState<Section[]>([])
  const [sectionsLoading, setSectionsLoading] = useState(true)

  const [matches, setMatches] = useState<EquipmentNodeMatch[]>([])
  /** False while a name lookup is outstanding. */
  const [settled, setSettled] = useState(true)
  const [reusing, setReusing] = useState<EquipmentNodeMatch | null>(null)
  const [forceCreate, setForceCreate] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)

  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const requestIdRef = useRef(0)

  useEffect(() => {
    listSections()
      .then(setSections)
      .catch(() => setSections([]))
      .finally(() => setSectionsLoading(false))
  }, [])

  const trimmedName = name.trim()
  // Derived during render rather than pushed into state from the effect below: there is
  // nothing to look up until both a family and a name exist, and that is a fact about the
  // current props, not an asynchronous result.
  const lookupPending = Boolean(trimmedName) && Boolean(familyCode)

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    // Nothing to look up yet. Clearing any previous matches is done by resetDecision(),
    // from the events that invalidate them, so this effect only ever issues requests.
    if (!lookupPending) return
    debounceRef.current = setTimeout(() => {
      const requestId = ++requestIdRef.current
      matchNodesByName({ family: familyCode }, trimmedName)
        .then((items) => {
          if (requestId === requestIdRef.current) setMatches(items)
        })
        .catch(() => {
          // A failed lookup must not block creation; the server's own duplicate guard is
          // the real protection and still applies.
          if (requestId === requestIdRef.current) setMatches(NO_MATCHES)
        })
        .finally(() => {
          if (requestId === requestIdRef.current) setSettled(true)
        })
    }, DEBOUNCE_MS)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [trimmedName, familyCode, lookupPending])

  /** Called from the events that invalidate a lookup - typing a name, changing family.
   * Marking the lookup unsettled HERE rather than inside the effect keeps the effect to
   * one job (issuing the request) and avoids a render pass that only flips a flag. */
  function resetDecision() {
    setSettled(false)
    setMatches(NO_MATCHES)
    setReusing(null)
    setForceCreate(false)
    setSelectedSections([])
    setError(null)
  }

  function toggleSection(code: string, checked: boolean) {
    setSelectedSections((prev) =>
      checked ? (prev.includes(code) ? prev : [...prev, code]) : prev.filter((c) => c !== code),
    )
  }

  const parentId = underParent && parent ? parent.node.id : null
  const showMatches = matches.length > 0 && !forceCreate
  // A root must carry its own mapping or it can never be booked; a child inherits its
  // ancestors', so it may legitimately be created without one.
  const sectionsRequired = reusing ? true : parentId === null

  function submit() {
    if (submitting) return
    const cleaned = name.trim().replace(/\s+/g, ' ')
    if (!familyCode) {
      setFieldError('Choose the locomotive family.')
      return
    }
    if (!reusing && !cleaned) {
      setFieldError('Enter the equipment name.')
      return
    }
    if (sectionsRequired && selectedSections.length === 0) {
      setFieldError(
        reusing
          ? 'Choose at least one section to add.'
          : 'A root-level item needs at least one section, or it can never be booked.',
      )
      return
    }
    setFieldError(null)
    setError(null)
    setSubmitting(true)

    const request = reusing
      ? addNodeSections(reusing.id, selectedSections).then((result) => ({
          node: reusing as EquipmentNode,
          path: reusing.path,
          sectionCodes: result.section_codes,
          reused: true,
        }))
      : adminCreateNode({
          familyCode,
          parentId,
          name: cleaned,
          sectionCodes: selectedSections,
        }).then((node) => ({
          node: node as EquipmentNode,
          path: node.path.length > 0 ? node.path : [{ id: node.id, name: node.name }],
          sectionCodes: node.section_codes,
          reused: false,
        }))

    request
      .then(onDone)
      .catch((err) => setError(friendlyErrorMessage(err)))
      .finally(() => setSubmitting(false))
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Add Equipment"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="modal-title">Add Equipment</h2>
        <p className="modal-description">
          Adds equipment to the master hierarchy, or gives equipment that already exists
          another responsible section.
        </p>

        <div className="field">
          <label className="field-label field-label-required" htmlFor="admin-add-family">
            Locomotive Family
          </label>
          <select
            id="admin-add-family"
            value={familyCode}
            aria-required="true"
            onChange={(e) => {
              setFamilyCode(e.target.value)
              setUnderParent(false)
              resetDecision()
            }}
          >
            <option value="">-- Select family --</option>
            {families.map((f) => (
              <option key={f.id} value={f.code}>
                {f.name}
              </option>
            ))}
          </select>
        </div>

        <fieldset className="section-choice">
          <legend className="field-label">Place Under</legend>
          <div className="section-choice-options">
            <label className="section-choice-option" htmlFor="admin-add-parent-root">
              <input
                id="admin-add-parent-root"
                type="radio"
                name="admin-add-parent"
                checked={!underParent}
                onChange={() => {
                  setUnderParent(false)
                  resetDecision()
                }}
              />
              <span>Top level of this family</span>
            </label>
            {parent && (
              <label className="section-choice-option" htmlFor="admin-add-parent-selected">
                <input
                  id="admin-add-parent-selected"
                  type="radio"
                  name="admin-add-parent"
                  checked={underParent}
                  onChange={() => {
                    setUnderParent(true)
                    resetDecision()
                  }}
                />
                <span>Under {parent.path.map((p) => p.name).join(' → ')}</span>
              </label>
            )}
          </div>
          {!parent && (
            <p className="field-hint">
              Select an item in the hierarchy before opening this dialog to add equipment
              beneath it.
            </p>
          )}
        </fieldset>

        <div className="field">
          <label className="field-label field-label-required" htmlFor="admin-add-name">
            Equipment Name
          </label>
          <input
            id="admin-add-name"
            type="text"
            value={name}
            aria-required="true"
            onChange={(e) => {
              setName(e.target.value)
              resetDecision()
            }}
          />
          {lookupPending && !settled && (
            <div className="field-loading" role="status">
              Checking existing equipment…
            </div>
          )}
        </div>

        {showMatches && (
          <div className="equipment-existing" role="group" aria-label="Existing equipment">
            <p className="equipment-existing-title">
              {matches.length === 1
                ? 'Equipment already exists.'
                : `"${name.trim()}" already exists in ${matches.length} locations.`}
            </p>
            <ul className="equipment-existing-list">
              {matches.map((match) => (
                <li key={match.id}>
                  <button
                    type="button"
                    className={
                      reusing?.id === match.id
                        ? 'equipment-existing-item is-chosen'
                        : 'equipment-existing-item'
                    }
                    aria-pressed={reusing?.id === match.id}
                    onClick={() => {
                      setReusing(match)
                      setForceCreate(false)
                      setSelectedSections([])
                      setFieldError(null)
                    }}
                  >
                    <span className="equipment-existing-path">
                      {match.path.map((p) => p.name).join(' → ')}
                    </span>
                    <span className="equipment-existing-sections">
                      {match.section_codes.length > 0
                        ? `Sections: ${match.section_codes.join(', ')}`
                        : 'Not mapped to any section'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {!reusing && (
              <p className="field-hint">
                Choose the equipment you mean to add sections to it, or create a new one.
              </p>
            )}
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => {
                setForceCreate(true)
                setReusing(null)
                setSelectedSections([])
              }}
            >
              Create new equipment instead
            </button>
          </div>
        )}

        {/* Sections are asked for once it is settled WHAT is being mapped - an existing
            node the administrator picked, or a new one. Asking sooner invites a choice
            made against the wrong equipment. */}
        {(reusing || !showMatches) &&
          (sectionsLoading ? (
            <div className="field-loading" role="status">
              Loading sections…
            </div>
          ) : (
            <SectionCheckboxGroup
              idPrefix="admin-add"
              legend={reusing ? 'Add Sections' : 'Map to Sections'}
              sections={sections}
              selected={selectedSections}
              alreadyMapped={reusing?.section_codes ?? []}
              onToggle={toggleSection}
              disabled={submitting}
            />
          ))}

        {(reusing || !showMatches) && (
          <p className="field-hint">
            {reusing
              ? 'The sections already mapped stay mapped; only the ones you tick are added.'
              : sectionsRequired
                ? 'A top-level item needs at least one section, or bookings against it cannot be routed.'
                : 'Optional: with none chosen, this item inherits its parent’s responsible sections.'}
          </p>
        )}

        {fieldError && (
          <p className="form-error" role="alert">
            {fieldError}
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <div className="modal-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onCancel}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={submit}
            disabled={submitting || (showMatches && !reusing)}
            aria-busy={submitting}
          >
            <ButtonLabel
              busy={submitting}
              label={reusing ? 'Add Section Mapping' : 'Add Equipment'}
              busyLabel="Saving…"
            />
          </button>
        </div>
      </div>
    </div>
  )
}
