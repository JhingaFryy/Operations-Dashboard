"""Orchestration behind POST /api/internal/bookings (app/api/internal.py).

This module owns validation and transaction boundary ONLY. Everything domain-specific is
delegated to the code that already owns it, so there is exactly one implementation of each rule
in this codebase, not two:

  * equipment existence + defect-type validity -> booking_creation_service.resolve_and_validate_bookings()
  * equipment -> section routing              -> equipment_service.resolve_sections(), reached via
                                                 booking_creation_service._resolve_sections_or_reject()
  * NO_SECTION_MAPPING rejection              -> booking_creation_service (same error shape as
                                                 every Dashboard-native creation flow)
  * booking + CREATED event + one AUTO_MAPPING
    assignment and AUTO_ROUTED event per
    resolved section                          -> booking_creation_service.create_booking()
  * idempotent retry semantics                -> booking_creation_service.create_booking_idempotent()

Nothing about the booking lifecycle changes here: the row is created OPEN with one OPEN
assignment per resolved section, exactly like a Test Before finding, and from that moment it
progresses through the ordinary assignment state machine
(OPEN -> IN_PROGRESS -> ATTENDED -> REOPENED -> IN_PROGRESS -> ATTENDED) with no special-casing
whatsoever. Shed Out gating (shed_out_service) sees it as an ordinary booking.

DEFENCE IN DEPTH. BL-DCMS is a semi-trusted caller: it holds the internal API key, which proves
it is BL-DCMS, and nothing more. It does not prove any particular claim in its request body is
true. So every field is re-validated here against Dashboard's own authoritative state, and the
two decisions that must never leave this application - which section(s) a booking routes to, and
whether it may be created unrouted - are made here and only here.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.clients.loco_master import LocoMasterClient
from app.db.models import BookingSectionAssignment, Section, ShedVisit, ShedVisitStage, User
from app.schemas.internal_bookings import (
    InternalBookingCreateRequest,
    InternalBookingCreateResponse,
)
from app.services import booking_creation_service, equipment_family_service
from app.services.booking_origin import CHECKSHEET_DERIVED_SOURCES, assert_internal_source_allowed
from app.services.workflow_common import assert_visit_open, error, get_visit_or_404


class _BookingInput:
    """Adapter to booking_creation_service.BookingInputLike, so the shared pre-write validator
    (equipment exists, defect type known and active) is reused verbatim for a batch of one rather
    than reimplemented for this entry point."""

    def __init__(self, equipment_node_id: int, defect_type_id: int, remarks: str):
        self.equipment_node_id = equipment_node_id
        self.defect_type_id = defect_type_id
        self.remarks = remarks


def _resolve_actor(db: Session, employee_id: str | None) -> User | None:
    """Resolves the acting technician from users.employee_id - the identifier both systems
    already share, since BL-DCMS and Operations Dashboard read the same `users` table.

    Omitted -> None (created_by NULL). Supplied but unknown -> rejected: silently attributing a
    booking to nobody because the caller sent an identity Dashboard cannot recognise would hide a
    real integration fault. is_active is NOT required: a technician may legitimately be
    deactivated between raising a defect and a later retry succeeding, and the booking is still
    theirs. Authentication of the human already happened at BL-DCMS's own JWT boundary; this
    lookup is attribution, not authorization."""
    if employee_id is None:
        return None
    user = db.query(User).filter(User.employee_id == employee_id).first()
    if user is None:
        raise error(
            422,
            "UNKNOWN_TECHNICIAN",
            "The supplied technician_employee_id is not a known user.",
            technician_employee_id=employee_id,
        )
    return user


def _resolve_stage_id(db: Session, visit: ShedVisit, booking_source: str) -> int | None:
    """Links the booking to the visit's own stage row when - and only when - its booking_source
    literally names a Minor-schedule workflow stage that exists on this visit. This is the same
    literal booking_source == stage_type correspondence
    checksheet_stage_reconciliation_service._stage_bookings_ready() already uses; it is NOT a new
    rule, and it does not change any gate (that service selects bookings by booking_source, never
    by stage_id).

    LOG_BOOK, MANUAL, TRIP_INSPECTION and GENERAL_CHECKING name no stage_type, so they get NULL -
    exactly like Shed In's own Log Book bookings (shed_visit_service.shed_in passes stage_id=None)."""
    stage = (
        db.query(ShedVisitStage)
        .filter(
            ShedVisitStage.shed_visit_id == visit.id,
            ShedVisitStage.stage_type == booking_source,
        )
        .first()
    )
    return stage.id if stage is not None else None


