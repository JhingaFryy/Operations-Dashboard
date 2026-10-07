import {
  bookingSourceGroupLabel,
  bookingSourceKey,
  compareBookingSources,
  isKnownBookingSource,
} from './bookingSource'
import type { AssignmentStatus, BookingPoolItem, PathItem, SectionAssignment } from '../types'

/** Client-side grouping/counting of data the page has ALREADY loaded.
 * Nothing here calls the API, changes routing, or re-derives a lifecycle —
 * counts are literal tallies of the statuses the server already returned. */

export interface StatusCounts {
  OPEN: number
  IN_PROGRESS: number
  ATTENDED: number
  REOPENED: number
  total: number
}

export function countStatuses(statuses: AssignmentStatus[]): StatusCounts {
  const counts: StatusCounts = { OPEN: 0, IN_PROGRESS: 0, ATTENDED: 0, REOPENED: 0, total: 0 }
  for (const s of statuses) {
    if (s in counts) counts[s] += 1
    counts.total += 1
  }
  return counts
}

export interface EquipmentGrouped<T> {
  key: string
  label: string
  path: PathItem[]
  items: T[]
}

/** Section Dashboard grouping: by equipment NODE ID, never by display name.
 *
 * This used to key on `equipment_node_name`, which silently merged two genuinely different
 * pieces of equipment that happen to print the same label. That is not a hypothetical: the
 * production hierarchy repeats names across branches heavily ("Others" appears hundreds of
 * times), so a Supervisor could see one card that was really two nodes' work, and acting on it
 * meant acting on the wrong equipment. It now follows the same rule the Booking Pool already
 * used - identity is the node id, and the hierarchy path is what disambiguates the label.
 *
 * An assignment whose booking has no equipment id is KEPT, in a single "No equipment specified"
 * group. A booking that exists must always be findable.
 */
