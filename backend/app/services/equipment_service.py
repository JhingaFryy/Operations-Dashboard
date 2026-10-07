import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterConflictError,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
    LocoMasterValidationError,
)
from app.db.models import Section
from app.schemas.equipment import EquipmentMappingOut, ResolvedSectionsOut

_MAX_ANCESTOR_HOPS = 100  # guards against a cyclic parent_id chain in bad data

_log = logging.getLogger(__name__)

#: Loco Master's own node_type for a top-level node (importer source_level 1).
#: Equipment created from a booking form is always a root, so this is the only
#: consistent value - see create_node.
ROOT_NODE_TYPE = "EQUIPMENT"


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="Loco Master is unavailable."
    )


def _call(fn, *args, **kwargs):
    """Runs a LocoMasterClient call, translating transport-level failures
    into the Dashboard's own HTTP errors. LocoMasterAuthError (401/403 from
    Loco Master) means *this app's* configured internal API key is missing
    or wrong — from the caller's point of view that's indistinguishable
    from Loco Master being unavailable, so it maps to the same 502 rather
    than leaking "your credentials are broken" details to Dashboard users."""
    try:
        return fn(*args, **kwargs)
    except (LocoMasterUnavailableError, LocoMasterAuthError):
        raise _unavailable()


def list_families(client: LocoMasterClient) -> list[dict]:
    return _call(client.get_equipment_families)


def resolve_family_id(client: LocoMasterClient, family_code: str) -> int:
    families = _call(client.get_equipment_families)
    for family in families:
        if family.get("code") == family_code:
            return family["id"]
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unknown equipment family code: {family_code}",
    )


def list_children(client: LocoMasterClient, family_code: str, parent_id: int | None) -> list[dict]:
    family_id = resolve_family_id(client, family_code)
    return _call(client.get_equipment_children, node_id=parent_id, family_id=family_id)


def search(client: LocoMasterClient, query: str, family_code: str | None) -> list[dict]:
    family_id = resolve_family_id(client, family_code) if family_code else None
    return _call(client.search_equipment, query, family_id=family_id)


def get_node_or_404(client: LocoMasterClient, node_id: int) -> dict:
    try:
        return _call(client.get_equipment_node, node_id)
    except LocoMasterNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment node not found")


def get_direct_mapping(client: LocoMasterClient, node_id: int) -> EquipmentMappingOut:
    get_node_or_404(client, node_id)
    try:
        mapping = _call(client.get_equipment_mapping, node_id)
    except LocoMasterNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment node not found")
    return EquipmentMappingOut(
        equipment_node_id=node_id, section_codes=mapping.get("section_codes", [])
    )


def resolve_sections(client: LocoMasterClient, node_id: int) -> ResolvedSectionsOut:
    """Most-specific-mapping-wins: check the exact node, then walk up parent_id
    until an ancestor with an active mapping is found. Stops at the first hit —
    never unions sections from multiple ancestor levels."""
    node = get_node_or_404(client, node_id)

    current_id: int | None = node_id
    current_node: dict | None = node
    hops = 0

    while current_id is not None and hops < _MAX_ANCESTOR_HOPS:
        try:
            mapping = _call(client.get_equipment_mapping, current_id)
        except LocoMasterNotFoundError:
            break

        section_codes = mapping.get("section_codes", [])
        if section_codes:
            return ResolvedSectionsOut(
                equipment_node_id=node_id,
                resolved_from_node_id=current_id,
                resolution="EXACT" if current_id == node_id else "ANCESTOR",
                section_codes=section_codes,
            )

        parent_id = current_node.get("parent_id") if current_node else None
        if parent_id is None:
            break

        try:
            current_node = _call(client.get_equipment_node, parent_id)
        except LocoMasterNotFoundError:
            break

        current_id = parent_id
        hops += 1

    return ResolvedSectionsOut(
        equipment_node_id=node_id, resolved_from_node_id=None, resolution="NONE", section_codes=[]
    )


