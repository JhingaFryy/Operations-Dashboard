import { describe, expect, it } from 'vitest'
import {
  compareLocoNumbers,
  countStatuses,
  groupAssignmentsByEquipment,
  groupAssignmentsByLocomotive,
  groupBookingsByEquipment,
  groupAssignmentsBySource,
  groupBookingsByLocomotive,
  groupBookingsBySource,
} from './grouping'
import {
  BOOKING_SOURCE_ORDER,
  bookingSourceGroupLabel,
  bookingSourceRank,
  compareBookingSources,
  isKnownBookingSource,
} from './bookingSource'
import type { AssignmentStatus, BookingPoolItem, SectionAssignment } from '../types'

function assignment(
  id: number,
  equipment: string | null,
  status: AssignmentStatus,
  nodeId: number | null = null,
  locoNumber = '39126',
  source = 'LOG_BOOK',
): SectionAssignment {
  return {
    id,
    booking_id: id,
    section_id: 1,
    section_code: 'M1-HR',
    status,
    assigned_at: '2026-09-01T00:00:00Z',
    started_at: null,
    started_by_name: null,
    attended_at: null,
    attended_by_name: null,
    attendance_remarks: null,
    booking: {
      id,
      status,
      description: `Booking ${id}`,
      booking_source: source,
      equipment_node_id: nodeId,
      equipment_node_name: equipment,
      defect_type: null,
      shed_visit: {
        id: 1,
        loco_number: locoNumber,
        schedule_family: 'MINOR',
        schedule_variant: 'IA',
        arrival_condition: 'WORKING',
      },
    },
  }
}

function booking(id: number, nodeId: number | null, name: string | null, createdAt: string): BookingPoolItem {
  return {
    id,
    status: 'OPEN',
    description: `Booking ${id}`,
    booking_source: 'LOG_BOOK',
    workflow_stage_type: null,
    equipment_node_id: nodeId,
    equipment_node_name: name,
    equipment_path: [],
    routed_sections: [],
    defect_type: null,
    shed_visit: { id: 1, loco_number: '39126', schedule_family: 'MINOR', schedule_variant: 'IA' },
    created_at: createdAt,
    started_by_name: null,
    started_by_section_code: null,
    started_at: null,
    attended_by_name: null,
    attended_by_section_code: null,
    attended_at: null,
    attendance_remarks: null,
  }
}

