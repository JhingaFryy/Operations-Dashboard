import { describe, expect, it } from 'vitest'
import {
  compareShedVisitsByArrivalDesc,
  orderShedVisitsByArrivalDesc,
  type ArrivalOrdered,
} from './shedVisitOrder'

function visit(id: number, loco: string, arrival: string): ArrivalOrdered {
  return { id, loco_number: loco, arrival_at: arrival }
}

/** The brief's own example, deliberately supplied out of order. */
const EXAMPLE = [
  visit(3, '30000', '2026-09-30T18:00:00+05:30'),
  visit(1, '11111', '2026-10-01T14:20:00+05:30'),
  visit(4, '44444', '2026-09-29T09:00:00+05:30'),
  visit(2, '22222', '2026-10-01T09:10:00+05:30'),
]

describe('active shed visits are ordered by arrival, newest first', () => {
  it('puts the newest arrival first and the oldest last', () => {
    const ordered = orderShedVisitsByArrivalDesc(EXAMPLE)
    expect(ordered[0].arrival_at).toBe('2026-10-01T14:20:00+05:30')
    expect(ordered[ordered.length - 1].arrival_at).toBe('2026-09-29T09:00:00+05:30')
  })

  it('orders three visits across different dates correctly', () => {
    const ordered = orderShedVisitsByArrivalDesc([
      visit(1, 'A', '2026-09-29T09:00:00+05:30'),
      visit(2, 'B', '2026-10-01T14:20:00+05:30'),
      visit(3, 'C', '2026-09-30T18:00:00+05:30'),
    ])
    expect(ordered.map((v) => v.loco_number)).toEqual(['B', 'C', 'A'])
  })

  it('orders same-day different times correctly', () => {
    const ordered = orderShedVisitsByArrivalDesc([
      visit(1, 'EARLY', '2026-10-01T09:10:00+05:30'),
      visit(2, 'LATE', '2026-10-01T14:20:00+05:30'),
      visit(3, 'MID', '2026-10-01T11:45:00+05:30'),
    ])
    expect(ordered.map((v) => v.loco_number)).toEqual(['LATE', 'MID', 'EARLY'])
  })

  it('reproduces the whole example list in the stated order', () => {
    expect(orderShedVisitsByArrivalDesc(EXAMPLE).map((v) => v.arrival_at)).toEqual([
      '2026-10-01T14:20:00+05:30',
      '2026-10-01T09:10:00+05:30',
      '2026-09-30T18:00:00+05:30',
      '2026-09-29T09:00:00+05:30',
    ])
  })

  it('compares the stored instant, not the text', () => {
    // The same moment written with two different offsets. A lexicographic compare would order these
    // by their digits and get it wrong; both are 08:50 UTC, so only the id tie-break separates them.
    const ordered = orderShedVisitsByArrivalDesc([
      visit(1, 'IST', '2026-10-01T14:20:00+05:30'),
      visit(2, 'UTC', '2026-10-01T08:50:00+00:00'),
    ])
    expect(ordered.map((v) => v.id)).toEqual([2, 1])
  })

  it('never sorts in place - the caller may be holding React state', () => {
    const input = EXAMPLE.slice()
    const snapshot = input.map((v) => v.id)
    orderShedVisitsByArrivalDesc(input)
    expect(input.map((v) => v.id)).toEqual(snapshot)
  })
})

describe('ties are broken deterministically', () => {
  it('equal arrivals fall back to visit id descending', () => {
    const same = '2026-10-01T09:10:00+05:30'
    const ordered = orderShedVisitsByArrivalDesc([
      visit(7, 'AAAAA', same),
      visit(9, 'ZZZZZ', same),
      visit(8, 'MMMMM', same),
    ])
    // The later-created visit is the newer one, which is the same intent as newest-first.
    expect(ordered.map((v) => v.id)).toEqual([9, 8, 7])
  })

  it('does not depend on the order the items arrive in', () => {
    const forward = orderShedVisitsByArrivalDesc(EXAMPLE).map((v) => v.id)
    const reversed = orderShedVisitsByArrivalDesc(EXAMPLE.slice().reverse()).map((v) => v.id)
    expect(reversed).toEqual(forward)
  })

  it('is a total order, so no pair is ever reported equal', () => {
    // Required for the result to be independent of the engine's sort stability.
    for (const a of EXAMPLE) {
      for (const b of EXAMPLE) {
        if (a.id === b.id) continue
        expect(compareShedVisitsByArrivalDesc(a, b)).not.toBe(0)
      }
    }
  })

  it('falls through to loco number only when arrival AND id match', () => {
    const same = '2026-10-01T09:10:00+05:30'
    // Unreachable with real rows (id is unique); asserted so the comparator is provably total.
    expect(compareShedVisitsByArrivalDesc(visit(5, '9126', same), visit(5, '39126', same))).toBeLessThan(0)
  })
})

describe('a missing or unparseable arrival', () => {
  it('sorts last rather than being treated as now', () => {
    // arrival_at is NOT NULL in the schema, so this is unreachable with real data - but inventing a
    // timestamp would put a broken row at the TOP, which is the worst possible place for it.
    const ordered = orderShedVisitsByArrivalDesc([
      { id: 1, loco_number: 'BROKEN', arrival_at: '' },
      visit(2, 'OLD', '2026-09-01T09:00:00+05:30'),
      visit(3, 'NEW', '2026-10-01T14:20:00+05:30'),
    ])
    expect(ordered.map((v) => v.loco_number)).toEqual(['NEW', 'OLD', 'BROKEN'])
  })

  it('orders two broken rows against each other deterministically', () => {
    const ordered = orderShedVisitsByArrivalDesc([
      { id: 1, loco_number: 'A', arrival_at: 'not-a-date' },
      { id: 2, loco_number: 'B', arrival_at: 'also-not-a-date' },
    ])
    expect(ordered.map((v) => v.id)).toEqual([2, 1])
  })
})
