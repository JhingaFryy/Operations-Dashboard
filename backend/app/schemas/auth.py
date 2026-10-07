from pydantic import BaseModel


class LoginRequest(BaseModel):
    employee_id: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SectionBrief(BaseModel):
    id: int
    code: str
    name: str


class Capabilities(BaseModel):
    """What this account may do, decided ENTIRELY server-side by app/core/authz.py.

    The frontend uses these for PRESENTATION only - hiding a control the user cannot use. Every
    one is independently re-enforced on the endpoint itself, so a tampered client gains nothing.
    """

    can_access_operations_dashboard: bool = False
    can_manage_loco_movement: bool = False
    #: The RESOLVED section kind. The frontend reads this rather than inferring "planner" from
    #: the absence of some other capability - see src/auth/permissions.isPlanningUser.
    is_planning_section: bool = False
    can_manage_own_section_bookings: bool = False
    #: Cross-section operational visibility - Admin only, never a Supervisor.
    can_access_all_sections: bool = False
    can_admin: bool = False


class Permissions(BaseModel):
    can_add_booking_sections: bool
    can_route_bookings: bool = False
    can_manage_equipment_mapping: bool


class CurrentUser(BaseModel):
    id: int
    employee_id: str
    name: str
    role: str
    section: SectionBrief | None
    dashboard_access: bool
    permissions: Permissions
    capabilities: Capabilities = Capabilities()
