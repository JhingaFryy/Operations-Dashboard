import { bookingSourceLabel, formatDateTimeShort, formatSchedule, stageLabel } from '../../lib/format'
import { multiSectionLabel, sectionChipMeta, sectionChipTitle } from '../../lib/assignmentStatus'
import { BookingStatusBadge, Tag } from '../ui/StatusBadge'
import type { BookingPoolItem } from '../../types'

/** One booking as a dense table row for the Admin Booking Pool.
 *
 * Read-only for the LIFECYCLE, by design: the global pool is Admin operational
 * visibility, and start/attend/reopen happen on the Section Dashboard against
 * the section assignment, never here. No lifecycle control is rendered — not
 * even a disabled one — so no invalid transition is ever offered.
 *
 * The one exception is `onDelete`, and it is not a lifecycle action: it opens a
 * confirmation dialog for permanently destroying a mistaken entry. It is given
 * only when the caller is an Admin, it is styled as a danger affordance rather
 * than sitting among ordinary controls, and it performs nothing itself — a
 * single click can never delete anything.
 *
 * The booking's own internal id is not displayed; loco + equipment +
 * description are what identify it operationally.
 */
export function BookingRow({
  booking,
  onDelete,
  onAddSections,
}: {
  booking: BookingPoolItem
  /** Admin only. Omitted entirely for everyone else, so there is no control to find. */
  onDelete?: (booking: BookingPoolItem) => void
  /**
   * Planner section ADDITION. Given whenever the caller holds the routing capability - for every
   * booking source, since the action can only add responsibility and never remove it. Absent for
   * anyone without the capability, so no control is rendered at all rather than a disabled one.
   */
  onAddSections?: (booking: BookingPoolItem) => void
}) {
  const handled =
    booking.status === 'ATTENDED'
      ? { verb: 'Attended', at: booking.attended_at, who: booking.attended_by_name, section: booking.attended_by_section_code }
      : booking.started_at
        ? { verb: 'Started', at: booking.started_at, who: booking.started_by_name, section: booking.started_by_section_code }
        : null

  /* What to say when the response carries no handler at all.
   *
   * "Not started" is only true of an OPEN booking. For IN_PROGRESS,
   * ATTENDED or REOPENED the work demonstrably HAS begun — the status is
   * derived server-side from this booking's section assignments — so
   * printing "Not started" there asserted something false. The fields this
   * column reads (started_at, started_by_name, attended_at,
   * attended_by_name on the booking itself) are the parent-level
   * lifecycle columns, which nothing writes any more: the
   * section-execution record lives on booking_section_assignments and is
   * surfaced by the Section Dashboard. So the pool response cannot say WHO
   * handled a booking, and this column now says exactly that instead of
   * guessing. No new field is invented and no name is inferred. */
  const noHandlerLabel = booking.status === 'OPEN' ? 'Not started' : 'No handler recorded'

  return (
    <tr className={`booking-row booking-row-${booking.status.toLowerCase()}`}>
      <td className="booking-row-loco">
        <span className="loco-number">{booking.shed_visit.loco_number}</span>
        <span className="booking-row-schedule">
          {formatSchedule(booking.shed_visit.schedule_family, booking.shed_visit.schedule_variant)}
        </span>
      </td>

      <td className="booking-row-defect">
        <p className="booking-row-description">{booking.description}</p>
        {/* "Added by PPIO" - PROVENANCE, not a booking source. The flag is derived server-side
            from bookings.created_by (see BookingPoolItemOut), so booking_source stays
            semantically correct - a planner-raised finding is MANUAL - while the row still says
            who raised it. No column and no migration were needed for this. */}
        {booking.created_by_planning_section && (
          /* One text node, not an interpolated pair - so the badge reads as a single phrase to a
             screen reader and matches as one string in a test. */
          <Tag tone="info">{`Added by ${booking.created_by_section_code ?? 'planning'}`}</Tag>
        )}
        {booking.defect_type && <Tag tone="info">{booking.defect_type.name}</Tag>}
      </td>

      <td className="booking-row-source">
        <span>{bookingSourceLabel(booking.booking_source)}</span>
        {booking.workflow_stage_type && (
          <span className="booking-row-stage">{stageLabel(booking.workflow_stage_type)}</span>
        )}
      </td>

      {/* PROVENANCE, not a source. created_by_planning_section is derived server-side from
          bookings.created_by, so booking_source stays semantically correct (a planner-raised
          finding is MANUAL) and the badge still says who raised it. */}
      {/* The section(s) this booking was ROUTED to, from its own assignment rows - not the
          equipment's current mapping. An equipment re-map does not rewrite history, so an old
          booking keeps showing where the work actually went. Several sections is normal. */}
      <td className="booking-row-section">
        {/* Defensive against an absent array, not just an empty one: during a rolling deploy the
            browser can hold this bundle while still talking to a backend that predates the
            field. Degrading to "Not routed" is the same choice _enrich_equipment makes server
            side - a display gap, never a crashed page. */}
        {/* EACH CHIP CARRIES ITS OWN SECTION'S STATUS, not the booking's aggregate.
            A multi-section booking is the whole point of planning: M4-HR may be ATTENDED while
            MACHINE SHOP is still OPEN, and a planner needs to see which of the two is holding the
            work. Rendering only the section code - as this did - made every chip look identical
            and hid exactly the information the pool exists to show.

            Never colour alone: the glyph is decorative (aria-hidden) and the status word travels
            with it as visually-hidden text, so the chip reads correctly in greyscale and to a
            screen reader. `title` repeats it for a sighted mouse user, mirroring how StatusBadge
            already handles every other status in this app. */}
        {(booking.routed_sections ?? []).length > 0 ? (
          (booking.routed_sections ?? []).map((section) => {
            const meta = sectionChipMeta(section.status)
            return (
              <Tag key={section.section_id} tone={meta.tone}>
                <span title={sectionChipTitle(section.section_code, section.status)}>
                  <span aria-hidden="true">{meta.glyph}</span> {section.section_code}
                  {/* ONE text node, not several - a label split across elements is not matchable
                      by getByText, which has already caused flaky assertions in this file. */}
                  <span className="visually-hidden">{` \u2014 ${meta.label}`}</span>
                </span>
              </Tag>
            )
          })
        ) : (
          <span className="booking-row-muted">Not routed</span>
        )}
        {/* Only when genuinely multi-section; multiSectionLabel returns null below 2 so the
            common single-section booking gains no redundant "1 sections" chip. */}
        {multiSectionLabel((booking.routed_sections ?? []).length) && (
          <span className="booking-row-section-count">
            {multiSectionLabel((booking.routed_sections ?? []).length)}
          </span>
        )}
        {/* Existing assignments stay visible either way; only the ACTION is conditional. */}
        {onAddSections && (
          <button
            type="button"
            className="booking-row-link-button"
            onClick={() => onAddSections(booking)}
          >
            Add sections
            <span className="visually-hidden"> to {booking.description}</span>
          </button>
        )}
      </td>

      <td className="booking-row-status">
        <BookingStatusBadge status={booking.status} size="small" />
      </td>

      <td className="booking-row-created">{formatDateTimeShort(booking.created_at)}</td>

      <td className="booking-row-handling">
        {handled && handled.at ? (
          <span className="booking-row-handling-line">
            {handled.verb} {formatDateTimeShort(handled.at)}
            {handled.who ? ` · ${handled.who}` : ''}
            {handled.section ? ` (${handled.section})` : ''}
          </span>
        ) : (
          <span
            className="booking-row-handling-none"
            title={
              booking.status === 'OPEN'
                ? undefined
                : 'No handler is recorded on this booking. Per-section start/attend detail is on the Section Dashboard.'
            }
          >
            {noHandlerLabel}
          </span>
        )}
        {booking.status === 'ATTENDED' && booking.attendance_remarks && (
          <p className="booking-row-remarks">{booking.attendance_remarks}</p>
        )}
        {onDelete && (
          <button
            type="button"
            className="btn btn-small btn-danger-subtle booking-row-delete"
            onClick={() => onDelete(booking)}
            aria-label={`Delete booking: ${booking.description}`}
          >
            Delete…
          </button>
        )}
      </td>
    </tr>
  )
}
