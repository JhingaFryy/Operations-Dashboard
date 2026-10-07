"""BL-DCMS Integration Phase 4: read-only checksheet status/progress for a
shed visit. Observation only — never mutates a stage, a booking, Shed Out
eligibility, or anything else Dashboard owns. See app/clients/bldcms.py
for the transport layer and app/schemas/checksheet_integration.py for the
normalized response contract.
"""

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSUnavailableError
from app.db.models import User
from app.schemas.checksheet_integration import (
    ChecksheetIntegrationItem,
    ChecksheetIntegrationListResponse,
    ChecksheetStageSummary,
    ChecksheetSummaryResponse,
)
from app.services.workflow_common import assert_visit_view_access, get_visit_or_404
from sqlalchemy.orm import Session


def _to_item(raw: dict) -> ChecksheetIntegrationItem:
    signature = raw.get("digital_signature") or {}
    return ChecksheetIntegrationItem(
        id=raw["checksheet_id"],
        template_id=raw["template_id"],
        template_name=raw.get("template_name"),
        section=raw.get("section_name"),
        equipment=raw.get("equipment_name"),
        loco=raw.get("locomotive_number"),
        schedule_family=raw.get("schedule_family"),
        schedule_variant=raw.get("schedule_variant"),
        workflow_stage_type=raw.get("workflow_stage_type"),
        status=raw["status"],
        approved=bool(raw.get("is_final")),
        signed=bool(signature.get("signed")),
        signing_timestamp=signature.get("signing_timestamp"),
        verification_status=signature.get("verification_status"),
    )


def get_visit_checksheets(
    db: Session,
    client: BLDCMSClient | None,
    visit_id: int,
    current_user: User,
    workflow_stage_type: str | None = None,
) -> ChecksheetIntegrationListResponse:
    get_visit_or_404(db, visit_id)
    assert_visit_view_access(db, visit_id, current_user)

    if client is None:
        return ChecksheetIntegrationListResponse(shed_visit_id=visit_id, available=False, items=[])

    try:
        raw = client.get_visit_checksheets(visit_id, workflow_stage_type=workflow_stage_type)
        items = [_to_item(item) for item in raw.get("items", [])]
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError, AttributeError):
        # Auth failures are folded into the same "unavailable" outcome as a genuine connection
        # failure, matching equipment_service's identical Loco Master convention: from a
        # Dashboard user's point of view "our configured key is wrong" and "BL-DCMS is down"
        # are indistinguishable and neither should leak credential detail into the response.
        # KeyError/TypeError/AttributeError here means BL-DCMS returned a response this client
        # couldn't map into the expected shape (e.g. "items" wasn't a list of objects) - treated
        # the same as unreachable, never as "zero checksheets".
        return ChecksheetIntegrationListResponse(shed_visit_id=visit_id, available=False, items=[])

    return ChecksheetIntegrationListResponse(shed_visit_id=visit_id, available=True, items=items)


def get_visit_checksheet_summary(
    db: Session, client: BLDCMSClient | None, visit_id: int, current_user: User
) -> ChecksheetSummaryResponse:
    get_visit_or_404(db, visit_id)
    assert_visit_view_access(db, visit_id, current_user)

    if client is None:
        return ChecksheetSummaryResponse(shed_visit_id=visit_id, available=False, stages=[])

    try:
        raw = client.get_visit_checksheet_summary(visit_id)
        stages = [ChecksheetStageSummary(**stage) for stage in raw.get("stages", [])]
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError, ValueError, AttributeError):
        return ChecksheetSummaryResponse(shed_visit_id=visit_id, available=False, stages=[])

    return ChecksheetSummaryResponse(shed_visit_id=visit_id, available=True, stages=stages)
