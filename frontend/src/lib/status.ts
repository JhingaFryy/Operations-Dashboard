/** The single status vocabulary for the whole app.
 *
 * Every status shown anywhere in the Dashboard resolves through one of the
 * maps below, so a colour never means two different things on two pages.
 * Status is never communicated by colour alone: each entry carries a text
 * label AND a distinct glyph, and StatusBadge always renders both.
 *
 * This module is presentation only. It never decides whether something is
 * satisfied, eligible, or permitted — those judgements come from the
 * backend responses verbatim.
 */

export type StatusTone =
  | 'neutral' // not started / inert
  | 'info' // informational, no action implied
  | 'active' // work underway
  | 'success' // done / satisfied
  | 'warn' // needs attention, not an error
  | 'danger' // rejected / blocked

export interface StatusMeta {
  label: string
  tone: StatusTone
  /** Decorative glyph, always rendered aria-hidden alongside the text label
   * so the status never depends on colour or shape alone. */
  glyph: string
}

const FALLBACK: StatusMeta = { label: '—', tone: 'neutral', glyph: '·' }

/** Booking + section-assignment lifecycle. Both use the same four values
 * server-side, so they share one vocabulary here.
 *
 * Labels stay in the backend's own uppercase vocabulary: operators read and
 * quote these exact words to each other and in BL-DCMS. */
export const ASSIGNMENT_STATUS_META: Record<string, StatusMeta> = {
  OPEN: { label: 'OPEN', tone: 'neutral', glyph: '○' },
  IN_PROGRESS: { label: 'IN PROGRESS', tone: 'active', glyph: '◐' },
  ATTENDED: { label: 'ATTENDED', tone: 'success', glyph: '✓' },
  // REOPENED is the "needs attention" state: distinct warm tone, distinct
  // glyph, and a left accent bar wherever a row/card carries it.
  REOPENED: { label: 'REOPENED', tone: 'warn', glyph: '↻' },
}

/** BL-DCMS checksheet record status. DRAFT/REJECTED do not satisfy a
 * requirement; SUBMITTED/UNDER_REVIEW/APPROVED do — that rule lives in the
 * backend and is only reflected (never re-derived) by the tones here. */
export const CHECKSHEET_STATUS_META: Record<string, StatusMeta> = {
  DRAFT: { label: 'DRAFT', tone: 'neutral', glyph: '○' },
  SUBMITTED: { label: 'SUBMITTED', tone: 'info', glyph: '↑' },
  UNDER_REVIEW: { label: 'UNDER REVIEW', tone: 'active', glyph: '◐' },
  APPROVED: { label: 'APPROVED', tone: 'success', glyph: '✓' },
  REJECTED: { label: 'REJECTED', tone: 'danger', glyph: '✕' },
}

/** Per-requirement progress state returned by
 * /checksheet-requirement-progress. */
export const CHECKSHEET_PROGRESS_META: Record<string, StatusMeta> = {
  NOT_STARTED: { label: 'NOT STARTED', tone: 'neutral', glyph: '○' },
  IN_PROGRESS: { label: 'IN PROGRESS', tone: 'active', glyph: '◐' },
  SATISFIED: { label: 'SUBMITTED / SATISFIED', tone: 'success', glyph: '✓' },
  REJECTED: { label: 'REJECTED — needs resubmission', tone: 'danger', glyph: '✕' },
}

/** shed_visit_stage.status. */
export const STAGE_STATUS_META: Record<string, StatusMeta> = {
  PENDING: { label: 'Pending', tone: 'neutral', glyph: '○' },
  IN_PROGRESS: { label: 'In Progress', tone: 'active', glyph: '◐' },
  COMPLETED: { label: 'Completed', tone: 'success', glyph: '✓' },
  // Test Before only: an Admin skip. Deliberately not the success tone - it is not a completion.
  SKIPPED: { label: 'Skipped', tone: 'warn', glyph: '↷' },
}

/** shed_visit.status. Kept in the backend's own vocabulary because it is
 * what the movement register and BL-DCMS both use. */
export const VISIT_STATUS_META: Record<string, StatusMeta> = {
  IN_SHED: { label: 'IN_SHED', tone: 'info', glyph: '▣' },
  CLOSED: { label: 'CLOSED', tone: 'neutral', glyph: '✓' },
}

export function statusMeta(map: Record<string, StatusMeta>, key: string | null | undefined): StatusMeta {
  if (!key) return FALLBACK
  return map[key] ?? { label: key.replace(/_/g, ' '), tone: 'neutral', glyph: '·' }
}
