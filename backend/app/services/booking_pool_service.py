"""Business Rule Alignment: the global Booking Pool is now Admin-only operational visibility, not
the primary Supervisor workflow.

list_bookings() remains: it gives Admin a cross-section, cross-visit operational view (see
app/api/bookings.py's require_admin gate on GET /api/bookings). A Supervisor's primary operational
booking surface is the Section Dashboard again (app/services/section_dashboard_service.py),
scoped to their own users.section_id, working booking_section_assignments rows directly.

booking.status shown here is DERIVED from booking_section_assignments by
section_dashboard_service.recompute_booking_status() - this module never writes it directly
anymore. The booking-level start/attend/reopen mutations that used to live here (Common Booking
Pool reform) are FROZEN below (see _deprecated_mutation()): assignment-level mutation
(POST /api/section-assignments/{id}/start|attend|reopen) is the sole authoritative lifecycle API
again, operating on assignment_id, never booking_id - a writable booking-level path would create a
second, disconnected state machine alongside the assignment-derived one.

Equipment (equipment_node_id) remains mandatory, validated booking metadata - see
booking_creation_service.py - and is resolved here (via the existing Loco Master client, the same
mechanism already used for booking detail/history display) purely so Admin's global view can group
and filter by individual equipment.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
)
from app.db.models import (
    Booking,
    BookingDefectType,
    BookingSectionAssignment,
    Section,
    ShedVisit,
    ShedVisitStage,
    User,
)
from app.core.authz import is_planning_section
from app.schemas.bookings import EquipmentPathItemBrief
from app.schemas.booking_pool import (
    BookingPoolItemOut,
    BookingPoolShedVisitBrief,
    RoutedSectionBrief,
)
from app.schemas.section_dashboard import DefectTypeBrief


def _enrich_equipment(client: LocoMasterClient, node_ids: set[int]) -> dict[int, dict]:
    """Best-effort node-id -> {name, path} lookup for display/grouping. A single missing or
    unreachable node degrades that one row's equipment display to name=None/path=[] rather than
    failing the whole pool listing."""
    out: dict[int, dict] = {}
    for node_id in node_ids:
        try:
            node = client.get_equipment_node(node_id)
            out[node_id] = {
                "name": node.get("name"),
                "path": [EquipmentPathItemBrief(id=p["id"], name=p["name"]) for p in node.get("path", [])],
            }
        except (LocoMasterNotFoundError, LocoMasterUnavailableError, LocoMasterAuthError):
            continue
    return out


def _to_out(
    booking: Booking,
    shed_visit: ShedVisit,
    stage_type: str | None,
    defect_type: BookingDefectType | None,
    equipment: dict | None,
    user_names: dict[int, str],
    routed_sections: list[RoutedSectionBrief],
    section_codes: dict[int, str],
    creators: dict,
) -> BookingPoolItemOut:
    return BookingPoolItemOut(
        id=booking.id,
        status=booking.status,
        description=booking.description,
        booking_source=booking.booking_source,
        workflow_stage_type=stage_type,
        equipment_node_id=booking.equipment_node_id,
        equipment_node_name=equipment.get("name") if equipment else None,
        equipment_path=equipment.get("path", []) if equipment else [],
        routed_sections=routed_sections,
        defect_type=DefectTypeBrief(id=defect_type.id, code=defect_type.code, name=defect_type.name)
        if defect_type
        else None,
        shed_visit=BookingPoolShedVisitBrief(
            id=shed_visit.id,
            loco_number=shed_visit.loco_number,
            schedule_family=shed_visit.schedule_family,
            schedule_variant=shed_visit.schedule_variant,
        ),
        created_at=booking.created_at,
        # VESTIGIAL fields - the only remaining readers of the parent-level
        # lifecycle columns in the whole codebase. Nothing writes them any more
        # (the Common Booking Pool reform that did was superseded by the
        # assignment-based model), so every one of these resolves to None for
        # every row today. Left in place deliberately: the response contract is
        # already nullable, historical rows written before the supersession
        # still render correctly, and removing the keys would be a frontend-
        # visible API change outside this schema fix's scope. Per-section
        # start/attend detail lives on booking_section_assignments and is
        # exposed by the Section Dashboard
        # (app/services/section_dashboard_service.py).
        started_by_name=user_names.get(booking.started_by) if booking.started_by else None,
        started_by_section_code=section_codes.get(booking.started_by_section_id)
        if booking.started_by_section_id
        else None,
        started_at=booking.started_at,
        attended_by_name=user_names.get(booking.attended_by) if booking.attended_by else None,
        attended_by_section_code=section_codes.get(booking.attended_by_section_id)
        if booking.attended_by_section_id
        else None,
        attended_at=booking.attended_at,
        attendance_remarks=booking.attendance_remarks,
        # Provenance, DERIVED from bookings.created_by - see BookingPoolItemOut. There is no
        # stored flag and no PPIO booking_source.
        created_by_name=(creators.get(booking.created_by) or (None, None, False))[0],
        created_by_section_code=(creators.get(booking.created_by) or (None, None, False))[1],
        created_by_planning_section=(creators.get(booking.created_by) or (None, None, False))[2],
    )


def _creators(db: Session, user_ids: set) -> dict:
    """(name, section_code, is_planning_section) for each booking creator.

    ONE query for the whole page, joined to sections, rather than a lookup per booking. The
    planning answer comes from authz.is_planning_section - the same function every other
    section-kind decision in this application uses - so the badge can never disagree with the
    authorization that allowed the booking to be raised.
    """
    if not user_ids:
        return {}
    rows = (
        db.query(User, Section)
        .outerjoin(Section, Section.id == User.section_id)
        .filter(User.id.in_(user_ids))
        .all()
    )
    return {
        user.id: (user.name, section.code if section else None, is_planning_section(section))
        for user, section in rows
    }


def _routed_sections(db: Session, booking_ids: list[int]) -> dict[int, list[RoutedSectionBrief]]:
    """booking_id -> the sections it was ROUTED to, from booking_section_assignments.

    This is the booking's own historical routing record, not a live lookup of where the
    equipment currently maps. The two answers diverge the moment an Admin re-maps a piece of
    equipment, and for an old booking the historical one is the true one.

    One query for the whole page, joined to sections for the code/name, ordered by section code
    so a multi-section booking renders the same way every time.
    """
    if not booking_ids:
        return {}

    rows = (
        db.query(BookingSectionAssignment, Section)
        .join(Section, Section.id == BookingSectionAssignment.section_id)
        .filter(BookingSectionAssignment.booking_id.in_(booking_ids))
        .order_by(BookingSectionAssignment.booking_id, Section.code)
        .all()
    )

    out: dict[int, list[RoutedSectionBrief]] = {}
    for assignment, section in rows:
        out.setdefault(assignment.booking_id, []).append(
            RoutedSectionBrief(
                section_id=section.id,
                section_code=section.code,
                section_name=section.name,
                assignment_source=assignment.assignment_source,
                status=assignment.status,
            )
        )
    return out


def _build_out_rows(db: Session, client: LocoMasterClient, bookings: list[Booking]) -> list[BookingPoolItemOut]:
    if not bookings:
        return []

    shed_visit_ids = {b.shed_visit_id for b in bookings}
    shed_visits = {v.id: v for v in db.query(ShedVisit).filter(ShedVisit.id.in_(shed_visit_ids)).all()}

    stage_ids = {b.stage_id for b in bookings if b.stage_id is not None}
    stage_types = (
        {s.id: s.stage_type for s in db.query(ShedVisitStage).filter(ShedVisitStage.id.in_(stage_ids)).all()}
        if stage_ids
        else {}
    )

    defect_type_ids = {b.defect_type_id for b in bookings if b.defect_type_id is not None}
    defect_types = (
        {d.id: d for d in db.query(BookingDefectType).filter(BookingDefectType.id.in_(defect_type_ids)).all()}
        if defect_type_ids
        else {}
    )

    equipment_node_ids = {b.equipment_node_id for b in bookings if b.equipment_node_id is not None}
    equipment = _enrich_equipment(client, equipment_node_ids)

    routed = _routed_sections(db, [b.id for b in bookings])
    # Provenance for the "Added by PPIO" badge, resolved once for the page.
    creators = _creators(db, {b.created_by for b in bookings if b.created_by})

    user_ids = {b.started_by for b in bookings if b.started_by} | {
        b.attended_by for b in bookings if b.attended_by
    }
    user_names = {u.id: u.name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    section_ids = {b.started_by_section_id for b in bookings if b.started_by_section_id} | {
        b.attended_by_section_id for b in bookings if b.attended_by_section_id
    }
    section_codes = (
        {s.id: s.code for s in db.query(Section).filter(Section.id.in_(section_ids)).all()}
        if section_ids
        else {}
    )

    return [
        _to_out(
            b,
            shed_visits[b.shed_visit_id],
            stage_types.get(b.stage_id) if b.stage_id else None,
            defect_types.get(b.defect_type_id) if b.defect_type_id else None,
            equipment.get(b.equipment_node_id) if b.equipment_node_id else None,
            user_names,
            routed.get(b.id, []),
            section_codes,
            creators,
        )
        for b in bookings
    ]


def list_bookings(
    db: Session,
    client: LocoMasterClient,
    current_user: User,
    *,
    shed_visit_id: int | None = None,
    booking_status: str | None = None,
    booking_source: str | None = None,
    equipment_node_id: int | None = None,
) -> list[BookingPoolItemOut]:
    """Admin-only global booking pool (enforced at the route layer - see app/api/bookings.py's
    require_admin gate on GET /api/bookings). Every booking across every section/visit, unscoped -
    a Supervisor's operational visibility is the Section Dashboard instead (scoped to their own
    users.section_id via booking_section_assignments)."""
    del current_user  # authorization already enforced at the route layer; kept for signature symmetry

    query = db.query(Booking)
    if shed_visit_id is not None:
        query = query.filter(Booking.shed_visit_id == shed_visit_id)
    if booking_status is not None:
        query = query.filter(Booking.status == booking_status)
    if booking_source is not None:
        query = query.filter(Booking.booking_source == booking_source)
    if equipment_node_id is not None:
        query = query.filter(Booking.equipment_node_id == equipment_node_id)

    bookings = query.order_by(Booking.created_at.desc()).all()
    return _build_out_rows(db, client, bookings)


_DEPRECATION_MESSAGE = (
    "Booking-level workflow operations are retired. Use assignment-level operations "
    "(POST /api/section-assignments/{assignment_id}/start|attend|reopen)."
)


def _deprecated_mutation() -> HTTPException:
    """Business Rule Alignment: every booking-level mutation route (start/attend/reopen) is
    FROZEN - it must never write to bookings.status directly again, since that status is now
    derived from booking_section_assignments (see section_dashboard_service.recompute_booking_
    status) and a writable booking-level path would silently create a second, disconnected state
    machine. 410 Gone (not 409) because this isn't a state conflict on a specific resource - the
    operation itself has been permanently retired in favor of the assignment-level route."""
    return HTTPException(status_code=status.HTTP_410_GONE, detail=_DEPRECATION_MESSAGE)


def start_booking(db: Session, client: LocoMasterClient, booking_id: int, current_user: User) -> BookingPoolItemOut:
    """FROZEN - always 410, never writes. See _deprecated_mutation()."""
    del db, client, booking_id, current_user
    raise _deprecated_mutation()


def attend_booking(
    db: Session, client: LocoMasterClient, booking_id: int, remarks: str, current_user: User
) -> BookingPoolItemOut:
    """FROZEN - always 410, never writes."""
    del db, client, booking_id, remarks, current_user
    raise _deprecated_mutation()


def reopen_booking(
    db: Session, client: LocoMasterClient, booking_id: int, reason: str, current_user: User
) -> BookingPoolItemOut:
    """FROZEN - always 410, never writes."""
    del db, client, booking_id, reason, current_user
    raise _deprecated_mutation()
