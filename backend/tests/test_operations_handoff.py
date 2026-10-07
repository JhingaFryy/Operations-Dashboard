"""Arriving at the Operations Dashboard from BL-DCMS, at its ROOT and nowhere else.

THE SHAPE OF IT. BL-DCMS submits a form carrying a one-time code to this application's root.
nginx routes POST / to the bootstrap endpoint below and GET / to the SPA, so the browser never
visits an intermediate sign-in URL - the address bar shows only the root, from the first request
onwards. Bootstrap redeems the code, authorizes the user under THIS application's rules, and
leaves an HttpOnly cookie holding an opaque id before redirecting to /. The SPA then exchanges
that cookie for its normal token over a same-origin call.

WHY THE TOKEN IS NOT IN THE REDIRECT. It cannot be handed to the SPA yet - the SPA does not
exist until the browser has followed the redirect. Putting it in the Location, the URL, the
fragment or the HTML would expose it exactly where this design avoids doing so. The bootstrap id
is the bridge, and it stands for nothing without the server-side record.

THE CODE PROVES IDENTITY, NOT PERMISSION. BL-DCMS answers only "this code was minted for
employee X"; every rule about whether X may be here is applied here, against this application's
own tables, identically to a password login.
"""

import pytest
import pytest

from tests.conftest import (
    ensure_section,
    grant_access,
    make_section_supervisor_headers,
    make_true_admin_headers,
    make_user,
)
from app.services import handoff_bootstrap

FORM = {"content-type": "application/x-www-form-urlencoded"}


@pytest.fixture(autouse=True)
def _no_inherited_origin_allowlist(monkeypatch):
    """Start every test in this module from "no Origin allow-list configured".

    WHY THIS IS NEEDED. Settings are read from the deployment's own backend/.env, which the test
    process loads like any other. Once BLDCMS_BROWSER_ORIGINS was actually configured on this
    machine, every bootstrap test in this file began returning 403 "Unrecognized origin" - the
    helper below posts without an Origin or Referer header, as a same-process TestClient does -
    even though nothing about the application had changed. A test suite whose result depends on
    the machine's deployment configuration is not testing the application.

    test_8 opts back in by setting the variable itself; it clears the cache in its own finally.
    """
    from app.core import config

    monkeypatch.setenv("BLDCMS_BROWSER_ORIGINS", "")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _bootstrap(client, code, headers=None):
    """The cross-origin form POST BL-DCMS makes, as nginx delivers it."""
    return client.post(
        "/api/auth/handoff/bootstrap",
        content=f"handoff_code={code}",
        headers={**FORM, **(headers or {})},
        follow_redirects=False,
    )


def _cookie_from(response):
    raw = response.headers.get("set-cookie", "")
    if "od_handoff_bootstrap=" not in raw:
        return None
    return raw.split("od_handoff_bootstrap=", 1)[1].split(";", 1)[0] or None


def _finalize(client, cookie=None):
    return client.post(
        "/api/auth/handoff/finalize",
        cookies={"od_handoff_bootstrap": cookie} if cookie else {},
    )


@pytest.fixture()
def shift_user(db_session):
    section = ensure_section(db_session, id=18, code="SHIFT")
    make_user(db_session, 40, "SHIFTSUP", "Shift Supervisor", "Supervisor", "hash",
              section_id=section.id)
    grant_access(db_session, 40, is_enabled=True)
    return "SHIFTSUP"


# ------------------------------------------------------------- the happy path --


def test_18_an_eligible_supervisor_is_signed_in(client, db_session, mock_bldcms_client, shift_user):
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)

    bootstrapped = _bootstrap(client, "code-1")
    assert bootstrapped.status_code == 303
    cookie = _cookie_from(bootstrapped)
    assert cookie

    finalized = _finalize(client, cookie)
    assert finalized.status_code == 200
    token = finalized.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["employee_id"] == shift_user


def test_17_an_admin_is_signed_in(client, db_session, mock_bldcms_client):
    make_true_admin_headers(db_session)
    mock_bldcms_client.issue_handoff_code("code-admin", "TRUEADM", role="Admin")

    cookie = _cookie_from(_bootstrap(client, "code-admin"))
    token = _finalize(client, cookie).json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "Admin"


def test_20_the_browser_is_sent_to_the_root_and_nowhere_else(
    client, db_session, mock_bldcms_client, shift_user
):
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)

    bootstrapped = _bootstrap(client, "code-1")

    assert bootstrapped.status_code == 303
    assert bootstrapped.headers["location"] == "/"


def test_12_finalize_exchanges_the_bootstrap_for_a_normal_token(
    client, db_session, mock_bldcms_client, shift_user
):
    """The token that comes back is this application's ordinary one - the same shape a password
    login returns, usable on the same endpoints."""
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)
    cookie = _cookie_from(_bootstrap(client, "code-1"))

    body = _finalize(client, cookie).json()

    assert set(body) >= {"access_token", "token_type"}
    assert body["token_type"] == "bearer"


# -------------------------------------------- nothing secret is ever in a URL --


