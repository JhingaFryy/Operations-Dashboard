"""Common Booking Pool + Equipment-Bifurcated Dashboard Reform.

The global, section-agnostic booking list/lifecycle contract. Every booking belonging to a shed
visit is visible to every authorized Dashboard user (Admin, and any Supervisor) regardless of
users.section_id or booking_section_assignments - see app/services/booking_pool_service.py's
module docstring. Equipment stays on every item (equipment_node_id/name/path) purely as display/
grouping metadata; it never determines visibility.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.bookings import EquipmentPathItemBrief
from app.schemas.section_dashboard import DefectTypeBrief

BOOKING_STATUSES = ("OPEN", "IN_PROGRESS", "ATTENDED", "REOPENED")

# The application-level booking_source vocabulary. A SUBSET of the database CHECK constraint
# (db/models.py chk_booking_source, migration 005), which additionally still permits the legacy
# SPECIAL_CHECKING value - deliberately absent here because no code path creates it and nothing
# should start.
#
# TRIP_INSPECTION / GENERAL_CHECKING (migration 005) name the originating performa of a finding
# raised during a Trip Inspection or General Checking. They are NOT Minor-schedule workflow stage
# types: unlike TEST_BEFORE / SCHEDULE_INSPECTION / TEST_AFTER, they correspond to no
# shed_visit_stages.stage_type, so checksheet_stage_reconciliation_service's literal
# booking_source == stage_type match never selects them and they gate no stage. That is correct
# and intentional - see the TI/GC domain note in app/api/internal.py.
BOOKING_SOURCES = (
    "LOG_BOOK",
    "TEST_BEFORE",
    "SCHEDULE_INSPECTION",
    "TEST_AFTER",
    "MANUAL",
    "TRIP_INSPECTION",
    "GENERAL_CHECKING",
)


class BookingPoolShedVisitBrief(BaseModel):
    id: int
    loco_number: str
    schedule_family: str | None
    schedule_variant: str | None


class RoutedSectionBrief(BaseModel):
    """A section this booking was ROUTED to, read from booking_section_assignments.

    HISTORICAL, NOT DERIVED. This is the section the booking was actually sent to at the moment
    it was created or forwarded - a real row, written once with an assignment_source of
    AUTO_MAPPING or MANUAL. It is deliberately NOT the equipment's CURRENT section mapping:
    equipment mappings change, and an old booking must keep showing where the work actually went.

    A booking can legitimately carry several of these; multi-section routing is supported
    (uq_booking_section is unique per booking+section, not per booking).
    """

    section_id: int
    section_code: str
    section_name: str
    #: AUTO_MAPPING (resolved from the equipment mapping at creation) or MANUAL (a person routed
    #: it). Carried for completeness and diagnostics; the UI does not normally show it.
    assignment_source: str
    #: This section's own execution status - the booking's overall status is an aggregate of them.
    status: str


class BookingPoolItemOut(BaseModel):
    id: int
    status: str
    description: str
    booking_source: str
    workflow_stage_type: str | None  # None for LOG_BOOK bookings (no stage_id)
    equipment_node_id: int | None
    equipment_node_name: str | None
    equipment_path: list[EquipmentPathItemBrief]
    #: Every section this booking was routed to, in a stable order. Empty only for a booking that
    #: has no assignment rows at all, which the creation path does not produce.
    routed_sections: list[RoutedSectionBrief] = []
    defect_type: DefectTypeBrief | None
    shed_visit: BookingPoolShedVisitBrief
    created_at: datetime

    started_by_name: str | None
    started_by_section_code: str | None
    started_at: datetime | None

    attended_by_name: str | None
    attended_by_section_code: str | None
    attended_at: datetime | None
    attendance_remarks: str | None

    # PROVENANCE, DERIVED - not a stored flag and not a booking_source.
    #
    # bookings.created_by already records who raised a booking, so "was this raised by planning?"
    # is answerable from data that exists: resolve the creator's section and ask whether it is a
    # planning section. No column, no new source value, no migration.
    #
    # booking_source is deliberately left alone. A planner-raised booking is semantically MANUAL -
    # a human raising a finding outside a stage - and overloading the source would corrupt a field
    # that drives stage reconciliation, the Minor work package and Test After gating. Provenance
    # and source are different questions and stay separate fields.
    created_by_name: str | None = None
    created_by_section_code: str | None = None
    #: True when the creator belonged to a planning section. Drives the "Added by PPIO" badge.
    created_by_planning_section: bool = False


class StartBookingRequest(BaseModel):
    """No fields today - the acting section is always derived from the
    authenticated user, never accepted from the client (see the reform
    brief's CLAIM/START BEHAVIOR section). A named request model is kept
    (rather than no body at all) so a future field can be added without an
    endpoint-shape change."""


class AttendBookingRequest(BaseModel):
    remarks: str = Field(..., min_length=1)

    @field_validator("remarks")
    @classmethod
    def remarks_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("remarks must not be blank")
        return stripped


class ReopenBookingRequest(BaseModel):
    reason: str = Field(..., min_length=1)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped
