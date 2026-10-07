from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_add_booking_sections_permission, require_admin
from app.core.authz import (
    can_access_all_sections,
    require_booking_pool_read,
    require_operations_user,
    require_route_bookings,
)
from app.db.models import User
from app.db.session import get_db
from app.clients.loco_master import LocoMasterClient
from app.schemas.booking_pool import (
    AttendBookingRequest,
    BookingPoolItemOut,
    ReopenBookingRequest,
    StartBookingRequest,
)
from app.schemas.bookings import (
    AddSectionRequest,
    BookingDetailOut,
    BookingHistoryEventOut,
    AddSectionAssignmentsRequest,
    AddSectionAssignmentsResponse,
    CreatePlanningBookingRequest,
    CreatePlanningBookingResponse,
    SectionSummaryOut,
)
from app.schemas.section_dashboard import SectionAssignmentOut
from app.services import booking_pool_service, booking_routing_service, booking_service
from app.services.auth_service import ADMIN_ROLE
from app.services.loco_master_client import get_loco_master_client

router = APIRouter(prefix="/api/bookings", tags=["bookings"], dependencies=[Depends(require_operations_user)])

# The GLOBAL, unscoped booking pool is cross-section visibility, deliberately NOT part of an
# ordinary Supervisor's workflow (their queue is the section-scoped assignment list). It keeps its
# own router so that no Supervisor - movement section included - can reach it merely by satisfying
# the operational gate above.
#
# WIDENED FROM require_admin TO require_booking_pool_read: Admin as before, plus an account
# holding the booking-routing capability, because a planner cannot decide where a finding should
# go without seeing the pool it lives in. It is still READ-only for them - the routing endpoint is
# gated separately by require_route_bookings, and every other mutation here is retired or
# Admin-only.
admin_router = APIRouter(
    prefix="/api/bookings",
    tags=["bookings-admin"],
    dependencies=[Depends(require_booking_pool_read)],
)


# Business Rule Alignment: the global Booking Pool is Admin-only operational visibility now, not
# the primary Supervisor workflow (see app/services/section_dashboard_service.py for that). Its
# booking-level lifecycle mutations (start/attend/reopen) are retired/frozen - always 410 - in
# favor of assignment-level mutations (POST /api/section-assignments/{id}/start|attend|reopen).


@admin_router.get("", response_model=list[BookingPoolItemOut])
def list_bookings(
    shed_visit_id: int | None = Query(default=None),
    booking_status: str | None = Query(default=None, alias="status"),
    booking_source: str | None = Query(default=None, alias="source"),
    equipment_node_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_booking_pool_read),
):
    return booking_pool_service.list_bookings(
        db,
        client,
        current_user,
        shed_visit_id=shed_visit_id,
        booking_status=booking_status,
        booking_source=booking_source,
        equipment_node_id=equipment_node_id,
    )


