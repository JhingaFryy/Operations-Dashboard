import { describe, expect, it } from 'vitest'
import { BOOKING_SOURCE_LABELS, bookingSourceLabel } from './format'

/** The friendly labels for bookings.booking_source.
 *
 * The canonical list is the database CHECK constraint on `bookings`, which permits exactly
 * eight values. Three of them used to be missing from the map, so those bookings showed the
 * user raw enum text.
 */

const CANONICAL: Record<string, string> = {
  LOG_BOOK: 'Log Book',
  TEST_BEFORE: 'Test Before',
  SCHEDULE_INSPECTION: 'Schedule Inspection',
  TEST_AFTER: 'Test After',
  SPECIAL_CHECKING: 'Special Checking',
  MANUAL: 'Manual',
  TRIP_INSPECTION: 'Trip Inspection',
  GENERAL_CHECKING: 'General Checking',
}

describe('bookingSourceLabel', () => {
  it.each(Object.entries(CANONICAL))('labels %s as "%s"', (value, label) => {
    expect(bookingSourceLabel(value)).toBe(label)
  })

  it('covers every canonical value and invents none', () => {
    // Guards both directions: a value added to the CHECK constraint without a label here, and
    // a made-up label for something the database cannot store.
    expect(Object.keys(BOOKING_SOURCE_LABELS).sort()).toEqual(Object.keys(CANONICAL).sort())
  })

  it('does not define TB or SYSTEM, which are not real sources', () => {
    expect(BOOKING_SOURCE_LABELS.TB).toBeUndefined()
    expect(BOOKING_SOURCE_LABELS.SYSTEM).toBeUndefined()
  })

  it('shows an unknown value verbatim rather than hiding it', () => {
    // Better a visibly odd label than a silently blank cell - it surfaces bad data.
    expect(bookingSourceLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW')
  })

  it('renders an absent source as a dash', () => {
    expect(bookingSourceLabel(null)).toBe('—')
    expect(bookingSourceLabel(undefined)).toBe('—')
    expect(bookingSourceLabel('')).toBe('—')
  })
})
