"""Phase 5B.2, refactored by Business Rule Alignment: Required Checksheet vs BL-DCMS submission
correlation.

Every value here is either read straight from a frozen work-package requirement row (Dashboard's
own historical snapshot) or from a live BL-DCMS checksheet read - never persisted, never a second
status invented on top of BL-DCMS's own. Business Rule Alignment: "satisfied" now means the
technician's maintenance work is done (BL-DCMS status SUBMITTED/UNDER_REVIEW/APPROVED), not that a
Supervisor has digitally signed it - DSC approval (APPROVED) remains meaningful recordkeeping but
no longer gates maintenance completion. See app/services/checksheet_progress_service.py for the
correlation logic.
"""

from datetime import datetime

from pydantic import BaseModel

# Derived per-requirement state - calculated fresh on every request, never stored.
PROGRESS_STATE_NOT_STARTED = "NOT_STARTED"
PROGRESS_STATE_IN_PROGRESS = "IN_PROGRESS"
PROGRESS_STATE_SATISFIED = "SATISFIED"
# Business Rule Alignment: a matching checksheet exists but every matching row is REJECTED (and
# none is SUBMITTED/UNDER_REVIEW/APPROVED) - never satisfied, but distinct from ordinary
# in-progress DRAFT work so the UI can flag it instead of presenting it as routine progress.
PROGRESS_STATE_REJECTED = "REJECTED"


class ChecksheetEvidenceOut(BaseModel):
    """One matching BL-DCMS checksheet row, safe fields only - never a signature blob,
    certificate internals, or anything credential-shaped."""

    checksheet_id: int
    status: str
    signed: bool
    signing_timestamp: datetime | None = None
    verification_status: str | None = None


class ChecksheetRequirementProgressOut(BaseModel):
    requirement_id: int
    applicability_id: int
    template_id: int
    template_name_snapshot: str
    section: str | None = None
    equipment: str | None = None
    workflow_stage_type: str
    is_required: bool

    # Derived - see checksheet_progress_service.py's correlation rule.
    progress_state: str
    satisfied: bool
    matching_checksheets: list[ChecksheetEvidenceOut] = []


class StageChecksheetProgressOut(BaseModel):
    workflow_stage_type: str
    required_total: int
    required_satisfied: int
    required_remaining: int
    optional_total: int
    optional_satisfied: int
    # Deliberately not "completed" - this never claims the Dashboard shed_visit_stage itself is
    # COMPLETED, only that every required work-package row for this stage currently has a
    # submission-satisfying BL-DCMS checksheet (SUBMITTED/UNDER_REVIEW/APPROVED). See the service
    # module docstring.
    ready_for_completion: bool
    requirements: list[ChecksheetRequirementProgressOut] = []


class VisitChecksheetProgressOut(BaseModel):
    shed_visit_id: int
    work_package_generated: bool
    # False whenever BL-DCMS could not be reached/authenticated/parsed, OR no work package
    # exists yet (nothing was queried) - `stages` is always [] in both cases. Callers must never
    # read an empty `stages` list as "zero requirements/all satisfied" unless both
    # work_package_generated and bldcms_available are True. Mirrors
    # ChecksheetIntegrationListResponse's own `available` convention (Phase 4).
    bldcms_available: bool
    stages: list[StageChecksheetProgressOut] = []