def _assert_checksheet_context(db: Session, visit: ShedVisit, payload: InternalBookingCreateRequest) -> None:
    """A TEST_BEFORE / TEST_AFTER booking must come from a real checksheet of that stage: it names the
    BL-DCMS checksheet it was raised from, and the visit is a Minor schedule that actually has that
    workflow stage. The source string alone is never enough."""
    if payload.checksheet_id is None:
        raise error(
            422,
            "CHECKSHEET_LINK_REQUIRED",
            f"A {payload.booking_source} booking must identify the checksheet it was raised from.",
        )
    if visit.schedule_family != "MINOR":
        raise error(
            409,
            "STAGE_NOT_ON_VISIT",
            f"Shed visit {visit.id} is not a Minor schedule, so it has no {payload.booking_source} stage.",
        )
    stage = (
        db.query(ShedVisitStage)
        .filter(ShedVisitStage.shed_visit_id == visit.id, ShedVisitStage.stage_type == payload.booking_source)
        .first()
    )
    if stage is None:
        raise error(
            409,
            "STAGE_NOT_ON_VISIT",
            f"Shed visit {visit.id} has no {payload.booking_source} stage.",
        )


def _section_codes_for(db: Session, booking_id: int) -> list[str]:
    rows = (
        db.query(Section.code)
        .join(BookingSectionAssignment, BookingSectionAssignment.section_id == Section.id)
        .filter(BookingSectionAssignment.booking_id == booking_id)
        .order_by(Section.code)
        .all()
    )
    return [r.code for r in rows]


def create_internal_booking(
    db: Session,
    client: LocoMasterClient,
    payload: InternalBookingCreateRequest,
    bldcms_client=None,
) -> InternalBookingCreateResponse:
    # Booking ownership: checked first, before any lookup - a request whose origin is wrong is
    # refused whatever else it claims.
    assert_internal_source_allowed(payload.booking_source)

    visit = get_visit_or_404(db, payload.shed_visit_id)

    # Shed Out gating is untouched by this route, and this is the check that keeps it that way: a
    # CLOSED visit accepts no new booking, exactly as every other finding-creation path already
    # refuses (workflow_common.assert_visit_open, same VISIT_CLOSED 409). A booking arriving after
    # Shed Out must never be able to retroactively re-block a departed locomotive.
    assert_visit_open(visit)

    if payload.booking_source in CHECKSHEET_DERIVED_SOURCES:
        _assert_checksheet_context(db, visit, payload)

    actor = _resolve_actor(db, payload.technician_employee_id)

    # Reuses the shared pre-write validator: equipment node exists in Loco Master (or 422
    # EQUIPMENT_NOT_FOUND / 502 if Loco Master is unreachable), defect type known and active (or
    # 422 UNKNOWN_DEFECT_TYPE). Deliberately run BEFORE the write transaction opens, same as
    # every Dashboard-native flow.
    booking_input = _BookingInput(
        equipment_node_id=payload.equipment_node_id,
        defect_type_id=payload.defect_type_id,
        remarks=payload.description,
    )

    # An idempotent retry must not be rejected just because the equipment node or defect type has
    # since been retired - the booking it refers to already exists and this call only has to
    # report it. So the pre-check is skipped when the key already resolves to a stored booking;
    # create_booking_idempotent() still compares the payload and still raises
    # IDEMPOTENCY_CONFLICT on a genuine mismatch.
    already_stored = booking_creation_service.find_by_client_booking_id(
        db, payload.client_booking_id
    )
    if already_stored is None:
        # The equipment must belong to THIS visit's locomotive's technology family, derived here
        # from the visit's own loco_number - not from anything in the request. BL-DCMS holding the
        # internal API key proves it is BL-DCMS, not that the node it forwarded is the right
        # technology; a stale or outdated Android app that sends a Conventional node for a 3-phase
        # locomotive is refused here even though its own screen filtered the list.
        family_code = equipment_family_service.family_code_for_loco_number(
            bldcms_client, visit.loco_number
        )
        booking_creation_service.resolve_and_validate_bookings(
            db, client, [booking_input], family_code=family_code
        )

    stage_id = _resolve_stage_id(db, visit, payload.booking_source)
    now = datetime.now(timezone.utc)

    # Audit-only breadcrumb, written once with the CREATED event and never on a retry, so a
    # booking can always be traced back to the BL-DCMS performa it was raised from. Carried on the
    # existing booking_events.event_data column rather than as a new `bookings` column - it is
    # provenance, not booking state, and adding a column for it was not authorized.
    created_event_data = {
        "origin": "BLDCMS_CHECKSHEET",
        "client_booking_id": payload.client_booking_id,
        "workflow_stage_type": payload.booking_source if payload.booking_source in CHECKSHEET_DERIVED_SOURCES else None,
    }
    if payload.checksheet_id is not None:
        created_event_data["checksheet_id"] = payload.checksheet_id

    try:
        booking, assignments_created, created = booking_creation_service.create_booking_idempotent(
            db,
            client,
            client_booking_id=payload.client_booking_id,
            shed_visit_id=payload.shed_visit_id,
            stage_id=stage_id,
            booking_source=payload.booking_source,
            equipment_node_id=payload.equipment_node_id,
            defect_type_id=payload.defect_type_id,
            description=payload.description,
            actor=actor,
            now=now,
            created_event_data=created_event_data,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(booking)

    return InternalBookingCreateResponse(
        booking_id=booking.id,
        client_booking_id=payload.client_booking_id,
        status=booking.status,
        booking_source=booking.booking_source,
        equipment_node_id=booking.equipment_node_id,
        section_codes=_section_codes_for(db, booking.id),
        created=created,
        section_assignments_created=assignments_created,
    )
