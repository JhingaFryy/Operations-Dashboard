from datetime import datetime

from pydantic import BaseModel, Field, field_validator

ASSIGNMENT_STATUSES = ("OPEN", "IN_PROGRESS", "ATTENDED", "REOPENED")


class DefectTypeBrief(BaseModel):
    id: int
    code: str
    name: str


class ShedVisitBrief(BaseModel):
    id: int
    loco_number: str
    schedule_family: str | None
    schedule_variant: str | None
    arrival_condition: str | None


class BookingBrief(BaseModel):
    id: int
    status: str
    description: str
    booking_source: str
    equipment_node_id: int | None
    equipment_node_name: str | None
    defect_type: DefectTypeBrief | None
    shed_visit: ShedVisitBrief


class SectionAssignmentOut(BaseModel):
    id: int
    booking_id: int
    section_id: int
    section_code: str
    status: str
    assigned_at: datetime
    started_at: datetime | None
    started_by_name: str | None
    attended_at: datetime | None
    attended_by_name: str | None
    attendance_remarks: str | None
    booking: BookingBrief


class AttendRequest(BaseModel):
    remarks: str = Field(..., min_length=1)

    @field_validator("remarks")
    @classmethod
    def remarks_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("remarks must not be blank")
        return stripped


class ReopenRequest(BaseModel):
    reason: str = Field(..., min_length=1)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped
