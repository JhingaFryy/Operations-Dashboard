import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db
from app.core.roles import canonical_role
from app.services.auth_service import ADMIN_ROLE, TECHNICIAN_ROLE, has_dashboard_access

_bearer_scheme = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _UNAUTHENTICATED

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _UNAUTHENTICATED

    employee_id = payload.get("sub")
    if not employee_id:
        raise _UNAUTHENTICATED

    user = db.query(User).filter(User.employee_id == employee_id).first()
    if not user or not user.is_active:
        raise _UNAUTHENTICATED

    # The same fail-closed rule the login path applies, re-applied on every request. It must be
    # here as well as at login: a role can change (or be corrected) after a token was issued, and
    # a token outlives the decision that minted it. canonical_role also normalizes the in-memory
    # instance, so every `.role ==` comparison downstream of this dependency sees the canonical
    # spelling rather than whatever the row happens to hold.
    role = canonical_role(user)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account does not have access to the Operations Dashboard web app.",
        )
    if role == TECHNICIAN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Technicians do not have access to the Operations Dashboard web app.",
        )

    return user


def require_dashboard_access(current_user: User = Depends(get_current_user)) -> User:
    if not has_dashboard_access(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Dashboard access is required."
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != ADMIN_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user


def require_equipment_mapping_permission(
    current_user: User = Depends(get_current_user),
) -> User:
    """Admin-page capability, unchanged by the base-access relaxation.

    Deliberately NOT chained through require_dashboard_access any more. Base Operations Dashboard
    access is now a matter of role, so routing a CAPABILITY check through an access check would
    read as if the entitlement still gated ordinary use. The capability flag lives on the
    dashboard_access row, so a Supervisor with no row still has no capability - the outcome is
    identical, the reasoning is now honest.
    """
    if current_user.role == ADMIN_ROLE:
        return current_user

    access = current_user.dashboard_access
    if not access or not access.can_manage_equipment_mapping:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Equipment mapping management permission is required.",
        )
    return current_user


def require_add_booking_sections_permission(
    current_user: User = Depends(get_current_user),
) -> User:
    """Explicit capability, unchanged - see require_equipment_mapping_permission above."""
    if current_user.role == ADMIN_ROLE:
        return current_user

    access = current_user.dashboard_access
    if not access or not access.can_add_booking_sections:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission to add booking sections is required.",
        )
    return current_user


# Phase 5B.4A: dedicated service-to-service auth for BL-DCMS's internal reconciliation callback
# (app/api/internal.py) - deliberately separate from the human JWT above. A human Bearer token,
# even an Admin's, does not satisfy this dependency, and this dependency alone never grants
# access to any human-facing route.
_INTERNAL_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
)


def require_internal_api_key(
    x_internal_api_key: str | None = Header(default=None, alias="X-Internal-API-Key"),
) -> None:
    configured = get_settings().operations_internal_api_key

    # Every failure path - key not configured at all, header missing, header present but wrong -
    # raises the exact same 401 with the exact same message. A caller (or an attacker) must never
    # be able to tell "this deployment has no internal key configured yet" apart from "you sent
    # the wrong one". The key itself is never included in this exception or logged anywhere.
    if not configured or not x_internal_api_key:
        raise _INTERNAL_UNAUTHENTICATED
    if not secrets.compare_digest(x_internal_api_key, configured):
        raise _INTERNAL_UNAUTHENTICATED
