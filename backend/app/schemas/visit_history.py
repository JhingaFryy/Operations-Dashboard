"""Shed visit history (archive) - the response shapes.

Summary rows are cheap (one page query plus batched lookups); everything detailed - timings,
bookings, checksheets, events - comes only from the detail endpoint when a visit is opened.
Every optional field means "not recorded"; nothing here ever reports a fabricated zero.
"""

from datetime import datetime

from pydantic import BaseModel


class VisitClosure(BaseModel):
    """How a closed visit ended. `kind` is SHED_OUT for a normal departure and ADMIN_RESET for an
    administrative system reset - which is never presented as a Shed Out."""

    kind: str  # "SHED_OUT" | "ADMIN_RESET" | "OTHER"
    label: str
    at: datetime | None = None
    source: str | None = None
    actor_name: str | None = None
    reason: str | None = None


class VisitHistoryRow(BaseModel):
    shed_visit_id: int
    loco_number: str
    loco_model: str | None = None
    technology: str | None = None
    schedule_family: str | None = None
    schedule_variant: str | None = None
    visit_type: str | None = None
    status: str
    operational_phase: str
    display_label: str
    arrival_at: datetime
    arrival_condition: str | None = None
    schedule_started_at: datetime | None = None
    ready_at: datetime | None = None
    departed_at: datetime | None = None
    departure_source: str | None = None
    closure: VisitClosure | None = None
    # Counts are scoped exactly like the detail: a Supervisor's count covers their own section.
    booking_count: int = 0
    # None when BL-DCMS could not be asked - never a misleading 0.
    checksheet_count: int | None = None


class VisitHistoryPage(BaseModel):
    items: list[VisitHistoryRow]
    total: int
    page: int
    page_size: int
    # False when BL-DCMS was unreachable: models/counts on this page are then unknown, not absent.
    locomotive_details_available: bool = True
    checksheet_counts_available: bool = True


class TimingMilestone(BaseModel):
    key: str
    label: str
    at: datetime | None = None
    actor_name: str | None = None
    note: str | None = None


class TimingSpan(BaseModel):
    key: str
    label: str
    seconds: int | None = None
    running: bool = False


class VisitTimings(BaseModel):
    milestones: list[TimingMilestone]
    spans: list[TimingSpan]


class VisitEventOut(BaseModel):
    at: datetime
    event_type: str
    sentence: str
    actor_name: str
    section_name: str | None = None
    remarks: str | None = None
    raw: dict | None = None


class ReopenRecord(BaseModel):
    at: datetime
    by_name: str
    reason: str | None = None


class AssignmentHistory(BaseModel):
    assignment_id: int
    section_id: int
    section_code: str | None = None
    section_name: str | None = None
    status: str
    assignment_source: str | None = None
    assigned_at: datetime | None = None
    assigned_by_name: str | None = None
    started_at: datetime | None = None
    started_by_name: str | None = None
    attended_at: datetime | None = None
    attended_by_name: str | None = None
    attendance_remarks: str | None = None
    reopens: list[ReopenRecord] = []


class EquipmentRef(BaseModel):
    node_id: int | None = None
    name: str | None = None
    path: list[str] = []


class BookingHistory(BaseModel):
    booking_id: int
    booking_source: str
    booking_source_label: str
    stage_type: str | None = None
    equipment: EquipmentRef
    defect_type: str | None = None
    remarks: str
    status: str
    created_at: datetime
    created_by_name: str
    origin_checksheet_id: int | None = None
    responsible_sections: list[str]
    assignments: list[AssignmentHistory]
    history: list[VisitEventOut]


class BookingSectionGroup(BaseModel):
    section_id: int | None = None
    section_code: str | None = None
    section_name: str
    booking_count: int
    bookings: list[BookingHistory]


class ChecksheetRequirementFlags(BaseModel):
    is_required: bool
    is_active: bool


class ChecksheetHistory(BaseModel):
    checksheet_id: int
    template_id: int | None = None
    template_name: str | None = None
    schedule_family: str | None = None
    workflow_stage_type: str | None = None
    # A stable grouping key: TEST_BEFORE / SCHEDULE_INSPECTION / TEST_AFTER for Minor, MAJOR for
    # Major, and the family itself for anything else (a future TI/GC) - never dropped.
    workflow_group: str
    section_id: int | None = None
    section_name: str | None = None
    equipment_label: str | None = None
    maintenance_type: str | None = None
    status: str
    created_at: datetime | None = None
    created_by_name: str | None = None
    submitted_at: datetime | None = None
    submitted_by_name: str | None = None
    approved_at: datetime | None = None
    approved_by_name: str | None = None
    rejected_at: datetime | None = None
    rejected_by_name: str | None = None
    rejection_reason: str | None = None
    signed: bool = False
    signed_at: datetime | None = None
    signed_by_name: str | None = None
    signature_verification: str | None = None
    signed_document_available: bool = False
    # An authenticated API route (never a file path); None when there is no signed document.
    signed_document_url: str | None = None
    requirement: ChecksheetRequirementFlags | None = None


class RequirementFlagEntry(BaseModel):
    """One requirement of the visit's work package, by its full identity - for a caller that
    composes the checksheet list itself (BL-DCMS) and annotates it the same way."""

    template_id: int | None = None
    workflow_stage_type: str | None = None
    section_id: int | None = None
    equipment_id: int | None = None
    maintenance_type: str | None = None
    minor_inspection_equipment_id: int | None = None
    is_required: bool
    is_active: bool


class VisitChecksheets(BaseModel):
    available: bool
    message: str | None = None
    items: list[ChecksheetHistory] = []


class VisitHistoryDetail(BaseModel):
    visit: VisitHistoryRow
    timings: VisitTimings
    bookings: list[BookingSectionGroup]
    booking_scope: str  # "ALL_SECTIONS" | "OWN_SECTION"
    checksheets: VisitChecksheets
    events: list[VisitEventOut]
    # For the Event Log that follows: the filter that opens this visit's events there.
    event_log_filter: dict
    # Filled only when checksheets are NOT composed here (the internal route): the work package's
    # requirement flags, so the composing service annotates checksheets identically.
    requirement_flags: list[RequirementFlagEntry] = []
