/** Per-section assignment status, presented as a chip in the global Booking Pool.
 *
 * WHY THIS IS NOT JUST ASSIGNMENT_STATUS_META
 * -------------------------------------------
 * lib/status.ts already owns the assignment vocabulary - the label and the glyph for each of
 * OPEN / IN_PROGRESS / ATTENDED / REOPENED - and this module reuses it rather than restating it.
 * What it cannot reuse is the TONE, because `Tag` and `StatusBadge` do not support the same tones:
 *
 *   .status-badge-{neutral,info,active,success,warn,danger}   <- all six exist
 *   .tag-{info,success,warn}                                  <- only three, plus the base .tag
 *
 * ASSIGNMENT_STATUS_META gives IN_PROGRESS the tone `active`, which is correct for a StatusBadge
 * and invisible on a Tag: there is no `.tag-active` rule, so the chip would silently fall back to
 * the plain base style and look identical to OPEN. Mapping it to `info` here keeps the chip
 * visually distinct using a class that actually exists.
 *
 * So: one vocabulary (imported), one tone table per component family (defined here). The
 * alternative - hardcoding tones inside BookingRow - is what this module exists to prevent.
 */

import { ASSIGNMENT_STATUS_META, statusMeta } from './status'

/** The tones `.tag-*` actually implements. `neutral` is the base `.tag` with no modifier class. */
export type TagTone = 'neutral' | 'info' | 'success' | 'warn'

/** Status -> Tag tone. Deliberately separate from ASSIGNMENT_STATUS_META.tone; see the module
 *  docstring for why they differ for IN_PROGRESS. */
const TAG_TONE: Record<string, TagTone> = {
  OPEN: 'neutral',
  IN_PROGRESS: 'info',
  ATTENDED: 'success',
  REOPENED: 'warn',
}

export interface SectionChipMeta {
  /** Status word in the backend's own vocabulary, e.g. "IN PROGRESS". */
  label: string
  /** Decorative only - always rendered aria-hidden next to real text. */
  glyph: string
  tone: TagTone
}

/** Presentation for one section's assignment status.
 *
 * An unrecognised status degrades rather than throwing: statusMeta() humanises the raw key and the
 * tone falls back to neutral. A backend that adds a fifth status should produce a readable chip,
 * not a blank one or a crash - the four values are CHECK-constrained server-side, so an unknown
 * one means this bundle is older than the API it is talking to. */
export function sectionChipMeta(status: string | null | undefined): SectionChipMeta {
  const meta = statusMeta(ASSIGNMENT_STATUS_META, status)
  return {
    label: meta.label,
    glyph: meta.glyph,
    tone: (status && TAG_TONE[status]) || 'neutral',
  }
}

/** The full text a chip conveys, used for its `title` and its screen-reader text.
 *
 * Returned as ONE string so it renders as a single text node. A label split across elements is
 * matchable by neither `getByText` nor `getByTitle` in the obvious way, and that has already
 * caused flaky assertions on the "Added by PPIO" badge in this codebase. */
export function sectionChipTitle(sectionCode: string, status: string | null | undefined): string {
  return `${sectionCode} — ${sectionChipMeta(status).label}`
}

/** "2 sections" when a booking is genuinely multi-section, otherwise null.
 *
 * null (not an empty string) for 0 or 1 so the caller's `{x && ...}` renders nothing at all: a
 * single-section booking is the normal case and must not gain a redundant "1 section" chip. */
export function multiSectionLabel(sectionCount: number): string | null {
  return sectionCount > 1 ? `${sectionCount} sections` : null
}

/** Does this booking still have outstanding work in any section?
 *
 * Mirrors the backend's UNRESOLVED_ASSIGNMENT_STATUSES (app/domain/booking_resolution.py). Display
 * only - never a gate. Every real gate is enforced server-side, and this must not become the
 * frontend's own competing definition of "resolved". */
export function hasUnresolvedSection(statuses: readonly string[]): boolean {
  return statuses.some((s) => s === 'OPEN' || s === 'IN_PROGRESS' || s === 'REOPENED')
}
