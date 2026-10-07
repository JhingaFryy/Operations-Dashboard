"""Business Rule Alignment: the Section Dashboard is the primary operational booking surface again.

GET /api/sections/{code}/assignments is section-scoped (Admin may pass any section_code;
Supervisor is restricted to their own users.section_id - see
section_dashboard_service._assert_can_operate_section). The three mutation routes
(POST /api/section-assignments/{id}/start|attend|reopen) are the sole authoritative booking
lifecycle API again - app/api/bookings.py's booking-level start/attend/reopen are retired/frozen
(always 410) in favor of these, which operate on assignment_id and derive parent Booking.status
via section_dashboard_service.recompute_booking_status().
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.clients.loco_master import LocoMasterClient
from app.core.authz import require_operations_user
from app.core.dependencies import require_admin
from app.db.models import User
from app.db.session import get_db
from app.schemas.section_dashboard import AttendRequest, ReopenRequest, SectionAssignmentOut
from app.services import section_dashboard_service
from app.services.loco_master_client import get_loco_master_client

section_router = APIRouter(
    prefix="/api/sections", tags=["section-dashboard"], dependencies=[Depends(require_operations_user)]
)

assignment_router = APIRouter(
    prefix="/api/section-assignments",
    tags=["section-dashboard"],
    dependencies=[Depends(require_operations_user)],
)

# Reopening an ATTENDED assignment remains ADMIN-ONLY. It lives on its own router precisely so it
# is NOT behind the Supervisor gate above: Operations Dashboard is Supervisor-only, but this one
# corrective action is a deliberate, explicit technical-Admin path that no Supervisor - movement
# section included - ever inherits.
admin_assignment_router = APIRouter(
    prefix="/api/section-assignments",
    tags=["section-dashboard-admin"],
    dependencies=[Depends(require_admin)],
)


@section_router.get("/{section_code}/assignments", response_model=list[SectionAssignmentOut])
def list_section_assignments(
    section_code: str,
    status: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return section_dashboard_service.list_section_assignments(
        db, client, section_code, current_user, status
    )


@assignment_router.post("/{assignment_id}/start", response_model=SectionAssignmentOut)
def start_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return section_dashboard_service.start_assignment(db, client, assignment_id, current_user)


@assignment_router.post("/{assignment_id}/attend", response_model=SectionAssignmentOut)
def attend_assignment(
    assignment_id: int,
    payload: AttendRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return section_dashboard_service.attend_assignment(
        db, client, assignment_id, payload.remarks, current_user
    )


@admin_assignment_router.post("/{assignment_id}/reopen", response_model=SectionAssignmentOut)
def reopen_assignment(
    assignment_id: int,
    payload: ReopenRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_admin),
):
    return section_dashboard_service.reopen_assignment(
        db, client, assignment_id, payload.reason, current_user
    )