def _normalize_section_codes(db: Session, section_codes: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for code in section_codes:
        stripped = code.strip()
        if not stripped:
            raise HTTPException(
                status_code=422,
                detail="section_codes must not contain blank values",
            )
        seen[stripped] = None
    normalized = list(seen.keys())

    if not normalized:
        return normalized

    known = {
        row.code
        for row in db.query(Section.code).filter(Section.code.in_(normalized)).all()
    }
    unknown = [code for code in normalized if code not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown section code(s): {', '.join(unknown)}",
        )
    return normalized


def update_mapping(
    db: Session,
    client: LocoMasterClient,
    node_id: int,
    section_codes: list[str],
    actor_employee_id: str,
) -> EquipmentMappingOut:
    get_node_or_404(client, node_id)
    normalized = _normalize_section_codes(db, section_codes)

    try:
        result = _call(client.update_equipment_mapping, node_id, normalized, actor_employee_id)
    except LocoMasterNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment node not found")
    except LocoMasterValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail)

    return EquipmentMappingOut(
        equipment_node_id=node_id, section_codes=result.get("section_codes", normalized)
    )


def match_by_name(client: LocoMasterClient, *, family_code: str, name: str) -> list[dict]:
    """Existing equipment in this family whose name matches what the user typed.

    Returns every match and chooses none. Production holds 7,423 active nodes in which
    1,129 names are reused under different parents ("Others" 637 times, "Split Pin" 149),
    so "the one with that name" is not a thing that exists in general - guessing would
    attach a booking to the wrong equipment silently.
    """
    family_id = resolve_family_id(client, family_code)
    return _call(client.match_equipment_by_name, family_id, name)


def add_sections(
    db: Session,
    client: LocoMasterClient,
    *,
    node_id: int,
    section_codes: list[str],
    actor_employee_id: str,
) -> dict:
    """Add section mappings to equipment that ALREADY EXISTS, without removing any.

    This is the alternative to making a user invent a second name for equipment that is
    already in the hierarchy just because it needs another responsible section. No node is
    created; equipment_section_map is already many-to-many.
    """
    normalized_sections = _normalize_section_codes(db, section_codes)
    if not normalized_sections:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SECTION_REQUIRED",
                "message": "Choose at least one section to add.",
            },
        )

    try:
        result = _call(
            client.add_equipment_sections, node_id, normalized_sections, actor_employee_id
        )
    except LocoMasterNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Equipment node not found"
        )
    except LocoMasterValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail)

    node = get_node_or_404(client, node_id)
    _log.info(
        "EQUIPMENT_SECTION_MAPPING_ADDED",
        extra={
            "event": "EQUIPMENT_SECTION_MAPPING_ADDED",
            "equipment_node_id": node_id,
            "equipment_name": node.get("name"),
            "already_present": result.get("already_present", []),
            "newly_added": result.get("newly_added", []),
            "section_codes": result.get("section_codes", []),
            "actor_employee_id": actor_employee_id,
        },
    )
    return result


