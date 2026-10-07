"""Phase 5B.2, refactored by Business Rule Alignment: Required Checksheet vs BL-DCMS submission
correlation.

Read-only. For every frozen work-package requirement row (Phase 5B.1 - see
app/services/checksheet_work_package_service.py), determines whether BL-DCMS holds at least one
checksheet for the SAME (shed_visit_id, workflow_stage_type, template_id) that has reached a
submission-satisfying status - SUBMITTED, UNDER_REVIEW, or APPROVED - then derives per-requirement
and per-stage progress.

Business Rule Alignment: technician submission means the maintenance work/checksheet has been
completed - Supervisor DSC approval (APPROVED) remains important final authenticated
recordkeeping, but must not block maintenance-workflow completion. This module therefore no
longer requires APPROVED specifically; DRAFT and REJECTED remain the only non-satisfying
statuses. Digital-signature state is never removed or hidden (see ChecksheetEvidenceOut.status,
still the raw BL-DCMS status of every matching row) - it simply no longer gates this correlation.

Matching key is exactly (workflow_stage_type, template_id) within one shed_visit_id - never
template name, section name, equipment name, or locomotive number, all of which are display
snapshots only (see ShedVisitChecksheetRequirement's own *_snapshot columns).

Never re-resolves BL-DCMS applicability: the frozen work-package rows are the sole source of
"what this visit requires" - a later change to BL-DCMS's applicability configuration is
deliberately invisible here, exactly as it already is to the work-package snapshot itself.

Never mutates anything: no shed_visit_stage, no booking, no work-package row. One BL-DCMS call
per request (GET /integration/checksheets/visit/{id}), not one per requirement - see
_correlate() below.
"""

from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSUnavailableError
from app.db.models import ShedVisitChecksheetRequirement, User
from app.schemas.checksheet_progress import (
    PROGRESS_STATE_IN_PROGRESS,
    PROGRESS_STATE_NOT_STARTED,
    PROGRESS_STATE_REJECTED,
    PROGRESS_STATE_SATISFIED,
    ChecksheetEvidenceOut,
    ChecksheetRequirementProgressOut,
    StageChecksheetProgressOut,
    VisitChecksheetProgressOut,
)
from app.services.checksheet_work_package_service import _get_existing_package
from app.services.shed_visit_service import MINOR_STAGE_SEQUENCE
from app.services.workflow_common import assert_visit_view_access, get_visit_or_404

# Business Rule Alignment: maintenance completion is technician submission, not Supervisor DSC
# approval - any of these three counts. APPROVED remains one of them (submission is a strict
# subset of what APPROVED implies), it just isn't the only one anymore.
SUBMISSION_SATISFYING_STATUSES = ("SUBMITTED", "UNDER_REVIEW", "APPROVED")
REJECTED_STATUS = "REJECTED"


def _no_package_response(visit_id: int) -> VisitChecksheetProgressOut:
    # bldcms_available is reported False here too - not because BL-DCMS was unreachable, but
    # because there is nothing to correlate against and BL-DCMS is deliberately never queried in
    # this case (see the module docstring / the brief's "NO WORK PACKAGE" section: never
    # generate a package or resolve applicability from this read-only endpoint). Callers must
    # check work_package_generated first; bldcms_available only carries meaning once a package
    # exists.
    return VisitChecksheetProgressOut(
        shed_visit_id=visit_id, work_package_generated=False, bldcms_available=False, stages=[]
    )


def _bldcms_unavailable_response(visit_id: int) -> VisitChecksheetProgressOut:
    return VisitChecksheetProgressOut(
        shed_visit_id=visit_id, work_package_generated=True, bldcms_available=False, stages=[]
    )


def _to_evidence(raw: dict) -> ChecksheetEvidenceOut:
    signature = raw.get("digital_signature") or {}
    return ChecksheetEvidenceOut(
        checksheet_id=raw["checksheet_id"],
        status=raw["status"],
        signed=bool(signature.get("signed")),
        signing_timestamp=signature.get("signing_timestamp"),
        verification_status=signature.get("verification_status"),
    )


def _fetch_checksheet_index(
    client: BLDCMSClient, visit_id: int
) -> dict[tuple[str, int], list[dict]] | None:
    """Returns a (workflow_stage_type, template_id) -> [raw checksheet dict] index built from a
    single BL-DCMS call, or None if BL-DCMS could not be reached/authenticated or returned
    anything this client can't confidently index (never partially indexed - a single malformed
    item means the whole read is treated as unavailable, matching
    checksheet_integration_service.py's identical convention)."""
    try:
        raw = client.get_visit_checksheets(visit_id)
        items = raw["items"]
        index: dict[tuple[str, int], list[dict]] = {}
        for item in items:
            # Direct indexing (not .get()) deliberately raises on a malformed item - see the
            # docstring above. checksheet_id/status are validated here too since both are
            # required for correlation/evidence, not just the index key.
            key = (item["workflow_stage_type"], item["template_id"])
            item["checksheet_id"]
            item["status"]
            index.setdefault(key, []).append(item)
        return index
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError, AttributeError):
        return None


def _correlate_requirement(
    req: ShedVisitChecksheetRequirement, index: dict[tuple[str, int], list[dict]]
) -> ChecksheetRequirementProgressOut:
    # Migration 010: a Minor Inspection equipment requirement is only ever satisfied by a checksheet
    # for that same directory equipment (an item without the field never matches such a requirement).
    if req.minor_inspection_equipment_id is not None:
        # Equipment-level: any alternative performa of this equipment, never another equipment.
        matches = [
            m for (stage, _template), items in index.items() if stage == req.workflow_stage_type
            for m in items if m.get("minor_inspection_equipment_id") == req.minor_inspection_equipment_id
        ]
    else:
        matches = [
            m for m in index.get((req.workflow_stage_type, req.template_id), [])
            if m.get("minor_inspection_equipment_id") is None
        ]

    # "if ANY matching checksheet is SUBMITTED/UNDER_REVIEW/APPROVED -> SATISFIED" - never picks
    # an arbitrary row, never assumes uniqueness (BL-DCMS's REJECTED status is terminal for that
    # row - see docs/EMBRIDGE_INTEGRATION.md / checksheet_service.VALID_STATUS_TRANSITIONS in the
    # BL-DCMS codebase - so a later resubmission after rejection is always a NEW checksheet row;
    # this any() already handles that correctly with no special-casing needed). Evidence is
    # preserved for every matching row, not just the satisfying one.
    satisfied = any(m["status"] in SUBMISSION_SATISFYING_STATUSES for m in matches)
    non_rejected_matches = [m for m in matches if m["status"] != REJECTED_STATUS]
    if satisfied:
        progress_state = PROGRESS_STATE_SATISFIED
    elif non_rejected_matches:
        # A DRAFT resubmission is already active - that's ordinary in-progress work, even if an
        # earlier attempt for this same requirement was REJECTED.
        progress_state = PROGRESS_STATE_IN_PROGRESS
    elif matches:
        # Every matching row is REJECTED (no DRAFT, no satisfying row) - distinct from ordinary
        # in-progress work so the UI can flag it rather than presenting it as routine progress.
        progress_state = PROGRESS_STATE_REJECTED
    else:
        progress_state = PROGRESS_STATE_NOT_STARTED

    return ChecksheetRequirementProgressOut(
        requirement_id=req.id,
        applicability_id=req.applicability_id,
        template_id=req.template_id,
        template_name_snapshot=req.template_name_snapshot,
        section=req.section_name_snapshot,
        equipment=req.equipment_name_snapshot,
        workflow_stage_type=req.workflow_stage_type,
        is_required=req.is_required,
        progress_state=progress_state,
        satisfied=satisfied,
        matching_checksheets=[_to_evidence(m) for m in matches],
    )


def _stage_progress(
    stage_type: str,
    requirements: list[ShedVisitChecksheetRequirement],
    index: dict[tuple[str, int], list[dict]],
) -> StageChecksheetProgressOut:
    rows = [
        _correlate_requirement(r, index)
        for r in sorted(requirements, key=lambda r: (r.template_id, r.applicability_id))
    ]

    required_rows = [r for r in rows if r.is_required]
    optional_rows = [r for r in rows if not r.is_required]
    required_total = len(required_rows)
    required_satisfied = sum(1 for r in required_rows if r.satisfied)
    optional_total = len(optional_rows)
    optional_satisfied = sum(1 for r in optional_rows if r.satisfied)

    # Zero required rows is a configuration/work-package problem, not automatic success - never
    # ready_for_completion=True unless there is at least one required row and every one of them
    # is satisfied.
    ready_for_completion = required_total > 0 and required_satisfied == required_total

    return StageChecksheetProgressOut(
        workflow_stage_type=stage_type,
        required_total=required_total,
        required_satisfied=required_satisfied,
        required_remaining=required_total - required_satisfied,
        optional_total=optional_total,
        optional_satisfied=optional_satisfied,
        ready_for_completion=ready_for_completion,
        requirements=rows,
    )


def get_visit_checksheet_progress(
    db: Session, client: BLDCMSClient | None, visit_id: int, current_user: User
) -> VisitChecksheetProgressOut:
    get_visit_or_404(db, visit_id)
    assert_visit_view_access(db, visit_id, current_user)

    package = _get_existing_package(db, visit_id)
    if package is None:
        return _no_package_response(visit_id)

    if client is None:
        return _bldcms_unavailable_response(visit_id)

    index = _fetch_checksheet_index(client, visit_id)
    if index is None:
        return _bldcms_unavailable_response(visit_id)

    by_stage: dict[str, list[ShedVisitChecksheetRequirement]] = {}
    for req in package.requirements:
        by_stage.setdefault(req.workflow_stage_type, []).append(req)

    stages = [
        _stage_progress(stage_type, by_stage.get(stage_type, []), index)
        for stage_type in MINOR_STAGE_SEQUENCE
    ]

    return VisitChecksheetProgressOut(
        shed_visit_id=visit_id, work_package_generated=True, bldcms_available=True, stages=stages
    )
