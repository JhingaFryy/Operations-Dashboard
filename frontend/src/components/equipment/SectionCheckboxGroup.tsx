import type { Section } from '../../types'

/** Choose one or more sections, from the real `sections` list.
 *
 * A checkbox group rather than a multi-select: every option and every choice is visible at
 * once without a second interaction, it is keyboard-operable with nothing custom (Tab to
 * each box, Space to toggle), and a section cannot be selected twice because each has
 * exactly one checkbox. There is no free-text path, so an arbitrary section name cannot be
 * submitted.
 *
 * `alreadyMapped` codes are shown ticked and disabled: they describe the equipment's
 * current state, which this control cannot remove. Removing a mapping is deliberately not
 * offered here - it belongs to Equipment Responsibility Mapping, which administers the
 * whole set rather than adding to it mid-booking. */
export function SectionCheckboxGroup({
  idPrefix,
  legend,
  sections,
  selected,
  alreadyMapped = [],
  onToggle,
  disabled = false,
}: {
  idPrefix: string
  legend: string
  sections: Section[]
  selected: string[]
  /** Codes the equipment already has; rendered ticked, disabled and not re-submittable. */
  alreadyMapped?: string[]
  onToggle: (code: string, checked: boolean) => void
  disabled?: boolean
}) {
  const mapped = new Set(alreadyMapped)

  return (
    <fieldset className="section-choice">
      <legend className="field-label field-label-required">{legend}</legend>
      <div className="section-choice-options">
        {sections.map((section) => {
          const isMapped = mapped.has(section.code)
          const checked = isMapped || selected.includes(section.code)
          return (
            <label
              key={section.id}
              className={isMapped ? 'section-choice-option is-mapped' : 'section-choice-option'}
              htmlFor={`${idPrefix}-section-${section.id}`}
            >
              <input
                id={`${idPrefix}-section-${section.id}`}
                type="checkbox"
                checked={checked}
                disabled={disabled || isMapped}
                onChange={(e) => onToggle(section.code, e.target.checked)}
              />
              <span>{section.name}</span>
              {isMapped && <span className="section-choice-note">already mapped</span>}
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
