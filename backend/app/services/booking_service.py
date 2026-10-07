from datetime import datetime, time, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.ist_time import ist_day_bounds, ist_today
from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
)
from app.db.models import (
    Booking,
    BookingDefectType,
    BookingEvent,
    BookingSectionAssignment,
    Section,
    ShedVisit,
    User,
)
from app.schemas.bookings import (
    BookingAssignmentDetail,
    BookingDetailOut,
    BookingHistoryEventOut,
    EquipmentPathItemBrief,
    SectionSummaryOut,
)
from app.schemas.section_dashboard import DefectTypeBrief, SectionAssignmentOut, ShedVisitBrief

PENDING_ASSIGNMENT_STATUSES = ("OPEN", "IN_PROGRESS", "REOPENED")


def _get_booking_or_404(db: Session, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    return booking


def assert_booking_visit_open(db: Session, booking_id: int) -> None:
    """Phase 3E.1: once a shed_visit is CLOSED (Shed Out), no booking or
    section-assignment mutation may occur against it — this is the shared
    check for every mutation route that reaches a booking indirectly
    (section start/attend/reopen, manual section addition), mirroring
    app.services.workflow_common.assert_visit_open()'s equivalent guard
    for the stage/finding-creation routes. Reads (booking/history detail,
    section dashboard listing) are untouched — historical CLOSED visits
    stay fully readable. Admin does not bypass this: closure is a hard
    stop for every actor, not a permission gate."""
    shed_visit_status = (
        db.query(ShedVisit.status)
        .join(Booking, Booking.shed_visit_id == ShedVisit.id)
        .filter(Booking.id == booking_id)
        .scalar()
    )
    if shed_visit_status == "CLOSED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "VISIT_CLOSED",
                "message": "This shed visit is closed; no further booking or assignment mutations are permitted.",
            },
        )


_ADD_SECTION_DEPRECATION_MESSAGE = (
    "Section assignments are legacy historical records. Use booking-level "
    "workflow operations (POST /api/bookings/{booking_id}/start|attend|reopen)."
)


def add_section(
    db: Session, booking_id: int, section_id: int, reason: str, current_user: User
) -> SectionAssignmentOut:
    """FROZEN (Booking Pool Hardening): this used to add an additional
    booking_section_assignments row. A forward/add-section-style mutation is
    exactly the kind of legacy operation that could masquerade as an
    alternate booking workflow, so it is retired the same way the
    section_dashboard_service start/attend/reopen mutations are - always 410,
    never writes. booking_section_assignments stays fully readable
    (get_booking_detail below) for historical/audit purposes."""
    del db, booking_id, section_id, reason, current_user
    raise HTTPException(status_code=status.HTTP_410_GONE, detail=_ADD_SECTION_DEPRECATION_MESSAGE)


