"""Request/response contract for POST /api/internal/bookings (see app/api/internal.py).

The service-to-service booking-creation contract. Its only intended caller today is BL-DCMS,
relaying a booking an Android technician staged locally while filling a checksheet and which was
held on the device until that checksheet was submitted. BL-DCMS attaches the internal API key
server-side; the Android app never holds or sends one.

Every field here is INDEPENDENTLY validated by Operations Dashboard - BL-DCMS is a semi-trusted
caller, not a trusted one (defence in depth). In particular Dashboard, not the caller, decides:
which section(s) the booking routes to (equipment_service.resolve_sections, unchanged), whether
the shed visit may still accept bookings, whether the equipment node exists, and whether the
defect type is known and active. The caller supplies facts; it never supplies decisions.
"""

from pydantic import BaseModel, Field, field_validator

from app.schemas.booking_pool import BOOKING_SOURCES


def _require_non_blank(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be blank")
    return stripped


class InternalBookingCreateRequest(BaseModel):
    # The idempotency key. Generated and stored by the originating client (Android's
    # StagedBooking.localBookingId) and relayed UNCHANGED - Dashboard never generates one, and
    # BL-DCMS must never substitute its own. See migration 004 for the retry semantics this
    # buys. Length-capped to match the bookings.client_booking_id VARCHAR(64) column so an
    # over-long key is a clean 422 rather than a database error.
    client_booking_id: str = Field(..., min_length=1, max_length=64)

    shed_visit_id: int

    # A Loco Master equipment_nodes.id. NOT BL-DCMS's own equipment.id - a different identifier
    # space entirely (see the Loco Master equipment-identity audit in the phase report). Dashboard
    # validates it against Loco Master via its existing client before creating anything.
    equipment_node_id: int

    booking_source: str
    defect_type_id: int

    # The technician's own words. Stored as bookings.description, which the database requires to
    # be non-blank (chk_booking_description).
    description: str = Field(..., min_length=1)

    # Audit-only context. Neither participates in routing, gating or the lifecycle. checksheet_id
    # is BL-DCMS's own checksheet_header.id, recorded on the CREATED booking event so a booking
    # can always be traced back to the performa it came from.
    checksheet_id: int | None = None

    # The acting technician, identified the way both systems already identify a person: by
    # users.employee_id (BL-DCMS and Operations Dashboard read the SAME `users` table in the
    # shared rdcms instance - see app/db/models.py's module docstring). Optional: when omitted the
    # booking is attributed to no user (created_by NULL) rather than to a fabricated one. When
    # present but unknown, the request is rejected rather than silently downgraded.
    technician_employee_id: str | None = None

    @field_validator("client_booking_id")
    @classmethod
    def _client_booking_id_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "client_booking_id")

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "description")

    @field_validator("booking_source")
    @classmethod
    def _known_booking_source(cls, value: str) -> str:
        stripped = _require_non_blank(value, "booking_source")
        if stripped not in BOOKING_SOURCES:
            raise ValueError(
                f"booking_source must be one of: {', '.join(BOOKING_SOURCES)}"
            )
        return stripped

    @field_validator("technician_employee_id")
    @classmethod
    def _technician_employee_id_blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class InternalBookingCreateResponse(BaseModel):
    booking_id: int
    client_booking_id: str
    status: str
    booking_source: str
    equipment_node_id: int | None
    section_codes: list[str]

    # False when this request matched an existing booking created by an earlier attempt with the
    # same client_booking_id. The caller should treat that outcome as SUCCESS, not as an error -
    # it means the earlier attempt did reach the Dashboard, whatever the client observed.
    created: bool
    section_assignments_created: int