describe('groupAssignmentsByEquipment', () => {
  it('groups by equipment node and sorts groups alphabetically by label', () => {
    const groups = groupAssignmentsByEquipment([
      assignment(1, 'Pantograph', 'OPEN', 10),
      assignment(2, 'Contactor', 'OPEN', 20),
      assignment(3, 'Pantograph', 'ATTENDED', 10),
    ])
    expect(groups.map((g) => g.label)).toEqual(['Contactor', 'Pantograph'])
    expect(groups[1].items).toHaveLength(2)
  })

  it('keeps two DIFFERENT nodes that share a display name separate', () => {
    // The production hierarchy repeats names across branches heavily ("Others" appears hundreds
    // of times). Merging them showed a Supervisor one card that was really two nodes' work.
    const groups = groupAssignmentsByEquipment([
      assignment(1, 'Others', 'OPEN', 10),
      assignment(2, 'Others', 'OPEN', 20),
    ])
    expect(groups).toHaveLength(2)
    expect(groups.map((g) => g.key).sort()).toEqual(['10', '20'])
  })

  it('keeps bookings with no equipment identity, in one labelled bucket', () => {
    const groups = groupAssignmentsByEquipment([
      assignment(1, null, 'OPEN'),
      assignment(2, null, 'OPEN'),
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('No equipment specified')
    expect(groups[0].items).toHaveLength(2)
  })
})

describe('groupAssignmentsByLocomotive', () => {
  it('nests equipment under each locomotive and tallies its statuses', () => {
    const groups = groupAssignmentsByLocomotive([
      assignment(1, 'Pantograph', 'OPEN', 10, '39126'),
      assignment(2, 'Contactor', 'ATTENDED', 20, '39126'),
      assignment(3, 'Pantograph', 'OPEN', 10, '33586'),
    ])

    expect(groups.map((g) => g.locoNumber)).toEqual(['33586', '39126'])
    const loco39126 = groups[1]
    expect(loco39126.total).toBe(2)
    expect(loco39126.counts.OPEN).toBe(1)
    expect(loco39126.counts.ATTENDED).toBe(1)
    // Both are LOG_BOOK (the fixture default), so there is one source group holding both.
    expect(loco39126.sources.map((s) => s.source)).toEqual(['LOG_BOOK'])
    expect(loco39126.sources[0].equipment.map((e) => e.label)).toEqual(['Contactor', 'Pantograph'])
  })

  it('keys locomotives by number so re-filtering cannot transfer expansion state', () => {
    const groups = groupAssignmentsByLocomotive([
      assignment(1, 'Pantograph', 'OPEN', 10, '39126'),
      assignment(2, 'Contactor', 'OPEN', 20, '33586'),
    ])
    expect(groups.map((g) => g.key)).toEqual(['33586', '39126'])
  })

  it('the same equipment node under two locomotives stays two groups', () => {
    const groups = groupAssignmentsByLocomotive([
      assignment(1, 'Pantograph', 'OPEN', 10, '39126'),
      assignment(2, 'Pantograph', 'OPEN', 10, '33586'),
    ])
    expect(groups).toHaveLength(2)
    for (const g of groups) {
      expect(g.sources).toHaveLength(1)
      expect(g.sources[0].equipment).toHaveLength(1)
    }
  })
})

/** What the Section Dashboard page does between loading and grouping: filter by status, then
 *  group. Replicated here rather than asserted through the page, because the ordering rule lives
 *  in the grouping function and this is the seam the page actually uses
 *  (`groupAssignmentsByLocomotive(visibleAssignments)`). If the page ever grouped the UNFILTERED
 *  list instead, these tests would still pass while the screen was wrong - so one test below
 *  pins the page's own wiring too. */
function visible(assignments: SectionAssignment[], statusFilter: AssignmentStatus | '') {
  return statusFilter ? assignments.filter((a) => a.status === statusFilter) : assignments
}

describe('groupAssignmentsByLocomotive ordering', () => {
  it('puts a 1-booking locomotive before a 2-booking locomotive', () => {
    const groups = groupAssignmentsByLocomotive([
      assignment(1, 'Pantograph', 'OPEN', 10, '22382'),
      assignment(2, 'Contactor', 'OPEN', 20, '22382'),
      assignment(3, 'Pantograph', 'OPEN', 10, '33586'),
    ])
    expect(groups.map((g) => [g.locoNumber, g.total])).toEqual([
      ['33586', 1],
      ['22382', 2],
    ])
  })

  it('puts a 2-booking locomotive before a 4-booking locomotive', () => {
    const groups = groupAssignmentsByLocomotive([
      ...[1, 2, 3, 4].map((id) => assignment(id, 'Pantograph', 'OPEN', 10, '11111')),
      ...[5, 6].map((id) => assignment(id, 'Contactor', 'OPEN', 20, '99999')),
    ])
    expect(groups.map((g) => [g.locoNumber, g.total])).toEqual([
      ['99999', 2],
      ['11111', 4],
    ])
  })

  it('orders the whole ascending ladder, not just adjacent pairs', () => {
    const groups = groupAssignmentsByLocomotive([
      ...Array.from({ length: 5 }, (_, i) => assignment(100 + i, 'E', 'OPEN', 10, '50005')),
      ...Array.from({ length: 3 }, (_, i) => assignment(200 + i, 'E', 'OPEN', 10, '40004')),
      ...Array.from({ length: 2 }, (_, i) => assignment(300 + i, 'E', 'OPEN', 10, '30003')),
      assignment(400, 'E', 'OPEN', 10, '20002'),
      assignment(500, 'E', 'OPEN', 10, '10001'),
    ])
    expect(groups.map((g) => g.total)).toEqual([1, 1, 2, 3, 5])
    // The two singles tie, so they fall back to locomotive number ascending.
    expect(groups.map((g) => g.locoNumber)).toEqual(['10001', '20002', '30003', '40004', '50005'])
  })

  it('breaks ties on locomotive number ascending, compared numerically', () => {
    const groups = groupAssignmentsByLocomotive([
      assignment(1, 'E', 'OPEN', 10, '39126'),
      assignment(2, 'E', 'OPEN', 10, '9126'),
      assignment(3, 'E', 'OPEN', 10, '22382'),
    ])
    // Numeric, not lexical: a string sort would put "9126" last, after "39126".
    expect(groups.map((g) => g.locoNumber)).toEqual(['9126', '22382', '39126'])
  })

  it('does not depend on the order the assignments arrive in', () => {
    const rows = [
      assignment(1, 'E', 'OPEN', 10, '22382'),
      assignment(2, 'E', 'OPEN', 10, '22382'),
      assignment(3, 'E', 'OPEN', 10, '33586'),
      assignment(4, 'E', 'OPEN', 10, '11111'),
    ]
    const forward = groupAssignmentsByLocomotive(rows).map((g) => g.locoNumber)
    const reversed = groupAssignmentsByLocomotive(rows.slice().reverse()).map((g) => g.locoNumber)
    expect(reversed).toEqual(forward)
    expect(forward).toEqual(['11111', '33586', '22382'])
  })

  it('sorts by the VISIBLE count once a status filter is applied', () => {
    // The brief's own example. Unfiltered, 22382 has the MORE bookings of the two; filtered to
    // OPEN it has the FEWER, so it must move above 33586. Sorting by the hidden total would
    // leave it below while its card displayed 1 against the other's 2.
    const rows = [
      assignment(1, 'E', 'OPEN', 10, '22382'),
      assignment(2, 'E', 'ATTENDED', 10, '22382'),
      assignment(3, 'E', 'ATTENDED', 10, '22382'),
      assignment(4, 'E', 'ATTENDED', 10, '22382'),
      assignment(5, 'E', 'ATTENDED', 10, '22382'),
      assignment(6, 'E', 'OPEN', 20, '33586'),
      assignment(7, 'E', 'OPEN', 20, '33586'),
    ]

    const unfiltered = groupAssignmentsByLocomotive(visible(rows, ''))
    expect(unfiltered.map((g) => [g.locoNumber, g.total])).toEqual([
      ['33586', 2],
      ['22382', 5],
    ])

    const open = groupAssignmentsByLocomotive(visible(rows, 'OPEN'))
    expect(open.map((g) => [g.locoNumber, g.total])).toEqual([
      ['22382', 1],
      ['33586', 2],
    ])
  })

  it('re-sorts using the restored count when the filter is removed', () => {
    const rows = [
      assignment(1, 'E', 'OPEN', 10, '22382'),
      assignment(2, 'E', 'ATTENDED', 10, '22382'),
      assignment(3, 'E', 'ATTENDED', 10, '22382'),
      assignment(4, 'E', 'OPEN', 20, '33586'),
      assignment(5, 'E', 'OPEN', 20, '33586'),
    ]
    const filtered = groupAssignmentsByLocomotive(visible(rows, 'OPEN')).map((g) => g.locoNumber)
    const cleared = groupAssignmentsByLocomotive(visible(rows, '')).map((g) => g.locoNumber)

    expect(filtered).toEqual(['22382', '33586'])
    // Back to 3 vs 2, so the order inverts again - the sort holds no state between renders.
    expect(cleared).toEqual(['33586', '22382'])
  })

  it('drops a locomotive with no visible bookings, exactly as before', () => {
    // NOT a new visibility rule. A locomotive group only exists if an assignment produced it, so
    // filtering every one of its bookings out removes the group - which is why the page shows its
    // "No bookings in this state" empty state rather than a row reading zero. The sort must not
    // invent a zero-count row at the top.
    const rows = [
      assignment(1, 'E', 'ATTENDED', 10, '22382'),
      assignment(2, 'E', 'OPEN', 20, '33586'),
    ]
    const open = groupAssignmentsByLocomotive(visible(rows, 'OPEN'))
    expect(open.map((g) => g.locoNumber)).toEqual(['33586'])
    expect(groupAssignmentsByLocomotive(visible(rows, 'REOPENED'))).toEqual([])
  })

  it('keeps expansion keys on locomotive identity across a reorder', () => {
    // The page holds expansion in a Set of these keys. Reordering may only change POSITIONS, so
    // an expanded locomotive stays expanded wherever it lands - the failure this guards against
    // is a key derived from array position, which would hand 44373's open state to whichever row
    // slid into its slot.
    const rows = [
      assignment(1, 'E', 'OPEN', 10, '44373'),
      assignment(2, 'E', 'ATTENDED', 10, '44373'),
      assignment(3, 'E', 'ATTENDED', 10, '44373'),
      assignment(4, 'E', 'OPEN', 20, '11111'),
    ]
    const openLocos = new Set(['44373'])

    const unfiltered = groupAssignmentsByLocomotive(visible(rows, ''))
    expect(unfiltered.map((g) => g.locoNumber)).toEqual(['11111', '44373'])
    expect(unfiltered.map((g) => openLocos.has(g.key))).toEqual([false, true])

    // Filtering to OPEN moves 44373 from last to a tie at one booking - position changes, key
    // does not, so the same Set still reports it expanded.
    const open = groupAssignmentsByLocomotive(visible(rows, 'OPEN'))
    expect(open.map((g) => g.locoNumber)).toEqual(['11111', '44373'])
    expect(open.find((g) => g.locoNumber === '44373')!.key).toBe('44373')
    expect(openLocos.has(open.find((g) => g.locoNumber === '44373')!.key)).toBe(true)
  })

  it('leaves equipment grouping and its order untouched', () => {
    const groups = groupAssignmentsByLocomotive([
      assignment(1, 'Pantograph', 'OPEN', 10, '22382'),
      assignment(2, 'Contactor', 'OPEN', 20, '22382'),
      assignment(3, 'Pantograph', 'OPEN', 10, '22382'),
      assignment(4, 'Pantograph', 'OPEN', 10, '33586'),
    ])
    const loco22382 = groups.find((g) => g.locoNumber === '22382')!
    // Still grouped by node id and still ordered by label - the booking-count rule applies to the
    // locomotive level only, and the new source level above did not disturb it.
    expect(loco22382.sources.map((s) => s.source)).toEqual(['LOG_BOOK'])
    expect(loco22382.sources[0].equipment.map((e) => [e.key, e.label, e.items.length])).toEqual([
      ['20', 'Contactor', 1],
      ['10', 'Pantograph', 2],
    ])
  })
})

describe('compareLocoNumbers', () => {
  it('orders numerically and is a total order', () => {
    expect(compareLocoNumbers('9126', '39126')).toBeLessThan(0)
    expect(compareLocoNumbers('39126', '9126')).toBeGreaterThan(0)
    expect(compareLocoNumbers('22382', '22382')).toBe(0)
    // Equivalent under the collator but not identical: still ordered, never 0, so two such groups
    // can never fall back on arrival order.
    expect(compareLocoNumbers('30542a', '30542A')).not.toBe(0)
  })
})

/** A pool booking on a named locomotive. The shared `booking()` helper above hard-codes loco
 *  39126, and every test here turns on which locomotive a booking belongs to. */
function poolBooking(
  id: number,
  locoNumber: string,
  nodeId: number,
  equipment: string,
  status: AssignmentStatus = 'OPEN',
  source = 'LOG_BOOK',
  description = `Booking ${id}`,
): BookingPoolItem {
  return {
    ...booking(id, nodeId, equipment, '2026-09-01T00:00:00Z'),
    status,
    booking_source: source,
    description,
    shed_visit: { id: 1, loco_number: locoNumber, schedule_family: 'MINOR', schedule_variant: 'IA' },
  }
}

/** What BookingPoolPage does between loading and grouping: narrow by status, source, equipment and
 *  search text, then group. All four are client-side, so the count the cards show is a tally of
 *  exactly this list - which is what makes sorting on it correct by construction. */
function poolVisible(
  bookings: BookingPoolItem[],
  filters: { status?: string; source?: string; equipment?: string; search?: string } = {},
) {
  const term = (filters.search ?? '').trim().toLowerCase()
  return bookings.filter((b) => {
    if (filters.status && b.status !== filters.status) return false
    if (filters.source && b.booking_source !== filters.source) return false
    if (filters.equipment && String(b.equipment_node_id) !== filters.equipment) return false
    if (term) {
      const haystack =
        `${b.description} ${b.shed_visit.loco_number} ${b.equipment_node_name ?? ''}`.toLowerCase()
      if (!haystack.includes(term)) return false
    }
    return true
  })
}

describe('groupBookingsByLocomotive ordering', () => {
  it('puts a 1-booking locomotive before a 2-booking locomotive', () => {
    const groups = groupBookingsByLocomotive([
      poolBooking(1, '11111', 10, 'Pantograph'),
      poolBooking(2, '11111', 20, 'Contactor'),
      poolBooking(3, '99999', 10, 'Pantograph'),
    ])
    // 11111 has TWO and 99999 has ONE, so the HIGHER number now leads. This is the assertion that
    // previously ran the other way round, when the pool ordered by locomotive number alone.
    expect(groups.map((g) => [g.locoNumber, g.total])).toEqual([
      ['99999', 1],
      ['11111', 2],
    ])
  })

  it('puts a 2-booking locomotive before a 4-booking locomotive', () => {
    const groups = groupBookingsByLocomotive([
      ...[1, 2, 3, 4].map((id) => poolBooking(id, '11111', 10, 'Pantograph')),
      ...[5, 6].map((id) => poolBooking(id, '99999', 20, 'Contactor')),
    ])
    expect(groups.map((g) => [g.locoNumber, g.total])).toEqual([
      ['99999', 2],
      ['11111', 4],
    ])
  })

  it('orders the whole ascending ladder, with ties on numeric locomotive number', () => {
    // The brief's own example.
    const groups = groupBookingsByLocomotive([
      poolBooking(1, '33586', 10, 'Pantograph'),
      poolBooking(2, '39015', 10, 'Pantograph'),
      ...[3, 4].map((id) => poolBooking(id, '32032', 20, 'Contactor')),
      ...[5, 6].map((id) => poolBooking(id, '37947', 20, 'Contactor')),
      ...[7, 8, 9, 10].map((id) => poolBooking(id, '44373', 30, 'IGBT')),
    ])
    expect(groups.map((g) => [g.locoNumber, g.total])).toEqual([
      ['33586', 1],
      ['39015', 1],
      ['32032', 2],
      ['37947', 2],
      ['44373', 4],
    ])
  })

  it('breaks ties numerically, not lexically', () => {
    const groups = groupBookingsByLocomotive([
      poolBooking(1, '39126', 10, 'Pantograph'),
      poolBooking(2, '9126', 10, 'Pantograph'),
      poolBooking(3, '22382', 10, 'Pantograph'),
    ])
    // A string sort would put "9126" last, after "39126".
    expect(groups.map((g) => g.locoNumber)).toEqual(['9126', '22382', '39126'])
  })

  it('does not depend on the order the bookings arrive in', () => {
    const rows = [
      poolBooking(1, '22382', 10, 'Pantograph'),
      poolBooking(2, '22382', 20, 'Contactor'),
      poolBooking(3, '33586', 10, 'Pantograph'),
      poolBooking(4, '11111', 10, 'Pantograph'),
    ]
    const forward = groupBookingsByLocomotive(rows).map((g) => g.locoNumber)
    const reversed = groupBookingsByLocomotive(rows.slice().reverse()).map((g) => g.locoNumber)
    expect(reversed).toEqual(forward)
    expect(forward).toEqual(['11111', '33586', '22382'])
  })

  it('re-sorts from the visible count when the STATUS filter is applied', () => {
    const rows = [
      poolBooking(1, '22382', 10, 'Pantograph', 'OPEN'),
      poolBooking(2, '22382', 10, 'Pantograph', 'ATTENDED'),
      poolBooking(3, '22382', 20, 'Contactor', 'ATTENDED'),
      poolBooking(4, '33586', 10, 'Pantograph', 'OPEN'),
      poolBooking(5, '33586', 20, 'Contactor', 'OPEN'),
    ]
    expect(groupBookingsByLocomotive(poolVisible(rows)).map((g) => [g.locoNumber, g.total])).toEqual(
      [
        ['33586', 2],
        ['22382', 3],
      ],
    )
    // OPEN: 22382 drops to 1 and must rise above 33586's 2.
    expect(
      groupBookingsByLocomotive(poolVisible(rows, { status: 'OPEN' })).map((g) => [
        g.locoNumber,
        g.total,
      ]),
    ).toEqual([
      ['22382', 1],
      ['33586', 2],
    ])
  })

  it('re-sorts from the visible count when the SOURCE filter is applied', () => {
    const rows = [
      poolBooking(1, '22382', 10, 'Pantograph', 'OPEN', 'LOG_BOOK'),
      poolBooking(2, '22382', 10, 'Pantograph', 'OPEN', 'SHED_IN'),
      poolBooking(3, '22382', 20, 'Contactor', 'OPEN', 'SHED_IN'),
      poolBooking(4, '33586', 10, 'Pantograph', 'OPEN', 'LOG_BOOK'),
      poolBooking(5, '33586', 20, 'Contactor', 'OPEN', 'LOG_BOOK'),
    ]
    expect(groupBookingsByLocomotive(poolVisible(rows)).map((g) => g.locoNumber)).toEqual([
      '33586',
      '22382',
    ])
    // LOG_BOOK only: 22382 falls to 1, 33586 keeps 2, so the order inverts.
    expect(
      groupBookingsByLocomotive(poolVisible(rows, { source: 'LOG_BOOK' })).map((g) => [
        g.locoNumber,
        g.total,
      ]),
    ).toEqual([
      ['22382', 1],
      ['33586', 2],
    ])
  })

  it('re-sorts from the visible count when the EQUIPMENT filter is applied', () => {
    const rows = [
      poolBooking(1, '22382', 10, 'Pantograph'),
      poolBooking(2, '22382', 20, 'Contactor'),
      poolBooking(3, '22382', 20, 'Contactor'),
      poolBooking(4, '33586', 10, 'Pantograph'),
      poolBooking(5, '33586', 10, 'Pantograph'),
    ]
    expect(groupBookingsByLocomotive(poolVisible(rows)).map((g) => g.locoNumber)).toEqual([
      '33586',
      '22382',
    ])
    // Node 10 only: 22382 has 1 there, 33586 has 2.
    expect(
      groupBookingsByLocomotive(poolVisible(rows, { equipment: '10' })).map((g) => [
        g.locoNumber,
        g.total,
      ]),
    ).toEqual([
      ['22382', 1],
      ['33586', 2],
    ])
  })

  it('re-sorts from the visible count when SEARCH text is applied', () => {
    const rows = [
      poolBooking(1, '22382', 10, 'Pantograph', 'OPEN', 'LOG_BOOK', 'Horn not working'),
      poolBooking(2, '22382', 20, 'Contactor', 'OPEN', 'LOG_BOOK', 'Flashover'),
      poolBooking(3, '22382', 20, 'Contactor', 'OPEN', 'LOG_BOOK', 'Flashover'),
      poolBooking(4, '33586', 10, 'Pantograph', 'OPEN', 'LOG_BOOK', 'Horn faulty'),
      poolBooking(5, '33586', 20, 'Contactor', 'OPEN', 'LOG_BOOK', 'Horn dead'),
    ]
    expect(groupBookingsByLocomotive(poolVisible(rows)).map((g) => g.locoNumber)).toEqual([
      '33586',
      '22382',
    ])
    // "horn": 22382 matches once, 33586 twice.
    expect(
      groupBookingsByLocomotive(poolVisible(rows, { search: 'horn' })).map((g) => [
        g.locoNumber,
        g.total,
      ]),
    ).toEqual([
      ['22382', 1],
      ['33586', 2],
    ])
  })

  it('restores the count-based order when the filters are cleared', () => {
    const rows = [
      poolBooking(1, '22382', 10, 'Pantograph', 'OPEN'),
      poolBooking(2, '22382', 10, 'Pantograph', 'ATTENDED'),
      poolBooking(3, '22382', 20, 'Contactor', 'ATTENDED'),
      poolBooking(4, '33586', 10, 'Pantograph', 'OPEN'),
      poolBooking(5, '33586', 20, 'Contactor', 'OPEN'),
    ]
    const filtered = groupBookingsByLocomotive(poolVisible(rows, { status: 'OPEN' })).map(
      (g) => g.locoNumber,
    )
    const cleared = groupBookingsByLocomotive(poolVisible(rows)).map((g) => g.locoNumber)
    expect(filtered).toEqual(['22382', '33586'])
    // Back to 3 vs 2, so the order inverts again - the sort holds no state between renders.
    expect(cleared).toEqual(['33586', '22382'])
  })

  it('keeps expansion keys on locomotive identity across a reorder', () => {
    const rows = [
      poolBooking(1, '44373', 10, 'Pantograph', 'OPEN'),
      poolBooking(2, '44373', 10, 'Pantograph', 'ATTENDED'),
      poolBooking(3, '44373', 20, 'Contactor', 'ATTENDED'),
      poolBooking(4, '11111', 10, 'Pantograph', 'OPEN'),
    ]
    const openLocos = new Set(['44373'])

    const all = groupBookingsByLocomotive(poolVisible(rows))
    expect(all.map((g) => g.locoNumber)).toEqual(['11111', '44373'])
    expect(all.map((g) => openLocos.has(g.key))).toEqual([false, true])

    // Filtering changes positions, never keys, so the same Set still reports it expanded.
    const open = groupBookingsByLocomotive(poolVisible(rows, { status: 'OPEN' }))
    expect(open.find((g) => g.locoNumber === '44373')!.key).toBe('44373')
    expect(openLocos.has(open.find((g) => g.locoNumber === '44373')!.key)).toBe(true)
  })

  it('leaves equipment grouping, its node-id keys and its order untouched', () => {
    const groups = groupBookingsByLocomotive([
      poolBooking(1, '22382', 10, 'Pantograph'),
      poolBooking(2, '22382', 20, 'Contactor'),
      poolBooking(3, '22382', 10, 'Pantograph'),
      poolBooking(4, '33586', 10, 'Pantograph'),
    ])
    const loco22382 = groups.find((g) => g.locoNumber === '22382')!
    // Still grouped by node id, still ordered by label - adding the source level above did not
    // change the equipment level's own rule, and the count rule still applies to the locomotive
    // level only. These four are all LOG_BOOK, so there is exactly one source group.
    expect(loco22382.sources.map((s) => s.source)).toEqual(['LOG_BOOK'])
    expect(loco22382.sources[0].equipment.map((e) => [e.key, e.label, e.items.length])).toEqual([
      ['20', 'Contactor', 1],
      ['10', 'Pantograph', 2],
    ])
  })

  it('keeps bookings inside an equipment group newest-first', () => {
    const groups = groupBookingsByLocomotive([
      { ...poolBooking(1, '22382', 10, 'Pantograph'), created_at: '2026-09-01T00:00:00Z' },
      { ...poolBooking(2, '22382', 10, 'Pantograph'), created_at: '2026-09-03T00:00:00Z' },
      { ...poolBooking(3, '22382', 10, 'Pantograph'), created_at: '2026-09-02T00:00:00Z' },
    ])
    expect(groups[0].sources[0].equipment[0].items.map((b) => b.id)).toEqual([2, 3, 1])
  })
})

describe('groupBookingsByEquipment', () => {
  it('keeps distinct equipment node ids in distinct groups', () => {
    const groups = groupBookingsByEquipment([
      booking(1, 10, 'Aux Converter', '2026-09-01T00:00:00Z'),
      booking(2, 20, 'Pantograph', '2026-09-01T00:00:00Z'),
    ])
    expect(groups).toHaveLength(2)
  })

  it('sorts bookings inside a group newest first', () => {
    const groups = groupBookingsByEquipment([
      booking(1, 10, 'Aux Converter', '2026-09-01T00:00:00Z'),
      booking(2, 10, 'Aux Converter', '2026-09-02T00:00:00Z'),
    ])
    expect(groups[0].items.map((b) => b.id)).toEqual([2, 1])
  })

  it('buckets bookings with no equipment separately', () => {
    const groups = groupBookingsByEquipment([booking(1, null, null, '2026-09-01T00:00:00Z')])
    expect(groups[0].label).toBe('No equipment specified')
  })
})

describe('countStatuses', () => {
  it('tallies each lifecycle state and the total', () => {
    expect(countStatuses(['OPEN', 'OPEN', 'IN_PROGRESS', 'ATTENDED', 'REOPENED'])).toEqual({
      OPEN: 2,
      IN_PROGRESS: 1,
      ATTENDED: 1,
      REOPENED: 1,
      total: 5,
    })
  })

  it('returns all zeroes for an empty list', () => {
    expect(countStatuses([])).toEqual({ OPEN: 0, IN_PROGRESS: 0, ATTENDED: 0, REOPENED: 0, total: 0 })
  })
})

// ===============================================================================================
// THE SOURCE LEVEL: locomotive -> booking source -> equipment -> bookings.
//
// Rows always carried a Source column, but a locomotive's Log Book, Test Before and Test After
// findings were interleaved under shared equipment groups, so reading "what did Test Before raise
// on this loco" meant scanning rows. These pin the new level and, just as importantly, pin that
// the levels around it did not change.
// ===============================================================================================

describe('booking source ordering', () => {
  it('ranks the canonical sources in operational order', () => {
    expect(BOOKING_SOURCE_ORDER).toEqual([
      'LOG_BOOK',
      'TEST_BEFORE',
      'SCHEDULE_INSPECTION',
      'TEST_AFTER',
      'SPECIAL_CHECKING',
      'TRIP_INSPECTION',
      'GENERAL_CHECKING',
      'MANUAL',
    ])
  })

  it('sorts a shuffled set of sources into that order', () => {
    const shuffled = [
      'MANUAL',
      'TEST_AFTER',
      'LOG_BOOK',
      'GENERAL_CHECKING',
      'SCHEDULE_INSPECTION',
      'TRIP_INSPECTION',
      'TEST_BEFORE',
      'SPECIAL_CHECKING',
    ]
    expect(shuffled.slice().sort(compareBookingSources)).toEqual([...BOOKING_SOURCE_ORDER])
  })

  it('is NOT alphabetical and NOT by count - the order is fixed', () => {
    // Alphabetical would put GENERAL_CHECKING first and TRIP_INSPECTION near the end.
    expect(compareBookingSources('LOG_BOOK', 'GENERAL_CHECKING')).toBeLessThan(0)
    expect(compareBookingSources('TEST_BEFORE', 'SCHEDULE_INSPECTION')).toBeLessThan(0)
    expect(compareBookingSources('MANUAL', 'TRIP_INSPECTION')).toBeGreaterThan(0)
  })

  it('ranks an unknown source after every known one', () => {
    expect(bookingSourceRank('FUTURE_SOURCE')).toBeGreaterThan(bookingSourceRank('MANUAL'))
    expect(compareBookingSources('FUTURE_SOURCE', 'LOG_BOOK')).toBeGreaterThan(0)
    expect(compareBookingSources('FUTURE_SOURCE', 'MANUAL')).toBeGreaterThan(0)
    expect(isKnownBookingSource('FUTURE_SOURCE')).toBe(false)
  })

  it('orders two unknown sources deterministically rather than by arrival', () => {
    expect(['ZZZ_SOURCE', 'AAA_SOURCE'].slice().sort(compareBookingSources)).toEqual([
      'AAA_SOURCE',
      'ZZZ_SOURCE',
    ])
    expect(['AAA_SOURCE', 'ZZZ_SOURCE'].slice().sort(compareBookingSources)).toEqual([
      'AAA_SOURCE',
      'ZZZ_SOURCE',
    ])
  })

  it('labels known sources and shows an unknown one as it is STORED', () => {
    expect(bookingSourceGroupLabel('LOG_BOOK')).toBe('Log Book')
    expect(bookingSourceGroupLabel('TEST_BEFORE')).toBe('Test Before')
    expect(bookingSourceGroupLabel('SCHEDULE_INSPECTION')).toBe('Schedule Inspection')
    expect(bookingSourceGroupLabel('TEST_AFTER')).toBe('Test After')
    expect(bookingSourceGroupLabel('SPECIAL_CHECKING')).toBe('Special Checking')
    expect(bookingSourceGroupLabel('MANUAL')).toBe('Manual')
    expect(bookingSourceGroupLabel('TRIP_INSPECTION')).toBe('Trip Inspection')
    expect(bookingSourceGroupLabel('GENERAL_CHECKING')).toBe('General Checking')
    // Raw, never "Unknown" - an operator can report the exact value they saw.
    expect(bookingSourceGroupLabel('FUTURE_SOURCE')).toBe('FUTURE_SOURCE')
  })

  it('keeps a booking whose source was not recorded, in its own labelled bucket', () => {
    expect(bookingSourceGroupLabel('')).toBe('Source not recorded')
    expect(bookingSourceGroupLabel(null)).toBe('Source not recorded')
    expect(bookingSourceRank(null)).toBe(BOOKING_SOURCE_ORDER.length)
  })
})

describe('groupBookingsBySource', () => {
  it('separates LOG_BOOK from TEST_BEFORE under the same locomotive', () => {
    const sources = groupBookingsBySource([
      poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
      poolBooking(2, '33335', 20, 'Bogie', 'OPEN', 'TEST_BEFORE'),
    ])
    expect(sources.map((s) => [s.source, s.total])).toEqual([
      ['LOG_BOOK', 1],
      ['TEST_BEFORE', 1],
    ])
  })

  it('gives TEST_AFTER its own group alongside the others', () => {
    const sources = groupBookingsBySource([
      poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'TEST_AFTER'),
      poolBooking(2, '33335', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
      poolBooking(3, '33335', 10, 'Air Dryer', 'OPEN', 'TEST_BEFORE'),
    ])
    expect(sources.map((s) => s.source)).toEqual(['LOG_BOOK', 'TEST_BEFORE', 'TEST_AFTER'])
    expect(sources.every((s) => s.total === 1)).toBe(true)
  })

  it('puts the SAME equipment under two sources into two different source groups', () => {
    // The case the old hierarchy merged: one "Air Dryer" group holding both a Log Book finding
    // and a Test After finding, with only the row's Source column to tell them apart.
    const sources = groupBookingsBySource([
      poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
      poolBooking(2, '33335', 10, 'Air Dryer', 'OPEN', 'TEST_AFTER'),
    ])
    expect(sources).toHaveLength(2)
    for (const group of sources) {
      expect(group.equipment).toHaveLength(1)
      expect(group.equipment[0].label).toBe('Air Dryer')
      expect(group.equipment[0].key).toBe('10')
      expect(group.equipment[0].items).toHaveLength(1)
    }
    expect(sources[0].equipment[0].items[0].id).toBe(1)
    expect(sources[1].equipment[0].items[0].id).toBe(2)
  })

  it('places an unknown source last without dropping it', () => {
    const sources = groupBookingsBySource([
      poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'FUTURE_SOURCE'),
      poolBooking(2, '33335', 20, 'Bogie', 'OPEN', 'MANUAL'),
      poolBooking(3, '33335', 30, 'Pantograph', 'OPEN', 'LOG_BOOK'),
    ])
    expect(sources.map((s) => s.source)).toEqual(['LOG_BOOK', 'MANUAL', 'FUTURE_SOURCE'])
    expect(sources[2].label).toBe('FUTURE_SOURCE')
    expect(sources[2].known).toBe(false)
    expect(sources[2].total).toBe(1)
  })

  it('never produces an empty source group - groups exist only where bookings do', () => {
    const sources = groupBookingsBySource([
      poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
    ])
    // Not eight groups with seven of them empty.
    expect(sources).toHaveLength(1)
    expect(sources.every((s) => s.total > 0)).toBe(true)
    expect(sources.every((s) => s.equipment.length > 0)).toBe(true)
  })

  it('tallies each source group over its OWN bookings', () => {
    const sources = groupBookingsBySource([
      poolBooking(1, '33335', 10, 'Bogie', 'OPEN', 'TEST_BEFORE'),
      poolBooking(2, '33335', 20, 'Bogie & its Mech. equip.', 'OPEN', 'TEST_BEFORE'),
      poolBooking(3, '33335', 20, 'Bogie & its Mech. equip.', 'OPEN', 'TEST_BEFORE'),
      poolBooking(4, '33335', 30, 'Air Dryer', 'ATTENDED', 'LOG_BOOK'),
      poolBooking(5, '33335', 40, 'Pantograph', 'IN_PROGRESS', 'LOG_BOOK'),
    ])
    const logBook = sources.find((s) => s.source === 'LOG_BOOK')!
    const testBefore = sources.find((s) => s.source === 'TEST_BEFORE')!

    // The brief's worked example: "Test Before / 3 bookings / 3 Open / 0 In progress / 0 Attended
    // / 0 Reopened".
    expect(testBefore.total).toBe(3)
    expect(testBefore.counts).toEqual({ OPEN: 3, IN_PROGRESS: 0, ATTENDED: 0, REOPENED: 0, total: 3 })
    expect(logBook.total).toBe(2)
    expect(logBook.counts).toEqual({ OPEN: 0, IN_PROGRESS: 1, ATTENDED: 1, REOPENED: 0, total: 2 })
  })

  it('tallies each equipment group over its own bookings, within its source', () => {
    const sources = groupBookingsBySource([
      poolBooking(1, '33335', 10, 'Bogie', 'OPEN', 'TEST_BEFORE'),
      poolBooking(2, '33335', 20, 'Bogie & its Mech. equip.', 'REOPENED', 'TEST_BEFORE'),
      poolBooking(3, '33335', 20, 'Bogie & its Mech. equip.', 'OPEN', 'TEST_BEFORE'),
    ])
    const testBefore = sources[0]
    expect(testBefore.equipment.map((e) => [e.label, e.items.length])).toEqual([
      ['Bogie', 1],
      ['Bogie & its Mech. equip.', 2],
    ])
    expect(countStatuses(testBefore.equipment[1].items.map((i) => i.status))).toEqual({
      OPEN: 1,
      IN_PROGRESS: 0,
      ATTENDED: 0,
      REOPENED: 1,
      total: 2,
    })
  })

  it('does not depend on the order the bookings arrived in', () => {
    const rows = [
      poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'TEST_AFTER'),
      poolBooking(2, '33335', 20, 'Bogie', 'OPEN', 'LOG_BOOK'),
      poolBooking(3, '33335', 30, 'Pantograph', 'OPEN', 'FUTURE_SOURCE'),
      poolBooking(4, '33335', 40, 'Contactor', 'OPEN', 'TEST_BEFORE'),
    ]
    const forward = groupBookingsBySource(rows).map((s) => s.source)
    const reversed = groupBookingsBySource(rows.slice().reverse()).map((s) => s.source)
    expect(forward).toEqual(['LOG_BOOK', 'TEST_BEFORE', 'TEST_AFTER', 'FUTURE_SOURCE'])
    expect(reversed).toEqual(forward)
  })
})

