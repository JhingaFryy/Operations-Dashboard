"""Shared booking-creation logic used by every flow that creates bookings
(Shed In's Log Book bookings, Test Before/Schedule Inspection/Test After
findings, and any future MANUAL booking flow). Extracted from what was
originally inline in shed_visit_service.shed_in (Phase 2B) so this logic
isn't duplicated per-flow — see the Phase 3A report.

Business Rule Alignment: bookings are routed to sections again via Loco Master's
equipment-section mapping (most-specific-mapping-wins - see
app/services/equipment_service.resolve_sections(), unchanged, already implements the
exact-node-then-ancestor-walk algorithm this restores). A booking is never created
unrouted: if zero sections resolve for its equipment, the whole creation is rejected
with a machine-readable NO_SECTION_MAPPING error - see _resolve_sections_or_reject()
below. One booking_section_assignments row is created per resolved section
(assignment_source="AUTO_MAPPING"), plus an AUTO_ROUTED booking event recording where it
went. This applies uniformly to every booking_source (LOG_BOOK/TEST_BEFORE/
SCHEDULE_INSPECTION/TEST_AFTER/MANUAL) - routing lives in this one shared function, not
duplicated per flow.

Two-phase by design, unchanged from the original:

  1. resolve_and_validate_bookings() — pure validation, safe to call
     before opening a DB write transaction. Validates each booking's
     equipment exists and every defect type is known/active. Raises a
     structured 422 (booking_index-aware) on the first problem — callers
     reject the whole batch together, never partially. Section-mapping
     resolution is NOT done here (it requires the same Loco Master call
     create_booking() would need anyway, and section availability can't
     change usefully between this pre-check and the write a moment
     later) — it happens inside create_booking() itself, inside the
     caller's own transaction, so a NO_SECTION_MAPPING failure rolls back
     the whole batch exactly like any other exception raised there.

  2. create_booking() — the actual write (one booking + its CREATED
     event + section-mapping resolution + one assignment per resolved
     section + an AUTO_ROUTED event). Does not commit — it runs inside
     the caller's own transaction, so the caller controls the
     commit/rollback boundary (e.g. alongside a shed_visits or
     shed_visit_stages mutation in the same atomic unit).
"""

from datetime import datetime
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.clients.loco_master import LocoMasterClient
from app.db.models import Booking, BookingDefectType, BookingEvent, BookingSectionAssignment, Section, User
from app.services import equipment_family_service, equipment_service


class BookingInputLike(Protocol):
    equipment_node_id: int
    defect_type_id: int
    remarks: str


def _error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def resolve_and_validate_bookings(
    db: Session,
    client: LocoMasterClient,
    bookings_in: list[BookingInputLike],
    family_code: str | None = None,
) -> None:
    """Validates every booking in the batch before any write happens.
    Raises on the first invalid booking — equipment not found, equipment
    from the wrong technology family, or an unknown/inactive defect type.
    Section-mapping resolution happens later, inside create_booking()
    itself (see module docstring) — not here.

    `family_code` is the equipment family the visit's locomotive allows,
    derived server-side by equipment_family_service — never a value the
    caller's client supplied. When given, a node from any other family is
    refused here, so both booking channels (Dashboard Log Book and the
    BL-DCMS checksheet route) enforce it through this one implementation.
    Omitted only by the Dashboard-native finding flows that do not yet
    resolve a family; those behave exactly as before."""
    for index, booking_in in enumerate(bookings_in):
        try:
            if family_code is not None:
                equipment_family_service.assert_node_in_family(
                    client, booking_in.equipment_node_id, family_code
                )
            else:
                equipment_service.get_node_or_404(client, booking_in.equipment_node_id)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise _error(
                    422,
                    "EQUIPMENT_NOT_FOUND",
                    "The selected equipment could not be found.",
                    booking_index=index,
                    equipment_node_id=booking_in.equipment_node_id,
                )
            raise  # already a 502 from equipment_service for Loco Master unavailability

    defect_type_ids = {b.defect_type_id for b in bookings_in}
    if defect_type_ids:
        rows = (
            db.query(BookingDefectType)
            .filter(BookingDefectType.id.in_(defect_type_ids), BookingDefectType.is_active.is_(True))
            .all()
        )
        valid_defect_type_ids = {d.id for d in rows}
        for index, booking_in in enumerate(bookings_in):
            if booking_in.defect_type_id not in valid_defect_type_ids:
                raise _error(
                    422,
                    "UNKNOWN_DEFECT_TYPE",
                    "Unknown or inactive defect type.",
                    booking_index=index,
                    defect_type_id=booking_in.defect_type_id,
                )


