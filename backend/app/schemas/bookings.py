from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.section_dashboard import DefectTypeBrief, ShedVisitBrief


class AddSectionRequest(BaseModel):
    section_id: int
    reason: str = Field(..., min_length=1)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class BookingHistoryEventOut(BaseModel):
    id: int
    event_type: str
    created_at: datetime
    actor_name: str | None
    remarks: str | None
    from_section_code: str | None
    to_section_code: str | None
    event_data: dict | None


class SectionSummaryOut(BaseModel):
    open: int
    in_progress: int
    attended_today: int
    reopened: int


class EquipmentPathItemBrief(BaseModel):
    id: int
    name: str


class BookingAssignmentDetail(BaseModel):
    id: int
    section_id: int
    section_code: str
    assignment_source: str
    status: str
    assigned_at: datetime
    started_at: datetime | None
    started_by_name: str | None
    attended_at: datetime | None
    attended_by_name: str | None
    attendance_remarks: str | None


class BookingDetailOut(BaseModel):
    id: int
    status: str
    description: str
    booking_source: str
    equipment_node_id: int | None
    equipment_node_name: str | None
    equipment_path: list[EquipmentPathItemBrief]
    defect_type: DefectTypeBrief | None
    shed_visit: ShedVisitBrief
    assignments: list[BookingAssignmentDetail]


class AddSectionAssignmentsRequest(BaseModel):
    """Sections to ADD to a booking. Not a desired set.

    THE SHAPE IS THE SAFEGUARD. An earlier version of this endpoint took the complete desired set
    and derived removals from it, which meant a client that merely omitted an existing section
    un-routed it - and a stale browser tab holding an older set would do that by accident. There
    is no way to express a removal here: the field names what to add, and the service never
    deletes a row. Admin's audited deletion flow remains the only way an assignment goes away.
    """

    section_ids: list[int] = Field(..., min_length=1)
    # Optional, stored on the audit event's `remarks`. The before/after section sets already
    # record WHAT changed; a reason records why, when the planner offers one.
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("section_ids")
    @classmethod
    def sections_are_sane(cls, value: list[int]) -> list[int]:
        if any(v is None or v <= 0 for v in value):
            raise ValueError("section_ids must be positive section ids")
        # De-duplicated here so "M2, M2" is not reported as two additions of the same section.
        return list(dict.fromkeys(value))

    @field_validator("reason")
    @classmethod
    def reason_blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class RoutedSectionOut(BaseModel):
    section_id: int
    section_code: str
    assignment_source: str
    status: str


class AddSectionAssignmentsResponse(BaseModel):
    """What the addition did - the same picture written to the audit event.

    No `removed_section_ids` field: this operation cannot remove anything, and a field that is
    always empty would suggest removal is merely unused rather than impossible.
    """

    booking_id: int
    booking_source: str
    previous_section_ids: list[int]
    new_section_ids: list[int]
    added_section_ids: list[int]
    # Requested sections the booking already had - reported, not an error.
    already_assigned_section_ids: list[int]
    sections: list[RoutedSectionOut]


class CreatePlanningBookingRequest(BaseModel):
    """A booking raised by planning.

    Carries only what a booking NEEDS. There is deliberately no status, no timestamps, no
    assignment_source and no booking_source: the source is fixed to MANUAL server-side and every
    lifecycle field is written by the creation service, so a planner cannot set them even by
    sending them.
    """

    shed_visit_id: int = Field(..., gt=0)
    equipment_node_id: int = Field(..., gt=0)
    defect_type_id: int = Field(..., gt=0)
    description: str = Field(..., min_length=1, max_length=2000)
    #: Sections to add ON TOP of whatever the equipment auto-maps to. The auto-mapped section is
    #: always kept; these are additional.
    additional_section_ids: list[int] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("description")
    @classmethod
    def description_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("description is required")
        return stripped

    @field_validator("additional_section_ids")
    @classmethod
    def sections_are_sane(cls, value: list[int]) -> list[int]:
        if any(v is None or v <= 0 for v in value):
            raise ValueError("additional_section_ids must be positive section ids")
        return list(dict.fromkeys(value))


class CreatePlanningBookingResponse(BaseModel):
    booking_id: int
    booking_source: str
    #: Every section the booking ended up with - the auto-mapped one(s) plus the planner's.
    sections: list[RoutedSectionOut]
    auto_mapped_section_ids: list[int]
    added_section_ids: list[int]

