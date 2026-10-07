from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.bookings import BookingAssignmentDetail, EquipmentPathItemBrief
from app.schemas.section_dashboard import DefectTypeBrief


class StageOut(BaseModel):
    id: int
    stage_type: str
    stage_order: int
    status: str
    started_at: datetime | None
    started_by: int | None
    completed_at: datetime | None
    completed_by: int | None
    # Migration 012: TEST_BEFORE only - an Admin explicitly skipped it for this visit (status SKIPPED).
    skipped_at: datetime | None = None
    skipped_by: int | None = None
    skip_reason: str | None = None


class MinorWorkflowAnalyticsOut(BaseModel):
    """Whole-second spans computed from recorded timestamps only (services/shed_visit_phase.py
    compute_minor_workflow_analytics). None = not measurable yet / never recorded / skipped."""

    arrival_to_test_before_start_seconds: int | None = None
    test_before_seconds: int | None = None
    test_before_skipped: bool = False
    test_before_skipped_at: datetime | None = None
    test_before_to_schedule_start_seconds: int | None = None
    inspection_seconds: int | None = None
    inspection_to_test_after_start_seconds: int | None = None
    test_after_seconds: int | None = None
    test_after_to_ready_seconds: int | None = None
    ready_to_shed_out_seconds: int | None = None
    total_dwell_seconds: int | None = None


class WorkflowOut(BaseModel):
    shed_visit_id: int
    loco_number: str
    arrival_at: datetime
    schedule_family: str | None
    schedule_variant: str | None
    status: str
    stages: list[StageOut]
    schedule_started_at: datetime | None = None
    inspection_completed_at: datetime | None = None
    ready_at: datetime | None = None
    departed_at: datetime | None = None
    analytics: MinorWorkflowAnalyticsOut = MinorWorkflowAnalyticsOut()
    # Schedule started under the previous workflow without a completed/skipped Test Before: Test
    # Before is waived downstream and its stage row is left untouched (no fabricated timestamps).
    test_before_legacy_waived: bool = False


class SkipTestBeforeRequest(BaseModel):
    """Optional free-text reason, stored on the stage row and in the TEST_BEFORE_SKIPPED event."""

    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason")
    @classmethod
    def blank_reason_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class InternalSkipTestBeforeRequest(SkipTestBeforeRequest):
    """BL-DCMS names the authenticated BL-DCMS Admin who confirmed the skip. Operations Dashboard
    re-checks that this user exists, is active and is an Admin - the internal key alone never
    authorises a skip on behalf of nobody."""

    actor_user_id: int


class InternalStageStartedRequest(BaseModel):
    """BL-DCMS created the visit's first checksheet for a Test Before / Test After stage."""

    started_at: datetime
    checksheet_id: int | None = None


class TestBeforeBookingIn(BaseModel):
    equipment_node_id: int
    defect_type_id: int
    remarks: str = Field(..., min_length=1)

    @field_validator("remarks")
    @classmethod
    def remarks_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("remarks must not be blank")
        return stripped


class TestBeforeBookingsRequest(BaseModel):
    bookings: list[TestBeforeBookingIn] = Field(..., min_length=1)


class TestBeforeBookingsResponse(BaseModel):
    bookings_created: int
    section_assignments_created: int


class TestBeforeBookingOut(BaseModel):
    id: int
    status: str
    description: str
    equipment_node_id: int | None
    equipment_node_name: str | None
    equipment_path: list[EquipmentPathItemBrief]
    defect_type: DefectTypeBrief | None
    assignments: list[BookingAssignmentDetail]


class InternalGenerateWorkPackageRequest(BaseModel):
    """BL-DCMS names the authenticated BL-DCMS Admin who confirmed the retry, exactly as
    InternalSkipTestBeforeRequest does: Operations Dashboard re-checks that this user exists, is
    active and is an Admin, so the internal key alone never generates on behalf of nobody."""

    actor_user_id: int


class InternalGenerateWorkPackageResponse(BaseModel):
    shed_visit_id: int
    schedule_family: str | None = None
    generated: bool
    requirement_count: int


class FrozenRequirementOut(BaseModel):
    """One requirement of a visit's frozen work package, including its per-visit overrides."""

    requirement_id: int
    workflow_stage_type: str
    applicability_id: int | None = None
    template_id: int
    is_required: bool
    is_active: bool
    #: Migration 014. Travels with the package so BL-DCMS's work list, Test After gate,
    #: section-signoff readiness and checksheet-creation refusal all read one value.
    under_amc: bool = False
    section_id: int | None = None
    equipment_id: int | None = None
    maintenance_type: str | None = None
    minor_inspection_equipment_id: int | None = None


class FrozenWorkPackageOut(BaseModel):
    """GET /api/internal/shed-visits/{id}/checksheet-work-package/frozen - what BL-DCMS treats as
    the authoritative scope of an existing visit. `frozen` is False only when no package exists yet
    (e.g. Shed In could not reach BL-DCMS); BL-DCMS then resolves live, exactly as generation would."""

    shed_visit_id: int
    schedule_family: str | None = None
    frozen: bool
    package_id: int | None = None
    generated_at: datetime | None = None
    minor_inspection_configuration_complete: bool | None = None
    requirements: list[FrozenRequirementOut] = []
