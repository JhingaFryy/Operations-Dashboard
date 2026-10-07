"""Equipment Responsibility Mapping (Booking Pool Hardening: purpose clarification).

This mapping records which section(s) are responsible for maintaining an equipment hierarchy
node's master/inventory information (make, model, serial number, commissioning date, lifecycle
history, and future failure/asset analytics) - the future Equipment Inventory module's ownership
chain: equipment node -> responsible section(s) -> authority to maintain that equipment's
inventory record. That module is not built yet; this API only manages the mapping itself.

It must NOT be removed and does NOT control booking visibility, ownership, or routing - since the
Common Booking Pool reform, every booking is globally visible and grouped by equipment directly
(see app/services/booking_pool_service.py), never by this mapping. Nothing in the booking
creation/lifecycle/Shed Out path reads from equipment_service.resolve_sections() or this mapping.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSClient
from app.clients.loco_master import LocoMasterClient
from app.core.dependencies import (
    get_current_user,
    require_admin,
    require_equipment_mapping_permission,
)
from app.core.authz import require_operations_user
from app.db.session import get_db
from app.schemas.equipment import (
    EquipmentFamily,
    EquipmentMappingOut,
    EquipmentMappingUpdateRequest,
    EquipmentNode,
    AdminEquipmentNodeCreateRequest,
    AdminEquipmentNodeUpdateRequest,
    EquipmentNodeCreated,
    EquipmentNodeMatch,
    EquipmentNodeSearchResult,
    EquipmentSectionAddRequest,
    EquipmentSectionAddResponse,
    ResolvedSectionsOut,
)
from app.services import equipment_family_service, equipment_service
from app.services.bldcms_client import get_bldcms_client
from app.services.loco_master_client import get_loco_master_client

router = APIRouter(prefix="/api/equipment", tags=["equipment"], dependencies=[Depends(require_operations_user)])

# THERE IS NO POST /api/equipment/nodes. Equipment used to be creatable from a Shed In Log Book
# booking by an Admin or a SHIFT Supervisor, from the "no equipment found" dead end. That is
# withdrawn: creating equipment is master-data administration and belongs to ONE place, the
# Equipment Responsibility Mapping page, behind POST /api/equipment/admin/nodes (require_admin).
# The route was removed rather than re-gated so a SHIFT client cannot reach it at all - hiding the
# button alone would have left the endpoint answering. Search and selection during Shed In are
# untouched: GET /nodes, /nodes/search and /nodes/{id} are what that workflow actually needs.


@router.get("/families", response_model=list[EquipmentFamily])
def list_families(client: LocoMasterClient = Depends(get_loco_master_client)):
    return equipment_service.list_families(client)


def _family_for(
    bldcms: BLDCMSClient | None, loco_number: str | None, family: str | None
) -> str:
    """Either the caller names a locomotive and the server decides the family, or (the Equipment
    Responsibility Mapping screen) the caller browses a family directly. Never both: a request
    that sends a locomotive AND a family is refused rather than silently letting the family win,
    which is exactly how a wrong-technology list would reach a booking form."""
    if loco_number is not None:
        if family is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "FAMILY_NOT_ACCEPTED",
                    "message": "When a locomotive is given, its equipment family is decided by the server.",
                },
            )
        return equipment_family_service.family_code_for_loco_number(bldcms, loco_number)
    if family is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "FAMILY_REQUIRED",
                "message": "Give either loco_number (bookings) or family (equipment mapping).",
            },
        )
    return family


@router.get("/nodes", response_model=list[EquipmentNode])
def list_nodes(
    family: str | None = Query(default=None, description="Equipment family code, e.g. 3PHASE"),
    loco_number: str | None = Query(
        default=None,
        description="Booking flows: the family is derived from this locomotive's technology, "
        "and no equipment of any other technology is returned - at the root and at every depth.",
    ),
    parent_id: int | None = Query(default=None),
    client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    """Children of `parent_id` (roots when omitted) within one family. The family filter applies at
    every level, so a cascade of any depth can never cross into the other technology."""
    family_code = _family_for(bldcms_client, loco_number, family)
    return equipment_service.list_children(client, family_code=family_code, parent_id=parent_id)


@router.get("/nodes/search", response_model=list[EquipmentNodeSearchResult])
def search_nodes(
    q: str = Query(...),
    family: str | None = Query(default=None, description="Equipment family code, e.g. 3PHASE"),
    loco_number: str | None = Query(default=None, description="See list_nodes."),
    client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    """Search stays a backend capability, and stays inside one family: with loco_number, a result
    from the other technology can never be returned, let alone selected."""
    family_code = _family_for(bldcms_client, loco_number, family) if (loco_number or family) else None
    return equipment_service.search(client, query=q, family_code=family_code)


@router.get("/nodes/match", response_model=list[EquipmentNodeMatch])
def match_nodes(
    name: str = Query(..., min_length=1),
    family: str | None = Query(default=None, description="Equipment mapping page."),
    loco_number: str | None = Query(
        default=None, description="Booking flows: the family is derived from this locomotive."
    ),
    client: LocoMasterClient = Depends(get_loco_master_client),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
):
    """Existing equipment in this locomotive's family whose name matches `name`.

    Lets the Add Equipment dialog say "this already exists" - with the path and current
    sections of EVERY namesake - instead of either creating a near-duplicate or guessing
    which one the user meant.

    Declared above /nodes/{node_id}: otherwise the path parameter matches "match" first
    and this route is never reached.
    """
    family_code = _family_for(bldcms_client, loco_number, family)
    return equipment_service.match_by_name(client, family_code=family_code, name=name)


@router.post("/admin/nodes", response_model=EquipmentNodeCreated, status_code=201)
def admin_create_node(
    payload: AdminEquipmentNodeCreateRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user=Depends(require_admin),
):
    """Create equipment from the Equipment Responsibility Mapping page. ADMIN ONLY.

    Deliberately a different endpoint from POST /nodes, with a different dependency,
    because these are two different capabilities that happen to write the same table:

      POST /nodes        Shed In, Admin OR SHIFT, family from the locomotive, root only.
      POST /admin/nodes  this one, Admin ONLY, explicit family and parent.

    Collapsing them would mean a SHIFT Supervisor could place equipment anywhere in the
    hierarchy by sending a family_code, which is administration of the master data rather
    than recording a defect they just found.

    Sections are optional here and only here: an administrator may legitimately add a
    child under a parent that is already mapped, and ancestor resolution routes it. A root
    created with no mapping would be unbookable, so that combination is still refused.
    """
    return equipment_service.create_node(
        db,
        client,
        family_code=payload.family_code,
        parent_id=payload.parent_id,
        name=payload.name,
        section_codes=payload.section_codes,
        actor_employee_id=current_user.employee_id,
        require_sections=payload.parent_id is None,
    )


@router.patch("/admin/nodes/{node_id}", response_model=EquipmentNodeCreated)
def admin_update_node(
    node_id: int,
    payload: AdminEquipmentNodeUpdateRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user=Depends(require_admin),
):
    """Edit existing equipment from the Equipment Responsibility Mapping page. ADMIN ONLY.

    Enforced here, server-side, by require_admin - not by hiding a button. A Supervisor with the
    can_manage_equipment_mapping capability may edit MAPPINGS through the mapping routes, but
    equipment master data itself (its name, description, active state) is Admin-only, and SHIFT
    never regains any of it.

    The browser never talks to Loco Master. This route authenticates the Admin, derives
    actor_employee_id from the authenticated user - never from the request body - and forwards
    it server-to-server over the internal-API-key channel.

    Section mapping, when present, is REPLACE-SET: the list sent becomes the complete mapping.
    The caller must load the full current mapping before sending one.
    """
    return equipment_service.update_node(
        db,
        client,
        node_id,
        name=payload.name,
        description=payload.description,
        description_provided="description" in payload.model_fields_set,
        is_active=payload.is_active,
        section_codes=payload.section_codes,
        actor_employee_id=current_user.employee_id,
    )


@router.post("/nodes/{node_id}/sections", response_model=EquipmentSectionAddResponse)
def add_node_sections(
    node_id: int,
    payload: EquipmentSectionAddRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user=Depends(require_equipment_mapping_permission),
):
    """ADD sections to existing equipment, removing none. Idempotent.

    Gated by the same permission as PUT /nodes/{id}/mapping beside it, because it changes
    the same thing - who is responsible for a piece of equipment - and that is the
    Equipment Responsibility Mapping capability, not the Shed In one. It is NOT the
    replace-set PUT: that one deactivates any code the caller omits, which is right for
    wholesale administration and wrong for "also make M7-HR responsible for this".
    """
    return equipment_service.add_sections(
        db,
        client,
        node_id=node_id,
        section_codes=payload.section_codes,
        actor_employee_id=current_user.employee_id,
    )


@router.get("/nodes/{node_id}", response_model=EquipmentNode)
def get_node(node_id: int, client: LocoMasterClient = Depends(get_loco_master_client)):
    return equipment_service.get_node_or_404(client, node_id)


@router.get("/nodes/{node_id}/mapping", response_model=EquipmentMappingOut)
def get_mapping(node_id: int, client: LocoMasterClient = Depends(get_loco_master_client)):
    return equipment_service.get_direct_mapping(client, node_id)


@router.get("/nodes/{node_id}/resolved-sections", response_model=ResolvedSectionsOut)
def get_resolved_sections(node_id: int, client: LocoMasterClient = Depends(get_loco_master_client)):
    return equipment_service.resolve_sections(client, node_id)


@router.put("/nodes/{node_id}/mapping", response_model=EquipmentMappingOut)
def put_mapping(
    node_id: int,
    payload: EquipmentMappingUpdateRequest,
    db: Session = Depends(get_db),
    client: LocoMasterClient = Depends(get_loco_master_client),
    current_user=Depends(require_equipment_mapping_permission),
):
    return equipment_service.update_mapping(
        db, client, node_id, payload.section_codes, current_user.employee_id
    )
