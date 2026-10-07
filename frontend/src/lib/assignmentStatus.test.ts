import { describe, expect, it } from 'vitest'

import {
  hasUnresolvedSection,
  multiSectionLabel,
  sectionChipMeta,
  sectionChipTitle,
} from './assignmentStatus'
import { ASSIGNMENT_STATUS_META } from './status'

describe('sectionChipMeta', () => {
  it('maps OPEN to the neutral tone', () => {
    expect(sectionChipMeta('OPEN')).toEqual({ label: 'OPEN', glyph: '○', tone: 'neutral' })
  })

  it('maps IN_PROGRESS to the info tone', () => {
    // NOT `active`: there is no .tag-active CSS rule, so an `active` chip renders as the plain
    // base tag and becomes visually indistinguishable from OPEN. See the module docstring.
    expect(sectionChipMeta('IN_PROGRESS')).toEqual({
      label: 'IN PROGRESS',
      glyph: '◐',
      tone: 'info',
    })
  })

  it('maps ATTENDED to the success tone', () => {
    expect(sectionChipMeta('ATTENDED')).toEqual({ label: 'ATTENDED', glyph: '✓', tone: 'success' })
  })

  it('maps REOPENED to the warn tone', () => {
    expect(sectionChipMeta('REOPENED')).toEqual({ label: 'REOPENED', glyph: '↻', tone: 'warn' })
  })

  it('reuses the labels and glyphs from ASSIGNMENT_STATUS_META rather than restating them', () => {
    // The vocabulary has exactly one definition. If someone edits a label in status.ts, this
    // module must follow automatically - a second hardcoded copy here is the bug being prevented.
    for (const [status, meta] of Object.entries(ASSIGNMENT_STATUS_META)) {
      const chip = sectionChipMeta(status)
      expect(chip.label).toBe(meta.label)
      expect(chip.glyph).toBe(meta.glyph)
    }
  })

  it('only ever returns a tone that .tag-* actually implements', () => {
    // .tag-{info,success,warn} plus the base .tag for neutral. A tone outside this set renders
    // unstyled, which is a silent visual regression rather than a test failure.
    const implemented = new Set(['neutral', 'info', 'success', 'warn'])
    for (const status of [...Object.keys(ASSIGNMENT_STATUS_META), 'SOMETHING_NEW', '']) {
      expect(implemented.has(sectionChipMeta(status).tone)).toBe(true)
    }
  })

  it('degrades readably for an unknown status instead of throwing', () => {
    // An older bundle talking to a newer API. The four values are CHECK-constrained server-side,
    // so this means a version skew, and a blank or crashed chip would be the worse outcome.
    const chip = sectionChipMeta('SOME_NEW_STATUS')
    expect(chip.label).toBe('SOME NEW STATUS')
    expect(chip.tone).toBe('neutral')
  })

  it('degrades for null and undefined', () => {
    expect(sectionChipMeta(null).tone).toBe('neutral')
    expect(sectionChipMeta(undefined).tone).toBe('neutral')
  })
})

describe('sectionChipTitle', () => {
  it('combines the section code and its status into one string', () => {
    expect(sectionChipTitle('M4-HR', 'ATTENDED')).toBe('M4-HR — ATTENDED')
    expect(sectionChipTitle('MACHINE SHOP', 'OPEN')).toBe('MACHINE SHOP — OPEN')
  })

  it('is a single string, so it is matchable as one text node', () => {
    // A label split across elements is matchable by neither getByText nor getByTitle in the
    // obvious way, and that has already produced flaky assertions in this codebase.
    expect(sectionChipTitle('M4-HR', 'IN_PROGRESS')).toBe('M4-HR — IN PROGRESS')
  })
})

describe('multiSectionLabel', () => {
  it('returns null below two sections so nothing renders', () => {
    expect(multiSectionLabel(0)).toBeNull()
    expect(multiSectionLabel(1)).toBeNull()
  })

  it('labels a genuinely multi-section booking', () => {
    expect(multiSectionLabel(2)).toBe('2 sections')
    expect(multiSectionLabel(3)).toBe('3 sections')
  })
})

describe('hasUnresolvedSection', () => {
  it('mirrors the backend UNRESOLVED_ASSIGNMENT_STATUSES', () => {
    expect(hasUnresolvedSection(['OPEN'])).toBe(true)
    expect(hasUnresolvedSection(['IN_PROGRESS'])).toBe(true)
    expect(hasUnresolvedSection(['REOPENED'])).toBe(true)
    expect(hasUnresolvedSection(['ATTENDED'])).toBe(false)
    expect(hasUnresolvedSection(['ATTENDED', 'ATTENDED'])).toBe(false)
    expect(hasUnresolvedSection(['ATTENDED', 'OPEN'])).toBe(true)
  })

  it('reports no outstanding work for an empty list', () => {
    // Display only. The backend treats "zero assignments" as UNRESOLVED for gating purposes; this
    // helper is asked a different question - "do any of these rows have work left" - and must not
    // become the frontend's competing definition of resolved.
    expect(hasUnresolvedSection([])).toBe(false)
  })
})
