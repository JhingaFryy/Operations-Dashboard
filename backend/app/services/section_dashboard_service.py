"""Business Rule Alignment: booking_section_assignments restored as the operational per-section
state.

A booking can have multiple assignments (one per section its equipment routes to - see
booking_creation_service.py's most-specific-mapping-wins routing). Each mapped section
independently works its own assignment through:

    OPEN       -> IN_PROGRESS   (start)
    REOPENED   -> IN_PROGRESS   (start)
    IN_PROGRESS -> ATTENDED     (attend)
    ATTENDED   -> REOPENED      (reopen, Admin only)

Direct OPEN -> ATTENDED or REOPENED -> ATTENDED transitions remain forbidden - every assignment
must pass through IN_PROGRESS. Booking.status is no longer an independent lifecycle - it is
DERIVED from this assignment set on every mutation by recompute_booking_status() (the single
central place that formula lives - see that function below; every mutation here calls it):

    any REOPENED               -> booking.status = REOPENED
    all ATTENDED                -> booking.status = ATTENDED
    any IN_PROGRESS             -> booking.status = IN_PROGRESS
    otherwise                   -> booking.status = OPEN

Section boundary: Admin may operate/view any section's assignments; a Supervisor may only
operate/view their own users.section_id (see _assert_can_operate_section). This is the Dashboard's
primary operational booking surface for a Supervisor again - see the global Booking Pool
(app/services/booking_pool_service.py), which is now Admin-only read visibility.

Concurrency: start/attend/reopen each row-lock the target assignment (SELECT ... FOR UPDATE) and
recheck its status inside that lock before writing, mirroring the same established pattern used
throughout this codebase (shed_out_service.shed_out(), the old booking-level mutations this
replaces). CLOSED-visit mutations are rejected the same way every other mutation route already
does (assert_booking_visit_open).
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.authz import is_planning_user
from app.domain.booking_resolution import aggregate_booking_status
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
from app.schemas.section_dashboard import (
    BookingBrief,
    DefectTypeBrief,
    SectionAssignmentOut,
    ShedVisitBrief,
)
from app.services.auth_service import ADMIN_ROLE
from app.services.booking_service import assert_booking_visit_open

_START_ALLOWED_FROM = {"OPEN", "REOPENED"}
_ATTEND_ALLOWED_FROM = {"IN_PROGRESS"}
_REOPEN_ALLOWED_FROM = {"ATTENDED"}


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _assert_can_operate_section(current_user: User, section_id: int) -> None:
    """The backend's authoritative section boundary. Admin bypasses this
    entirely (user.role == "Admin" is sufficient — never gated by
    users.section_id, dashboard_access flags, or section ownership).
    Supervisor may only operate their own section, and only if they have
    one at all; there is no frontend-only version of this check.

    A PLANNING SECTION HAS NO SECTION WORK QUEUE AT ALL. PPIO is not a work-performing
    destination - nothing is ever routed to it - so there is no such thing as "PPIO's
    assignments", and an account in a planning section is refused here rather than being shown an
    empty dashboard for a section that can never have work. Checked before the ownership test
    below, because the refusal is about the KIND of section, not about which one: a planner naming
    their own section id must be refused just as firmly as one naming somebody else's.

    Hiding the nav item is not sufficient and is not what this relies on - a planner typing
    /section-dashboard reaches these same routes.
    """
    if current_user.role == ADMIN_ROLE:
        return
    if is_planning_user(current_user):
        raise _forbidden(
            "A planning section has no section work queue. Planning routes work to the sections "
            "that perform it - see the global Booking Pool."
        )
    if current_user.section_id is None:
        raise _forbidden("No section is assigned to this account.")
    if current_user.section_id != section_id:
        raise _forbidden("You may only operate your own section's assignments.")


def _get_section_or_404(db: Session, section_code: str) -> Section:
    section = db.query(Section).filter(Section.code == section_code).first()
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
    return section


def _get_assignment_or_404(db: Session, assignment_id: int) -> BookingSectionAssignment:
    assignment = db.get(BookingSectionAssignment, assignment_id)
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return assignment


def _lock_assignment_or_404(db: Session, assignment_id: int) -> BookingSectionAssignment:
    assignment = (
        db.query(BookingSectionAssignment)
        .filter(BookingSectionAssignment.id == assignment_id)
        .with_for_update()
        .one_or_none()
    )
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return assignment


def recompute_booking_status(db: Session, booking_id: int) -> None:
    """The single authoritative place parent Booking.status is derived from its
    booking_section_assignments rows. Every assignment lifecycle mutation below calls this
    immediately after writing its own status change - there is exactly one parent-state-derivation
    formula in this codebase, never duplicated at each call site.

        any REOPENED    -> REOPENED
        all ATTENDED     -> ATTENDED   (only once every assignment - and there is at least one -
                                         is ATTENDED; a booking with zero assignments is never
                                         reported ATTENDED here)
        any IN_PROGRESS  -> IN_PROGRESS
        otherwise        -> OPEN

    Flushes first so the query below sees the caller's own just-set (but not yet committed)
    assignment status change, regardless of the session's autoflush setting.
    """
    db.flush()
    assignments = (
        db.query(BookingSectionAssignment.status)
        .filter(BookingSectionAssignment.booking_id == booking_id)
        .all()
    )
    statuses = [row[0] for row in assignments]

    booking = db.get(Booking, booking_id)
    if booking is None:
        return

    # The formula itself now lives in app/domain/booking_resolution.py, because the Shed Out
    # blocker display and the shed-visit pending count need the SAME answer and three hand-copied
    # versions of it would drift. This remains the only place the result is WRITTEN.
    booking.status = aggregate_booking_status(statuses)


def _enrich_equipment_names(client: LocoMasterClient, node_ids: set[int]) -> dict[int, str]:
    """Best-effort node-id -> name lookup for display. A single missing or
    unreachable node degrades that one row's name to None rather than
    failing the whole dashboard — this is display enrichment, not a
    correctness-critical read."""
    names: dict[int, str] = {}
    for node_id in node_ids:
        try:
            node = client.get_equipment_node(node_id)
            names[node_id] = node.get("name")
        except (LocoMasterNotFoundError, LocoMasterUnavailableError, LocoMasterAuthError):
            continue
    return names


def _to_out(
    assignment: BookingSectionAssignment,
    booking: Booking,
    shed_visit: ShedVisit,
    section: Section,
    defect_type: BookingDefectType | None,
    equipment_name: str | None,
    user_names: dict[int, str],
) -> SectionAssignmentOut:
    return SectionAssignmentOut(
        id=assignment.id,
        booking_id=booking.id,
        section_id=section.id,
        section_code=section.code,
        status=assignment.status,
        assigned_at=assignment.assigned_at,
        started_at=assignment.started_at,
        started_by_name=user_names.get(assignment.started_by) if assignment.started_by else None,
        attended_at=assignment.attended_at,
        attended_by_name=user_names.get(assignment.attended_by) if assignment.attended_by else None,
        attendance_remarks=assignment.attendance_remarks,
        booking=BookingBrief(
            id=booking.id,
            status=booking.status,
            description=booking.description,
            booking_source=booking.booking_source,
            equipment_node_id=booking.equipment_node_id,
            equipment_node_name=equipment_name,
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
        ),
    )


def _build_out_rows(
    db: Session, client: LocoMasterClient, assignments: list[BookingSectionAssignment]
) -> list[SectionAssignmentOut]:
    if not assignments:
        return []

    booking_ids = {a.booking_id for a in assignments}
    bookings = {b.id: b for b in db.query(Booking).filter(Booking.id.in_(booking_ids)).all()}

    shed_visit_ids = {b.shed_visit_id for b in bookings.values()}
    shed_visits = {
        v.id: v for v in db.query(ShedVisit).filter(ShedVisit.id.in_(shed_visit_ids)).all()
    }

    defect_type_ids = {b.defect_type_id for b in bookings.values() if b.defect_type_id is not None}
    defect_types = (
        {
            d.id: d
            for d in db.query(BookingDefectType).filter(BookingDefectType.id.in_(defect_type_ids)).all()
        }
        if defect_type_ids
        else {}
    )

    user_ids = {a.started_by for a in assignments if a.started_by} | {
        a.attended_by for a in assignments if a.attended_by
    }
    user_names = (
        {u.id: u.name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    )

    equipment_node_ids = {
        b.equipment_node_id for b in bookings.values() if b.equipment_node_id is not None
    }
    equipment_names = _enrich_equipment_names(client, equipment_node_ids)

    section_ids = {a.section_id for a in assignments}
    sections = {s.id: s for s in db.query(Section).filter(Section.id.in_(section_ids)).all()}

    rows = []
    for assignment in assignments:
        booking = bookings[assignment.booking_id]
        rows.append(
            _to_out(
                assignment,
                booking,
                shed_visits[booking.shed_visit_id],
                sections[assignment.section_id],
                defect_types.get(booking.defect_type_id) if booking.defect_type_id else None,
                equipment_names.get(booking.equipment_node_id) if booking.equipment_node_id else None,
                user_names,
            )
        )
    return rows


def list_section_assignments(
    db: Session,
    client: LocoMasterClient,
    section_code: str,
    current_user: User,
    statuses: list[str] | None = None,
) -> list[SectionAssignmentOut]:
    section = _get_section_or_404(db, section_code)
    _assert_can_operate_section(current_user, section.id)

    query = db.query(BookingSectionAssignment).filter(
        BookingSectionAssignment.section_id == section.id
    )
    if statuses:
        query = query.filter(BookingSectionAssignment.status.in_(statuses))

    assignments = query.order_by(BookingSectionAssignment.assigned_at.desc()).all()
    return _build_out_rows(db, client, assignments)


def start_assignment(
    db: Session, client: LocoMasterClient, assignment_id: int, current_user: User
) -> SectionAssignmentOut:
    """OPEN/REOPENED -> IN_PROGRESS. Row-locks the assignment first so two concurrent start
    attempts can't both succeed."""
    pre = _get_assignment_or_404(db, assignment_id)
    _assert_can_operate_section(current_user, pre.section_id)
    assert_booking_visit_open(db, pre.booking_id)

    now = datetime.now(timezone.utc)
    try:
        assignment = _lock_assignment_or_404(db, assignment_id)
        if assignment.status not in _START_ALLOWED_FROM:
            raise _conflict(
                f"Cannot start an assignment in status {assignment.status}; expected {sorted(_START_ALLOWED_FROM)}."
            )

        assignment.status = "IN_PROGRESS"
        assignment.started_by = current_user.id
        assignment.started_at = now
        assignment.updated_at = now

        recompute_booking_status(db, assignment.booking_id)

        db.add(
            BookingEvent(
                booking_id=assignment.booking_id,
                event_type="STARTED",
                to_section_id=assignment.section_id,
                created_by=current_user.id,
                created_at=now,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return _build_out_rows(db, client, [assignment])[0]


def attend_assignment(
    db: Session, client: LocoMasterClient, assignment_id: int, remarks: str, current_user: User
) -> SectionAssignmentOut:
    """IN_PROGRESS -> ATTENDED. Row-locked the same way as start_assignment."""
    pre = _get_assignment_or_404(db, assignment_id)
    _assert_can_operate_section(current_user, pre.section_id)
    assert_booking_visit_open(db, pre.booking_id)

    now = datetime.now(timezone.utc)
    try:
        assignment = _lock_assignment_or_404(db, assignment_id)
        if assignment.status not in _ATTEND_ALLOWED_FROM:
            raise _conflict(
                f"Cannot attend an assignment in status {assignment.status}; expected {sorted(_ATTEND_ALLOWED_FROM)}."
            )

        assignment.status = "ATTENDED"
        assignment.attended_by = current_user.id
        assignment.attended_at = now
        assignment.attendance_remarks = remarks
        assignment.updated_at = now

        recompute_booking_status(db, assignment.booking_id)

        db.add(
            BookingEvent(
                booking_id=assignment.booking_id,
                event_type="ATTENDED",
                to_section_id=assignment.section_id,
                remarks=remarks,
                created_by=current_user.id,
                created_at=now,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return _build_out_rows(db, client, [assignment])[0]


def reopen_assignment(
    db: Session,
    client: LocoMasterClient,
    assignment_id: int,
    reason: str,
    current_user: User,
) -> SectionAssignmentOut:
    """ATTENDED -> REOPENED. Admin only. attendance_remarks is deliberately left untouched so the
    prior attend note isn't lost; the reopen reason is recorded on the booking_events row instead
    (this model has no reopened_by/reopened_at columns of its own - status + updated_at + the
    event row together are the full record)."""
    if current_user.role != ADMIN_ROLE:
        raise _forbidden("Only Admin may reopen an attended assignment.")

    pre = _get_assignment_or_404(db, assignment_id)
    assert_booking_visit_open(db, pre.booking_id)

    now = datetime.now(timezone.utc)
    try:
        assignment = _lock_assignment_or_404(db, assignment_id)
        if assignment.status not in _REOPEN_ALLOWED_FROM:
            raise _conflict(
                f"Cannot reopen an assignment in status {assignment.status}; expected {sorted(_REOPEN_ALLOWED_FROM)}."
            )

        assignment.status = "REOPENED"
        assignment.updated_at = now

        recompute_booking_status(db, assignment.booking_id)

        db.add(
            BookingEvent(
                booking_id=assignment.booking_id,
                event_type="REOPENED",
                to_section_id=assignment.section_id,
                remarks=reason,
                created_by=current_user.id,
                created_at=now,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return _build_out_rows(db, client, [assignment])[0]
