/**
 * Planner booking routing: what may be added, and where.
 *
 * MIRRORS THE BACKEND, OWNS NOTHING. The server decides all of this independently
 * (app/services/booking_routing_service.py, app/core/authz.py) and refuses anything this file
 * would wrongly permit. These helpers exist so a planner is not offered an action that will be
 * refused, and so the decision is testable rather than buried in JSX.
 *
 * ADDITIVE ONLY. There is no "desired set" anywhere in this module and no way to express a
 * removal - an earlier version took the complete set, which let a client un-route a section by
 * omitting it. A planner adds responsibility; it never transfers or withdraws it.
 */

/**
 * EVERY booking source is routable. There is no allow-list here any more.
 *
 * Planning responsibility covers the whole pool, and because the operation can only ADD a
 * section, the source a finding came from is not what makes it safe. Keeping a list would also
 * mean a new legitimate source needed a frontend change before a planner could route it.
 */
export function isRoutableSource(_source: string | null | undefined): boolean {
  return true
}

export interface RoutableSectionOption {
  id: number
  code: string
  name?: string | null
}

/**
 * Planning sections - they route, and are never routed TO.
 *
 * The ONLY exclusion. Mirrors authz.PLANNING_SECTION_CODES, and it is the only section identity
 * this file knows: everything else comes from the sections the API returns.
 */
export const PLANNING_SECTION_CODES: readonly string[] = ['PPIO']

function normalize(code: string | null | undefined): string {
  return (code ?? '').trim().toUpperCase()
}

export function isPlanningSectionCode(code: string | null | undefined): boolean {
  return PLANNING_SECTION_CODES.includes(normalize(code))
}

/**
 * Whether a booking may be routed TO this section.
 *
 * DERIVED, NOT LISTED. An earlier version of this file carried ASSIGNABLE_WORK_SECTION_CODES -
 * ten codes copied from the Minor Inspection section set - and it defeated the point of the
 * feature: planning exists precisely to route work beyond the default equipment mapping, and the
 * real cases (MACHINE SHOP for welding, PRE-MONSOON for rain leakage, SHIFT, CMS Lab, MILL-WRIGHT)
 * were all excluded by it. Now the only exclusion is a planning section, matching the backend, so
 * a section added through the normal section-management workflow becomes selectable with no
 * frontend change.
 */
export function isAssignableWorkSectionCode(code: string | null | undefined): boolean {
  const normalized = normalize(code)
  if (!normalized) return false
  return !PLANNING_SECTION_CODES.includes(normalized)
}

/**
 * The sections the dialog may offer, from the sections the API returned.
 *
 * A planning section is filtered out entirely rather than shown disabled: it is not a thing a
 * planner could ever choose, so presenting it would only invite the question.
 */
export function routableDestinations<T extends RoutableSectionOption>(sections: readonly T[]): T[] {
  return sections
    .filter((s) => isAssignableWorkSectionCode(s.code))
    .slice()
    .sort((a, b) => a.code.localeCompare(b.code))
}

/** What submitting would add. There is no `removed` - removal is not expressible. */
export interface AdditionPlan {
  toAdd: number[]
  alreadyAssigned: number[]
  isNoOp: boolean
}

export function additionPlan(
  currentlyAssigned: readonly number[],
  selected: readonly number[],
): AdditionPlan {
  const current = new Set(currentlyAssigned)
  const toAdd = selected.filter((id) => !current.has(id))
  const alreadyAssigned = selected.filter((id) => current.has(id))
  return { toAdd, alreadyAssigned, isNoOp: toAdd.length === 0 }
}

/** Whether the selection may be submitted - at least one section that is not already assigned. */
export function canSubmitAddition(
  currentlyAssigned: readonly number[],
  selected: readonly number[],
): boolean {
  return additionPlan(currentlyAssigned, selected).toAdd.length > 0
}

/**
 * Whether a checkbox for an already-assigned section must be locked.
 *
 * ALWAYS true. An existing assignment is shown so the planner can see it, and locked so it
 * cannot be unchecked - the backend has no removal path at all, so an unchecked box could only
 * ever mislead.
 */
export function isLockedSelection(
  sectionId: number,
  currentlyAssigned: readonly number[],
): boolean {
  return currentlyAssigned.includes(sectionId)
}
