"""Single source of truth for "how resolved is a booking", derived from its
booking_section_assignments rows.

WHY THIS MODULE EXISTS
----------------------
Booking.status is not an independent lifecycle - it is a DERIVED aggregate over the booking's
per-section assignment rows (see section_dashboard_service.recompute_booking_status, which is the
only writer). The aggregate formula was previously written out at its single write site, and three
readers needed the same answer for different purposes:

  * recompute_booking_status()                  - to WRITE the parent status
  * shed_out_service                            - to DISPLAY why a booking blocks Shed Out
  * shed_visit_service                          - to COUNT a visit's unresolved bookings

Re-typing `any REOPENED -> ... elif all ATTENDED -> ...` at three sites is the kind of duplication
that drifts silently: one copy gets a new status or an inverted condition and the gate, the count
and the badge start disagreeing about the same booking. The functions here are pure (they take
statuses, not a Session) so every caller gets the identical answer, and the formula can be tested
without a database.

This module deliberately does NOT know about SQLAlchemy, sessions, or HTTP. It defines no new
status values: OPEN/IN_PROGRESS/ATTENDED/REOPENED are exactly the four values
chk_booking_section_status already permits.
"""

from __future__ import annotations

from collections.abc import Iterable

#: An assignment in one of these statuses still has outstanding work. Mirrors the
#: NOT_YET_ATTENDED_ASSIGNMENT_STATUSES tuples that shed_out_service and
#: checksheet_stage_reconciliation_service already gate on - same three values, one definition.
UNRESOLVED_ASSIGNMENT_STATUSES: tuple[str, ...] = ("OPEN", "IN_PROGRESS", "REOPENED")

#: The single resolved status. There is no "CLOSED" in this system: ATTENDED is terminal.
RESOLVED_ASSIGNMENT_STATUS = "ATTENDED"

#: Severity order used when one status must stand for the whole booking. Deliberately the same
#: precedence as the aggregate formula below, so a displayed status can never contradict the
#: stored aggregate.
_PRECEDENCE: tuple[str, ...] = ("REOPENED", "IN_PROGRESS", "OPEN")


def aggregate_booking_status(statuses: Iterable[str]) -> str:
    """The canonical parent-status formula.

        any REOPENED    -> REOPENED
        all ATTENDED    -> ATTENDED   (requires at least one assignment; a booking with zero
                                       assignments is never reported ATTENDED)
        any IN_PROGRESS -> IN_PROGRESS
        otherwise       -> OPEN

    A booking with no assignment rows at all yields OPEN - unresolved, which is the safe answer.
    It is not ATTENDED (nothing has been attended) and inventing a fourth parent value for it
    would need DDL on chk_booking_status.
    """
    values = list(statuses)

    if "REOPENED" in values:
        return "REOPENED"
    if values and all(s == RESOLVED_ASSIGNMENT_STATUS for s in values):
        return RESOLVED_ASSIGNMENT_STATUS
    if "IN_PROGRESS" in values:
        return "IN_PROGRESS"
    return "OPEN"


def is_booking_resolved(statuses: Iterable[str]) -> bool:
    """True only when there is at least one assignment and every one of them is ATTENDED.

    Zero assignments is NOT resolved. Booking creation always writes at least one row (see
    booking_creation_service), so an empty set means a genuine routing gap rather than finished
    work - treating it as resolved would let such a booking pass a completion check silently.
    """
    values = list(statuses)
    return bool(values) and all(s == RESOLVED_ASSIGNMENT_STATUS for s in values)


def unresolved_display_status(statuses: Iterable[str]) -> str | None:
    """The single status that best represents an UNRESOLVED booking, or None if it is resolved.

    Used where one status has to be shown for a booking that is holding something up - the Shed
    Out blocker list, for instance. Returning None for a resolved booking is intentional: a
    resolved booking is not a blocker, so a caller that still has one in hand has a bug, and
    None surfaces that rather than printing a reassuring "ATTENDED" next to a blocking row.
    """
    values = list(statuses)
    if is_booking_resolved(values):
        return None
    for candidate in _PRECEDENCE:
        if candidate in values:
            return candidate
    # No assignment rows at all. Distinct from "has rows, none resolved" and the caller usually
    # wants to say so explicitly (shed_out_service reports NO_ASSIGNMENTS), but OPEN is the
    # truthful aggregate: there is outstanding work and nobody has started it.
    return "OPEN"
