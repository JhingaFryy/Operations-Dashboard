import { describe, expect, it } from 'vitest'
import { buildShedOutReadiness } from './shedOutReadiness'
import type { ShedOutEligibility } from '../types'

function eligibility(overrides: Partial<ShedOutEligibility> = {}): ShedOutEligibility {
  return {
    shed_visit_id: 700,
    eligible: true,
    stage_blockers: [],
    booking_blockers: [],
    ...overrides,
  }
}

describe('buildShedOutReadiness', () => {
  it('reports every stage as satisfied and no reasons when eligible', () => {
    const r = buildShedOutReadiness(eligibility())
    expect(r.eligible).toBe(true)
    expect(r.reasons).toEqual([])
    expect(r.stageLines.map((l) => l.blocked)).toEqual([false, false, false])
    expect(r.stageLines.every((l) => l.statusText === 'Completed')).toBe(true)
  })

  it('never invents eligibility — it echoes the backend flag', () => {
    const r = buildShedOutReadiness(eligibility({ eligible: false }))
    expect(r.eligible).toBe(false)
  })

  it('names an incomplete stage in plain language and keeps its raw status', () => {
    const r = buildShedOutReadiness(
      eligibility({ eligible: false, stage_blockers: [{ stage_type: 'TEST_AFTER', status: 'IN_PROGRESS' }] }),
    )
    expect(r.reasons).toContain('Test After is not complete yet.')
    const line = r.stageLines.find((l) => l.stageType === 'TEST_AFTER')
    expect(line).toMatchObject({ blocked: true, statusText: 'IN_PROGRESS', rawLabel: 'TEST AFTER' })
  })

  it('counts blocking bookings by their own status - the backend contract', () => {
    // Exactly backend BookingBlockerOut: {booking_id, booking_source, status}. The earlier
    // `pending_sections` / `reason` shape is gone - the backend never sent it, and rendering it
    // crashed on real data.
    const r = buildShedOutReadiness(
      eligibility({
        eligible: false,
        booking_blockers: [
          { booking_id: 905, booking_source: 'TEST_BEFORE', status: 'OPEN' },
          { booking_id: 911, booking_source: 'LOG_BOOK', status: 'OPEN' },
          { booking_id: 912, booking_source: 'LOG_BOOK', status: 'IN_PROGRESS' },
        ],
      }),
    )
    expect(r.reasons).toContain('2 bookings still Open.')
    expect(r.reasons).toContain('1 booking still In Progress.')
    expect(r.bookingLines[0]).toMatchObject({ bookingId: 905, pendingText: 'Open', noSectionAssignment: false })
  })

  it('calls out reopened bookings distinctly', () => {
    const r = buildShedOutReadiness(
      eligibility({
        eligible: false,
        booking_blockers: [{ booking_id: 1, booking_source: 'LOG_BOOK', status: 'REOPENED' }],
      }),
    )
    expect(r.reasons).toContain('1 booking Reopened and awaiting work.')
  })

  it('reports a booking with no section assignment separately from a pending one', () => {
    const r = buildShedOutReadiness(
      eligibility({
        eligible: false,
        booking_blockers: [{ booking_id: 42, booking_source: 'LOG_BOOK', status: 'NO_ASSIGNMENTS' }],
      }),
    )
    expect(r.reasons).toContain('1 booking has no responsible section assigned yet.')
    expect(r.reasons.some((x) => x.includes('NO ASSIGNMENTS'))).toBe(false)
    expect(r.bookingLines[0]).toMatchObject({ bookingId: 42, noSectionAssignment: true, pendingText: '' })
  })

  it('names outstanding checksheets as a reason, for Minor and Major alike', () => {
    const r = buildShedOutReadiness(
      eligibility({
        eligible: false,
        checksheet_blockers: [
          { kind: 'REQUIREMENT_PENDING', requirement_id: 1 },
          { kind: 'REQUIREMENT_PENDING', requirement_id: 2 },
        ],
      }),
    )
    expect(r.reasons).toContain('2 required checksheets are still outstanding.')
  })

  it('names a missing work package rather than implying nothing is required', () => {
    const r = buildShedOutReadiness(
      eligibility({ eligible: false, checksheet_blockers: [{ kind: 'WORK_PACKAGE_NOT_GENERATED' }] }),
    )
    expect(r.reasons.some((x) => x.includes('No checksheet work package'))).toBe(true)
  })
})