def _resolve_sections_or_reject(
    db: Session,
    client: LocoMasterClient,
    equipment_node_id: int,
    unmapped_fallback_sections: list[Section] | None = None,
) -> tuple[list[Section], str]:
    """Most-specific-mapping-wins (see equipment_service.resolve_sections — unchanged, this just
    calls it): exact node mapping wins outright if present; otherwise the first ancestor with an
    active mapping wins, using ALL of that single level's sections, never unioned across levels.

    Returns (sections, assignment_source) — the source differs between the two outcomes, and
    saying so here keeps the caller from having to guess.

    `unmapped_fallback_sections` IS USED ONLY WHEN AUTO-MAPPING RESOLVES NOTHING, and it changes
    nothing for any caller that does not pass it (every existing one). It exists because planning
    can legitimately raise a booking for equipment Loco Master has no mapping for - the planner
    states the responsible sections themselves, which is exactly the information the mapping would
    otherwise have supplied. It CANNOT override or supplement a mapping that does resolve: if the
    equipment maps, the mapped sections win outright and the fallback is ignored, so a caller
    cannot use it to launder a manual choice into an auto-routing or to bypass the mapping.

    "A booking is never created unrouted" is unchanged. With no mapping AND no fallback this still
    raises NO_SECTION_MAPPING, which is what every ordinary flow continues to get.
    """
    resolved = equipment_service.resolve_sections(client, equipment_node_id)
    if not resolved.section_codes:
        if unmapped_fallback_sections:
            # MANUAL, not AUTO_MAPPING: nothing was auto-mapped. Recording these as AUTO_MAPPING
            # would claim Loco Master routed the work when a person did.
            return list(unmapped_fallback_sections), "MANUAL"
        raise _error(
            422,
            "NO_SECTION_MAPPING",
            "No equipment-section mapping could be resolved for this equipment; the booking "
            "cannot be routed to any section.",
            equipment_node_id=equipment_node_id,
        )

    sections = db.query(Section).filter(Section.code.in_(resolved.section_codes)).all()
    if not sections:
        # Loco Master returned mapped section code(s) this Dashboard doesn't recognize — treat the
        # same as unrouted rather than silently creating zero assignments. A supplied fallback
        # rescues this case too, for the same reason it rescues the no-mapping case.
        if unmapped_fallback_sections:
            return list(unmapped_fallback_sections), "MANUAL"
        raise _error(
            422,
            "NO_SECTION_MAPPING",
            "The mapped section code(s) for this equipment are not recognized by this Dashboard.",
            equipment_node_id=equipment_node_id,
            section_codes=resolved.section_codes,
        )
    return sections, "AUTO_MAPPING"


def create_booking(
    db: Session,
    client: LocoMasterClient,
    *,
    shed_visit_id: int,
    stage_id: int | None,
    booking_source: str,
    equipment_node_id: int,
    defect_type_id: int,
    description: str,
    actor: User | None,
    now: datetime,
    client_booking_id: str | None = None,
    created_event_data: dict | None = None,
    unmapped_fallback_sections: list[Section] | None = None,
) -> tuple[Booking, int]:
    """Creates one booking + its CREATED event, resolves the equipment's mapped section(s)
    (rejecting the whole creation with NO_SECTION_MAPPING if none resolve — see
    _resolve_sections_or_reject above), and creates one booking_section_assignments row per
    resolved section (assignment_source="AUTO_MAPPING") plus an AUTO_ROUTED event recording where
    it went. Runs inside the caller's already-open transaction (does not commit or catch
    exceptions — the caller owns that boundary; a NO_SECTION_MAPPING HTTPException raised here
    propagates to the caller's own except/rollback, same as any other exception). Equipment/defect
    must already be validated via resolve_and_validate_bookings(). Returns
    (booking, assignments_created_count).

    `unmapped_fallback_sections` is for the planning path ONLY and defaults to None, so every
    existing caller behaves exactly as before - including the NO_SECTION_MAPPING rejection. When
    supplied, it is used ONLY if auto-mapping resolves nothing, and those assignments are written
    MANUAL with a FORWARDED event rather than AUTO_MAPPING/AUTO_ROUTED, because a person routed
    them. It can never override a mapping that does resolve.
    """
    sections, assignment_source = _resolve_sections_or_reject(
        db, client, equipment_node_id, unmapped_fallback_sections
    )
    actor_id = actor.id if actor is not None else None

    booking = Booking(
        shed_visit_id=shed_visit_id,
        stage_id=stage_id,
        booking_source=booking_source,
        description=description,
        equipment_node_id=equipment_node_id,
        defect_type_id=defect_type_id,
        status="OPEN",
        client_booking_id=client_booking_id,
        created_by=actor_id,
        created_at=now,
        updated_by=actor_id,
        updated_at=now,
    )
    db.add(booking)
    db.flush()

    # created_event_data is provenance only (who/what originated this booking outside Dashboard),
    # carried on the existing booking_events.event_data JSON column rather than as new columns on
    # `bookings`. None for every Dashboard-native flow, exactly as before.
    db.add(
        BookingEvent(
            booking_id=booking.id,
            event_type="CREATED",
            event_data=created_event_data,
            created_by=actor_id,
            created_at=now,
        )
    )

    for section in sections:
        db.add(
            BookingSectionAssignment(
                booking_id=booking.id,
                section_id=section.id,
                assignment_source=assignment_source,
                status="OPEN",
                assigned_by=actor_id,
                assigned_at=now,
                updated_at=now,
            )
        )
        db.add(
            BookingEvent(
                booking_id=booking.id,
                # AUTO_ROUTED only when it genuinely was. A section supplied because the
                # equipment has no mapping was routed by a person, and FORWARDED is the event
                # type this application already uses for that - both are permitted by
                # chk_booking_event_type, so no DDL and no new vocabulary.
                event_type="AUTO_ROUTED" if assignment_source == "AUTO_MAPPING" else "FORWARDED",
                to_section_id=section.id,
                created_by=actor_id,
                created_at=now,
            )
        )

    return booking, len(sections)


