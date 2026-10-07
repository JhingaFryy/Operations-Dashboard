from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.schedule import validate_schedule_pair

# Schedule domain: MINOR (IA/IA0/IB/IC/IC0) and MAJOR (IOH/TOH). SCHEDULE_MATRIX in
# app/domain/schedule.py is the single source of truth for which
# (schedule_family, schedule_variant) pairs are valid — see that module's
# docstring. Do not add new family/variant checks here; extend the matrix
# instead.
ARRIVAL_CONDITIONS = {"WORKING", "DEAD"}


class LogBookBookingIn(BaseModel):
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


class ShedInRequest(BaseModel):
    loco_number: str = Field(..., min_length=1)
    schedule_family: str
    schedule_variant: str
    arrival_condition: str
    arrival_at: datetime
    log_book_bookings: list[LogBookBookingIn] = []

    @model_validator(mode="after")
    def check_schedule_pair(self) -> "ShedInRequest":
        # Centralized matrix validation (app/domain/schedule.py) — the only
        # place that decides which schedule_family/schedule_variant pairs
        # are legal. Cross-field, so it has to run as a model validator
        # rather than two independent field_validators.
        validate_schedule_pair(self.schedule_family, self.schedule_variant)
        return self

    @field_validator("arrival_condition")
    @classmethod
    def validate_arrival_condition(cls, value: str) -> str:
        if value not in ARRIVAL_CONDITIONS:
            raise ValueError(
                f"Unsupported arrival_condition {value!r}; expected one of "
                f"{sorted(ARRIVAL_CONDITIONS)}."
            )
        return value


class ShedInResponse(BaseModel):
    id: int
    loco_number: str
    status: str
    arrival_at: datetime
    schedule_family: str
    schedule_variant: str
    arrival_condition: str
    bookings_created: int
    section_assignments_created: int
    stages_created: int


class VisitTimingsOut(BaseModel):
    """Durations in whole seconds, COMPUTED from timestamps - never stored, because a running
    duration is wrong the moment it is written. A *_running flag marks a span whose closing
    timestamp has not happened yet, so "4h 38m so far" is distinguishable from "took 4h 38m"."""

    waiting_seconds: int | None = None          # arrival -> schedule start
    waiting_running: bool = False
    # schedule start -> inspection complete (MINOR, migration 012+) / schedule complete (MAJOR)
    schedule_seconds: int | None = None
    schedule_running: bool = False
    ready_delay_seconds: int | None = None      # schedule complete -> departure
    ready_delay_running: bool = False
    total_seconds: int | None = None            # arrival -> departure
    total_running: bool = False


class CurrentShedVisitOut(BaseModel):
    id: int
    loco_number: str
    schedule_family: str | None
    schedule_variant: str | None
    arrival_condition: str | None
    arrival_at: datetime
    status: str

    # New shed workflow. `status` keeps its physical meaning (IN_SHED/READY/CLOSED);
    # operational_phase is DERIVED from the timestamps by services/shed_visit_phase.py - the one
    # definition in the system - and display_label composes it with the schedule variant
    # ("Spare IA", "IA In Progress", "Ready"). No SPARE_IA-style value is ever persisted.
    schedule_started_at: datetime | None = None
    # MINOR (migration 012): the actual Minor Inspection ended (Complete Schedule). Not READY.
    inspection_completed_at: datetime | None = None
    ready_at: datetime | None = None
    departed_at: datetime | None = None
    operational_phase: str
    display_label: str
    # Actions this phase legitimately offers, so the UI never renders an invalid one.
    available_actions: list[str] = []
    timings: VisitTimingsOut = VisitTimingsOut()

    booking_total: int
    pending_booking_count: int


class ActiveShedVisitOut(BaseModel):
    """Phase 5B.4B: shape returned to BL-DCMS by the internal
    GET /api/internal/shed-visits/active read endpoint, so its Android app can offer a
    locomotive-selection screen for the Minor Schedule workflow without already knowing a visit
    id. Deliberately only the fields BL-DCMS needs for that screen - no internal audit columns
    (created_by/updated_by/timestamps other than arrival, arrival_source, etc.) and no
    locomotive-technology field, since that isn't available in this read path without a new
    Loco Master call this endpoint must not make."""

    shed_visit_id: int
    loco_number: str
    schedule_family: str | None
    schedule_variant: str | None
    status: str
    arrival_at: datetime
    # Migration 012 - what BL-DCMS needs to gate Test Before / Test After work server-side:
    # TEST_BEFORE stage status (PENDING/IN_PROGRESS/COMPLETED/SKIPPED; None when the visit has no
    # such stage), and whether the actual Minor Inspection is complete (Complete Schedule, or a
    # visit completed under the pre-012 workflow, which only ever recorded ready_at).
    test_before_status: str | None = None
    test_before_skipped: bool = False
    # Grandfathered legacy start (workflow_common.test_before_legacy_waived): BL-DCMS treats Test
    # Before as not outstanding for Test After. Never true for a visit started through the gate.
    test_before_legacy_waived: bool = False
    inspection_completed: bool = False
    inspection_completed_at: datetime | None = None


