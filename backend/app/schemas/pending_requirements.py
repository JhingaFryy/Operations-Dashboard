"""Operational Control phase: the Pending Checksheets panel contract.

One purpose-built response for the whole panel - every active visit and all of its requirement
rows in a single call, so the frontend never fans out one request per locomotive.

Deliberately starts from the VISIT REQUIREMENT SNAPSHOT and left-joins the checksheet instance,
never the other way round: work that has not been started yet has no checksheet_header row at
all, and is exactly the work the panel most needs to show.
"""

from datetime import datetime

from pydantic import BaseModel

# Display vocabulary. Raw BL-DCMS enums are never surfaced to operational users.
DISPLAY_PENDING = "Pending"            # requirement exists, no checksheet started
DISPLAY_DRAFT = "Draft"                # checksheet started, not yet submitted
DISPLAY_NEEDS_CORRECTION = "Needs Correction"  # BL-DCMS REJECTED - the real state, not a new one
# Migration 012:
DISPLAY_SKIPPED = "Skipped"            # TEST_BEFORE explicitly skipped by an Admin for this visit
DISPLAY_AWAITING_INSPECTION = "Awaiting Inspection"  # TEST_AFTER before the inspection is complete
DISPLAY_LEGACY_WAIVED = "Not Required (Legacy Start)"  # TEST_BEFORE of a schedule started pre-012

# Statuses that mean the work is done for operational purposes and the row leaves this panel.
# Mirrors checksheet_progress_service.SUBMISSION_SATISFYING_STATUSES - submission is maintenance
# completion; Supervisor DSC approval is recordkeeping and must not be what clears the queue.
SATISFYING_STATUSES = ("SUBMITTED", "UNDER_REVIEW", "APPROVED")


class PendingRequirementOut(BaseModel):
    requirement_id: int
    shed_visit_id: int
    loco_number: str
    schedule_family: str | None = None
    schedule_variant: str | None = None

    section_id: int | None = None
    section_name: str | None = None
    equipment_id: int | None = None
    equipment_name: str | None = None
    template_id: int
    template_name: str
    workflow_stage_type: str | None = None
    maintenance_type: str | None = None
    # Minor Inspection equipment directory identity (frozen code/name) - e.g. "A" / "VCD".
    minor_inspection_equipment_id: int | None = None
    minor_inspection_equipment_code: str | None = None
    minor_inspection_equipment_name: str | None = None
    # Display label frozen at generation ("A - VCD", "HB CUBICLE-1"); null on pre-011 rows.
    minor_inspection_equipment_label: str | None = None
    requirement_source: str

    # Per-visit operational control. Never reflects BL-DCMS global applicability. For TEST_BEFORE /
    # TEST_AFTER these are the EFFECTIVE values (always required and active - migration 012); a
    # legacy per-visit override on such a row is preserved in the table but no longer applies.
    is_required: bool
    is_active: bool

    # --- Under AMC (migration 014) ------------------------------------------------------------
    # A THIRD state, reported alongside the other two and never folded into them. The Dashboard must
    # be able to show OPTIONAL, UNDER AMC and DEACTIVATED as different things.
    under_amc: bool = False
    amc_marked_by: int | None = None
    amc_marked_at: datetime | None = None
    #: Retained after AMC is lifted, so the row still records who restored it.
    amc_cleared_at: datetime | None = None
    #: The single resolved state - REQUIRED / OPTIONAL / UNDER_AMC / DEACTIVATED - in precedence
    #: order. Supplied so the page never has to combine three booleans and risk combining them
    #: differently from the completion gate.
    effective_state: str = "REQUIRED"
    #: Whether an AMC toggle may be offered at all: MINOR and MAJOR schedule work only, never
    #: Test Before or Test After. Decided server-side so the page cannot offer a refused control.
    amc_eligible: bool = False
    # False for TEST_BEFORE / TEST_AFTER: Mark Optional / Mark Required / Deactivate are rejected.
    configurable: bool = True

    # Left-joined checksheet instance, matched on full requirement identity within this visit.
    checksheet_id: int | None = None
    checksheet_status: str | None = None

    display_status: str
    updated_at: datetime | None = None
    changed_by: int | None = None


class PendingRequirementCounts(BaseModel):
    total: int = 0
    pending: int = 0
    optional: int = 0
    deactivated: int = 0
    satisfied: int = 0
    # Migration 012: a skipped Test Before is not pending; a Test After that is not operational yet
    # (inspection not complete) is not pending either, but stays required.
    skipped: int = 0
    awaiting_inspection: int = 0
    legacy_waived: int = 0


class PendingTestBeforeOut(BaseModel):
    """Visit-level Test Before state for the Admin's Skip Test Before control. Omitted (None) on
    section-scoped (Supervisor) responses."""

    stage_status: str | None = None          # PENDING / IN_PROGRESS / COMPLETED / SKIPPED; None = no stage
    skipped_at: datetime | None = None
    skipped_by: int | None = None
    skip_reason: str | None = None
    # True while an Admin may still skip: MINOR, open visit, stage exists, not COMPLETED/SKIPPED, and
    # the schedule has not started.
    can_skip: bool = False
    legacy_waived: bool = False


class PendingVisitGroupOut(BaseModel):
    """One locomotive currently in shed, with its schedule - the panel's top-level grouping."""

    shed_visit_id: int
    loco_number: str
    schedule_family: str | None = None
    schedule_variant: str | None = None
    technology: str | None = None
    arrival_at: datetime | None = None
    status: str

    # False when no snapshot has been generated for this visit yet - distinct from "generated and
    # nothing is pending", which the frontend must present very differently.
    work_package_generated: bool
    # Populated when generation was attempted and could not complete (e.g. nothing configured).
    generation_note: str | None = None
    # MINOR only, generated packages only: workflow stages for which the frozen snapshot holds NO
    # requirement row at all (BL-DCMS had no applicability when the package was generated). Such a
    # stage is UNCONFIGURED - it can never complete (reconciliation STAGE_NOT_CONFIGURED) - and must
    # never be presented as "nothing outstanding". Derived from the snapshot; no row is invented.
    unconfigured_stages: list[str] = []
    # MINOR only: stages with SOME configured requirements whose full configuration is not yet
    # complete (today: SCHEDULE_INSPECTION when not every required section is configured). Such a
    # stage cannot complete even when every listed requirement is satisfied.
    partially_configured_stages: list[str] = []

    counts: PendingRequirementCounts
    requirements: list[PendingRequirementOut] = []
    # MINOR only (migration 012). None for MAJOR and for section-scoped responses.
    test_before: PendingTestBeforeOut | None = None
    # MINOR: the actual Minor Inspection is complete, so Test After is operational.
    inspection_completed: bool = False


class PendingRequirementsResponse(BaseModel):
    generated_at: datetime
    # False when BL-DCMS could not be reached: requirement rows are still returned (they are
    # local), but no checksheet instance could be correlated, so display_status would wrongly
    # read "Pending" for work that is actually submitted. The UI must warn rather than mislead.
    bldcms_available: bool
    # Set when the response was restricted to one section (a Supervisor's own); None = all sections.
    scope_section_id: int | None = None
    groups: list[PendingVisitGroupOut] = []