@router.post("/{booking_id}/start", response_model=BookingPoolItemOut)
def start_booking(
    booking_id: int,
    _payload: StartBookingRequest | None = None,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    """FROZEN (Business Rule Alignment) - always 410. See booking_pool_service._deprecated_mutation()."""
    return booking_pool_service.start_booking(db, client, booking_id, current_user)


@router.post("/{booking_id}/attend", response_model=BookingPoolItemOut)
def attend_booking(
    booking_id: int,
    payload: AttendBookingRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    """FROZEN (Business Rule Alignment) - always 410."""
    return booking_pool_service.attend_booking(db, client, booking_id, payload.remarks, current_user)


@router.post("/{booking_id}/reopen", response_model=BookingPoolItemOut)
def reopen_booking(
    booking_id: int,
    payload: ReopenBookingRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    """FROZEN (Business Rule Alignment) - always 410."""
    return booking_pool_service.reopen_booking(db, client, booking_id, payload.reason, current_user)


# --- Legacy: manual section-assignment addition -----------------------------------------------
#
# booking_section_assignments is no longer operationally required (Common Booking Pool reform) -
# it no longer drives visibility, start/attend/reopen, booking.status, or Shed Out eligibility.
# This route is kept only for backward compatibility with any existing caller; the new frontend
# does not use it. Still requires the same can_add_booking_sections permission as before.


@router.post("/{booking_id}/sections", response_model=SectionAssignmentOut)
def add_section(
    booking_id: int,
    payload: AddSectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_add_booking_sections_permission),
):
    return booking_service.add_section(db, booking_id, payload.section_id, payload.reason, current_user)


# --- Planner section routing (ADDITIVE ONLY) ---------------------------------------------------


@router.post(
    "/{booking_id}/section-assignments",
    response_model=AddSectionAssignmentsResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_booking_section_assignments(
    booking_id: int,
    payload: AddSectionAssignmentsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_route_bookings),
):
    """ADD maintenance sections to a booking. Never remove, never replace.

    A planner is saying "these sections are ALSO responsible for this finding". An assignment the
    booking already has - typically the AUTO_MAPPING row Loco Master's equipment-section mapping
    created - is left exactly as it is: not re-saved, so its source, timestamps and lifecycle
    survive. A section already present is reported as already-assigned rather than duplicated.
    There is no way to express a removal through this endpoint.

    This REPLACES an earlier PUT .../sections that took the complete desired set. That contract
    let a client un-route a section by omitting it, which is not an operation a planner should be
    able to perform even by accident, so the shape was changed rather than the rules tightened
    around it. The legacy POST .../sections remains retired at 410 and is untouched.

    Authorization is two independent questions, both answered server-side:
      * WHO may route - require_route_bookings (Admin, or the explicit dashboard_access
        capability from migration 015);
      * WHERE to - booking_routing_service, which admits any section that EXISTS and is not a
        planning section. The sections table is the source of truth, so a section added through
        the normal section-management workflow becomes assignable with no code change.

    Every booking source is routable. The permission boundary is what may be DONE, not which
    source it was raised from, so a source this code has never seen needs no change here.
    """
    outcome = booking_routing_service.add_sections(
        db, booking_id, payload.section_ids, current_user, payload.reason
    )
    return booking_routing_service.describe(db, outcome)


@router.post(
    "/planning",
    response_model=CreatePlanningBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_planning_booking(
    payload: CreatePlanningBookingRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_route_bookings),
):
    """Raise a booking as a planner, optionally routing it to extra sections in the same call.

    Reuses booking_creation_service.create_booking - the same entity, the same CREATED event, the
    same Loco Master auto-mapping, the same NO_SECTION_MAPPING refusal. The equipment's mapped
    section is always kept; `additional_section_ids` are added alongside it, never instead of it.

    booking_source is fixed to MANUAL server-side and cannot be supplied. WHO raised it is
    bookings.created_by, which is what the pool response turns into the "Added by PPIO" badge -
    provenance, not an overloaded source value.
    """
    booking, outcome = booking_routing_service.create_planning_booking(
        db,
        client,
        shed_visit_id=payload.shed_visit_id,
        equipment_node_id=payload.equipment_node_id,
        defect_type_id=payload.defect_type_id,
        description=payload.description,
        additional_section_ids=payload.additional_section_ids,
        current_user=current_user,
        reason=payload.reason,
    )
    described = booking_routing_service.describe(db, outcome)
    return CreatePlanningBookingResponse(
        booking_id=booking.id,
        booking_source=booking.booking_source,
        sections=described["sections"],
        auto_mapped_section_ids=outcome.previous_section_ids,
        added_section_ids=outcome.added_section_ids,
    )


@router.get("/{booking_id}/history", response_model=list[BookingHistoryEventOut])
def get_booking_history(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operations_user),
):
    return booking_service.get_booking_history(db, booking_id, current_user)


@router.get("/summary", response_model=SectionSummaryOut)
def get_summary(
    section_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_operations_user),
):
    """Section booking summary.

    SUPERVISOR: always their OWN section. The `section_id` parameter is ignored entirely for
    them - not merely validated - so naming another section cannot widen scope. This is the
    tamper-resistance the section-isolation tests assert, and enabling Admin below must not and
    does not weaken it.

    ADMIN: may name any section (cross-section visibility). An Admin has no section of their own
    in production (section_id IS NULL), so the parameter is how they choose one; omitting it is a
    422 rather than a silent empty answer.
    """
    if can_access_all_sections(current_user):
        if section_id is None:
            raise HTTPException(
                status_code=422,
                detail="section_id is required when querying as an Admin.",
            )
        return booking_service.get_section_summary(db, section_id)

    # Supervisor: own section only; `section_id` is never read on this branch.
    if current_user.section_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No section is assigned to this account.",
        )
    return booking_service.get_section_summary(db, current_user.section_id)


# Registered after the static /summary route above — a GET /{booking_id}
# route registered earlier would swallow "/summary" as booking_id="summary"
# (path matching is by registration order, not static-before-dynamic).
@router.get("/{booking_id}", response_model=BookingDetailOut)
def get_booking_detail(
    booking_id: int,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    return booking_service.get_booking_detail(db, client, booking_id, current_user)