class StageBlockerOut(BaseModel):
    stage_type: str
    status: str  # actual stage status, or "MISSING" if the stage row doesn't exist at all


class BookingBlockerOut(BaseModel):
    """Business Rule Alignment: a booking blocks Shed Out because at least one of its
    booking_section_assignments rows isn't ATTENDED yet, OR it has zero assignments at all
    (status "NO_ASSIGNMENTS" - a routing gap, since booking creation always creates at least
    one). The gate itself is always derived from the assignment rows, never from booking.status
    alone, even though that status is kept in sync (see shed_out_service.py)."""

    booking_id: int
    booking_source: str
    status: str  # the booking's derived status (OPEN / IN_PROGRESS / REOPENED), or "NO_ASSIGNMENTS"


class ChecksheetRequirementBlockerOut(BaseModel):
    """Operational Control phase: a checksheet requirement this visit must satisfy before Shed
    Out, or a reason the requirement set could not be evaluated at all.

    kind is one of:
      WORK_PACKAGE_NOT_GENERATED - no requirement snapshot exists, so the required set is
                                   UNKNOWN. Deliberately a blocker: an ungenerated package must
                                   never read as "nothing to do".
      BLDCMS_UNAVAILABLE         - the snapshot exists but its checksheets could not be read, so
                                   satisfaction is unknown.
      REQUIREMENT_PENDING        - a specific active+required requirement is not yet satisfied.
    """

    kind: str
    requirement_id: int | None = None
    template_id: int | None = None
    template_name: str | None = None
    section_name: str | None = None
    equipment_name: str | None = None
    maintenance_type: str | None = None
    workflow_stage_type: str | None = None
    checksheet_status: str | None = None


class ChecksheetRequirementSummaryOut(BaseModel):
    work_package_generated: bool = False
    total: int = 0
    # blocking = active AND required - the only rows that can hold Shed Out.
    blocking: int = 0
    optional: int = 0
    deactivated: int = 0
    satisfied: int = 0


class ShedOutEligibilityOut(BaseModel):
    shed_visit_id: int
    eligible: bool
    stage_blockers: list[StageBlockerOut]
    booking_blockers: list[BookingBlockerOut]
    # Additive: pre-existing consumers that only read stage/booking blockers are unaffected.
    checksheet_blockers: list[ChecksheetRequirementBlockerOut] = []
    checksheet_summary: ChecksheetRequirementSummaryOut = ChecksheetRequirementSummaryOut()


class StartScheduleRequest(BaseModel):
    """The UI defaults started_at to the current local time but keeps it editable - shed staff
    routinely record an action after the fact - so the same ordering validation applies whether
    the value was defaulted or typed."""

    started_at: datetime
    remarks: str | None = None


class CompleteScheduleRequest(BaseModel):
    completed_at: datetime
    remarks: str | None = None


class MarkReadyRequest(BaseModel):
    """MINOR only: the locomotive is READY after Test After."""

    ready_at: datetime
    remarks: str | None = None


class ScheduleTransitionResponse(BaseModel):
    id: int
    loco_number: str
    status: str
    schedule_family: str | None = None
    schedule_variant: str | None = None
    arrival_at: datetime
    schedule_started_at: datetime | None = None
    inspection_completed_at: datetime | None = None
    ready_at: datetime | None = None
    departed_at: datetime | None = None
    operational_phase: str
    display_label: str
    available_actions: list[str] = []
    timings: VisitTimingsOut = VisitTimingsOut()


class ShedOutRequest(BaseModel):
    departed_at: datetime
    remarks: str | None = None


class ShedOutResponse(BaseModel):
    id: int
    loco_number: str
    status: str
    departed_at: datetime
    departure_source: str
