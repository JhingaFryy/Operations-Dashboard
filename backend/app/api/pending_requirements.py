"""Operational Control phase: the Pending Checksheets panel and its per-visit overrides.

One read endpoint for the whole panel (every active visit and all its requirement rows in a
single call) plus two narrow mutations that write ONLY to this visit's requirement snapshot.

Nothing here can reach BL-DCMS's global checksheet_template_applicability: that is master
configuration governing every future locomotive, and is edited through BL-DCMS's own admin
routes. The whole point of this phase is that Mark Optional / Deactivate are visit-scoped.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSClient
from app.core.authz import can_access_all_sections, require_operations_user
from app.core.dependencies import require_admin
from app.db.models import User
from app.db.session import get_db
from app.schemas.pending_requirements import PendingRequirementOut, PendingRequirementsResponse
from app.services.bldcms_client import get_bldcms_client
from app.services.pending_requirement_service import (
    apply_requirement_override,
    get_pending_requirements,
)

router = APIRouter(prefix="/api/admin", tags=["Pending Checksheets"])


@router.get("/pending-checksheet-requirements", response_model=PendingRequirementsResponse)
def list_pending_checksheet_requirements(
    loco_number: str | None = Query(default=None),
    schedule_variant: str | None = Query(default=None, description="IA/IA0/IB/IC/IC0/IOH/TOH (exact match)"),
    section_id: int | None = Query(default=None),
    equipment_id: int | None = Query(default=None),
    display_status: str | None = Query(default=None, description="Pending / Draft / Needs Correction"),
    technology: str | None = Query(default=None),
    include_deactivated: bool = Query(default=False, description="The 'Deactivated' view."),
    include_satisfied: bool = Query(default=False, description="Diagnostics only."),
    db: Session = Depends(get_db),
    client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_operations_user),
):
    """Every actionable checksheet requirement across all locomotives currently in shed.

    Grouped by visit (locomotive + schedule). Requirements come from the frozen per-visit
    snapshot and are left-joined to their checksheet instance, so work that has not been started
    - and therefore has no checksheet_header row at all - still appears, as Pending.

    Admin sees every section. A Supervisor sees every locomotive in shed but only their OWN
    section's requirements (and counts) - scoped here, server-side, whatever filter they send.
    """
    scope_section_id = None if can_access_all_sections(current_user) else current_user.section_id
    if scope_section_id is None and not can_access_all_sections(current_user):
        from app.services.workflow_common import error

        raise error(403, "NO_SECTION", "Your account has no section assigned; pending checksheets cannot be scoped.")
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


@router.patch(
    "/pending-checksheet-requirements/{requirement_id}/required",
    response_model=PendingRequirementOut,
)
def set_requirement_required(
    requirement_id: int,
    is_required: bool = Query(...),
    db: Session = Depends(get_db),
    # Requirement configuration is Admin-only (was: any movement Supervisor).
    current_user: User = Depends(require_admin),
):
    """Mark Optional / Mark Required, for THIS shed visit only.

    is_required=False means the requirement no longer blocks visit or checksheet completion, but
    stays visible on the panel with an Optional badge - staff may still choose to perform it. It
    leaves the panel only once a checksheet for it has been submitted, exactly like a required
    row. BL-DCMS's global applicability is untouched.
    """
    return apply_requirement_override(
        db, requirement_id, is_required=is_required, actor_id=current_user.id
    )


@router.patch(
    "/pending-checksheet-requirements/{requirement_id}/active",
    response_model=PendingRequirementOut,
)
def set_requirement_active(
    requirement_id: int,
    is_active: bool = Query(...),
    db: Session = Depends(get_db),
    # Requirement configuration is Admin-only (was: any movement Supervisor).
    current_user: User = Depends(require_admin),
):
    """Deactivate / Re-enable, for THIS shed visit only.

    is_active=False means the requirement is not expected for this visit: it stops blocking
    completion and drops out of the default pending list, but the row is never deleted and stays
    reachable through include_deactivated=true so an Admin can re-enable it. BL-DCMS's global
    applicability is untouched - deactivating 39018/IA/Test Before does not deactivate the
    template for every future IA visit.
    """
    return apply_requirement_override(
        db, requirement_id, is_active=is_active, actor_id=current_user.id
    )