export function groupAssignmentsByEquipment(
  assignments: SectionAssignment[],
): EquipmentGrouped<SectionAssignment>[] {
  const groups = new Map<string, SectionAssignment[]>()
  for (const a of assignments) {
    const nodeId = a.booking.equipment_node_id
    const key = nodeId != null ? String(nodeId) : 'none'
    const list = groups.get(key) ?? []
    list.push(a)
    groups.set(key, list)
  }
  return Array.from(groups.entries())
    .map(([key, items]) => ({
      key,
      label:
        key === 'none'
          ? 'No equipment specified'
          : (items.find((i) => i.booking.equipment_node_name)?.booking.equipment_node_name ??
            'Unknown equipment'),
      path: [],
      items: items.slice().sort((a, b) => b.assigned_at.localeCompare(a.assigned_at)),
    }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

/** Locomotive numbers, ascending, numerically where they are numeric.
 *
 * Plain string comparison puts "9126" after "39126" because it compares "9" against "3", so the
 * numeric collator is used instead - production loco numbers are 5-digit numerals and a lexical
 * order reads as scrambled. The second comparison is not redundant: the collator reports 0 for
 * strings it considers equivalent but that are not identical (differing case, or accents), and
 * returning 0 there would leave those two groups ordered by whatever order they arrived in. This
 * gives a TOTAL order, so the result never depends on Map insertion order.
 */
export function compareLocoNumbers(a: string, b: string): number {
  const numeric = a.localeCompare(b, undefined, { numeric: true })
  if (numeric !== 0) return numeric
  return a < b ? -1 : a > b ? 1 : 0
}

export interface AssignmentLocomotiveGrouped {
  /** Stable identity for expansion state - the loco number, never an array index. */
  key: string
  locoNumber: string
  scheduleVariant: string | null
  /** Every assignment under this locomotive, grouped by booking source and then by equipment
   *  node id - the same three levels, the same order and the same labels as the Booking Pool. */
  sources: SourceGrouped<SectionAssignment>[]
  /** Literal tally of the assignments below, not a server-reported number. */
  total: number
  /** Status tally over this locomotive's own assignments. */
  counts: StatusCounts
}

/** Section Dashboard: locomotive -> booking source -> equipment -> bookings.
 *
 * The same shape as the Admin Booking Pool, minus the section level: this page is already
 * scoped to one section, so a section grouping would be a click that reveals nothing. The source
 * level is groupBySource and the equipment level is groupAssignmentsByEquipment, so both rules
 * carry over - including that two distinct nodes sharing a display name stay separate, and that
 * the same node raised from two different sources appears under each of them.
 *
 * Supervisor scoping is NOT performed here and never was: the server returns only the section's
 * own assignments (GET /api/sections/{code}/assignments). This function groups whatever it is
 * handed, which is why every count below is a tally of the visible, section-scoped rows.
 */
export function groupAssignmentsByLocomotive(
  assignments: SectionAssignment[],
): AssignmentLocomotiveGrouped[] {
  const byLoco = new Map<string, SectionAssignment[]>()
  for (const a of assignments) {
    const key = a.booking.shed_visit.loco_number
    const list = byLoco.get(key) ?? []
    list.push(a)
    byLoco.set(key, list)
  }
  return Array.from(byLoco.entries())
    .map(([locoNumber, items]) => ({
      key: locoNumber,
      locoNumber,
      scheduleVariant:
        items.find((i) => i.booking.shed_visit.schedule_variant)?.booking.shed_visit
          .schedule_variant ?? null,
      sources: groupAssignmentsBySource(items),
      total: items.length,
      counts: countStatuses(items.map((i) => i.status)),
    }))
    // FEWEST BOOKINGS FIRST, so the locomotives a section can finish quickly sit at the top.
    //
    // `total` is the count the card displays, and it is a tally of the assignments actually
    // passed in - which on the Section Dashboard is the STATUS-FILTERED list. So selecting OPEN
    // re-sorts by each locomotive's visible OPEN count automatically, and there is no way for the
    // order to be driven by a hidden unfiltered total while the cards show something else: there
    // is only one number, and it is this one.
    //
    // Ties go to the lower locomotive number, compared numerically. Note this orders the GROUPS
    // only - the bookings inside each equipment group keep their newest-first order, and the
    // equipment groups keep their label order.
    .sort((a, b) => a.total - b.total || compareLocoNumbers(a.locoNumber, b.locoNumber))
}

/** Booking Pool grouping — by equipment node id, so two distinct nodes that
 * happen to share a display name stay separate groups. */
export function groupBookingsByEquipment(bookings: BookingPoolItem[]): EquipmentGrouped<BookingPoolItem>[] {
  const byEquipment = new Map<string, BookingPoolItem[]>()
  for (const b of bookings) {
    const key = b.equipment_node_id != null ? String(b.equipment_node_id) : 'none'
    const list = byEquipment.get(key) ?? []
    list.push(b)
    byEquipment.set(key, list)
  }
  return Array.from(byEquipment.entries())
    .map(([key, items]) => ({
      key,
      label:
        key === 'none'
          ? 'No equipment specified'
          : (items.find((i) => i.equipment_node_name)?.equipment_node_name ?? 'Unknown equipment'),
      path: items.find((i) => i.equipment_path.length > 0)?.equipment_path ?? [],
      items: items.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)),
    }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

/** One booking SOURCE under a locomotive: Log Book, Test Before, Test After, and so on.
 *
 * WHY THIS LEVEL EXISTS. Every row already carried a Source column, but a locomotive's Log Book
 * findings, its Test Before findings and its Test After findings were interleaved under the same
 * equipment groups, so reading "what did Test Before raise on this loco" meant scanning rows.
 * These are different pieces of work raised by different activities at different points in the
 * visit; grouping by them is how the page now matches how the shed actually works.
 */
export interface SourceGrouped<T> {
  /** The STORED value, used as the expansion-key component - '' when none was recorded. */
  key: string
  /** Identical to `key`; named for readability at the call site. */
  source: string
  /** Friendly label where one is defined, otherwise the raw stored value. */
  label: string
  /** False for a value this build has no defined position for - it sorts last and is labelled
   *  with its raw value rather than being hidden. */
  known: boolean
  /** This source's items under this locomotive, grouped by equipment node id. */
  equipment: EquipmentGrouped<T>[]
  /** Literal tally of the items below. */
  total: number
  /** Status tally over this source's own items. */
  counts: StatusCounts
}

/** The source level, for any booking-shaped row.
 *
 * ONE IMPLEMENTATION, TWO PAGES. The Admin Booking Pool works on BookingPoolItem (source at
 * `booking_source`) and the Supervisor Section Dashboard works on SectionAssignment (source at
 * `booking.booking_source`, status on the assignment rather than the booking). Those are the only
 * differences, so they are passed in as accessors rather than copied into a second grouper - a
 * duplicated source order or label map is exactly how the two surfaces would drift.
 *
 * Built only from items that are actually present, so an EMPTY SOURCE GROUP CANNOT EXIST -
 * including after filtering, because both callers filter before grouping. There is no fixed set
 * of source rows to leave behind.
 */
export function groupBySource<T>(
  items: T[],
  accessors: {
    sourceOf: (item: T) => string | null | undefined
    statusOf: (item: T) => AssignmentStatus
    groupEquipment: (items: T[]) => EquipmentGrouped<T>[]
  },
): SourceGrouped<T>[] {
  const bySource = new Map<string, T[]>()
  for (const item of items) {
    const key = bookingSourceKey(accessors.sourceOf(item))
    const list = bySource.get(key) ?? []
    list.push(item)
    bySource.set(key, list)
  }
  return Array.from(bySource.entries())
    .map(([key, group]) => ({
      key,
      source: key,
      label: bookingSourceGroupLabel(key),
      known: isKnownBookingSource(key),
      // Unchanged rule, one level down: equipment identity is the node id, never the display
      // name, so the SAME equipment name appearing under two sources is two separate groups in
      // two separate source sections.
      equipment: accessors.groupEquipment(group),
      total: group.length,
      counts: countStatuses(group.map(accessors.statusOf)),
    }))
    // Fixed operational order, NOT by count and not alphabetical - an operator looks for "Test
    // Before" in the same place on every locomotive, and in the same place on BOTH pages.
    // compareBookingSources is a total order, so this never depends on Map insertion order.
    .sort((a, b) => compareBookingSources(a.key, b.key))
}

/** Booking Pool: the bookings of ONE locomotive, split by source, in operational order. */
export function groupBookingsBySource(bookings: BookingPoolItem[]): SourceGrouped<BookingPoolItem>[] {
  return groupBySource(bookings, {
    sourceOf: (b) => b.booking_source,
    statusOf: (b) => b.status,
    groupEquipment: groupBookingsByEquipment,
  })
}

/** Section Dashboard: the assignments of ONE locomotive, split by source, in operational order.
 *
 * The source is the BOOKING's (`a.booking.booking_source`) because that is where it is recorded;
 * the status is the ASSIGNMENT's, because a Supervisor works their own section's assignment of a
 * booking, not the booking itself. Same order, same labels, same empty-group rule as the pool.
 */
export function groupAssignmentsBySource(
  assignments: SectionAssignment[],
): SourceGrouped<SectionAssignment>[] {
  return groupBySource(assignments, {
    sourceOf: (a) => a.booking.booking_source,
    statusOf: (a) => a.status,
    groupEquipment: groupAssignmentsByEquipment,
  })
}

export interface LocomotiveGrouped {
  /** Stable identity for expansion state - the loco number, never an array index. */
  key: string
  locoNumber: string
  /** The schedule this visit is running, when the payload carries one. NOT the locomotive
   * model: BookingPoolShedVisitBrief does not report loco_type, and inventing an API field
   * for a group heading is not worth a backend change. */
  scheduleVariant: string | null
  /** Every booking under this locomotive, grouped by source and then by equipment node id. */
  sources: SourceGrouped<BookingPoolItem>[]
  /** Literal tally of the bookings below, not a server-reported number. */
  total: number
  /** Status tally over this locomotive's own bookings - the same four numbers this card has
   *  always shown, now computed here rather than re-flattened at the call site. */
  counts: StatusCounts
}

/** Booking Pool: locomotive -> booking source -> equipment -> bookings.
 *
 * Three levels of grouping over data the page has already loaded. The source level is the
 * addition; the equipment level below it reuses groupBookingsByEquipment, so its rule carries
 * over unchanged: bookings group by equipment_node_id, NEVER by display name. The hierarchy
 * legitimately repeats names across branches ("Others" appears hundreds of times in production),
 * so two distinct nodes that happen to print the same label stay two groups, distinguished by
 * their path - and now, additionally, the same node under two different sources is two groups in
 * two different source sections, which is the point of the change.
 *
 * A booking whose equipment cannot be identified is kept under its source in a single
 * "No equipment specified" group rather than dropped - a booking that exists must always be
 * findable, even when Loco Master could not name its equipment. The same holds one level up for
 * a booking whose source is not recorded.
 */
export function groupBookingsByLocomotive(bookings: BookingPoolItem[]): LocomotiveGrouped[] {
  const byLoco = new Map<string, BookingPoolItem[]>()
  for (const b of bookings) {
    const key = b.shed_visit.loco_number
    const list = byLoco.get(key) ?? []
    list.push(b)
    byLoco.set(key, list)
  }
  return Array.from(byLoco.entries())
    .map(([locoNumber, items]) => ({
      key: locoNumber,
      locoNumber,
      scheduleVariant:
        items.find((i) => i.shed_visit.schedule_variant)?.shed_visit.schedule_variant ?? null,
      sources: groupBookingsBySource(items),
      total: items.length,
      counts: countStatuses(items.map((i) => i.status)),
    }))
    // FEWEST BOOKINGS FIRST, the same rule as groupAssignmentsByLocomotive above, with the same
    // numeric tie-break. Both pages now order locomotives identically, so moving between them
    // does not mean re-learning where to look.
    //
    // `total` is the count the card displays, and it tallies the bookings actually passed in -
    // which on the Booking Pool is the list left after ALL FOUR of its filters (status, source,
    // equipment, search text) have been applied client-side. So every filter re-sorts, and the
    // order can never be driven by a hidden unfiltered total while the cards show something else:
    // there is one number, and it is this one.
    .sort((a, b) => a.total - b.total || compareLocoNumbers(a.locoNumber, b.locoNumber))
}