def get_booking_history(
    db: Session, booking_id: int, current_user: User
) -> list[BookingHistoryEventOut]:
    """Common Booking Pool reform: history is visible to any authenticated Dashboard user
    (Admin or Supervisor) - no longer gated by an assignment in the Supervisor's own section,
    which would otherwise make history unreadable for any booking created after this reform
    (new bookings get no booking_section_assignments rows at all)."""
    del current_user  # visibility is global; dashboard access already enforced at the route layer
    _get_booking_or_404(db, booking_id)

    events = (
        db.query(BookingEvent)
        .filter(BookingEvent.booking_id == booking_id)
        .order_by(BookingEvent.created_at, BookingEvent.id)
        .all()
    )

    user_ids = {e.created_by for e in events if e.created_by}
    user_names = (
        {u.id: u.name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    )

    section_ids = {e.from_section_id for e in events if e.from_section_id} | {
        e.to_section_id for e in events if e.to_section_id
    }
    section_codes = (
        {s.id: s.code for s in db.query(Section).filter(Section.id.in_(section_ids)).all()}
        if section_ids
        else {}
    )

    return [
        BookingHistoryEventOut(
            id=e.id,
            event_type=e.event_type,
            created_at=e.created_at,
            actor_name=user_names.get(e.created_by) if e.created_by else None,
            remarks=e.remarks,
            from_section_code=section_codes.get(e.from_section_id) if e.from_section_id else None,
            to_section_code=section_codes.get(e.to_section_id) if e.to_section_id else None,
            event_data=e.event_data,
        )
        for e in events
    ]


def get_booking_detail(
    db: Session, client: LocoMasterClient, booking_id: int, current_user: User
) -> BookingDetailOut:
    """Common Booking Pool reform: detail is visible to any authenticated Dashboard user - see
    get_booking_history's docstring for why the old assignment-based Supervisor gate was
    removed."""
    del current_user  # visibility is global; dashboard access already enforced at the route layer
    booking = _get_booking_or_404(db, booking_id)

    shed_visit = db.get(ShedVisit, booking.shed_visit_id)
    defect_type = db.get(BookingDefectType, booking.defect_type_id) if booking.defect_type_id else None

    equipment_name: str | None = None
    equipment_path: list[EquipmentPathItemBrief] = []
    if booking.equipment_node_id is not None:
        try:
            node = client.get_equipment_node(booking.equipment_node_id)
            equipment_name = node.get("name")
            equipment_path = [
                EquipmentPathItemBrief(id=p["id"], name=p["name"]) for p in node.get("path", [])
            ]
        except (LocoMasterNotFoundError, LocoMasterUnavailableError, LocoMasterAuthError):
            pass

    assignments = (
        db.query(BookingSectionAssignment)
        .filter(BookingSectionAssignment.booking_id == booking_id)
        .order_by(BookingSectionAssignment.assigned_at)
        .all()
    )
    section_ids = {a.section_id for a in assignments}
    sections = {s.id: s for s in db.query(Section).filter(Section.id.in_(section_ids)).all()} if section_ids else {}
    user_ids = {a.started_by for a in assignments if a.started_by} | {
        a.attended_by for a in assignments if a.attended_by
    }
    user_names = (
        {u.id: u.name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    )

    return BookingDetailOut(
        id=booking.id,
        status=booking.status,
        description=booking.description,
        booking_source=booking.booking_source,
        equipment_node_id=booking.equipment_node_id,
        equipment_node_name=equipment_name,
        equipment_path=equipment_path,
        defect_type=DefectTypeBrief(id=defect_type.id, code=defect_type.code, name=defect_type.name)
        if defect_type
        else None,
        shed_visit=ShedVisitBrief(
            id=shed_visit.id,
            loco_number=shed_visit.loco_number,
            schedule_family=shed_visit.schedule_family,
            schedule_variant=shed_visit.schedule_variant,
            arrival_condition=shed_visit.arrival_condition,
        ),
        assignments=[
            BookingAssignmentDetail(
                id=a.id,
                section_id=a.section_id,
                section_code=sections[a.section_id].code,
                assignment_source=a.assignment_source,
                status=a.status,
                assigned_at=a.assigned_at,
                started_at=a.started_at,
                started_by_name=user_names.get(a.started_by) if a.started_by else None,
                attended_at=a.attended_at,
                attended_by_name=user_names.get(a.attended_by) if a.attended_by else None,
                attendance_remarks=a.attendance_remarks,
            )
            for a in assignments
        ],
    )


def get_section_summary(db: Session, section_id: int) -> SectionSummaryOut:
    section = db.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")

    counts = dict(
        db.query(BookingSectionAssignment.status, func.count(BookingSectionAssignment.id))
        .filter(
            BookingSectionAssignment.section_id == section_id,
            BookingSectionAssignment.status.in_(("OPEN", "IN_PROGRESS", "REOPENED")),
        )
        .group_by(BookingSectionAssignment.status)
        .all()
    )

    # Bounds computed in Python (rather than a SQL date() function) so the exact same query works
    # identically on SQLite (tests) and Postgres (RDCMS) - and derived for the ASIA/KOLKATA
    # calendar day, not the UTC one: a 00:30 IST attendance belongs to that IST day, which a UTC
    # day boundary would push back to the previous day. Storage stays UTC (app/core/ist_time.py).
    today = ist_today()
    day_start, day_end = ist_day_bounds(today)
    attended_today = (
        db.query(func.count(BookingSectionAssignment.id))
        .filter(
            BookingSectionAssignment.section_id == section_id,
            BookingSectionAssignment.status == "ATTENDED",
            BookingSectionAssignment.attended_at >= day_start,
            BookingSectionAssignment.attended_at < day_end,
        )
        .scalar()
        or 0
    )

    return SectionSummaryOut(
        open=counts.get("OPEN", 0),
        in_progress=counts.get("IN_PROGRESS", 0),
        attended_today=attended_today,
        reopened=counts.get("REOPENED", 0),
    )