def create_node(
    db: Session,
    client: LocoMasterClient,
    *,
    family_code: str,
    name: str,
    section_codes: list[str],
    actor_employee_id: str,
    parent_id: int | None = None,
    require_sections: bool = True,
) -> dict:
    """Create an equipment node in LOCO MASTER and map it to its responsible section(s).

    Ownership: Loco Master owns equipment_nodes and equipment_section_map, and this
    app has no grant on that database - every write crosses the boundary over HTTP.
    This function only orchestrates the two calls and translates their failures.

    THE MAPPING IS NOT OPTIONAL. A booking resolves its section from the equipment's
    own mapping or its nearest mapped ancestor, and booking_creation_service rejects
    an unresolvable one with NO_SECTION_MAPPING. A node created at the root has no
    ancestor to inherit from, so equipment created without a mapping could never be
    booked - which is the whole point of creating it. Hence section_codes is required
    and validated against this Dashboard's own sections before anything is created.
    """
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(
            status_code=422,
            detail={"code": "NAME_REQUIRED", "message": "Equipment name must not be blank."},
        )

    # Validated FIRST: a node created and then left unmapped would be unbookable, and
    # there is no transaction spanning the two Loco Master calls to roll it back.
    normalized_sections = _normalize_section_codes(db, section_codes)
    if require_sections and not normalized_sections:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SECTION_REQUIRED",
                "message": "New equipment must be mapped to at least one section.",
            },
        )

    family_id = resolve_family_id(client, family_code)

    try:
        node = _call(
            client.create_equipment_node,
            family_id=family_id,
            parent_id=parent_id,
            name=cleaned,
            node_type=ROOT_NODE_TYPE,
            actor_employee_id=actor_employee_id,
        )
    except LocoMasterConflictError as exc:
        # Carry Loco Master's structured detail through - it names the existing node, so
        # the client can offer to reuse it instead of demanding a different name for the
        # same equipment.
        detail = exc.detail
        if not isinstance(detail, dict):
            detail = {"code": "DUPLICATE_EQUIPMENT", "message": str(detail)}
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    except LocoMasterNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Equipment family not found"
        )
    except LocoMasterValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail)

    node_id = node["id"]
    if not normalized_sections:
        # Only reachable with require_sections=False: an administrator placing a child
        # under a parent that already carries a mapping, which the new node inherits by
        # ancestor resolution. Nothing to write.
        _log.info(
            "EQUIPMENT_CREATED",
            extra={
                "event": "EQUIPMENT_CREATED",
                "equipment_node_id": node_id,
                "equipment_name": node.get("name"),
                "family_code": family_code,
                "equipment_path": [p.get("name") for p in node.get("path", [])],
                "section_codes": [],
                "actor_employee_id": actor_employee_id,
            },
        )
        return {**node, "section_codes": []}

    try:
        mapping = _call(
            client.update_equipment_mapping, node_id, normalized_sections, actor_employee_id
        )
    except (LocoMasterNotFoundError, LocoMasterValidationError):
        # The node exists but could not be mapped, so it is not yet bookable. Say so
        # precisely rather than pretending the whole operation failed - a retry of the
        # create would now hit the duplicate guard and confuse the user further.
        _log.warning(
            "equipment created but section mapping failed",
            extra={"equipment_node_id": node_id, "actor_employee_id": actor_employee_id},
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "MAPPING_FAILED",
                "message": (
                    f"Equipment '{cleaned}' was created but could not be mapped to a section. "
                    "Map it from Equipment Responsibility Mapping before booking against it."
                ),
                "equipment_node_id": node_id,
            },
        )

    _log.info(
        "EQUIPMENT_CREATED",
        extra={
            "event": "EQUIPMENT_CREATED",
            "equipment_node_id": node_id,
            "equipment_name": node.get("name"),
            "family_code": family_code,
            "equipment_path": [p.get("name") for p in node.get("path", [])],
            "section_codes": mapping.get("section_codes", normalized_sections),
            "actor_employee_id": actor_employee_id,
        },
    )
    return {**node, "section_codes": mapping.get("section_codes", normalized_sections)}


