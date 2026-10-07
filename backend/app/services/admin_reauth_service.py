"""Password re-authentication for destructive Admin actions.

WHY RE-AUTHENTICATE AT ALL. A valid session proves someone logged in; it does not prove the person
at the keyboard right now is that person. For a reversible action that is an acceptable trade. For
permanently destroying a shed visit's records it is not - an unattended desk, a borrowed laptop or a
stolen token would otherwise be enough.

THE CALLER IS NEVER THE AUTHORITY ON WHO THEY ARE. Everything about identity comes from the verified
token: this module takes the already-authenticated User and the submitted password, and nothing
else. It accepts no employee_id, no user_id and no role from the request body, so there is no
parameter through which a browser could nominate a different account. That is why the signature
below has no identifier argument at all - the absence is the defence, and a future caller cannot
pass one by accident.

WHAT IS CHECKED, IN ORDER, AND WHY THE ORDER MATTERS
  1. The account is re-read from the database inside the request, not taken from a cached token
     claim. A role revoked or an account deactivated one minute ago must take effect now.
  2. The role is still Admin.
  3. The account is still active.
  4. The password verifies against THAT row's hash.
Each check is against the same freshly-read row, so there is no window in which (1) names one
account and (4) verifies another's secret.

ONE GENERIC FAILURE. Wrong password, deactivated account, demoted role and missing hash all produce
the identical response. Distinguishing them would turn this endpoint into an account enumerator and
a role oracle: "your password is right but you are no longer Admin" tells an attacker their guess
was correct. The reason IS logged server-side, where only an administrator with log access can read
it, so operability does not suffer.

NOTHING HERE LOGS, STORES OR RETURNS THE PASSWORD. It arrives as a SecretStr, is read exactly once
at the comparison, and is never placed in an exception, a log record, an audit row or a return
value.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.rate_limit import (
    REAUTH_ACTION,
    REAUTH_MAX_ATTEMPTS,
    REAUTH_WINDOW_SECONDS,
    limiter,
)
from app.core.roles import ADMIN_ROLE, canonical_role
from app.core.security import verify_password
from app.db.models import User

logger = logging.getLogger("app.security")

#: The single message every failure returns. Deliberately says nothing about which check failed.
_GENERIC_FAILURE = "Password verification failed."


def _refuse() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "REAUTH_FAILED", "message": _GENERIC_FAILURE},
    )


def verify_destructive_action_password(
    db: Session,
    current_user: User,
    password: SecretStr | None,
) -> User:
    """Re-verify the CURRENT account's password before a destructive action.

    Returns the freshly-read, still-Admin, still-active user on success. Raises 401 with one generic
    message on any failure, or 429 when the account has exhausted its failed-attempt budget.

    The returned user is the one the caller must attribute the deletion to - not the `current_user`
    passed in - because this is the row that was actually checked.
    """
    # Keyed on the authenticated employee id, not the client IP: a workshop shares one network, so
    # an IP budget would let one person's typo lock out everyone else.
    rate_key = str(getattr(current_user, "employee_id", "") or "unknown")
    limiter.check(
        key=rate_key,
        action=REAUTH_ACTION,
        max_attempts=REAUTH_MAX_ATTEMPTS,
        window_seconds=REAUTH_WINDOW_SECONDS,
    )

    def fail(reason: str, *, employee_id: str | None = None) -> HTTPException:
        """Count the failure, log WHY on the server, and return the generic refusal."""
        limiter.record_failure(key=rate_key, action=REAUTH_ACTION)
        logger.warning(
            "Destructive action re-authentication failed",
            extra={
                "action": "ADMIN_REAUTH_FAILED",
                "success": False,
                # The employee id of the ACCOUNT, never the submitted secret.
                "employee_id": employee_id or rate_key,
                "failure_reason": reason,
            },
        )
        return _refuse()

    if password is None or not password.get_secret_value():
        raise fail("no password supplied")

    # Re-read inside the request. A token issued five minutes ago may name an account that has since
    # been deactivated or demoted, and a destructive action must honour the current state.
    user = db.query(User).filter(User.id == current_user.id).first()
    if user is None:
        raise fail("account no longer exists")

    if canonical_role(user) != ADMIN_ROLE:
        raise fail("account is no longer Admin", employee_id=user.employee_id)

    if not user.is_active:
        raise fail("account is not active", employee_id=user.employee_id)

    if not user.password_hash:
        # A real state: accounts created by tooling can have a null hash. It must never verify.
        raise fail("account has no password set", employee_id=user.employee_id)

    if not verify_password(password.get_secret_value(), user.password_hash):
        raise fail("incorrect password", employee_id=user.employee_id)

    # Cleared on success, so an Admin who mistypes twice then succeeds does not carry the near-miss
    # into their next deletion.
    limiter.reset(key=rate_key, action=REAUTH_ACTION)
    logger.info(
        "Destructive action re-authentication succeeded",
        extra={
            "action": "ADMIN_REAUTH_OK",
            "success": True,
            "employee_id": user.employee_id,
        },
    )
    return user
