"""Adding maintenance sections to a booking. Planning's one write.

PPIO is the planning section: it performs no work, but it decides which maintenance sections are
responsible for a finding. This module is that decision and nothing else.

ADDITIVE ONLY (revised 2026-10-06)
----------------------------------
This started as a SET replacement - the caller stated the complete desired set and the service
derived additions and removals. That model is now wrong for a planner, and the reason is worth
stating because the earlier version was not obviously unsafe:

A booking arrives already routed by Loco Master's equipment-section mapping. A Traction Motor
finding maps to M35-TM, and that assignment records who the equipment actually belongs to. A
planner adding M2-HR and M6-HR is saying "these sections are ALSO responsible" - never "M35-TM is
not". With a desired-set contract, a client that simply omitted M35-TM would silently un-route the
section that owns the equipment, and a stale browser tab holding an older set would do it by
accident. The contract itself has to make that impossible, not merely discourage it.

So: the request carries sections to ADD. There is no way to express a removal, and nothing here
deletes a row. Admin's audited deletion flow is untouched and remains the only way an assignment
goes away.

WHAT IT IS NOT
--------------
It is not a revival of booking_service.add_section(). That route stays retired - it raises 410
unconditionally and is untouched by this file.

It is also not a booking editor. Nothing here reads or writes the booking's description, defect
type, source, equipment, locomotive, schedule, status, attendance, timestamps or any other field.
The only rows it touches are new booking_section_assignments, plus the booking_events that record
the addition.

ALL SOURCES ARE ROUTABLE
------------------------
The earlier version allowed only LOG_BOOK / TEST_BEFORE / TEST_AFTER. That restriction is gone:
PPIO has planning responsibility across the whole pool, and the permission boundary is no longer
"which source" but "what may be done" - sections may be added, and nothing else may be touched.
A booking source this code has never heard of is therefore routable too, which is the point: a
new legitimate source must not need a code change here before a planner can route it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import (
    Booking,
    BookingEvent,
    BookingSectionAssignment,
    Section,
    ShedVisit,
    User,
)
from app.core.authz import is_assignable_work_section, is_planning_section

# Reused rather than invented. booking_events.event_type is CHECK-constrained to
# ('CREATED','AUTO_ROUTED','FORWARDED','STARTED','ATTENDED','REOPENED'), so a new "ROUTED" type
# would need DDL. FORWARDED already means "this booking moved between sections", already carries
# from_section_id / to_section_id, and already has a formatter (event_formatting.py) - it is the
# existing vocabulary for exactly this change, and no parallel audit system is created.
ROUTING_EVENT_TYPE = "FORWARDED"

@dataclass
class RoutingOutcome:
    """What an addition actually did, for the response and the audit event.

    There is no `removed_section_ids`, deliberately: this operation cannot remove anything, and a
    field that is always empty invites a reader to believe removal is merely unused rather than
    impossible.
    """

    booking_id: int
    previous_section_ids: list[int] = field(default_factory=list)
    new_section_ids: list[int] = field(default_factory=list)
    added_section_ids: list[int] = field(default_factory=list)
    """Requested sections the booking already had - reported, not an error, and not re-added."""
    already_assigned_section_ids: list[int] = field(default_factory=list)
    unchanged_section_ids: list[int] = field(default_factory=list)


def _booking_or_404(db: Session, booking_id: int) -> Booking:
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return booking


# NO SOURCE GATE, DELIBERATELY. An earlier version allowed only LOG_BOOK / TEST_BEFORE /
# TEST_AFTER and refused everything else. PPIO's planning responsibility covers the whole pool,
# and because this endpoint can now only ADD a section - it cannot remove, replace or edit
# anything - the booking's source is not what makes the operation safe. Keeping a source
# allow-list would also mean a new legitimate source needed a code change here before a planner
# could route it, which is exactly the coupling the revision removes.
#
# A source value this code has never seen is therefore routable. That is safe: the booking already
# exists and its source is never read, written or validated by anything below.


def _resolve_destinations(db: Session, section_ids: list[int]) -> list[Section]:
    """Every requested destination, validated.

    Three separate refusals, each with its own message, because they are three different operator
    mistakes: a section that does not exist, a planning section (PPIO cannot be routed TO - it
    routes), and an empty set.
    """
    requested = list(dict.fromkeys(section_ids))  # de-duplicated, order preserved
    if not requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one section to add is required.",
        )

    found = db.query(Section).filter(Section.id.in_(requested)).all()
    by_id = {s.id: s for s in found}

    missing = [sid for sid in requested if sid not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown section id(s): {', '.join(str(m) for m in missing)}.",
        )

    planning = [by_id[sid].code for sid in requested if is_planning_section(by_id[sid])]
    if planning:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{', '.join(planning)} is a planning section and cannot be a routing "
                "destination. Bookings are routed TO maintenance sections."
            ),
        )

    # Every section that exists qualifies except a planning one - the sections table is the
    # source of truth, so SHIFT, PRE-MONSOON, MILL-WRIGHT, MACHINE SHOP, CMS Lab and any section
    # added later are all assignable without a code change. The planning exclusion is reached
    # through is_assignable_work_section -> is_planning_section, never by comparing a code here.
    #
    # The planning check above already refused PPIO with a more specific message; this is the
    # general rule, and it also catches anything is_planning_section grows to exclude.
    not_assignable = [
        by_id[sid].code for sid in requested if not is_assignable_work_section(by_id[sid])
    ]
    if not_assignable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{', '.join(not_assignable)} cannot be a routing destination. Bookings are "
                "routed to sections that perform the work, never to a planning section."
            ),
        )

    return [by_id[sid] for sid in requested]


def add_sections(
    db: Session,
    booking_id: int,
    section_ids: list[int],
    current_user: User,
    reason: str | None = None,
) -> RoutingOutcome:
    """ADD the given maintenance sections to a booking. Never remove, never rewrite.

    EXISTING ASSIGNMENTS ARE NOT TOUCHED AT ALL. A section already on the booking is skipped - its
    row is not re-saved, so its assignment_source, assigned_at, status, started_at and attended_at
    survive exactly as they are. That matters most for an AUTO_MAPPING row: it records that Loco
    Master's equipment-section mapping put the work there, and re-stating it as MANUAL would erase
    how the booking originally reached that section. A duplicate request is a no-op, not a second
    row - uq_booking_section would refuse one anyway, and reporting it as "already assigned" is a
    truer answer than an error.

    There is no removal path through this function, by construction: the request names sections to
    add, and nothing here issues a delete.
    """
    booking = _booking_or_404(db, booking_id)

    destinations = _resolve_destinations(db, section_ids)
    requested_ids = [s.id for s in destinations]

    existing = (
        db.query(BookingSectionAssignment)
        .filter(BookingSectionAssignment.booking_id == booking_id)
        .all()
    )
    existing_ids = {a.section_id for a in existing}
    previous_ids = sorted(existing_ids)

    added = [sid for sid in requested_ids if sid not in existing_ids]
    already = [sid for sid in requested_ids if sid in existing_ids]

    if not added:
        # Everything requested was already assigned. No row, and no event: an audit trail of
        # "nothing happened" entries makes the real additions harder to find.
        return RoutingOutcome(
            booking_id=booking_id,
            previous_section_ids=previous_ids,
            new_section_ids=previous_ids,
            already_assigned_section_ids=sorted(already),
            unchanged_section_ids=previous_ids,
        )

    sections_by_id = {
        s.id: s
        for s in db.query(Section).filter(Section.id.in_(existing_ids | set(requested_ids))).all()
    }

    now = datetime.now(timezone.utc)
    for section_id in added:
        db.add(
            BookingSectionAssignment(
                booking_id=booking_id,
                section_id=section_id,
                status="OPEN",
                # MANUAL, not a new enum value. assignment_source is CHECK-constrained to
                # ('AUTO_MAPPING','MANUAL'), and a planner's addition IS a manual assignment -
                # a dedicated PPIO value would need DDL and would record WHO in the wrong place.
                # Who added it is recorded on the booking_event below, which is where actor
                # identity already lives for every other assignment change.
                assignment_source="MANUAL",
                assigned_at=now,
                updated_at=now,
            )
        )

    outcome = RoutingOutcome(
        booking_id=booking_id,
        previous_section_ids=previous_ids,
        new_section_ids=sorted(existing_ids | set(added)),
        added_section_ids=sorted(added),
        already_assigned_section_ids=sorted(already),
        unchanged_section_ids=previous_ids,
    )
    _record_events(db, booking_id, outcome, sections_by_id, current_user, reason, now)
    db.commit()
    return outcome


def _code(sections_by_id: dict[int, Section], section_id: int) -> str:
    section = sections_by_id.get(section_id)
    return section.code if section else str(section_id)


def _record_events(
    db: Session,
    booking_id: int,
    outcome: RoutingOutcome,
    sections_by_id: dict[int, Section],
    current_user: User,
    reason: str | None,
    now: datetime,
) -> None:
    """One FORWARDED event per section ADDED, each carrying the whole before/after picture.

    Why both: the per-section rows give to_section_id, which is what the existing history
    formatter and the existing from/to columns are built for. The event_data snapshot gives the
    complete previous and new sets, so a single row answers "what did this change do" without
    reassembling it from siblings. Actor and timestamp come from the columns booking_events
    already has - no parallel audit system is introduced, and assignment_source stays MANUAL
    because WHO added it belongs here, not in an enum.

    `from_section_id` is always NULL: nothing was moved away from. A planner adds responsibility;
    it never transfers it.
    """
    snapshot = {
        "change": "SECTION_RESPONSIBILITY_ADDED",
        "previous_section_ids": outcome.previous_section_ids,
        "new_section_ids": outcome.new_section_ids,
        "added_section_ids": outcome.added_section_ids,
        "already_assigned_section_ids": outcome.already_assigned_section_ids,
        "previous_section_codes": [_code(sections_by_id, s) for s in outcome.previous_section_ids],
        "added_section_codes": [_code(sections_by_id, s) for s in outcome.added_section_ids],
        "new_section_codes": [_code(sections_by_id, s) for s in outcome.new_section_ids],
        "actor_employee_id": current_user.employee_id,
    }

    for section_id in outcome.added_section_ids:
        db.add(
            BookingEvent(
                booking_id=booking_id,
                event_type=ROUTING_EVENT_TYPE,
                from_section_id=None,
                to_section_id=section_id,
                remarks=reason,
                event_data=snapshot,
                created_by=current_user.id,
                created_at=now,
            )
        )


def describe(db: Session, outcome: RoutingOutcome) -> dict:
    """The outcome plus the booking's resulting assignments, read back from the database rather
    than assembled from what we intended to write - so the response reflects the committed state,
    including the assignment_source of the rows that were left untouched."""
    booking = _booking_or_404(db, outcome.booking_id)
    rows = (
        db.query(BookingSectionAssignment, Section)
        .join(Section, Section.id == BookingSectionAssignment.section_id)
        .filter(BookingSectionAssignment.booking_id == outcome.booking_id)
        .order_by(Section.code)
        .all()
    )
    return {
        "booking_id": outcome.booking_id,
        "booking_source": booking.booking_source,
        "previous_section_ids": outcome.previous_section_ids,
        "new_section_ids": outcome.new_section_ids,
        "added_section_ids": outcome.added_section_ids,
        "already_assigned_section_ids": outcome.already_assigned_section_ids,
        "sections": [
            {
                "section_id": assignment.section_id,
                "section_code": section.code,
                "assignment_source": assignment.assignment_source,
                "status": assignment.status,
            }
            for assignment, section in rows
        ],
    }


# =================================================================================================
# PLANNER BOOKING CREATION
#
# Reuses booking_creation_service.create_booking entirely - the same Booking entity, the same
# CREATED event, the same Loco Master equipment-section auto-mapping, the same NO_SECTION_MAPPING
# refusal. No parallel booking table, no second creation path, no bypass of any rule a booking
# raised anywhere else must satisfy.
#
# WHAT THIS ADDS is only the planner's extra sections, applied after the auto-mapping, in the
# SAME transaction. So a Traction Motor finding still auto-maps to M35-TM (AUTO_MAPPING) and the
# planner's M2-HR and M6-HR arrive alongside it (MANUAL) - never instead of it.
#
# booking_source is MANUAL, not a new "PPIO" value. A planner raising a finding outside a stage IS
# a manual booking; overloading the source would corrupt a field that drives stage reconciliation,
# the Minor work package and Test After gating. WHO raised it is bookings.created_by, which the
# creation service already writes, and which the pool response turns into the "Added by PPIO"
# badge without a column or a migration.
# =================================================================================================

PLANNER_BOOKING_SOURCE = "MANUAL"


def create_planning_booking(
    db: Session,
    client,
    *,
    shed_visit_id: int,
    equipment_node_id: int,
    defect_type_id: int,
    description: str,
    additional_section_ids: list[int],
    current_user: User,
    reason: str | None = None,
) -> tuple[Booking, RoutingOutcome]:
    """Create a booking as a planner, then add the planner's chosen sections to it.

    One transaction. If the extra sections are invalid, the whole creation rolls back rather than
    leaving a booking the planner did not finish routing.
    """
    from app.services import booking_creation_service

    now = datetime.now(timezone.utc)

    # Validated exactly as every other creation path validates: equipment must exist and belong to
    # the visit's technology family, and the defect type must be known and active.
    visit = db.query(ShedVisit).filter(ShedVisit.id == shed_visit_id).first()
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shed visit not found")

    # Destinations are validated BEFORE the booking is written, so an invalid section cannot
    # leave a half-routed booking behind even momentarily.
    destinations = _resolve_destinations(db, additional_section_ids) if additional_section_ids else []

    booking_input = SimpleNamespace(
        equipment_node_id=equipment_node_id,
        defect_type_id=defect_type_id,
        description=description,
    )
    booking_creation_service.resolve_and_validate_bookings(db, client, [booking_input])

    booking, _assignments = booking_creation_service.create_booking(
        db,
        client,
        shed_visit_id=shed_visit_id,
        stage_id=None,
        booking_source=PLANNER_BOOKING_SOURCE,
        equipment_node_id=equipment_node_id,
        defect_type_id=defect_type_id,
        description=description,
        actor=current_user,
        now=now,
        created_event_data={
            # Provenance on the CREATED event as well as on bookings.created_by, so the audit
            # trail states it without a reader having to resolve the creator's section.
            "origin": "PLANNING",
            "actor_employee_id": current_user.employee_id,
        },
        # WHY PLANNING MAY CREATE A BOOKING FOR UNMAPPED EQUIPMENT.
        #
        # Ordinary creation refuses equipment with no Loco Master mapping (NO_SECTION_MAPPING),
        # because nobody would be responsible for the finding. That rule is about the OUTCOME -
        # a booking must never exist with zero assignments - not about where the sections came
        # from. A planner naming the responsible sections supplies exactly the information the
        # mapping would have, so the outcome is satisfied and the booking is routed.
        #
        # Used only if the mapping resolves nothing, so mapped equipment still auto-routes and
        # these sections are then added on top as usual. With no mapping and nothing selected,
        # create_booking still raises NO_SECTION_MAPPING - which is the zero-assignment guarantee,
        # kept rather than relaxed.
        unmapped_fallback_sections=destinations or None,
    )
    db.flush()  # the booking needs an id before its extra assignments can reference it

    outcome = RoutingOutcome(booking_id=booking.id)
    if destinations:
        # Read back what the creation actually produced. If the equipment mapped, this is the
        # AUTO_MAPPING set and the planner's choices are genuinely new. If it did not, the
        # planner's choices ARE this set already (they were the fallback), so the loop below adds
        # nothing and reports them as already assigned - no duplicate row, no second event.
        existing_ids = {
            a.section_id
            for a in db.query(BookingSectionAssignment)
            .filter(BookingSectionAssignment.booking_id == booking.id)
            .all()
        }
        added = [s.id for s in destinations if s.id not in existing_ids]
        already = [s.id for s in destinations if s.id in existing_ids]
        for section_id in added:
            db.add(
                BookingSectionAssignment(
                    booking_id=booking.id,
                    section_id=section_id,
                    status="OPEN",
                    assignment_source="MANUAL",
                    assigned_at=now,
                    updated_at=now,
                )
            )
        outcome = RoutingOutcome(
            booking_id=booking.id,
            previous_section_ids=sorted(existing_ids),
            new_section_ids=sorted(existing_ids | set(added)),
            added_section_ids=sorted(added),
            already_assigned_section_ids=sorted(already),
            unchanged_section_ids=sorted(existing_ids),
        )
        if added:
            sections_by_id = {
                s.id: s
                for s in db.query(Section)
                .filter(Section.id.in_(existing_ids | set(added)))
                .all()
            }
            _record_events(db, booking.id, outcome, sections_by_id, current_user, reason, now)

    db.commit()
    db.refresh(booking)
    return booking, outcome
