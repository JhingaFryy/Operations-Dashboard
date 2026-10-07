"""Password re-authentication for destructive Admin actions.

Everything here is about what the endpoint must REFUSE. The success path is one test; the rest are
the ways an attacker, a stale token or a careless caller could try to get past it.
"""

import logging

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core.rate_limit import REAUTH_ACTION, REAUTH_MAX_ATTEMPTS, limiter
from app.core.security import pwd_context
from app.db.models import Section, User
from app.services.admin_reauth_service import verify_destructive_action_password


@pytest.fixture(autouse=True)
def _clean_limiter():
    """Module-level limiter state leaks between tests otherwise, and a test that exhausts the budget
    would silently 429 the next one."""
    limiter.reset_for_tests()
    yield
    limiter.reset_for_tests()


@pytest.fixture()
def world(db_session):
    section = Section(name="M1-HR", code="M1-HR")
    db_session.add(section)
    db_session.commit()

    admin = User(
        employee_id="ADM1", name="Admin One", mobile="9000000001", role="Admin", is_active=True,
        password_hash=pwd_context.hash("correct-horse"),
    )
    other_admin = User(
        employee_id="ADM2", name="Admin Two", mobile="9000000002", role="Admin", is_active=True,
        password_hash=pwd_context.hash("other-admin-password"),
    )
    supervisor = User(
        employee_id="SUP1", name="Supervisor", mobile="9000000003", role="Supervisor",
        is_active=True, section_id=section.id, password_hash=pwd_context.hash("sup-password"),
    )
    db_session.add_all([admin, other_admin, supervisor])
    db_session.commit()
    return {"db": db_session, "admin": admin, "other_admin": other_admin, "supervisor": supervisor}


def _code(excinfo):
    detail = excinfo.value.detail
    return detail["code"] if isinstance(detail, dict) else detail


# ================================================================== the one success path ==


def test_the_correct_password_of_the_current_account_succeeds(world):
    verified = verify_destructive_action_password(
        world["db"], world["admin"], SecretStr("correct-horse")
    )
    # Returns the FRESHLY READ row, which is the one that was actually checked and therefore the one
    # the deletion must be attributed to.
    assert verified.id == world["admin"].id
    assert verified.employee_id == "ADM1"


# ============================================================================= refusals ==


def test_a_wrong_password_is_refused(world):
    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(world["db"], world["admin"], SecretStr("wrong"))
    assert excinfo.value.status_code == 401
    assert _code(excinfo) == "REAUTH_FAILED"


def test_another_admins_password_is_refused(world):
    """The check is against the CURRENT account, not "any Admin". Accepting a colleague's password
    would make the re-authentication meaningless: anyone who knew any Admin password could delete as
    somebody else, and the ledger would name the wrong person."""
    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(
            world["db"], world["admin"], SecretStr("other-admin-password")
        )
    assert excinfo.value.status_code == 401


def test_an_account_deactivated_since_the_token_was_issued_is_refused(world):
    """The token is still cryptographically valid. The account is not. A destructive action must
    honour the CURRENT state, which is why the row is re-read rather than trusted from the claim."""
    db = world["db"]
    world["admin"].is_active = False
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(db, world["admin"], SecretStr("correct-horse"))
    assert excinfo.value.status_code == 401


def test_an_account_demoted_since_the_token_was_issued_is_refused(world):
    db = world["db"]
    world["admin"].role = "Supervisor"
    db.commit()

    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(db, world["admin"], SecretStr("correct-horse"))
    assert excinfo.value.status_code == 401


def test_a_non_admin_is_refused_even_with_the_right_password(world):
    """Defence in depth. The route already requires Admin; this refuses again, so a future caller
    that forgot the dependency cannot delete anything."""
    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(
            world["db"], world["supervisor"], SecretStr("sup-password")
        )
    assert excinfo.value.status_code == 401


def test_an_account_with_no_password_hash_can_never_verify(world):
    """A real state - accounts created by tooling can have a null hash. An empty-string comparison
    or a short-circuit that treated "no hash" as "no check" would make such an account a free pass."""
    db = world["db"]
    world["admin"].password_hash = None
    db.commit()

    for attempt in ("", "anything", "correct-horse"):
        with pytest.raises(HTTPException) as excinfo:
            verify_destructive_action_password(db, world["admin"], SecretStr(attempt))
        assert excinfo.value.status_code == 401


@pytest.mark.parametrize("password", [None, SecretStr(""), SecretStr("   ")])
def test_a_missing_or_blank_password_is_refused(world, password):
    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(world["db"], world["admin"], password)
    assert excinfo.value.status_code == 401


def test_a_case_variant_role_still_resolves_to_admin(world):
    """users.role is a free VARCHAR written by several systems. "admin" must be recognised, or a
    legitimately-Admin account would be refused - the same normalization the rest of authorization
    uses (core/roles.canonical_role)."""
    db = world["db"]
    world["admin"].role = "admin"
    db.commit()

    verified = verify_destructive_action_password(db, world["admin"], SecretStr("correct-horse"))
    assert verified.employee_id == "ADM1"


# ======================================================== every failure looks the same ==


