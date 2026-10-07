"""Which Loco Master equipment family a shed visit's locomotive is allowed to book against.

ONE RULE, DERIVED SERVER-SIDE, IN ONE PLACE. The technician (Android) and the Shed In / Log Book
user (Dashboard) never choose a technology: the locomotive on the visit decides it, and both
channels reach this module for the answer. A `technology=` or `family=` value in a request body is
never treated as authorization - it is not accepted at all.

Chain of authority, none of it duplicated here:
  * the visit names a loco_number (shed_visits.loco_number, this app's own row),
  * BL-DCMS owns that locomotive's technology ('3_PHASE' / 'CONVENTIONAL') and answers for it over
    the internal channel (clients/bldcms.get_locomotive_identity),
  * Loco Master owns the equipment families and every node's family_id.

This module only maps technology -> family code and compares a node's family against it.
"""

from fastapi import HTTPException, status

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSNotFoundError, BLDCMSUnavailableError
from app.clients.loco_master import LocoMasterClient
from app.services import equipment_service

# The two production families, by Loco Master's own existing codes - not renamed, not re-coded.
TECHNOLOGY_TO_FAMILY_CODE = {
    "3_PHASE": "3PHASE",
    "CONVENTIONAL": "CONVENTIONAL",
}


def _error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def _bldcms_unavailable() -> HTTPException:
    return _error(
        status.HTTP_502_BAD_GATEWAY,
        "BLDCMS_UNAVAILABLE",
        "The locomotive's technology could not be confirmed right now. Please try again.",
    )


def family_code_for_loco_number(bldcms: BLDCMSClient | None, loco_number: str) -> str:
    """The equipment family this locomotive may be booked against. Never guesses: an unknown
    locomotive, an unmapped technology or an unreachable BL-DCMS all refuse the request rather than
    silently falling back to one family (which would let the wrong technology's equipment through)."""
    if bldcms is None:
        raise _bldcms_unavailable()
    try:
        identity = bldcms.get_locomotive_identity(loco_number)
    except BLDCMSNotFoundError:
        raise _error(
            422,
            "LOCOMOTIVE_NOT_FOUND",
            "This locomotive is not known to BL-DCMS, so its equipment family cannot be determined.",
            loco_number=loco_number,
        )
    except (BLDCMSUnavailableError, BLDCMSAuthError):
        raise _bldcms_unavailable()

    technology = (identity or {}).get("technology")
    family_code = TECHNOLOGY_TO_FAMILY_CODE.get(technology)
    if family_code is None:
        raise _error(
            422,
            "EQUIPMENT_FAMILY_UNRESOLVED",
            "This locomotive's technology has no equipment family, so equipment cannot be chosen.",
            loco_number=loco_number,
            technology=technology,
        )
    return family_code


def assert_node_in_family(client: LocoMasterClient, node_id: int, family_code: str) -> dict:
    """The submitted node must belong to `family_code`. Returns the node so a caller that needs it
    does not fetch it twice.

    This is the server-side enforcement the UI filtering cannot provide: a client that sends a
    Conventional node id on a 3-phase visit - stale state, an old app version, a hand-made request -
    is refused here, whatever it filtered or displayed."""
    node = equipment_service.get_node_or_404(client, node_id)
    family_id = equipment_service.resolve_family_id(client, family_code)
    if node.get("family_id") != family_id:
        raise _error(
            422,
            "EQUIPMENT_FAMILY_MISMATCH",
            "That equipment does not belong to this locomotive's equipment family.",
            equipment_node_id=node_id,
            expected_family=family_code,
        )
    return node