describe('groupBookingsByLocomotive with the source level', () => {
  const mixed = () => [
    poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
    poolBooking(2, '33335', 20, 'Pantograph', 'ATTENDED', 'LOG_BOOK'),
    poolBooking(3, '33335', 30, 'Bogie', 'OPEN', 'TEST_BEFORE'),
    poolBooking(4, '33335', 40, 'Bogie & its Mech. equip.', 'OPEN', 'TEST_BEFORE'),
    poolBooking(5, '33335', 10, 'Air Dryer', 'IN_PROGRESS', 'TEST_AFTER'),
    poolBooking(6, '44373', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
  ]

  it('nests source groups under each locomotive, in operational order', () => {
    const groups = groupBookingsByLocomotive(mixed())
    const loco33335 = groups.find((g) => g.locoNumber === '33335')!
    expect(loco33335.sources.map((s) => [s.label, s.total])).toEqual([
      ['Log Book', 2],
      ['Test Before', 2],
      ['Test After', 1],
    ])
  })

  it('leaves the LOCOMOTIVE totals exactly as they were', () => {
    const groups = groupBookingsByLocomotive(mixed())
    const loco33335 = groups.find((g) => g.locoNumber === '33335')!
    // Five bookings across three sources - the locomotive total is still the flat count of its
    // own bookings, and its pills still tally every status below it.
    expect(loco33335.total).toBe(5)
    expect(loco33335.counts).toEqual({
      OPEN: 3,
      IN_PROGRESS: 1,
      ATTENDED: 1,
      REOPENED: 0,
      total: 5,
    })
    // And the locomotive total equals the sum of its source totals - no booking lost, none double
    // counted by the new level.
    expect(loco33335.sources.reduce((n, s) => n + s.total, 0)).toBe(loco33335.total)
  })

  it('preserves the visible-booking-count ASC locomotive ordering', () => {
    const groups = groupBookingsByLocomotive(mixed())
    // 44373 has one booking, 33335 has five - fewest first, unchanged by the source level.
    expect(groups.map((g) => [g.locoNumber, g.total])).toEqual([
      ['44373', 1],
      ['33335', 5],
    ])
  })

  it('still breaks a count tie by locomotive number, numerically', () => {
    const groups = groupBookingsByLocomotive([
      poolBooking(1, '39126', 10, 'Air Dryer', 'OPEN', 'TEST_AFTER'),
      poolBooking(2, '9126', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
    ])
    expect(groups.map((g) => g.locoNumber)).toEqual(['9126', '39126'])
  })

  it('applies filters BEFORE grouping, so every level reflects only visible rows', () => {
    const rows = mixed()
    const groups = groupBookingsByLocomotive(poolVisible(rows, { source: 'TEST_BEFORE' }))
    expect(groups.map((g) => g.locoNumber)).toEqual(['33335'])
    const loco = groups[0]
    // Locomotive total is the VISIBLE count, not the unfiltered five.
    expect(loco.total).toBe(2)
    expect(loco.counts.total).toBe(2)
    // Log Book and Test After are gone entirely rather than present-and-empty.
    expect(loco.sources.map((s) => s.source)).toEqual(['TEST_BEFORE'])
    expect(loco.sources[0].total).toBe(2)
    expect(loco.sources[0].equipment.map((e) => e.label)).toEqual([
      'Bogie',
      'Bogie & its Mech. equip.',
    ])
  })

  it('hides a source group whose only bookings were filtered out by STATUS', () => {
    const groups = groupBookingsByLocomotive(poolVisible(mixed(), { status: 'ATTENDED' }))
    const loco = groups.find((g) => g.locoNumber === '33335')!
    // Only the one ATTENDED Log Book booking survives; Test Before and Test After disappear.
    expect(loco.sources.map((s) => [s.source, s.total])).toEqual([['LOG_BOOK', 1]])
    expect(loco.total).toBe(1)
    expect(loco.counts).toEqual({ OPEN: 0, IN_PROGRESS: 0, ATTENDED: 1, REOPENED: 0, total: 1 })
  })

  it('re-sorts locomotives when a filter changes their visible counts', () => {
    const rows = mixed()
    expect(groupBookingsByLocomotive(poolVisible(rows)).map((g) => g.locoNumber)).toEqual([
      '44373',
      '33335',
    ])
    // Filtering to TEST_AFTER leaves 33335 with one and 44373 with none, so 44373 vanishes.
    expect(
      groupBookingsByLocomotive(poolVisible(rows, { source: 'TEST_AFTER' })).map((g) => [
        g.locoNumber,
        g.total,
      ]),
    ).toEqual([['33335', 1]])
  })

  it('produces expansion keys that cannot collide across sources', () => {
    // The page's three key shapes. The equipment key carries BOTH ancestors, which is the whole
    // reason the same node under two sources expands independently.
    const groups = groupBookingsByLocomotive([
      poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'),
      poolBooking(2, '33335', 10, 'Air Dryer', 'OPEN', 'TEST_AFTER'),
    ])
    const loco = groups[0]
    const locoKey = `loco:${loco.key}`
    const sourceKeys = loco.sources.map((s) => `source:${loco.key}:${s.key}`)
    const equipmentKeys = loco.sources.flatMap((s) =>
      s.equipment.map((e) => `equipment:${loco.key}:${s.key}:${e.key}`),
    )

    expect(sourceKeys).toEqual(['source:33335:LOG_BOOK', 'source:33335:TEST_AFTER'])
    expect(equipmentKeys).toEqual([
      'equipment:33335:LOG_BOOK:10',
      'equipment:33335:TEST_AFTER:10',
    ])
    // Same node id, same locomotive, two distinct keys.
    expect(new Set(equipmentKeys).size).toBe(2)
    // And no key at any level is a duplicate of another.
    const all = [locoKey, ...sourceKeys, ...equipmentKeys]
    expect(new Set(all).size).toBe(all.length)
  })

  it('keeps a booking with no equipment identity, under its own source', () => {
    const groups = groupBookingsByLocomotive([
      { ...poolBooking(1, '33335', 10, 'Air Dryer', 'OPEN', 'LOG_BOOK'), equipment_node_id: null, equipment_node_name: null },
      poolBooking(2, '33335', 20, 'Bogie', 'OPEN', 'TEST_BEFORE'),
    ])
    const loco = groups[0]
    expect(loco.total).toBe(2)
    const logBook = loco.sources.find((s) => s.source === 'LOG_BOOK')!
    expect(logBook.equipment.map((e) => e.label)).toEqual(['No equipment specified'])
  })
})

// ===============================================================================================
// THE SOURCE LEVEL ON THE SECTION DASHBOARD.
//
// Mirrors the Booking Pool exactly, through the SAME helpers - groupBySource with accessors, not a
// second grouper. These tests deliberately re-assert ordering and labelling here rather than
// trusting the pool's own tests, because the whole point is that the two surfaces agree; if a
// future change splits them, one of these fails.
// ===============================================================================================

describe('groupAssignmentsBySource', () => {
  it('separates LOG_BOOK from TEST_BEFORE under the same locomotive', () => {
    const sources = groupAssignmentsBySource([
      assignment(1, 'JB to ACD/TPWS/DPWCS', 'OPEN', 10, '42818', 'LOG_BOOK'),
      assignment(2, 'Bogie', 'OPEN', 20, '42818', 'TEST_BEFORE'),
    ])
    expect(sources.map((s) => [s.source, s.total])).toEqual([
      ['LOG_BOOK', 1],
      ['TEST_BEFORE', 1],
    ])
  })

  it('gives TEST_AFTER its own group', () => {
    const sources = groupAssignmentsBySource([
      assignment(1, 'Air Dryer', 'OPEN', 10, '42818', 'TEST_AFTER'),
      assignment(2, 'Bogie', 'OPEN', 20, '42818', 'TEST_BEFORE'),
      assignment(3, 'JB to ACD/TPWS/DPWCS', 'OPEN', 30, '42818', 'LOG_BOOK'),
    ])
    expect(sources.map((s) => s.label)).toEqual(['Log Book', 'Test Before', 'Test After'])
  })

  it('renders the same equipment NODE under two sources independently', () => {
    const sources = groupAssignmentsBySource([
      assignment(1, 'Air Dryer', 'OPEN', 10, '42818', 'LOG_BOOK'),
      assignment(2, 'Air Dryer', 'ATTENDED', 10, '42818', 'TEST_AFTER'),
    ])
    expect(sources).toHaveLength(2)
    for (const group of sources) {
      expect(group.equipment).toHaveLength(1)
      expect(group.equipment[0].key).toBe('10')
      expect(group.equipment[0].label).toBe('Air Dryer')
      expect(group.equipment[0].items).toHaveLength(1)
    }
    expect(sources[0].equipment[0].items[0].id).toBe(1)
    expect(sources[1].equipment[0].items[0].id).toBe(2)
  })

  it('uses the canonical order, identical to the Booking Pool', () => {
    const everySource = [...BOOKING_SOURCE_ORDER].reverse()
    const viaAssignments = groupAssignmentsBySource(
      everySource.map((src, i) => assignment(i + 1, 'Air Dryer', 'OPEN', 10, '42818', src)),
    ).map((g) => g.source)
    const viaPool = groupBookingsBySource(
      everySource.map((src, i) => poolBooking(i + 1, '42818', 10, 'Air Dryer', 'OPEN', src)),
    ).map((g) => g.source)

    expect(viaAssignments).toEqual([...BOOKING_SOURCE_ORDER])
    // The assertion that matters: the two pages cannot drift.
    expect(viaAssignments).toEqual(viaPool)
  })

  it('uses the same friendly labels as the Booking Pool, from the shared utility', () => {
    for (const src of BOOKING_SOURCE_ORDER) {
      const [group] = groupAssignmentsBySource([
        assignment(1, 'Air Dryer', 'OPEN', 10, '42818', src),
      ])
      expect(group.label).toBe(bookingSourceGroupLabel(src))
    }
  })

  it('places an unknown source last, labelled with its raw stored value', () => {
    const sources = groupAssignmentsBySource([
      assignment(1, 'Air Dryer', 'OPEN', 10, '42818', 'FUTURE_SOURCE'),
      assignment(2, 'Bogie', 'OPEN', 20, '42818', 'MANUAL'),
      assignment(3, 'Pantograph', 'OPEN', 30, '42818', 'LOG_BOOK'),
    ])
    expect(sources.map((s) => s.source)).toEqual(['LOG_BOOK', 'MANUAL', 'FUTURE_SOURCE'])
    expect(sources[2].label).toBe('FUTURE_SOURCE')
    expect(sources[2].known).toBe(false)
  })

  it('keeps a blank source visible as "Source not recorded", not dropped', () => {
    const sources = groupAssignmentsBySource([
      assignment(1, 'Air Dryer', 'OPEN', 10, '42818', ''),
      assignment(2, 'Bogie', 'OPEN', 20, '42818', 'LOG_BOOK'),
    ])
    expect(sources.map((s) => s.label)).toEqual(['Log Book', 'Source not recorded'])
    expect(sources[1].total).toBe(1)
    expect(sources[1].equipment[0].items[0].id).toBe(1)
  })

  it('never produces an empty source group', () => {
    const sources = groupAssignmentsBySource([
      assignment(1, 'Air Dryer', 'OPEN', 10, '42818', 'LOG_BOOK'),
    ])
    expect(sources).toHaveLength(1)
    expect(sources.every((s) => s.total > 0 && s.equipment.length > 0)).toBe(true)
  })

  it('tallies the ASSIGNMENT status, not the booking status', () => {
    // A Supervisor works their own section's assignment of a booking. The two can differ, and the
    // pills on this page have always reported the assignment's state.
    const sources = groupAssignmentsBySource([
      assignment(1, 'Air Dryer', 'IN_PROGRESS', 10, '42818', 'LOG_BOOK'),
      assignment(2, 'Bogie', 'ATTENDED', 20, '42818', 'LOG_BOOK'),
      assignment(3, 'Pantograph', 'OPEN', 30, '42818', 'TEST_BEFORE'),
    ])
    const logBook = sources.find((s) => s.source === 'LOG_BOOK')!
    expect(logBook.counts).toEqual({ OPEN: 0, IN_PROGRESS: 1, ATTENDED: 1, REOPENED: 0, total: 2 })
    const testBefore = sources.find((s) => s.source === 'TEST_BEFORE')!
    expect(testBefore.counts).toEqual({ OPEN: 1, IN_PROGRESS: 0, ATTENDED: 0, REOPENED: 0, total: 1 })
  })

  it('does not depend on the order the assignments arrived in', () => {
    const rows = [
      assignment(1, 'Air Dryer', 'OPEN', 10, '42818', 'TEST_AFTER'),
      assignment(2, 'Bogie', 'OPEN', 20, '42818', 'LOG_BOOK'),
      assignment(3, 'Pantograph', 'OPEN', 30, '42818', 'FUTURE_SOURCE'),
      assignment(4, 'Contactor', 'OPEN', 40, '42818', 'TEST_BEFORE'),
    ]
    const forward = groupAssignmentsBySource(rows).map((s) => s.source)
    expect(forward).toEqual(['LOG_BOOK', 'TEST_BEFORE', 'TEST_AFTER', 'FUTURE_SOURCE'])
    expect(groupAssignmentsBySource(rows.slice().reverse()).map((s) => s.source)).toEqual(forward)
  })
})

describe('groupAssignmentsByLocomotive with the source level', () => {
  const mixed = () => [
    assignment(1, 'JB to ACD/TPWS/DPWCS', 'OPEN', 10, '42818', 'LOG_BOOK'),
    assignment(2, 'Pantograph', 'ATTENDED', 20, '42818', 'LOG_BOOK'),
    assignment(3, 'Bogie', 'OPEN', 30, '42818', 'TEST_BEFORE'),
    assignment(4, 'Air Dryer', 'IN_PROGRESS', 40, '42818', 'TEST_AFTER'),
    assignment(5, 'Air Dryer', 'OPEN', 40, '33586', 'LOG_BOOK'),
  ]

  it('nests source groups under each locomotive in operational order', () => {
    const loco = groupAssignmentsByLocomotive(mixed()).find((g) => g.locoNumber === '42818')!
    expect(loco.sources.map((s) => [s.label, s.total])).toEqual([
      ['Log Book', 2],
      ['Test Before', 1],
      ['Test After', 1],
    ])
  })

  it('leaves the LOCOMOTIVE counts exactly as they were', () => {
    const loco = groupAssignmentsByLocomotive(mixed()).find((g) => g.locoNumber === '42818')!
    expect(loco.total).toBe(4)
    expect(loco.counts).toEqual({ OPEN: 2, IN_PROGRESS: 1, ATTENDED: 1, REOPENED: 0, total: 4 })
    // No assignment lost and none double counted by the new level.
    expect(loco.sources.reduce((n, s) => n + s.total, 0)).toBe(loco.total)
  })

  it('preserves the existing locomotive ordering - fewest bookings first', () => {
    const groups = groupAssignmentsByLocomotive(mixed())
    expect(groups.map((g) => [g.locoNumber, g.total])).toEqual([
      ['33586', 1],
      ['42818', 4],
    ])
  })

  it('applies the status filter BEFORE grouping, so every level reflects visible rows only', () => {
    const groups = groupAssignmentsByLocomotive(visible(mixed(), 'OPEN'))
    const loco = groups.find((g) => g.locoNumber === '42818')!
    expect(loco.total).toBe(2)
    // Test After held only the IN_PROGRESS row, so that whole source group is gone - not present
    // and empty. Log Book drops from 2 to 1.
    expect(loco.sources.map((s) => [s.source, s.total])).toEqual([
      ['LOG_BOOK', 1],
      ['TEST_BEFORE', 1],
    ])
    expect(loco.sources[0].equipment.map((e) => e.label)).toEqual(['JB to ACD/TPWS/DPWCS'])
  })

  it('hides a source group whose only assignment was filtered out', () => {
    const loco = groupAssignmentsByLocomotive(visible(mixed(), 'ATTENDED')).find(
      (g) => g.locoNumber === '42818',
    )!
    expect(loco.sources.map((s) => [s.source, s.total])).toEqual([['LOG_BOOK', 1]])
    expect(loco.total).toBe(1)
  })

  it('produces expansion keys that cannot collide across sources', () => {
    const loco = groupAssignmentsByLocomotive([
      assignment(1, 'Air Dryer', 'OPEN', 40, '42818', 'LOG_BOOK'),
      assignment(2, 'Air Dryer', 'OPEN', 40, '42818', 'TEST_AFTER'),
    ])[0]
    const locoKey = `loco:${loco.key}`
    const sourceKeys = loco.sources.map((s) => `source:${loco.key}:${s.key}`)
    const equipmentKeys = loco.sources.flatMap((s) =>
      s.equipment.map((e) => `equipment:${loco.key}:${s.key}:${e.key}`),
    )
    expect(sourceKeys).toEqual(['source:42818:LOG_BOOK', 'source:42818:TEST_AFTER'])
    expect(equipmentKeys).toEqual([
      'equipment:42818:LOG_BOOK:40',
      'equipment:42818:TEST_AFTER:40',
    ])
    expect(new Set(equipmentKeys).size).toBe(2)
    const all = [locoKey, ...sourceKeys, ...equipmentKeys]
    expect(new Set(all).size).toBe(all.length)
  })

  it('keeps an assignment with no equipment identity, under its own source', () => {
    const loco = groupAssignmentsByLocomotive([
      assignment(1, null, 'OPEN', null, '42818', 'TEST_BEFORE'),
    ])[0]
    expect(loco.sources[0].source).toBe('TEST_BEFORE')
    expect(loco.sources[0].equipment.map((e) => e.label)).toEqual(['No equipment specified'])
  })
})
