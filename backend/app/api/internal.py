"""Operations Dashboard Phase 5B.4A/5B.4B: internal service-to-service endpoints.

Not part of the human-facing Dashboard API - every route in this router is gated on
app.core.dependencies.require_internal_api_key (X-Internal-API-Key header, timing-safe
comparison against OPERATIONS_INTERNAL_API_KEY) instead of a human JWT. No Admin/Supervisor
Bearer token satisfies this auth, and this auth alone never grants access to any human-facing
route - the two trust domains are intentionally kept separate.

Exposes:
- POST /{visit_id}/reconcile-checksheet-stages (Phase 5B.4A): letting BL-DCMS ask Dashboard to
  re-evaluate a shed visit's checksheet stages after a checksheet becomes APPROVED. BL-DCMS
  identifies the visit only - it never tells Dashboard which stage to complete, and the callback
  payload is never trusted as completion evidence. This route is a thin wrapper around the exact
  same reconcile_visit_checksheet_stages() the Admin-triggered
  POST /api/shed-visits/{visit_id}/reconcile-checksheet-stages route calls - there is exactly one
  completion implementation in this codebase, not two.
- GET /active (Phase 5B.4B): a strictly read-only list of currently active shed visits, so
  BL-DCMS's Android app can offer a locomotive-selection screen for the Minor Schedule workflow
  without already knowing a visit id. Thin wrapper around shed_visit_service.list_active_visits(),
  which reuses the exact same OPEN_VISIT_STATUSES predicate/ordering already used by
  GET /api/shed-visits/current - no second definition of "active visit" is introduced here.
- POST /api/internal/bookings + GET /api/internal/booking-defect-types (internal_api_router below): server-to-server booking creation, so a
  defect an Android technician recorded while filling a BL-DCMS checksheet can reach the section
  that has to attend it. Same trust boundary and the SAME require_internal_api_key dependency as
  the two routes above - no new auth mechanism is introduced, and an Admin Bearer token alone
  still does not satisfy it. Thin wrapper around internal_booking_service, which in turn delegates
  routing and creation to the existing booking_creation_service; see that module.

  TI/GC domain note. booking_source now admits TRIP_INSPECTION and GENERAL_CHECKING (migration
  005, app/schemas/booking_pool.BOOKING_SOURCES). These are NOT workflow_stage_type values and
  must not become them: workflow_stage_type describes a stage of ONE Minor schedule visit
  (shed_visit_stages, TEST_BEFORE -> SCHEDULE_INSPECTION -> TEST_AFTER, ordered, gated,
  completion-tracked), whereas a Trip Inspection or a General Checking is a performa in its own
  right that need not belong to a Minor schedule visit at all. Modelling them as stage types
  would give every Minor visit two stages nobody performs and would make TI/GC findings gate
  Minor-schedule stage completion. The recommended eventual shape is a separate performa/
  checksheet-type concept alongside workflow_stage_type, not an extension of it - but ONLY the
  booking_source vocabulary is implemented here; no parallel checksheet-type system is built this
  phase, and no TI/GC checksheet template or content exists yet in BL-DCMS."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSClient
from app.clients.loco_master import LocoMasterClient
from app.core.dependencies import require_internal_api_key
from app.db.models import BookingDefectType, User
from app.api.visit_history import history_filters
from app.db.session import get_db
from app.schemas.checksheet_reconciliation import VisitReconciliationOut
from app.schemas.defect_types import DefectTypeOut
from app.schemas.visit_history import VisitHistoryDetail, VisitHistoryPage
from app.services import visit_history_service
from app.services.visit_history_service import HistoryFilters
from app.schemas.internal_bookings import (
    InternalBookingCreateRequest,
    InternalBookingCreateResponse,
)
from app.schemas.equipment import EquipmentFamily, EquipmentNode, EquipmentNodeSearchResult
from app.schemas.shed_visits import ActiveShedVisitOut
from app.schemas.workflow import (
    InternalGenerateWorkPackageRequest,
    InternalGenerateWorkPackageResponse,
    FrozenWorkPackageOut,
    InternalSkipTestBeforeRequest,
    InternalStageStartedRequest,
    StageOut,
)
from app.services import stage_timing_service, test_before_service
from app.services.workflow_common import error, get_visit_or_404
from app.services import checksheet_work_package_service, equipment_service, internal_booking_service, shed_visit_service
from app.services.bldcms_client import get_bldcms_client
from app.services.checksheet_stage_reconciliation_service import (
    ReconciliationActor,
    reconcile_visit_checksheet_stages,
)
from app.services.loco_master_client import get_loco_master_client
from app.schemas.pending_requirements import PendingRequirementOut, PendingRequirementsResponse
from app.services.pending_requirement_service import (
    apply_requirement_override,
    get_pending_requirements,
    set_requirement_under_amc,
)

router = APIRouter(
    prefix="/api/internal/shed-visits",
    tags=["internal"],
    dependencies=[Depends(require_internal_api_key)],
)

# A second router rather than more routes on the one above, purely because the existing router's
# prefix is /api/internal/shed-visits and these resources are not sub-resources of a shed visit
# path. Identical auth: the same require_internal_api_key dependency object, not a copy of it.
internal_api_router = APIRouter(
    prefix="/api/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_api_key)],
)

_SYSTEM_ACTOR = ReconciliationActor(actor_type="SYSTEM", actor_user_id=None)


@router.post("/{visit_id}/reconcile-checksheet-stages", response_model=VisitReconciliationOut)
def internal_reconcile_checksheet_stages(
    visit_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    # No request body: the caller identifies the visit only. Dashboard always re-fetches the
    # complete current BL-DCMS visit-checksheet state itself via reconcile_visit_checksheet_stages
    # (the same one BL-DCMS call the Admin-triggered route makes) and recomputes every stage from
    # scratch - an incoming payload claiming a template/status is never accepted as evidence.
    return reconcile_visit_checksheet_stages(db, bldcms_client, visit_id, _SYSTEM_ACTOR)


@router.post("/{visit_id}/stages/test-before/skip", response_model=StageOut)
def internal_skip_test_before(
    visit_id: int,
    payload: InternalSkipTestBeforeRequest,
    db: Session = Depends(get_db),
):
    """BL-DCMS Pending Checksheets: its Admin confirmed Skip Test Before. BL-DCMS has already
    required an Admin session; the named actor is re-verified here against the shared users table
    (active Admin) so the internal key can never skip on behalf of nobody or of a Supervisor."""
    actor = db.get(User, payload.actor_user_id)
    if actor is None:
        raise error(403, "ADMIN_REQUIRED", "Only an Admin can skip Test Before.")
    return test_before_service.skip_test_before(
        db,
        visit_id,
        actor,
        reason=payload.reason,
        channel=test_before_service.SKIP_CHANNEL_BLDCMS_DASHBOARD,
    )


@router.post(
    "/{visit_id}/checksheet-work-package/generate",
    response_model=InternalGenerateWorkPackageResponse,
)
def internal_generate_checksheet_work_package(
    visit_id: int,
    payload: InternalGenerateWorkPackageRequest,
    db: Session = Depends(get_db),
    loco_client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    """BL-DCMS Pending Checksheets: its Admin retried generation for a visit that has no work
    package.

    Shed In generates every visit's package automatically and best-effort, so a visit reaches this
    route only when that attempt could not complete (BL-DCMS or Loco Master unreachable at that
    moment, or - as on 2026-09-18 - a locomotive model BL-DCMS did not yet recognise). The retry
    runs the SAME generation services the Dashboard's own Admin routes call, dispatched by the
    visit's own schedule_family, and is idempotent: a visit that already has a package gets its
    existing snapshot back, regenerating and rewriting nothing.

    Like the skip route above, the named actor is re-verified here against the shared users table
    (active Admin), so the internal key can never generate on behalf of nobody or of a Supervisor.
    """
    actor = db.get(User, payload.actor_user_id)
    if actor is None or not actor.is_active or actor.role != "Admin":
        raise error(403, "ADMIN_REQUIRED", "Only an Admin can generate a checksheet work package.")

    visit = get_visit_or_404(db, visit_id)
    if visit.schedule_family == "MAJOR":
        count = checksheet_work_package_service.generate_major_work_package(
            db, loco_client, bldcms_client, visit_id, actor
        )
    elif visit.schedule_family == "MINOR":
        checksheet_work_package_service.generate_work_package(
            db, loco_client, bldcms_client, visit_id, actor
        )
        count = checksheet_work_package_service.requirement_count(db, visit_id)
    else:
        raise error(
            409,
            "NO_SCHEDULE_FAMILY",
            "This visit has no schedule family, so there is no checksheet work to resolve.",
        )
    return InternalGenerateWorkPackageResponse(
        shed_visit_id=visit_id,
        schedule_family=visit.schedule_family,
        generated=count > 0,
        requirement_count=count,
    )


@router.get("/{visit_id}/checksheet-work-package/frozen", response_model=FrozenWorkPackageOut)
def internal_get_frozen_checksheet_work_package(visit_id: int, db: Session = Depends(get_db)):
    """BL-DCMS: the visit's frozen work package - its authoritative scope. Read-only; never
    generates, refreshes or re-resolves anything. 404 for an unknown visit."""
    return checksheet_work_package_service.get_frozen_work_package(db, visit_id)


@router.post("/{visit_id}/stages/{stage_type}/started", response_model=StageOut)
def internal_record_stage_started(
    visit_id: int,
    payload: InternalStageStartedRequest,
    stage_type: str = Path(pattern="^(TEST_BEFORE|TEST_AFTER)$"),
    db: Session = Depends(get_db),
):
    """BL-DCMS created this visit's first checksheet for the stage. Idempotent: the first recorded
    start stands - see app/services/stage_timing_service.py."""
    return stage_timing_service.record_stage_started(db, visit_id, stage_type, payload.started_at)


@router.get("/active", response_model=list[ActiveShedVisitOut])
def internal_list_active_shed_visits(
    schedule_family: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    # Read-only: no visit is created, modified, or closed by this route. schedule_family, when
    # given, is passed straight through as an equality filter on the stored column - omitting it
    # returns every authoritative active visit, not just MINOR ones.
    return shed_visit_service.list_active_visits(db, schedule_family=schedule_family)


@internal_api_router.get("/booking-defect-types", response_model=list[DefectTypeOut])
def internal_list_booking_defect_types(db: Session = Depends(get_db)):
    """Read-only list of the ACTIVE booking defect types, so BL-DCMS can offer a technician the
    authoritative choices instead of a hardcoded copy that silently drifts.

    Identical query, ordering and response model to the human-facing
    GET /api/booking-defect-types (app/api/defect_types.py) - the same one definition of "active
    defect type", reachable from the other trust domain. That human route stays exactly as it was
    (require_dashboard_access), which technicians do not and must not satisfy; this route exists
    because the internal key is the only credential BL-DCMS has, and BL-DCMS never forwards a
    technician's JWT to Dashboard. Exposes id/code/name only - no internal flags, no sort_order,
    no created_at."""
    return (
        db.query(BookingDefectType)
        .filter(BookingDefectType.is_active.is_(True))
        .order_by(BookingDefectType.sort_order, BookingDefectType.id)
        .all()
    )


@internal_api_router.post("/bookings", response_model=InternalBookingCreateResponse)
def internal_create_booking(
    payload: InternalBookingCreateRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    """Creates one booking on behalf of an authenticated-elsewhere technician.

    The caller supplies facts, never decisions. Dashboard independently validates the shed visit
    (exists, not CLOSED), the Loco Master equipment node (exists), the defect type (known and
    active), the booking source (a value it recognises) and the technician identity; it then
    resolves the equipment's section(s) with its OWN unchanged
    equipment_service.resolve_sections() and refuses to create an unroutable booking
    (NO_SECTION_MAPPING). It also derives the visit locomotive's equipment family itself and
    refuses a node from the other technology (EQUIPMENT_FAMILY_MISMATCH) - the request carries no
    technology or family field, and could not be believed if it did. None of that logic exists in,
    or may be influenced by, the caller.

    Idempotent on client_booking_id: a retry of a request that already succeeded returns the same
    booking with created=false and writes nothing; a retry carrying a conflicting payload under
    the same key is rejected 409 IDEMPOTENCY_CONFLICT and never overwrites what is stored."""
    return internal_booking_service.create_internal_booking(
        db, client, payload, bldcms_client=bldcms_client
    )


# --- Operational Control phase: Pending Checksheets, service-to-service ------------------------
# BL-DCMS's Admin Dashboard hosts the operational panel, but the requirement snapshot lives here
# (Operations Dashboard owns shed visits and their work packages). BL-DCMS holds only the
# internal API key - never a Dashboard user's JWT - so these mirror the human-facing routes in
# app/api/pending_requirements.py exactly, differing only in the credential they accept.
#
# Admin authorization for the two mutations is enforced by BL-DCMS on its own side before it
# calls these: the internal key denotes a trusted service, not an end user, which is the same
# trust model every other /api/internal route already uses.


@internal_api_router.get(
    "/pending-checksheet-requirements", response_model=PendingRequirementsResponse
)
def internal_list_pending_checksheet_requirements(
    loco_number: str | None = Query(default=None),
    schedule_variant: str | None = Query(default=None),
    section_id: int | None = Query(default=None),
    equipment_id: int | None = Query(default=None),
    display_status: str | None = Query(default=None),
    technology: str | None = Query(default=None),
    include_deactivated: bool = Query(default=False),
    include_satisfied: bool = Query(default=False),
    # BL-DCMS sets this from the authenticated Supervisor's own section (None for an Admin).
    scope_section_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    return get_pending_requirements(
        db,
        client,
        loco_number=loco_number,
        schedule_variant=schedule_variant,
        section_id=section_id,
        equipment_id=equipment_id,
        display_status=display_status,
        technology=technology,
        include_deactivated=include_deactivated,
        include_satisfied=include_satisfied,
        scope_section_id=scope_section_id,
    )


@internal_api_router.patch(
    "/pending-checksheet-requirements/{requirement_id}/required",
    response_model=PendingRequirementOut,
)
def internal_set_requirement_required(
    requirement_id: int,
    is_required: bool = Query(...),
    db: Session = Depends(get_db),
):
    # actor_id is None: the caller is a trusted service, not a Dashboard user.
    return apply_requirement_override(db, requirement_id, is_required=is_required)


@internal_api_router.patch(
    "/pending-checksheet-requirements/{requirement_id}/active",
    response_model=PendingRequirementOut,
)
def internal_set_requirement_active(
    requirement_id: int,
    is_active: bool = Query(...),
    db: Session = Depends(get_db),
):
    return apply_requirement_override(db, requirement_id, is_active=is_active)


class InternalUnderAmcRequest(BaseModel):
    """Body for the AMC toggle.

    actor_user_id is REQUIRED, unlike the /required and /active routes above which pass actor_id=None
    because any trusted service call is equivalent. AMC is not equivalent: a Supervisor may only
    change their own section, so the decision depends on WHO is asking. Operations Dashboard re-reads
    that user and re-checks role, active status and section itself - BL-DCMS having authorized the
    request is not authorization for the service that owns the row.
    """

    under_amc: bool
    actor_user_id: int


@internal_api_router.patch(
    "/pending-checksheet-requirements/{requirement_id}/under-amc",
    response_model=PendingRequirementOut,
)
def internal_set_requirement_under_amc(
    requirement_id: int,
    payload: InternalUnderAmcRequest,
    db: Session = Depends(get_db),
):
    """BL-DCMS: mark or clear "Under AMC" for ONE requirement on ONE visit.

    Every rule - role, section scope, the transition matrix, the open-visit check and MINOR/MAJOR
    eligibility - is applied in set_requirement_under_amc, so this route adds nothing a caller could
    omit.
    """
    return set_requirement_under_amc(
        db,
        requirement_id,
        under_amc=payload.under_amc,
        actor_id=payload.actor_user_id,
    )


# --- Booking target selection for checksheet-derived bookings (booking-ownership correction) ------
# Read-only Loco Master equipment hierarchy, so a technician filling a Test Before / Test After
# checksheet on Android (through BL-DCMS, which authenticates the technician) picks the SAME
# authoritative booking target the Dashboard's Log Book form picks. Thin wrappers over the exact
# equipment_service calls GET /api/equipment/* already use - no second hierarchy, and never BL-DCMS's
# Minor Inspection equipment (that is checksheet applicability, not a booking target).


@internal_api_router.get("/equipment/families", response_model=list[EquipmentFamily])
def internal_list_equipment_families(client: LocoMasterClient = Depends(get_loco_master_client)):
    return equipment_service.list_families(client)


@internal_api_router.get("/equipment/nodes", response_model=list[EquipmentNode])
def internal_list_equipment_nodes(
    family: str = Query(...),
    parent_id: int | None = Query(default=None),
    client: LocoMasterClient = Depends(get_loco_master_client),
):
    return equipment_service.list_children(client, family_code=family, parent_id=parent_id)


@internal_api_router.get("/equipment/nodes/search", response_model=list[EquipmentNodeSearchResult])
def internal_search_equipment_nodes(
    q: str = Query(..., min_length=1),
    family: str | None = Query(default=None),
    client: LocoMasterClient = Depends(get_loco_master_client),
):
    return equipment_service.search(client, query=q, family_code=family)


# --- Shed visit history, for BL-DCMS Dashboard ---------------------------------------------------
#
# BL-DCMS authenticates its own user and passes that user's scope (None for an Admin, the
# Supervisor's section otherwise) - the same contract as the pending-requirements routes above.
# Checksheets are NOT included: BL-DCMS owns them and composes them itself, so there is no
# BL-DCMS -> Dashboard -> BL-DCMS round trip.


@internal_api_router.get("/shed-visit-history", response_model=VisitHistoryPage)
def internal_list_visit_history(
    filters: HistoryFilters = Depends(history_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=visit_history_service.DEFAULT_PAGE_SIZE, ge=1,
                           le=visit_history_service.MAX_PAGE_SIZE),
    scope_section_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    return visit_history_service.search_visits(
        db, filters, page=page, page_size=page_size, scope_section_id=scope_section_id, bldcms=client,
    )


@internal_api_router.get("/shed-visit-history/{visit_id}", response_model=VisitHistoryDetail)
def internal_get_visit_history(
    visit_id: int,
    scope_section_id: int | None = Query(default=None),
    include_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
    client: BLDCMSClient | None = Depends(get_bldcms_client),
    loco_client: LocoMasterClient = Depends(get_loco_master_client),
):
    return visit_history_service.get_visit_detail(
        db, visit_id, scope_section_id=scope_section_id, include_raw=include_raw,
        include_checksheets=False, bldcms=client, loco=loco_client,
    )