def update_node(
    db: Session,
    client: LocoMasterClient,
    node_id: int,
    *,
    name: str | None,
    description: str | None,
    description_provided: bool,
    is_active: bool | None,
    section_codes: list[str] | None,
    actor_employee_id: str,
) -> dict:
    """Edit an existing equipment node's metadata and/or its section mapping. ADMIN only.

    TWO SYSTEMS, NO SHARED TRANSACTION. The node lives in Loco Master and the section list is
    validated against this Dashboard's own sections; the metadata PATCH and the mapping PUT are
    two separate HTTP calls to a database this app has no grant on. There is therefore no way to
    make them atomic, and pretending otherwise would be worse than saying so. The order and the
    reporting are chosen around that:

      1. section codes are validated FIRST, locally, so a bad section never causes a half-done
         edit - nothing has been written at that point;
      2. metadata is written next, because it is the change the Admin is most likely to have
         actually asked for;
      3. the mapping is written last, as a REPLACE-SET.

    If step 3 fails after step 2 succeeded, this raises with `partial: true` and names exactly
    what did land. The caller must NOT report success. Retrying is safe: the metadata write is
    idempotent (an unchanged save writes nothing) and the mapping PUT is a replace-set.

    MOVEMENT IS NOT POSSIBLE HERE. No family or parent is accepted, sent, or written.
    """
    # Validated before anything is written, and against THIS app's sections - Loco Master does
    # not know which section codes the shed considers real.
    normalized_sections: list[str] | None = None
    if section_codes is not None:
        normalized_sections = _normalize_section_codes(db, section_codes)
        if not normalized_sections:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "SECTION_REQUIRED",
                    "message": "Equipment must stay mapped to at least one section.",
                },
            )

    metadata_requested = name is not None or description_provided or is_active is not None

    node: dict | None = None
    if metadata_requested:
        try:
            node = _call(
                client.update_equipment_node,
                node_id,
                name=name,
                description=description,
                description_provided=description_provided,
                is_active=is_active,
                actor_employee_id=actor_employee_id,
            )
        except LocoMasterConflictError as exc:
            # Loco Master's structured detail names the conflicting sibling, which is what makes
            # the message usable ("Others already exists at this level"). Carried through rather
            # than flattened to a generic 409.
            detail = exc.detail
            if not isinstance(detail, dict):
                detail = {"code": "DUPLICATE_EQUIPMENT", "message": str(detail)}
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
        except LocoMasterNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipment not found")
        except LocoMasterValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.detail)

        _log.info(
            "EQUIPMENT_UPDATED",
            extra={
                "event": "EQUIPMENT_UPDATED",
                "equipment_node_id": node_id,
                "changed_fields": sorted(
                    field
                    for field, changed in (
                        ("name", name is not None),
                        ("description", description_provided),
                        ("is_active", is_active is not None),
                    )
                    if changed
                ),
                "new_name": node.get("name"),
                "new_is_active": node.get("is_active"),
                "actor_employee_id": actor_employee_id,
            },
        )
    else:
        node = get_node_or_404(client, node_id)

    if normalized_sections is None:
        return {**node, "section_codes": get_direct_mapping(client, node_id).section_codes}

    previous = get_direct_mapping(client, node_id).section_codes
    try:
        mapping = _call(
            client.update_equipment_mapping, node_id, normalized_sections, actor_employee_id
        )
    except (LocoMasterNotFoundError, LocoMasterValidationError) as exc:
        _log.warning(
            "EQUIPMENT_MAPPING_UPDATE_FAILED",
            extra={
                "event": "EQUIPMENT_MAPPING_UPDATE_FAILED",
                "equipment_node_id": node_id,
                "metadata_applied": metadata_requested,
                "actor_employee_id": actor_employee_id,
            },
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "MAPPING_UPDATE_FAILED",
                # The caller must be able to tell the Admin precisely what state they are in.
                # Saying "saved" here would be a lie, and saying "failed" would also be one.
                "partial": metadata_requested,
                "message": (
                    "The equipment details were saved, but its section mapping could not be "
                    "updated. Try saving the sections again."
                    if metadata_requested
                    else "The section mapping could not be updated."
                ),
            },
        ) from exc

    final = mapping.get("section_codes", normalized_sections)
    _log.info(
        "EQUIPMENT_MAPPING_UPDATED",
        extra={
            "event": "EQUIPMENT_MAPPING_UPDATED",
            "equipment_node_id": node_id,
            "previous_section_codes": previous,
            "final_section_codes": final,
            "actor_employee_id": actor_employee_id,
        },
    )
    return {**node, "section_codes": final}
