from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSClient
from app.clients.loco_master import LocoMasterClient
from app.core.authz import require_loco_movement, require_operations_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.shed_visits import (
    CompleteScheduleRequest,
    CurrentShedVisitOut,
    MarkReadyRequest,
    ScheduleTransitionResponse,
    ShedInRequest,
    ShedInResponse,
    ShedOutEligibilityOut,
    ShedOutRequest,
    ShedOutResponse,
    StartScheduleRequest,
    VisitTimingsOut,
)
from app.services import schedule_lifecycle_service, shed_out_service, shed_visit_service
from app.services.bldcms_client import get_bldcms_client
from app.services.shed_visit_phase import (
    PHASE_ACTIONS,
    compute_timings,
    derive_phase,
    phase_display_label,
)
from app.services.loco_master_client import get_loco_master_client

router = APIRouter(
    prefix="/api/shed-visits", tags=["shed-visits"], dependencies=[Depends(require_operations_user)]
)


@router.post("/in", response_model=ShedInResponse)
def shed_in(
    payload: ShedInRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    # Locomotive movement: restricted to Supervisors of the shed movement sections (SHIFT/PPIO
    # by configuration). Every other Supervisor gets 403 here even if they call the API directly.
    current_user: User = Depends(require_loco_movement),
):
    return shed_visit_service.shed_in(db, client, payload, current_user, bldcms_client=bldcms_client)


@router.get("/current", response_model=list[CurrentShedVisitOut])
def current_shed_visits(db: Session = Depends(get_db)):
    return shed_visit_service.list_current_visits(db)


@router.post("/{visit_id}/start-schedule", response_model=ScheduleTransitionResponse)
def start_schedule(
    visit_id: int,
    payload: StartScheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_loco_movement),
):
    """Marks the authoritative beginning of actual schedule work (SPARE -> SCHEDULE_IN_PROGRESS).

    Physical status stays IN_SHED: only the derived operational phase changes.
    """
    visit = schedule_lifecycle_service.start_schedule(db, visit_id, payload.started_at, current_user)
    return _transition_response(visit)


@router.post("/{visit_id}/complete-schedule", response_model=ScheduleTransitionResponse)
def complete_schedule(
    visit_id: int,
    payload: CompleteScheduleRequest,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    """MINOR: the actual Minor Inspection is complete (SCHEDULE_IN_PROGRESS -> INSPECTION_COMPLETED),
    writing inspection_completed_at - NOT ready; Test After is next.
    MAJOR: the schedule is complete (SCHEDULE_IN_PROGRESS -> READY), writing ready_at - and so
    refused with CHECKSHEETS_INCOMPLETE while any required Major checksheet is outstanding.

    Neither means "cleared to depart" - Shed Out independently re-evaluates its stage, booking and
    checksheet gates and will still refuse if any is unmet.
    """
    visit = schedule_lifecycle_service.complete_schedule(
        db, visit_id, payload.completed_at, current_user, bldcms_client=bldcms_client
    )
    return _transition_response(visit)


@router.post("/{visit_id}/mark-ready", response_model=ScheduleTransitionResponse)
def mark_ready(
    visit_id: int,
    payload: MarkReadyRequest,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    """MINOR only: INSPECTION_COMPLETED -> READY, writing ready_at. Requires Test After COMPLETED
    and every required checksheet for the visit satisfied (409 CHECKSHEETS_INCOMPLETE otherwise)."""
    visit = schedule_lifecycle_service.mark_ready(
        db, visit_id, payload.ready_at, current_user, bldcms_client=bldcms_client
    )
    return _transition_response(visit)


def _transition_response(visit) -> ScheduleTransitionResponse:
    phase = derive_phase(
        schedule_started_at=visit.schedule_started_at,
        inspection_completed_at=visit.inspection_completed_at,
        ready_at=visit.ready_at,
        departed_at=visit.departed_at,
    )
    timings = compute_timings(
        arrival_at=visit.arrival_at,
        schedule_started_at=visit.schedule_started_at,
        inspection_completed_at=visit.inspection_completed_at,
        ready_at=visit.ready_at,
        departed_at=visit.departed_at,
    )
    return ScheduleTransitionResponse(
        id=visit.id,
        loco_number=visit.loco_number,
        status=visit.status,
        schedule_family=visit.schedule_family,
        schedule_variant=visit.schedule_variant,
        arrival_at=visit.arrival_at,
        schedule_started_at=visit.schedule_started_at,
        inspection_completed_at=visit.inspection_completed_at,
        ready_at=visit.ready_at,
        departed_at=visit.departed_at,
        operational_phase=phase,
        display_label=phase_display_label(phase, visit.schedule_variant),
        available_actions=list(PHASE_ACTIONS.get(phase, ())),
        timings=VisitTimingsOut(**asdict(timings)),
    )


@router.get("/{visit_id}/shed-out-eligibility", response_model=ShedOutEligibilityOut)
def shed_out_eligibility(
    visit_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_loco_movement),
):
    return shed_out_service.get_shed_out_eligibility(db, visit_id, current_user, bldcms_client)


@router.post("/{visit_id}/out", response_model=ShedOutResponse)
def shed_out(
    visit_id: int,
    payload: ShedOutRequest,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    # Permission to INVOKE Shed Out. It does not bypass readiness: shed_out_service still
    # re-evaluates the stage, booking and checksheet gates inside its own transaction.
    current_user: User = Depends(require_loco_movement),
):
    return shed_out_service.shed_out(db, visit_id, payload, current_user, bldcms_client)
