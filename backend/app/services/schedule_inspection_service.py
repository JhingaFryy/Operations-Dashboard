"""Schedule Inspection (Minor Schedule, stage_order 2) workflow: start,
findings creation, findings listing, and the completion gate.

Phase 3B.1 update: `bookings.booking_source` has been deliberately
extended (by DDL outside this codebase's control) to include
SCHEDULE_INSPECTION alongside LOG_BOOK, TEST_BEFORE, TEST_AFTER,
SPECIAL_CHECKING, MANUAL. Findings creation/listing below use exactly
that value and reuse the same shared booking_creation_service as Test
Before — see app/services/test_before_service.py, which this module
deliberately mirrors.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSClient
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
    User,
)
from app.schemas.bookings import BookingAssignmentDetail, EquipmentPathItemBrief
from app.schemas.section_dashboard import DefectTypeBrief
from app.schemas.workflow import (
    StageOut,
    TestBeforeBookingIn,
    TestBeforeBookingOut,
    TestBeforeBookingsResponse,
)
from app.services.booking_creation_service import create_booking, resolve_and_validate_bookings
from app.services.workflow_common import (
    STAGE_SATISFIED_STATUSES,
    stage_out,
    test_before_legacy_waived,
    assert_no_later_stage_started,
    assert_visit_open,
    assert_visit_view_access,
    error,
    get_stage_or_404,
    get_visit_or_404,
    require_minor,
)

SCHEDULE_INSPECTION = "SCHEDULE_INSPECTION"
TEST_BEFORE = "TEST_BEFORE"


def _stage_out(stage) -> StageOut:
    return stage_out(stage)


def start_schedule_inspection(db: Session, visit_id: int, current_user: User) -> StageOut:
    visit = get_visit_or_404(db, visit_id)
    require_minor(visit)

    if visit.status not in ("IN_SHED", "READY"):
        raise error(
            status.HTTP_409_CONFLICT,
            "VISIT_NOT_OPEN",
            f"Shed visit is {visit.status}; SCHEDULE_INSPECTION can only be started while the visit is open.",
        )

    try:
        test_before = get_stage_or_404(db, visit_id, TEST_BEFORE, for_update=True)
        if test_before.status not in STAGE_SATISFIED_STATUSES and not test_before_legacy_waived(visit, test_before):
            raise error(
                status.HTTP_409_CONFLICT,
                "PREVIOUS_STAGE_NOT_COMPLETED",
                f"TEST_BEFORE is {test_before.status}; it must be COMPLETED (or skipped by an Admin) before SCHEDULE_INSPECTION can start.",
            )

        stage = get_stage_or_404(db, visit_id, SCHEDULE_INSPECTION, for_update=True)
        if stage.status != "PENDING":
            raise error(
                status.HTTP_409_CONFLICT,
                "STAGE_NOT_PENDING",
                f"SCHEDULE_INSPECTION is {stage.status}; it can only be started from PENDING.",
            )
        # Belt-and-braces: also confirms TEST_AFTER is still PENDING (and,
        # for a legacy visit that still has a SPECIAL_CHECKING row, that
        # too) — this is the same generic "no later stage has started"
        # invariant Test Before enforces.
        assert_no_later_stage_started(db, visit_id, stage.stage_order, SCHEDULE_INSPECTION)

        now = datetime.now(timezone.utc)
        stage.status = "IN_PROGRESS"
        stage.started_at = now
        stage.started_by = current_user.id
        stage.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(stage)
    return _stage_out(stage)


def list_schedule_inspection_bookings(
    db: Session, client: LocoMasterClient, visit_id: int, current_user: User
) -> list[TestBeforeBookingOut]:
    get_visit_or_404(db, visit_id)
    assert_visit_view_access(db, visit_id, current_user)
    stage = get_stage_or_404(db, visit_id, SCHEDULE_INSPECTION)

    bookings = (
        db.query(Booking)
        .filter(
            Booking.shed_visit_id == visit_id,
            Booking.stage_id == stage.id,
            Booking.booking_source == SCHEDULE_INSPECTION,
        )
        .order_by(Booking.id)
        .all()
    )
    if not bookings:
        return []

    booking_ids = [b.id for b in bookings]
    assignments = (
        db.query(BookingSectionAssignment)
        .filter(BookingSectionAssignment.booking_id.in_(booking_ids))
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

    defect_type_ids = {b.defect_type_id for b in bookings if b.defect_type_id is not None}
    defect_types = (
        {d.id: d for d in db.query(BookingDefectType).filter(BookingDefectType.id.in_(defect_type_ids)).all()}
        if defect_type_ids
        else {}
    )

    equipment_node_ids = {b.equipment_node_id for b in bookings if b.equipment_node_id is not None}
    equipment_names: dict[int, str] = {}
    equipment_paths: dict[int, list[EquipmentPathItemBrief]] = {}
    for node_id in equipment_node_ids:
        try:
            node = client.get_equipment_node(node_id)
            equipment_names[node_id] = node.get("name")
            equipment_paths[node_id] = [
                EquipmentPathItemBrief(id=p["id"], name=p["name"]) for p in node.get("path", [])
            ]
        except (LocoMasterNotFoundError, LocoMasterUnavailableError, LocoMasterAuthError):
            continue

    assignments_by_booking: dict[int, list[BookingSectionAssignment]] = {}
    for a in assignments:
        assignments_by_booking.setdefault(a.booking_id, []).append(a)

    results = []
    for booking in bookings:
        defect_type = defect_types.get(booking.defect_type_id) if booking.defect_type_id else None
        results.append(
            TestBeforeBookingOut(
                id=booking.id,
                status=booking.status,
                description=booking.description,
                equipment_node_id=booking.equipment_node_id,
                equipment_node_name=equipment_names.get(booking.equipment_node_id)
                if booking.equipment_node_id
                else None,
                equipment_path=equipment_paths.get(booking.equipment_node_id, []),
                defect_type=DefectTypeBrief(id=defect_type.id, code=defect_type.code, name=defect_type.name)
                if defect_type
                else None,
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
                    for a in assignments_by_booking.get(booking.id, [])
                ],
            )
        )
    return results


def complete_schedule_inspection(
    db: Session, bldcms_client: BLDCMSClient | None, visit_id: int, current_user: User
) -> StageOut:
    """Phase 5B.3: see test_before_service.complete_test_before's docstring - this is the same
    thin wrapper around the single authoritative reconciliation gate, not a second copy of the
    completion rule."""
    from app.services.checksheet_stage_reconciliation_service import (
        ReconciliationActor,
        reconcile_visit_checksheet_stages,
    )

    actor = ReconciliationActor(actor_type="USER", actor_user_id=current_user.id)
    result = reconcile_visit_checksheet_stages(db, bldcms_client, visit_id, actor)
    stage_result = next(s for s in result.stages if s.workflow_stage_type == SCHEDULE_INSPECTION)

    if stage_result.reason != "COMPLETED":
        raise error(
            status.HTTP_409_CONFLICT,
            "STAGE_NOT_READY_FOR_COMPLETION",
            f"SCHEDULE_INSPECTION cannot be completed: {stage_result.reason}.",
            stage=SCHEDULE_INSPECTION,
            reason=stage_result.reason,
            checksheets_ready=stage_result.checksheets_ready,
            bookings_ready=stage_result.bookings_ready,
        )

    stage = get_stage_or_404(db, visit_id, SCHEDULE_INSPECTION)
    return _stage_out(stage)