# --- Idempotent creation for externally-originated bookings ------------------------------------
#
# Used only by POST /api/internal/bookings (app/api/internal.py). Every Dashboard-native flow
# continues to call create_booking() directly and leaves client_booking_id NULL.
#
# The logical payload compared on a retry. Deliberately excludes anything the server derives or
# stamps itself (id, status, created_at, created_by, stage_id) - a retry that differs only in
# when it arrived, or in which technician's JWT relayed it, is still the same booking. It also
# excludes `description` normalization beyond a strip: the remarks a technician typed are part of
# the booking's identity, so a retry claiming DIFFERENT remarks under the same key is a genuine
# conflict, not a match.
_IDEMPOTENT_IDENTITY_FIELDS = (
    "shed_visit_id",
    "booking_source",
    "equipment_node_id",
    "defect_type_id",
    "description",
)


def _identity_of(booking: Booking) -> dict:
    return {
        "shed_visit_id": booking.shed_visit_id,
        "booking_source": booking.booking_source,
        "equipment_node_id": booking.equipment_node_id,
        "defect_type_id": booking.defect_type_id,
        "description": (booking.description or "").strip(),
    }


def find_by_client_booking_id(db: Session, client_booking_id: str) -> Booking | None:
    return db.query(Booking).filter(Booking.client_booking_id == client_booking_id).first()


def create_booking_idempotent(
    db: Session,
    client: LocoMasterClient,
    *,
    client_booking_id: str,
    shed_visit_id: int,
    stage_id: int | None,
    booking_source: str,
    equipment_node_id: int,
    defect_type_id: int,
    description: str,
    actor: User | None,
    now: datetime,
    created_event_data: dict | None = None,
) -> tuple[Booking, int, bool]:
    """Creates a booking keyed by a stable client-generated `client_booking_id`, or returns the
    one that key already created.

    Returns (booking, assignments_created_count, created). `created` is False when an existing
    booking was matched - in that case NOTHING is written: no second booking, no second
    booking_events row, no second booking_section_assignments row, and the existing booking is
    not mutated in any way.

    A retry carrying the same key but a CONFLICTING logical payload raises 409
    IDEMPOTENCY_CONFLICT rather than either creating a duplicate or silently overwriting what is
    stored. The stored booking always wins; the newer claim is rejected.

    Creation delegates to create_booking() above - the exact same sequence (section resolution
    via equipment_service.resolve_sections, NO_SECTION_MAPPING rejection, booking + CREATED event
    + one AUTO_MAPPING assignment and AUTO_ROUTED event per resolved section). There is no second
    implementation of routing here. Like create_booking(), this does not commit: the caller owns
    the transaction boundary, so a NO_SECTION_MAPPING or IDEMPOTENCY_CONFLICT rolls the whole
    thing back."""
    existing = find_by_client_booking_id(db, client_booking_id)
    if existing is not None:
        incoming = {
            "shed_visit_id": shed_visit_id,
            "booking_source": booking_source,
            "equipment_node_id": equipment_node_id,
            "defect_type_id": defect_type_id,
            "description": (description or "").strip(),
        }
        stored = _identity_of(existing)
        differing = [f for f in _IDEMPOTENT_IDENTITY_FIELDS if stored[f] != incoming[f]]
        if differing:
            raise _error(
                status.HTTP_409_CONFLICT,
                "IDEMPOTENCY_CONFLICT",
                "A different booking already exists for this client_booking_id; the stored "
                "booking was not modified.",
                client_booking_id=client_booking_id,
                booking_id=existing.id,
                conflicting_fields=sorted(differing),
            )
        return existing, 0, False

    booking, assignments = create_booking(
        db,
        client,
        shed_visit_id=shed_visit_id,
        stage_id=stage_id,
        booking_source=booking_source,
        equipment_node_id=equipment_node_id,
        defect_type_id=defect_type_id,
        description=description,
        actor=actor,
        now=now,
        client_booking_id=client_booking_id,
        created_event_data=created_event_data,
    )
    return booking, assignments, True