def test_5_6_7_no_code_token_or_password_appears_in_any_url(
    client, db_session, mock_bldcms_client, shift_user
):
    mock_bldcms_client.issue_handoff_code("code-secret", shift_user)

    bootstrapped = _bootstrap(client, "code-secret")
    location = bootstrapped.headers["location"]

    # Not in the query string, not in the fragment, not anywhere in the redirect target.
    assert location == "/"
    assert "?" not in location and "#" not in location
    assert "code-secret" not in location

    cookie = _cookie_from(bootstrapped)
    token = _finalize(client, cookie).json()["access_token"]
    assert token not in location
    # The token is delivered in a response BODY, never as a cookie the SPA has to parse out of
    # a URL and never in the redirect.
    assert token not in bootstrapped.headers.get("set-cookie", "")


def test_4_the_code_is_read_from_the_form_body_only(client, db_session, mock_bldcms_client, shift_user):
    """A code in a query string would reach browser history and the access log, so the endpoint
    does not look there at all."""
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)

    as_query = client.post(
        "/api/auth/handoff/bootstrap?handoff_code=code-1",
        content="",
        headers=FORM,
        follow_redirects=False,
    )

    assert as_query.status_code == 400
    assert _cookie_from(as_query) is None


def test_a_non_form_content_type_is_refused(client, db_session, mock_bldcms_client, shift_user):
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)

    resp = client.post(
        "/api/auth/handoff/bootstrap",
        json={"handoff_code": "code-1"},
        follow_redirects=False,
    )

    assert resp.status_code == 415


# --------------------------------------------------- the bootstrap is a bridge --


def test_13_the_cookie_is_httponly_and_samesite_and_carries_no_user_data(
    client, db_session, mock_bldcms_client, shift_user
):
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)

    raw = _bootstrap(client, "code-1").headers["set-cookie"]

    assert "httponly" in raw.lower()
    assert "samesite=lax" in raw.lower()
    assert "path=/" in raw.lower()
    # A second code: the first was spent by the bootstrap above, and a spent code yields no
    # cookie at all - which would make the length check below pass vacuously.
    mock_bldcms_client.issue_handoff_code("code-2", shift_user)
    value = _cookie_from(_bootstrap(client, "code-2")) or ""
    assert shift_user not in value
    assert "Supervisor" not in value
    assert len(value) >= 32


def test_11_14_a_bootstrap_is_single_use_and_a_replay_fails(
    client, db_session, mock_bldcms_client, shift_user
):
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)
    cookie = _cookie_from(_bootstrap(client, "code-1"))

    assert _finalize(client, cookie).status_code == 200
    replay = _finalize(client, cookie)

    assert replay.status_code == 401
    assert "access_token" not in replay.text


def test_13b_the_cookie_is_cleared_on_every_outcome(
    client, db_session, mock_bldcms_client, shift_user
):
    """A spent bootstrap must not linger in the browser, and neither must a rejected one."""
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)
    cookie = _cookie_from(_bootstrap(client, "code-1"))

    ok = _finalize(client, cookie)
    refused = _finalize(client, cookie)

    for response in (ok, refused):
        assert 'od_handoff_bootstrap=""' in response.headers.get("set-cookie", "") or \
               "od_handoff_bootstrap=;" in response.headers.get("set-cookie", "")


