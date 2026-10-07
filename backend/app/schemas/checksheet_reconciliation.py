"""Phase 5B.3: Authoritative Checksheet Stage Reconciliation.

The response contract for POST /api/shed-visits/{visit_id}/reconcile-checksheet-stages. Every
field is either the stage row's own persisted state or a value derived fresh during this one
reconciliation call - see app/services/checksheet_stage_reconciliation_service.py.
"""

from pydantic import BaseModel

# Machine-readable reasons - never free-text-only logic internally. One of these is always the
# per-stage `reason`; COMPLETED is also used as the terminal "this stage just completed" result.
REASON_COMPLETED = "COMPLETED"
REASON_ALREADY_COMPLETED = "ALREADY_COMPLETED"
REASON_WORK_PACKAGE_MISSING = "WORK_PACKAGE_MISSING"
# Zero requirement rows exist for this stage at all - it is NOT CONFIGURED. Blocking: a stage
# with nothing configured must never complete vacuously.
REASON_STAGE_NOT_CONFIGURED = "STAGE_NOT_CONFIGURED"
# Requirement rows DO exist, but none of them is both active and required (all were marked
# Optional or Deactivated for this visit). Non-blocking by design: there is genuinely nothing
# left that must be done. Deliberately distinct from STAGE_NOT_CONFIGURED, which looks identical
# if you only count blocking rows.
REASON_NO_BLOCKING_REQUIREMENTS = "NO_BLOCKING_REQUIREMENTS"
# SCHEDULE_INSPECTION only: requirement rows exist (some sections ARE configured) but BL-DCMS has not
# declared EVERY required section of the technology's Minor Inspection configured
# (package.minor_inspection_configuration_complete is not True). Blocking - finishing the configured
# sections is not finishing the Inspection stage.
REASON_STAGE_CONFIGURATION_INCOMPLETE = "STAGE_CONFIGURATION_INCOMPLETE"
REASON_REQUIRED_CHECKSHEETS_PENDING = "REQUIRED_CHECKSHEETS_PENDING"
REASON_BOOKINGS_PENDING = "BOOKINGS_PENDING"
REASON_PREVIOUS_STAGE_NOT_COMPLETED = "PREVIOUS_STAGE_NOT_COMPLETED"
REASON_BLDCMS_UNAVAILABLE = "BLDCMS_UNAVAILABLE"
# SCHEDULE_INSPECTION only (BL-DCMS migration 076): every required checksheet is satisfied, but at
# least one section's Supervisor has not yet DSC-signed that section's combined Minor Inspection
# document. Only ever reported for a visit BL-DCMS says follows the section sign-off workflow - a
# pre-cutover visit never sees it.
REASON_SECTION_SIGNOFF_PENDING = "SECTION_SIGNOFF_PENDING"
# BL-DCMS follows the section sign-off workflow for this visit but could not resolve its own
# sign-off state (its frozen work package was unavailable). Deliberately NOT treated as "nothing
# to sign": the stage stays incomplete until the answer is known.
REASON_SECTION_SIGNOFF_UNKNOWN = "SECTION_SIGNOFF_UNKNOWN"
# TEST_BEFORE only (migration 012): an Admin skipped it for this visit. Not completed, never evaluated.
REASON_SKIPPED = "SKIPPED"


class StageReconciliationOut(BaseModel):
    workflow_stage_type: str
    previous_status: str
    current_status: str
    # None (not False) whenever evidence genuinely was never evaluated for this stage this call
    # (already completed, blocked by ordering, no package, or BL-DCMS unavailable) - never
    # fabricated as "not ready" when it was actually "not checked". See the module docstring.
    checksheets_ready: bool | None = None
    bookings_ready: bool | None = None
    reason: str


class VisitReconciliationOut(BaseModel):
    shed_visit_id: int
    changed: bool
    stages: list[StageReconciliationOut]
