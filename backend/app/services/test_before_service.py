"""Test Before (Minor Schedule, stage_order 1) workflow: view, start,
findings creation, findings listing, and the completion gate.

See app/services/workflow_common.py for the shared visit/stage lookups and
authorization checks (extracted in Phase 3B so Schedule Inspection doesn't
duplicate them), and its module docstring for the shed_visit_events audit
limitation this phase already worked around.
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
    ShedVisit,
    ShedVisitEvent,
    User,
)
from app.services.auth_service import ADMIN_ROLE
from app.services.shed_visit_phase import compute_minor_workflow_analytics
from app.schemas.bookings import BookingAssignmentDetail, EquipmentPathItemBrief
from app.schemas.section_dashboard import DefectTypeBrief
from app.schemas.workflow import (
    MinorWorkflowAnalyticsOut,
    StageOut,
    TestBeforeBookingIn,
    TestBeforeBookingOut,
    TestBeforeBookingsResponse,
    WorkflowOut,
)
from app.services.booking_creation_service import create_booking, resolve_and_validate_bookings
from app.services.workflow_common import (
    STAGE_SATISFIED_STATUSES,
    stage_out,
    assert_no_later_stage_started,
    assert_visit_open,
    assert_visit_view_access,
    error,
    get_stage_or_404,
    get_stages,
    get_visit_or_404,
    require_minor,
    test_before_legacy_waived,
)

TEST_BEFORE = "TEST_BEFORE"


def _stage_out(stage) -> StageOut:
    return stage_out(stage)


def get_workflow(db: Session, visit_id: int, current_user: User) -> WorkflowOut:
    visit = get_visit_or_404(db, visit_id)
    assert_visit_view_access(db, visit_id, current_user)
    require_minor(visit)

    stages = get_stages(db, visit_id)
    stages_by_type = {s.stage_type: s for s in stages}
    analytics = compute_minor_workflow_analytics(visit, stages_by_type)
    return WorkflowOut(
        shed_visit_id=visit.id,
        loco_number=visit.loco_number,
        arrival_at=visit.arrival_at,
        schedule_family=visit.schedule_family,
        schedule_variant=visit.schedule_variant,
        status=visit.status,
        stages=[_stage_out(s) for s in stages],
        schedule_started_at=visit.schedule_started_at,
        inspection_completed_at=visit.inspection_completed_at,
        ready_at=visit.ready_at,
        departed_at=visit.departed_at,
        analytics=MinorWorkflowAnalyticsOut(**vars(analytics)),
        test_before_legacy_waived=test_before_legacy_waived(visit, stages_by_type.get(TEST_BEFORE)),
    )


SKIP_CHANNEL_OPERATIONS_DASHBOARD = "OPERATIONS_DASHBOARD"
SKIP_CHANNEL_BLDCMS_DASHBOARD = "BLDCMS_DASHBOARD"


def skip_test_before(
    db: Session,
    visit_id: int,
    actor: User,
    *,
    reason: str | None = None,
    channel: str = SKIP_CHANNEL_OPERATIONS_DASHBOARD,
) -> StageOut:
    """Admin-only, audited skip of THIS visit's Test Before (migration 012).

    Not "optional": the stage becomes SKIPPED (never COMPLETED, completed_at stays NULL), no
    checksheet is created, and a TEST_BEFORE_SKIPPED event records who/when/why/from where. Test
    After is untouched and stays required. Idempotent: skipping an already-skipped Test Before
    returns the existing record and writes nothing.

    The route layer authenticates the Admin; this re-checks the role so no caller (including the
    internal service route acting for a BL-DCMS user) can skip on behalf of a non-Admin.
    """
    if actor is None or not getattr(actor, "is_active", False) or actor.role != ADMIN_ROLE:
        raise error(
            status.HTTP_403_FORBIDDEN,
            "ADMIN_REQUIRED",
            "Only an Admin can skip Test Before.",
        )

    get_visit_or_404(db, visit_id)
    try:
        visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).with_for_update().one()
        if visit.status == "CLOSED":
            raise error(
                status.HTTP_409_CONFLICT,
                "VISIT_CLOSED",
                "This shed visit is closed; Test Before can no longer be skipped.",
            )
        require_minor(visit)

        stage = get_stage_or_404(db, visit_id, TEST_BEFORE, for_update=True)
        if stage.status == "SKIPPED":
            db.rollback()
            return _stage_out(stage)
        if stage.status == "COMPLETED":
            raise error(
                status.HTTP_409_CONFLICT,
                "STAGE_ALREADY_COMPLETED",
                "Test Before is already completed for this shed visit; there is nothing to skip.",
            )
        if visit.schedule_started_at is not None:
            # The skip exists to allow Start Schedule. After the schedule has started it would only
            # write a skipped_at that describes nothing - a schedule started under the previous
            # workflow is grandfathered instead (workflow_common.test_before_legacy_waived).
            raise error(
                status.HTTP_409_CONFLICT,
                "SCHEDULE_ALREADY_STARTED",
                "The schedule has already started; Test Before can no longer be skipped.",
            )

        now = datetime.now(timezone.utc)
        previous_status = stage.status
        stage.status = "SKIPPED"
        stage.skipped_at = now
        stage.skipped_by = actor.id
        stage.skip_reason = reason
        stage.updated_at = now
        db.add(
            ShedVisitEvent(
                shed_visit_id=visit.id,
                event_type="TEST_BEFORE_SKIPPED",
                event_time=now,
                source="DASHBOARD",
                remarks=reason,
                event_data={
                    "stage": TEST_BEFORE,
                    "previous_stage_status": previous_status,
                    "channel": channel,
                    "schedule_family": visit.schedule_family,
                    "schedule_variant": visit.schedule_variant,
                },
                created_by=actor.id,
                created_at=now,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(stage)
    return _stage_out(stage)


def start_test_before(db: Session, visit_id: int, current_user: User) -> StageOut:
    visit = get_visit_or_404(db, visit_id)
    require_minor(visit)

    if visit.status not in ("IN_SHED", "READY"):
        raise error(
            status.HTTP_409_CONFLICT,
            "VISIT_NOT_OPEN",
            f"Shed visit is {visit.status}; TEST_BEFORE can only be started while the visit is open.",
        )

    try:
        stage = get_stage_or_404(db, visit_id, TEST_BEFORE, for_update=True)
        if stage.status != "PENDING":
            raise error(
                status.HTTP_409_CONFLICT,
                "STAGE_NOT_PENDING",
                f"TEST_BEFORE is {stage.status}; it can only be started from PENDING.",
            )
        assert_no_later_stage_started(db, visit_id, stage.stage_order, TEST_BEFORE)

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


def list_test_before_bookings(
    db: Session, client: LocoMasterClient, visit_id: int, current_user: User
) -> list[TestBeforeBookingOut]:
    get_visit_or_404(db, visit_id)
    assert_visit_view_access(db, visit_id, current_user)
    stage = get_stage_or_404(db, visit_id, TEST_BEFORE)

    bookings = (
        db.query(Booking)
        .filter(Booking.shed_visit_id == visit_id, Booking.stage_id == stage.id)
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


def complete_test_before(
    db: Session, bldcms_client: BLDCMSClient | None, visit_id: int, current_user: User
) -> StageOut:
    """Phase 5B.3: completion is now evidence-driven, not an unrestricted Admin action - this is
    a thin wrapper around the single authoritative reconciliation gate
    (checksheet_stage_reconciliation_service.reconcile_visit_checksheet_stages), reused rather
    than duplicated so there is exactly one completion rule in the whole app. If the evidence
    gate doesn't complete this specific stage, the reconciliation's own machine-readable reason
    is surfaced as a structured 409 - never a silent bypass."""
    from app.services.checksheet_stage_reconciliation_service import (
        ReconciliationActor,
        reconcile_visit_checksheet_stages,
    )

    actor = ReconciliationActor(actor_type="USER", actor_user_id=current_user.id)
    result = reconcile_visit_checksheet_stages(db, bldcms_client, visit_id, actor)
    stage_result = next(s for s in result.stages if s.workflow_stage_type == TEST_BEFORE)

    if stage_result.reason != "COMPLETED":
        raise error(
            status.HTTP_409_CONFLICT,
            "STAGE_NOT_READY_FOR_COMPLETION",
            f"TEST_BEFORE cannot be completed: {stage_result.reason}.",
            stage=TEST_BEFORE,
            reason=stage_result.reason,
            checksheets_ready=stage_result.checksheets_ready,
            bookings_ready=stage_result.bookings_ready,
        )

    stage = get_stage_or_404(db, visit_id, TEST_BEFORE)
    return _stage_out(stage)