def test_every_failure_returns_the_identical_message(world):
    """No oracle. If a deactivated account produced a different message from a wrong password, the
    endpoint would confirm when a guessed password was correct."""
    db = world["db"]
    messages = set()

    def capture(user, password):
        try:
            verify_destructive_action_password(db, user, password)
        except HTTPException as exc:
            messages.add(str(exc.detail))
            limiter.reset_for_tests()

    capture(world["admin"], SecretStr("wrong"))
    capture(world["supervisor"], SecretStr("sup-password"))
    capture(world["admin"], SecretStr("other-admin-password"))

    world["admin"].is_active = False
    db.commit()
    capture(world["admin"], SecretStr("correct-horse"))

    assert len(messages) == 1, messages
    # And it says nothing about which check failed.
    only = messages.pop().lower()
    for leak in ("inactive", "deactivated", "role", "admin", "exists", "hash", "no password set"):
        assert leak not in only, f"the generic message leaks {leak!r}: {only}"


def test_the_password_never_reaches_the_logs(world, caplog):
    """The one thing that must never appear anywhere. Checked against the whole captured log stream,
    including the structured extras, not just the formatted message."""
    secret = "correct-horse"
    with caplog.at_level(logging.DEBUG):
        verify_destructive_action_password(world["db"], world["admin"], SecretStr(secret))
        try:
            verify_destructive_action_password(world["db"], world["admin"], SecretStr("wrong-xyz"))
        except HTTPException:
            pass

    blob = "\n".join(
        [r.getMessage() for r in caplog.records]
        + [repr(r.__dict__) for r in caplog.records]
    )
    assert secret not in blob
    assert "wrong-xyz" not in blob
    # The ACCOUNT is identified, so a failure is still investigable.
    assert "ADM1" in blob


def test_a_failure_is_logged_with_its_reason_server_side(world, caplog):
    """The client learns nothing; the server records exactly why. Operability without an oracle."""
    with caplog.at_level(logging.WARNING):
        try:
            verify_destructive_action_password(world["db"], world["admin"], SecretStr("wrong"))
        except HTTPException:
            pass
    reasons = [getattr(r, "failure_reason", None) for r in caplog.records]
    assert "incorrect password" in reasons


# ================================================================== rate limiting ==


def test_repeated_failures_are_eventually_rate_limited(world):
    for _ in range(REAUTH_MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as excinfo:
            verify_destructive_action_password(world["db"], world["admin"], SecretStr("wrong"))
        assert excinfo.value.status_code == 401

    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(world["db"], world["admin"], SecretStr("wrong"))
    assert excinfo.value.status_code == 429
    assert _code(excinfo) == "TOO_MANY_ATTEMPTS"
    assert "Retry-After" in (excinfo.value.headers or {})


def test_the_limit_blocks_even_the_CORRECT_password(world):
    """The point of a limiter. If the right password still worked after the budget was spent, an
    attacker's successful guess would not be stopped by it."""
    for _ in range(REAUTH_MAX_ATTEMPTS):
        with pytest.raises(HTTPException):
            verify_destructive_action_password(world["db"], world["admin"], SecretStr("wrong"))

    with pytest.raises(HTTPException) as excinfo:
        verify_destructive_action_password(world["db"], world["admin"], SecretStr("correct-horse"))
    assert excinfo.value.status_code == 429


def test_a_success_clears_the_near_misses(world):
    """An Admin who mistypes twice and then gets it right starts clean, rather than carrying three
    strikes into their next deletion an hour later."""
    db = world["db"]
    for _ in range(REAUTH_MAX_ATTEMPTS - 1):
        with pytest.raises(HTTPException):
            verify_destructive_action_password(db, world["admin"], SecretStr("wrong"))

    verify_destructive_action_password(db, world["admin"], SecretStr("correct-horse"))

    # The budget is full again: all five failures are available, and the fifth is still a 401.
    for _ in range(REAUTH_MAX_ATTEMPTS):
        with pytest.raises(HTTPException) as excinfo:
            verify_destructive_action_password(db, world["admin"], SecretStr("wrong"))
        assert excinfo.value.status_code == 401


def test_the_budget_is_per_account_not_global(world):
    """A shared office network must not mean one person's typos lock out another Admin."""
    db = world["db"]
    for _ in range(REAUTH_MAX_ATTEMPTS + 1):
        with pytest.raises(HTTPException):
            verify_destructive_action_password(db, world["admin"], SecretStr("wrong"))

    # The other Admin is unaffected and can still act.
    verified = verify_destructive_action_password(
        db, world["other_admin"], SecretStr("other-admin-password")
    )
    assert verified.employee_id == "ADM2"


def test_the_reauth_budget_is_namespaced_to_its_own_action(world):
    """Failures recorded against an unrelated action must not consume the re-auth budget."""
    for _ in range(REAUTH_MAX_ATTEMPTS + 3):
        limiter.record_failure(key="ADM1", action="SOME_OTHER_ACTION")

    verified = verify_destructive_action_password(
        world["db"], world["admin"], SecretStr("correct-horse")
    )
    assert verified.employee_id == "ADM1"
    assert REAUTH_ACTION != "SOME_OTHER_ACTION"
