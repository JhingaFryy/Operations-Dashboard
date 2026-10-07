from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSError, BLDCMSUnavailableError
from app.core.security import create_access_token, verify_password
from app.db.models import User
from app.core.roles import (
    ADMIN_ROLE as _ADMIN_ROLE,
    SUPERVISOR_ROLE as _SUPERVISOR_ROLE,
    TECHNICIAN_ROLE as _TECHNICIAN_ROLE,
    canonical_role,
)
from app.schemas.auth import Capabilities, CurrentUser, Permissions, SectionBrief, TokenResponse

# Re-exported from app.core.roles so existing importers keep working unchanged. That module is
# the single place a stored role string is interpreted - see its docstring for why exact
# case-sensitive comparison was fail-open.
TECHNICIAN_ROLE = _TECHNICIAN_ROLE
ADMIN_ROLE = _ADMIN_ROLE
SUPERVISOR_ROLE = _SUPERVISOR_ROLE

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid employee ID or password"
)

_FORBIDDEN_TECHNICIAN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Technicians do not have access to the Operations Dashboard web app.",
)

#: An account whose role this application does not recognise. Deliberately indistinguishable in
#: wording from the Technician refusal: a probe learns nothing about how the account is spelled.
_FORBIDDEN_UNKNOWN_ROLE = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="This account does not have access to the Operations Dashboard web app.",
)


def authorize_login_role(user: User) -> str:
    """The ONE rule deciding whether an authenticated account may hold an Operations Dashboard
    session. Returns the canonical role; raises otherwise.

    WHO MAY LOG IN
      * Admin      - yes.
      * Supervisor - yes, provided the account is active. A dashboard_access row is deliberately
                     NOT required to AUTHENTICATE (see below).
      * Technician - no.
      * anything else, including an unrecognised or blank role - no, fail closed.

    WHY dashboard_access NO LONGER GATES LOGIN. It used to, and the effect in production was that
    a legitimate active Supervisor with no entitlement row was refused at the sign-in screen with
    a bare "You don't have permission to do that." - which reads as a broken account, not as a
    missing grant, and is indistinguishable from a bug. Authentication answers "are you who you
    say you are, and may you hold a session at all"; dashboard_access answers "what may you do
    once inside". Those are different questions and are now asked in different places.

    dashboard_access IS STILL ENFORCED, unchanged, on the operational routes themselves (see
    core/authz.require_operations_user and the permission dependencies beside it). A Supervisor
    without the entitlement can sign in and will still be refused the operational pages. That is
    the intended separation, not an oversight - page and action authorization is untouched by
    this function.

    Nothing here consults section_id, can_manage_equipment_mapping, can_add_booking_sections,
    SHIFT membership or any movement permission. None of those has ever gated login, and none
    should: they restrict pages and actions.
    """
    role = canonical_role(user)
    if role is None:
        raise _FORBIDDEN_UNKNOWN_ROLE
    if role == TECHNICIAN_ROLE:
        raise _FORBIDDEN_TECHNICIAN
    return role


def has_dashboard_access(user: User) -> bool:
    if canonical_role(user) == ADMIN_ROLE:
        return True
    access = user.dashboard_access
    return bool(access and access.is_enabled)


#: Every handoff failure answers the same way - unknown, spent, expired, or an account that is
#: no longer active are indistinguishable, so a probe learns nothing from the difference.
_HANDOFF_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Sign-in from BL-DCMS is unavailable right now. Please try again, or sign in here.",
)

_INVALID_HANDOFF = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="This sign-in link is no longer valid. Please sign in to the Operations Dashboard.",
)


def login(db: Session, employee_id: str, password: str) -> TokenResponse:
    user = db.query(User).filter(User.employee_id == employee_id).first()

    if not user or not user.is_active:
        raise _INVALID_CREDENTIALS

    if not verify_password(password, user.password_hash):
        raise _INVALID_CREDENTIALS

    role = authorize_login_role(user)

    # The CANONICAL role goes into the token, never the raw stored spelling, so nothing
    # downstream has to re-normalize it.
    token = create_access_token({"sub": user.employee_id, "user_id": user.id, "role": role})
    return TokenResponse(access_token=token)


def login_with_handoff(db: Session, bldcms, handoff_code: str) -> TokenResponse:
    """Sign in a user who arrived from BL-DCMS holding a single-use handoff code.

    THE CODE PROVES IDENTITY, NOT PERMISSION. BL-DCMS answers only "this code was minted for
    employee X". Every rule that decides whether X may use THIS application is applied here,
    against this application's own tables, exactly as it is for a password login - the checks
    below are deliberately the same ones login() runs, in the same order. A user BL-DCMS is
    happy with can still be refused here, and that is correct: the two applications grant
    different things.

    The browser never asserts anything. It carries an opaque code; the identity behind it is
    fetched server-to-server over the internal-API-key channel. No BL-DCMS token is accepted,
    trusted or even seen - their signing keys are different and stay that way.
    """
    if not handoff_code or not handoff_code.strip():
        raise _INVALID_HANDOFF

    if bldcms is None:
        raise _HANDOFF_UNAVAILABLE
    try:
        identity = bldcms.redeem_operations_handoff(handoff_code.strip())
    except (BLDCMSUnavailableError, BLDCMSError):
        # BL-DCMS is the only thing that can say whose code this is. If it cannot be reached,
        # the honest answer is "try again", NOT a refusal that reads like a bad code and not a
        # 500. Nobody is signed in either way.
        raise _HANDOFF_UNAVAILABLE
    employee_id = (identity or {}).get("employee_id")
    if not employee_id:
        raise _INVALID_HANDOFF

    user = db.query(User).filter(User.employee_id == employee_id).first()
    if not user or not user.is_active:
        raise _INVALID_HANDOFF

    # Literally the same rule as login(), by calling the same function - not a copy of it that
    # could drift. A code BL-DCMS is happy with still has to satisfy this application's own
    # authorization.
    role = authorize_login_role(user)

    token = create_access_token({"sub": user.employee_id, "user_id": user.id, "role": role})
    return TokenResponse(access_token=token)


def to_current_user(user: User) -> CurrentUser:
    # Imported here, not at module scope: app.core.authz imports this module for the role
    # constants, so a top-level import would be circular.
    from app.core.authz import can_route_bookings as authz_can_route_bookings
    from app.core.authz import capabilities_for

    access = user.dashboard_access
    is_admin = user.role == ADMIN_ROLE
    return CurrentUser(
        id=user.id,
        employee_id=user.employee_id,
        name=user.name,
        role=user.role,
        section=SectionBrief(id=user.section.id, code=user.section.code, name=user.section.name)
        if user.section
        else None,
        dashboard_access=has_dashboard_access(user),
        capabilities=Capabilities(**capabilities_for(user)),
        permissions=Permissions(
            can_add_booking_sections=is_admin or bool(access and access.can_add_booking_sections),
            # DELEGATED, not recomputed. This used to read the dashboard_access flag directly,
            # which quietly disagreed with authz.can_route_bookings once PPIO identity became
            # sufficient: a normal PPIO Supervisor was told can_route_bookings=false here while
            # the routes themselves allowed them, so the sidebar hid the Booking Pool they could
            # actually use. One rule, one place.
            can_route_bookings=authz_can_route_bookings(user),
            can_manage_equipment_mapping=is_admin
            or bool(access and access.can_manage_equipment_mapping),
        ),
    )
