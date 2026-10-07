import { BOOKING_SOURCE_LABELS } from './format'

/** The booking-source level of the Booking Pool hierarchy: its canonical order and its labels.
 *
 * STORED VALUES ARE NEVER REWRITTEN. Everything here is display ordering and display labelling
 * over the `booking_source` the API already returns on every booking (BookingPoolItem.
 * booking_source, from backend/app/schemas/booking_pool.py). No source is renamed, merged or
 * invented, and nothing in this file touches persistence.
 */

/** Operational order, not alphabetical: the order a shed actually works through a visit.
 *
 * Log Book first (what the crew reported on arrival), then the Minor-schedule stages in the
 * sequence they run (Test Before -> Schedule Inspection -> Test After), then the non-stage
 * performa sources, with Manual last because it is the one an Admin raises by hand.
 *
 * SPECIAL_CHECKING is listed even though backend BOOKING_SOURCES deliberately excludes it: the
 * database CHECK constraint still permits it (migration 005) and legacy rows may carry it. A
 * value this page can be shown must have a defined position, or those bookings would sort into
 * the unknown bucket purely because no current code path creates new ones.
 */
export const BOOKING_SOURCE_ORDER: readonly string[] = [
  'LOG_BOOK',
  'TEST_BEFORE',
  'SCHEDULE_INSPECTION',
  'TEST_AFTER',
  'SPECIAL_CHECKING',
  'TRIP_INSPECTION',
  'GENERAL_CHECKING',
  'MANUAL',
]

/** Shown for a booking whose source is missing or blank - which the schema says cannot happen,
 *  so if it ever appears it is a data problem the operator should be able to SEE, not one this
 *  page hides by dropping the row. */
export const SOURCE_NOT_RECORDED_LABEL = 'Source not recorded'

/** The grouping key for a booking's source. Blank/missing collapses to one bucket. */
export function bookingSourceKey(source: string | null | undefined): string {
  return typeof source === 'string' && source.trim().length > 0 ? source : ''
}

/**
 * Position in the operational order. An unrecognised value - a source added to the backend
 * before this list is updated, or a legacy value - ranks AFTER every known one rather than
 * being dropped or silently folded into another group.
 */
export function bookingSourceRank(source: string | null | undefined): number {
  const index = BOOKING_SOURCE_ORDER.indexOf(bookingSourceKey(source))
  return index === -1 ? BOOKING_SOURCE_ORDER.length : index
}

/** Whether this page has a defined position for the value, i.e. it is not in the trailing bucket. */
export function isKnownBookingSource(source: string | null | undefined): boolean {
  return BOOKING_SOURCE_ORDER.includes(bookingSourceKey(source))
}

/**
 * The heading for a source group: the friendly label where one exists, otherwise the RAW stored
 * value. An unknown source is shown as it is stored, never as "Unknown" - an operator who sees
 * an unfamiliar heading can at least report the exact value.
 */
export function bookingSourceGroupLabel(source: string | null | undefined): string {
  const key = bookingSourceKey(source)
  if (key === '') return SOURCE_NOT_RECORDED_LABEL
  return BOOKING_SOURCE_LABELS[key] ?? key
}

/**
 * Canonical order, with a TOTAL tie-break so the result never depends on Map insertion order.
 * Two unknown sources sort by their displayed label, then by their raw value.
 */
export function compareBookingSources(a: string | null | undefined, b: string | null | undefined): number {
  const rank = bookingSourceRank(a) - bookingSourceRank(b)
  if (rank !== 0) return rank

  const keyA = bookingSourceKey(a)
  const keyB = bookingSourceKey(b)
  if (keyA === keyB) return 0

  const byLabel = bookingSourceGroupLabel(keyA).localeCompare(bookingSourceGroupLabel(keyB))
  if (byLabel !== 0) return byLabel
  return keyA < keyB ? -1 : 1
}
