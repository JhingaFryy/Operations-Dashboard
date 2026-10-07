/**
 * The ONE ordering for active shed visits: newest arrival first.
 *
 * WHY A SHARED COMPARATOR WHEN THE API ALREADY ORDERS. Both pages read the same endpoint
 * (listCurrentShedVisits -> list_current_visits), which orders by arrival_at DESC, id DESC. Shed
 * Movement simply rendered that order and was correct by accident; the Overview re-sorted it and was
 * wrong - it put the most outstanding bookings first and, within that, the OLDEST arrival. The two
 * pages disagreed, and nothing could notice, because each decided ordering for itself.
 *
 * So the rule lives here, both pages apply it explicitly, and neither depends on the order a response
 * happens to arrive in. The backend ordering stays as well: a list that is already sorted costs
 * nothing to sort again, and the server should not hand out an arbitrary order in the first place.
 *
 * SHED-IN CHRONOLOGY, NOTHING ELSE. arrival_at only - never loco number, schedule, status, ready_at,
 * updated_at, a booking time or a workflow-stage start. A newly shed-in locomotive is at the top the
 * moment either page re-fetches.
 */

export interface ArrivalOrdered {
  id: number
  loco_number: string
  /** shed_visits.arrival_at, ISO-8601. NOT NULL in the schema. */
  arrival_at: string
}

/** The stored instant, in milliseconds. NaN for an unparseable value. */
function arrivalTime(value: string | null | undefined): number {
  // Parsed, not compared as text. Two ISO strings only compare correctly as strings when they share
  // a timezone offset and format, and these are serialized from timestamptz - so a lexicographic
  // compare would silently mis-order the moment an offset differed. Formatting for display
  // ("01 Oct, 07:40 AM IST") is never involved in ordering at all.
  return value ? Date.parse(value) : Number.NaN
}

/**
 * Newest arrival first, with a total order.
 *
 * Tie-breaks, in order:
 *   1. arrival_at descending - the requirement.
 *   2. id descending - two locomotives can be shed in within the same second, and the later-created
 *      visit is the newer one.
 *   3. loco_number ascending, numerically - only ever reached if two rows share an arrival AND an id,
 *      which cannot happen for real rows. It is here so the comparator is a TOTAL order and the list
 *      can never depend on the order items arrived in.
 *
 * An unparseable or missing arrival sorts LAST rather than being treated as "now". The schema says
 * NOT NULL, so this is unreachable with real data; inventing a timestamp would put a broken row at
 * the top, which is the worst place for it.
 */
export function compareShedVisitsByArrivalDesc(a: ArrivalOrdered, b: ArrivalOrdered): number {
  const left = arrivalTime(a.arrival_at)
  const right = arrivalTime(b.arrival_at)
  const leftBad = Number.isNaN(left)
  const rightBad = Number.isNaN(right)
  if (leftBad !== rightBad) return leftBad ? 1 : -1
  if (!leftBad && left !== right) return right - left

  if (a.id !== b.id) return b.id - a.id

  const byNumber = a.loco_number.localeCompare(b.loco_number, undefined, { numeric: true })
  return byNumber !== 0 ? byNumber : 0
}

/** A new array in newest-arrival-first order. Never sorts in place: the caller's array may be React
 *  state, and mutating it would be a silent correctness bug rather than a visible one. */
export function orderShedVisitsByArrivalDesc<T extends ArrivalOrdered>(visits: readonly T[]): T[] {
  return visits.slice().sort(compareShedVisitsByArrivalDesc)
}