def test_15_an_expired_bootstrap_fails(client, db_session, mock_bldcms_client, shift_user, monkeypatch):
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)
    cookie = _cookie_from(_bootstrap(client, "code-1"))

    # Age it past its TTL without waiting.
    from datetime import datetime, timedelta, timezone

    with handoff_bootstrap._lock:  # noqa: SLF001 - the test is about the store's own behaviour
        stored = handoff_bootstrap._store[cookie]
        handoff_bootstrap._store[cookie] = type(stored)(
            employee_id=stored.employee_id,
            access_token=stored.access_token,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    assert _finalize(client, cookie).status_code == 401


def test_10_the_ttl_is_short(client):
    assert handoff_bootstrap.BOOTSTRAP_TTL_SECONDS <= 300


def test_finalize_without_any_cookie_is_refused(client, db_session):
    """Every ordinary signed-out visit calls this once; it must be a cheap, clean refusal."""
    assert _finalize(client).status_code == 401


def test_an_unknown_bootstrap_id_is_refused(client, db_session):
    assert _finalize(client, "not-a-real-bootstrap").status_code == 401


# ------------------------------- this application's authorization is authoritative --


def test_19_a_technician_is_refused(client, db_session, mock_bldcms_client):
    make_user(db_session, 41, "TECH1", "A Technician", "Technician", "hash")
    mock_bldcms_client.issue_handoff_code("code-tech", "TECH1", role="Technician")

    bootstrapped = _bootstrap(client, "code-tech")

    # Sent to sign in, with no bootstrap left behind and nothing to finalize.
    assert bootstrapped.status_code == 303
    assert bootstrapped.headers["location"] == "/login"
    assert _cookie_from(bootstrapped) is None


def test_16_a_supervisor_without_the_entitlement_is_signed_in_like_any_other(
    client, db_session, mock_bldcms_client
):
    """Mirrors the password-login rule change: dashboard_access no longer gates AUTHENTICATION,
    so it must not gate the handoff either. If these two paths ever disagree about who may hold
    a session, one of them is the weaker door."""
    section = ensure_section(db_session, id=9, code="M1-HR")
    make_user(db_session, 42, "NOACCESS", "No Access", "Supervisor", "hash", section_id=section.id)
    grant_access(db_session, 42, is_enabled=False)
    mock_bldcms_client.issue_handoff_code("code-2", "NOACCESS")

    bootstrapped = _bootstrap(client, "code-2")

    assert bootstrapped.headers["location"] == "/"
    assert _cookie_from(bootstrapped) is not None


def test_a_deactivated_account_is_refused(client, db_session, mock_bldcms_client, shift_user):
    from app.db.models import User

    user = db_session.query(User).filter_by(employee_id=shift_user).first()
    user.is_active = False
    db_session.commit()
    mock_bldcms_client.issue_handoff_code("code-3", shift_user)

    assert _cookie_from(_bootstrap(client, "code-3")) is None


def test_an_employee_this_database_does_not_know_is_refused(client, db_session, mock_bldcms_client):
    mock_bldcms_client.issue_handoff_code("code-4", "GHOST")

    assert _cookie_from(_bootstrap(client, "code-4")) is None


def test_a_spent_bl_code_cannot_be_bootstrapped_twice(client, db_session, mock_bldcms_client, shift_user):
    mock_bldcms_client.issue_handoff_code("code-1", shift_user)
    assert _cookie_from(_bootstrap(client, "code-1")) is not None

    assert _cookie_from(_bootstrap(client, "code-1")) is None


def test_the_handoff_runs_the_same_authorization_source_as_password_login():
    """The handoff must not be a weaker second door. Asserted on the source rather than by
    logging in twice, because the two paths must run the SAME checks in the same order - a
    behavioural test would pass even if one path had its own relaxed copy of them."""
    import inspect

    from app.services import auth_service

    handoff = inspect.getsource(auth_service.login_with_handoff)
    password = inspect.getsource(auth_service.login)

    # Stronger than the previous form of this test, which listed the individual checks and
    # therefore had to be edited every time the rule changed - and would have passed if one path
    # grew its own relaxed copy of those same checks. The two paths now call ONE function, so
    # they cannot drift at all.
    for source in (handoff, password):
        assert "authorize_login_role(user)" in source
    assert "user.is_active" in handoff and "user.is_active" in password

    # And that one function is the whole rule: role recognition, fail-closed on unknown, and
    # technician refusal. Nothing about dashboard_access, which no longer gates authentication.
    rule = inspect.getsource(auth_service.authorize_login_role)
    assert "canonical_role(user)" in rule
    assert "_FORBIDDEN_UNKNOWN_ROLE" in rule
    assert "_FORBIDDEN_TECHNICIAN" in rule


# --------------------------------------------------------------- origin checks --


def test_8_an_unrecognized_origin_is_refused_when_one_is_configured(
    client, db_session, mock_bldcms_client, shift_user, monkeypatch
):
    """The bootstrap POST is the one cross-origin request in this design, so its Origin is
    checked against an allow-list rather than accepted from anywhere."""
    from app.core import config

    monkeypatch.setenv("BLDCMS_BROWSER_ORIGINS", "http://10.94.36.10")
    config.get_settings.cache_clear()
    try:
        mock_bldcms_client.issue_handoff_code("code-1", shift_user)

        refused = _bootstrap(client, "code-1", headers={"origin": "http://evil.example"})
        assert refused.status_code == 403
        assert _cookie_from(refused) is None

        mock_bldcms_client.issue_handoff_code("code-2", shift_user)
        allowed = _bootstrap(client, "code-2", headers={"origin": "http://10.94.36.10"})
        assert allowed.status_code == 303
        assert _cookie_from(allowed) is not None
    finally:
        config.get_settings.cache_clear()


def test_transport_failure_does_not_sign_anyone_in(client, db_session, mock_bldcms_client, shift_user):
    mock_bldcms_client.issue_handoff_code("code-7", shift_user)
    mock_bldcms_client.unavailable = True

    bootstrapped = _bootstrap(client, "code-7")

    assert _cookie_from(bootstrapped) is None
    assert "access_token" not in bootstrapped.text


# --------------------------------------------- nothing else about auth changed --


def test_21_normal_password_login_still_works(client, db_session, mock_bldcms_client, shift_user):
    from app.core.security import pwd_context
    from app.db.models import User

    user = db_session.query(User).filter_by(employee_id=shift_user).first()
    user.password_hash = pwd_context.hash("secret123")
    db_session.commit()

    resp = client.post("/api/auth/login", json={"employee_id": shift_user, "password": "secret123"})

    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_1_2_the_retired_sign_in_route_is_gone_from_the_api():
    from app.main import app

    paths = app.openapi()["paths"]
    assert not [p for p in paths if "sign-in-from-bldcms" in p]
    # The old JSON handoff route went with the page that used it.
    assert "/api/auth/handoff" not in paths
    assert "/api/auth/handoff/bootstrap" in paths
    assert "/api/auth/handoff/finalize" in paths
