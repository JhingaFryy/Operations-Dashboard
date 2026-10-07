from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSClient
from app.clients.loco_master import LocoMasterClient
from app.core.authz import require_loco_movement, require_operations_user
from app.core.dependencies import require_admin
from app.db.models import User
from app.db.session import get_db
from app.schemas.checksheet_integration import ChecksheetIntegrationListResponse, ChecksheetSummaryResponse
from app.schemas.checksheet_progress import VisitChecksheetProgressOut
from app.schemas.checksheet_reconciliation import VisitReconciliationOut
from app.schemas.checksheet_work_package import (
    ChecksheetWorkPackageOut,
    MajorChecksheetWorkPackageOut,
)
from app.schemas.workflow import (
    SkipTestBeforeRequest,
    StageOut,
    TestBeforeBookingOut,
    TestBeforeBookingsRequest,
    TestBeforeBookingsResponse,
    WorkflowOut,
)
from app.schemas.workflow import TestBeforeBookingOut as ScheduleInspectionBookingOut
from app.schemas.workflow import TestBeforeBookingsRequest as ScheduleInspectionBookingsRequest
from app.schemas.workflow import TestBeforeBookingsResponse as ScheduleInspectionBookingsResponse
from app.schemas.workflow import TestBeforeBookingOut as TestAfterBookingOut
from app.schemas.workflow import TestBeforeBookingsRequest as TestAfterBookingsRequest
from app.schemas.workflow import TestBeforeBookingsResponse as TestAfterBookingsResponse
from app.services import (
    checksheet_integration_service,
    checksheet_progress_service,
    checksheet_stage_reconciliation_service,
    checksheet_work_package_service,
    schedule_inspection_service,
    test_after_service,
    test_before_service,
)
from app.services.bldcms_client import get_bldcms_client
from app.services.checksheet_stage_reconciliation_service import ReconciliationActor
from app.services.loco_master_client import get_loco_master_client

router = APIRouter(
    prefix="/api/shed-visits", tags=["workflow"], dependencies=[Depends(require_operations_user)]
)


def raise_dashboard_checksheet_booking_refused(booking_source: str):
    """Booking ownership: the Dashboard creates LOG_BOOK bookings only (at Shed In). TEST_BEFORE /
    TEST_AFTER bookings originate on the Android checksheet; SCHEDULE_INSPECTION bookings do not
    exist. See app/services/booking_origin.py."""
    from app.services.booking_origin import dashboard_creation_refused

    raise dashboard_creation_refused(booking_source)


@router.get("/{visit_id}/workflow", response_model=WorkflowOut)
def get_workflow(
    visit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operations_user),
):
    return test_before_service.get_workflow(db, visit_id, current_user)


@router.post("/{visit_id}/stages/test-before/start", response_model=StageOut)
def start_test_before(
    visit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_loco_movement),
):
    return test_before_service.start_test_before(db, visit_id, current_user)


@router.post("/{visit_id}/stages/test-before/skip", response_model=StageOut)
def skip_test_before(
    visit_id: int,
    payload: SkipTestBeforeRequest | None = None,
    db: Session = Depends(get_db),
    # Admin only - no Supervisor, movement section or not. The service re-checks the role too.
    current_user: User = Depends(require_admin),
):
    """Skip THIS visit's Test Before (audited, irreversible, never a completed Test Before).
    Test After is unaffected and stays required."""
    return test_before_service.skip_test_before(
        db, visit_id, current_user, reason=payload.reason if payload else None
    )


@router.post("/{visit_id}/test-before/bookings", status_code=409)
def reject_dashboard_test_before_booking_creation(visit_id: int):
    """Removed (booking-ownership correction). Test Before bookings are created only from the Android Test Before checksheet (relayed by BL-DCMS over POST /api/internal/bookings).
    Kept as an explicit refusal - not a silent 404/405 - so a stale client gets the reason."""
    raise_dashboard_checksheet_booking_refused("TEST_BEFORE")


