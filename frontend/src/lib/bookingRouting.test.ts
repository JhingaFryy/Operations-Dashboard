import { describe, expect, it } from 'vitest'
import {
  PLANNING_SECTION_CODES,
  additionPlan,
  canSubmitAddition,
  isAssignableWorkSectionCode,
  isLockedSelection,
  isPlanningSectionCode,
  isRoutableSource,
  routableDestinations,
} from './bookingRouting'

describe('every booking source is routable', () => {
  it('permits all eight known sources', () => {
    for (const source of [
      'LOG_BOOK',
      'TEST_BEFORE',
      'SCHEDULE_INSPECTION',
      'TEST_AFTER',
      'SPECIAL_CHECKING',
      'TRIP_INSPECTION',
      'GENERAL_CHECKING',
      'MANUAL',
    ]) {
      expect(isRoutableSource(source)).toBe(true)
    }
  })

  it('permits a source this build has never heard of', () => {
    // Planning responsibility covers the whole pool, and the operation can only ADD a section -
    // so a new legitimate source must not need a frontend change before it can be routed.
    expect(isRoutableSource('FUTURE_SOURCE')).toBe(true)
    expect(isRoutableSource(null)).toBe(true)
  })
})

describe('assignable destinations - derived, not listed', () => {
  const sections = [
    { id: 8, code: 'M2-HR' },
    { id: 1, code: 'M1-HR' },
    { id: 90, code: 'PPIO' },
    { id: 4, code: 'M6-HR' },
    { id: 93, code: 'SHIFT' },
    { id: 94, code: 'CMS Lab' },
    { id: 95, code: 'MACHINE SHOP' },
    { id: 96, code: 'PRE-MONSOON' },
    { id: 97, code: 'MILL-WRIGHT' },
  ]

  it('offers every section the API returned EXCEPT planning ones', () => {
    // The real planning cases - welding at MACHINE SHOP, rain leakage to PRE-MONSOON - were all
    // refused by the old hardcoded ten-code list. They are offered now.
    expect(routableDestinations(sections).map((s) => s.code)).toEqual([
      'CMS Lab',
      'M1-HR',
      'M2-HR',
      'M6-HR',
      'MACHINE SHOP',
      'MILL-WRIGHT',
      'PRE-MONSOON',
      'SHIFT',
    ])
  })

  it('filters a planning section out entirely rather than showing it disabled', () => {
    expect(routableDestinations(sections).some((s) => s.code === 'PPIO')).toBe(false)
    expect(isPlanningSectionCode('PPIO')).toBe(true)
    expect(isPlanningSectionCode(' ppio ')).toBe(true)
    expect(isAssignableWorkSectionCode('PPIO')).toBe(false)
    expect([...PLANNING_SECTION_CODES]).toEqual(['PPIO'])
  })

  it('accepts the sections the old allow-list wrongly refused', () => {
    for (const code of ['SHIFT', 'PRE-MONSOON', 'MILL-WRIGHT', 'MACHINE SHOP', 'CMS Lab']) {
      expect(isAssignableWorkSectionCode(code)).toBe(true)
    }
  })

  it('accepts a section this build has never heard of', () => {
    // A section added through the normal section-management workflow must become selectable with
    // no frontend change. This is the assertion that would fail if a list came back.
    expect(isAssignableWorkSectionCode('BRAND-NEW-SECTION')).toBe(true)
    expect(
      routableDestinations([...sections, { id: 999, code: 'BRAND-NEW-SECTION' }])
        .some((s) => s.code === 'BRAND-NEW-SECTION'),
    ).toBe(true)
  })

  it('refuses a blank or missing code', () => {
    expect(isAssignableWorkSectionCode('')).toBe(false)
    expect(isAssignableWorkSectionCode('   ')).toBe(false)
    expect(isAssignableWorkSectionCode(null)).toBe(false)
    expect(isAssignableWorkSectionCode(undefined)).toBe(false)
  })

  it('orders destinations by code so the checklist does not move about', () => {
    const codes = routableDestinations(sections).map((s) => s.code)
    expect(codes).toEqual([...codes].sort((a, b) => a.localeCompare(b)))
  })
})

describe('additionPlan - additive only', () => {
  it('reports what would be added', () => {
    expect(additionPlan([8], [8, 4])).toEqual({
      toAdd: [4],
      alreadyAssigned: [8],
      isNoOp: false,
    })
  })

  it('has NO concept of removal', () => {
    // The shape is the safeguard: there is no `removed` field, so no caller can construct one.
    const plan = additionPlan([8, 4], [8])
    expect(plan).not.toHaveProperty('removed')
    // Omitting 4 adds nothing and removes nothing.
    expect(plan.toAdd).toEqual([])
    expect(plan.isNoOp).toBe(true)
  })

  it('treats a fully-already-assigned selection as a no-op', () => {
    expect(additionPlan([8, 4], [4, 8]).isNoOp).toBe(true)
  })

  it('treats the first routing of an unrouted booking as all additions', () => {
    expect(additionPlan([], [8, 4]).toAdd).toEqual([8, 4])
  })
})

describe('canSubmitAddition', () => {
  it('requires at least one NEW section', () => {
    expect(canSubmitAddition([8], [8])).toBe(false)
    expect(canSubmitAddition([8], [8, 4])).toBe(true)
    expect(canSubmitAddition([], [])).toBe(false)
    expect(canSubmitAddition([], [4])).toBe(true)
  })
})

describe('isLockedSelection', () => {
  it('locks every already-assigned section', () => {
    // The server has no removal path, so an un-checkable box is the only honest control.
    expect(isLockedSelection(8, [8, 4])).toBe(true)
    expect(isLockedSelection(1, [8, 4])).toBe(false)
    expect(isLockedSelection(8, [])).toBe(false)
  })
})
