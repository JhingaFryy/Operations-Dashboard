from datetime import datetime

from pydantic import BaseModel


class SectionBrief(BaseModel):
    id: int
    code: str
    name: str


class DashboardAccessUserOut(BaseModel):
    id: int
    employee_id: str
    name: str
    role: str
    section: SectionBrief | None
    is_active: bool
    dashboard_access_enabled: bool
    can_add_booking_sections: bool
    can_manage_equipment_mapping: bool
    # Migration 015. Distinct from can_add_booking_sections above, which gates the retired
    # add-section route; this one gates PUT /api/bookings/{id}/sections.
    can_route_bookings: bool = False
    granted_at: datetime | None
    revoked_at: datetime | None


class DashboardAccessUpdateRequest(BaseModel):
    is_enabled: bool
    can_add_booking_sections: bool = False
    can_manage_equipment_mapping: bool = False
    # Defaults to False, so an existing Admin client that does not send this field REVOKES
    # routing rather than silently preserving it - the same fail-closed convention the two flags
    # above already follow.
    can_route_bookings: bool = False