@router.get("/{visit_id}/test-before/bookings", response_model=list[TestBeforeBookingOut])
def list_test_before_bookings(
    visit_id: int,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return test_before_service.list_test_before_bookings(db, client, visit_id, current_user)


@router.post("/{visit_id}/stages/test-before/complete", response_model=StageOut)
def complete_test_before(
    visit_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    return test_before_service.complete_test_before(db, bldcms_client, visit_id, current_user)


# --- Schedule Inspection -----------------------------------------------


@router.post("/{visit_id}/stages/schedule-inspection/start", response_model=StageOut)
def start_schedule_inspection(
    visit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_loco_movement),
):
    return schedule_inspection_service.start_schedule_inspection(db, visit_id, current_user)


@router.post("/{visit_id}/schedule-inspection/bookings", status_code=409)
def reject_dashboard_schedule_inspection_booking_creation(visit_id: int):
    """Removed (booking-ownership correction). Minor Inspection checksheets never raise bookings, from any client.
    Kept as an explicit refusal - not a silent 404/405 - so a stale client gets the reason."""
    raise_dashboard_checksheet_booking_refused("SCHEDULE_INSPECTION")


@router.get(
    "/{visit_id}/schedule-inspection/bookings", response_model=list[ScheduleInspectionBookingOut]
)
def list_schedule_inspection_bookings(
    visit_id: int,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return schedule_inspection_service.list_schedule_inspection_bookings(db, client, visit_id, current_user)


@router.post("/{visit_id}/stages/schedule-inspection/complete", response_model=StageOut)
def complete_schedule_inspection(
    visit_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    return schedule_inspection_service.complete_schedule_inspection(db, bldcms_client, visit_id, current_user)


# --- Test After ----------------------------------------------------------


@router.post("/{visit_id}/stages/test-after/start", response_model=StageOut)
def start_test_after(
    visit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_loco_movement),
):
    return test_after_service.start_test_after(db, visit_id, current_user)


@router.post("/{visit_id}/test-after/bookings", status_code=409)
def reject_dashboard_test_after_booking_creation(visit_id: int):
    """Removed (booking-ownership correction). Test After bookings are created only from the Android Test After checksheet (relayed by BL-DCMS over POST /api/internal/bookings).
    Kept as an explicit refusal - not a silent 404/405 - so a stale client gets the reason."""
    raise_dashboard_checksheet_booking_refused("TEST_AFTER")


@router.get("/{visit_id}/test-after/bookings", response_model=list[TestAfterBookingOut])
def list_test_after_bookings(
    visit_id: int,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return test_after_service.list_test_after_bookings(db, client, visit_id, current_user)


@router.get("/{visit_id}/test-after/reference-bookings", response_model=list[TestAfterBookingOut])
def list_test_after_reference_bookings(
    visit_id: int,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return test_after_service.list_test_after_reference_bookings(db, client, visit_id, current_user)


@router.post("/{visit_id}/stages/test-after/complete", response_model=StageOut)
def complete_test_after(
    visit_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    return test_after_service.complete_test_after(db, bldcms_client, visit_id, current_user)


# --- BL-DCMS checksheet integration (Phase 4, read-only) --------------------
# Observation only: never starts/completes a stage, never touches a booking or Shed Out
# eligibility. See app/services/checksheet_integration_service.py.


@router.get("/{visit_id}/checksheets", response_model=ChecksheetIntegrationListResponse)
def list_visit_checksheets(
    visit_id: int,
    workflow_stage_type: str | None = None,
    db: Session = Depends(get_db),
    client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_operations_user),
):
    return checksheet_integration_service.get_visit_checksheets(
        db, client, visit_id, current_user, workflow_stage_type=workflow_stage_type
    )


@router.get("/{visit_id}/checksheet-summary", response_model=ChecksheetSummaryResponse)
def get_visit_checksheet_summary(
    visit_id: int,
    db: Session = Depends(get_db),
    client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_operations_user),
):
    return checksheet_integration_service.get_visit_checksheet_summary(db, client, visit_id, current_user)


# --- BL-DCMS materialized checksheet work packages (Phase 5B.1) -------------
# Generation is Admin-only and explicit (not wired into Shed In yet). Never marks a stage
# complete, never touches Shed Out eligibility, never creates a BL-DCMS checksheet_header row.
# See app/services/checksheet_work_package_service.py.


@router.get("/{visit_id}/checksheet-work-package", response_model=ChecksheetWorkPackageOut)
def get_checksheet_work_package(
    visit_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operations_user),
):
    return checksheet_work_package_service.get_work_package(db, visit_id, current_user)


@router.post("/{visit_id}/checksheet-work-package", response_model=ChecksheetWorkPackageOut)
def generate_checksheet_work_package(
    visit_id: int,
    db: Session = Depends(get_db),
    loco_client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    return checksheet_work_package_service.generate_work_package(
        db, loco_client, bldcms_client, visit_id, current_user
    )


@router.post(
    "/{visit_id}/checksheet-work-package/major", response_model=MajorChecksheetWorkPackageOut
)
def generate_major_checksheet_work_package(
    visit_id: int,
    db: Session = Depends(get_db),
    loco_client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    """Generate a MAJOR visit's work package by hand.

    Shed In already generates every visit's package automatically (shed_visit_service.shed_in ->
    checksheet_work_package_service.generate_requirements_snapshot), best-effort: if BL-DCMS or
    Loco Master was unreachable at that moment, the visit stays package-less and Pending
    Checksheets reports work_package_generated=false. The Minor sibling route above has always
    given an operator a way to recover from that; MAJOR had none, because generation there was
    reachable only from the Shed In path - so a Major visit that missed its package could never
    get one. Same authorization, same idempotency (a second call returns the existing snapshot
    untouched, generating nothing).
    """
    count = checksheet_work_package_service.generate_major_work_package(
        db, loco_client, bldcms_client, visit_id, current_user
    )
    return MajorChecksheetWorkPackageOut(
        shed_visit_id=visit_id, generated=count > 0, requirement_count=count
    )


@router.post("/{visit_id}/checksheet-work-package/refresh")
def refresh_checksheet_work_package(
    visit_id: int,
    db: Session = Depends(get_db),
    loco_client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    """Additive refresh of an open MINOR visit's work package: adds requirements configured in
    BL-DCMS since generation; never removes or rewrites existing rows. Same authorization as
    generation."""
    return checksheet_work_package_service.refresh_work_package(
        db, loco_client, bldcms_client, visit_id, current_user
    )


# --- BL-DCMS Required Checksheet vs APPROVED correlation (Phase 5B.2, read-only) --------------
# Correlates the frozen work-package snapshot (Phase 5B.1) against live BL-DCMS checksheet
# records by (shed_visit_id, workflow_stage_type, template_id). Never mutates a stage, a booking,
# Shed Out eligibility, or the work package itself; never re-resolves BL-DCMS applicability. See
# app/services/checksheet_progress_service.py.


@router.get("/{visit_id}/checksheet-requirement-progress", response_model=VisitChecksheetProgressOut)
def get_checksheet_requirement_progress(
    visit_id: int,
    db: Session = Depends(get_db),
    client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_operations_user),
):
    return checksheet_progress_service.get_visit_checksheet_progress(db, client, visit_id, current_user)


# --- Authoritative Checksheet Stage Reconciliation (Phase 5B.3, internal trigger 5B.4A) -------
# The sole mutation path that may transition a shed_visit_stage to COMPLETED - see
# app/services/checksheet_stage_reconciliation_service.py for the full evidence-driven rule. This
# route is the Admin-triggered entry point; app/api/internal.py is the BL-DCMS service-to-service
# entry point added in Phase 5B.4A - both call the exact same reconcile_visit_checksheet_stages,
# never a second completion implementation. Never triggered from a GET request; no background
# polling/webhook yet (a later phase may add automatic triggering after DSC approval directly
# from BL-DCMS's own callback, which is what Phase 5B.4A's internal endpoint now supports).


@router.post("/{visit_id}/reconcile-checksheet-stages", response_model=VisitReconciliationOut)
def reconcile_checksheet_stages(
    visit_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    actor = ReconciliationActor(actor_type="USER", actor_user_id=current_user.id)
    return checksheet_stage_reconciliation_service.reconcile_visit_checksheet_stages(
        db, bldcms_client, visit_id, actor
    )
