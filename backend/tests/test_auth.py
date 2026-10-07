from tests.conftest import auth_header, grant_access, hash_password, make_section, make_user


def test_admin_login_allowed_without_dashboard_access_row(client, db_session):
    make_user(db_session, 1, "E1", "Alice Admin", "Admin", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E1", "password": "secret123"})

    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_supervisor_with_access_allowed(client, db_session):
    make_user(db_session, 2, "E2", "Sam Supervisor", "Supervisor", hash_password("secret123"))
    grant_access(db_session, 2, is_enabled=True)

    resp = client.post("/api/auth/login", json={"employee_id": "E2", "password": "secret123"})

    assert resp.status_code == 200


def test_supervisor_without_dashboard_access_can_still_log_in(client, db_session):
    """Business rule change: dashboard_access no longer gates AUTHENTICATION.

    It used to, and in production that refused legitimate active Supervisors at the sign-in
    screen with a bare "You don't have permission to do that." - indistinguishable from a broken
    account. The entitlement is still enforced, unchanged, on the operational routes; see
    test_a_supervisor_without_the_entitlement_is_still_refused_the_operational_pages below.
    """
    make_user(db_session, 3, "E3", "Sam NoAccess", "Supervisor", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E3", "password": "secret123"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_supervisor_with_disabled_access_can_still_log_in(client, db_session):
    make_user(db_session, 4, "E4", "Sam Disabled", "Supervisor", hash_password("secret123"))
    grant_access(db_session, 4, is_enabled=False)

    resp = client.post("/api/auth/login", json={"employee_id": "E4", "password": "secret123"})

    assert resp.status_code == 200, resp.text


def test_a_supervisor_without_the_entitlement_can_use_the_operational_pages(
    client, db_session
):
    """The rule, end to end: sign in AND work, with no dashboard_access row anywhere.

    This is the pair of the login test above. Previously this account could not log in; then it
    could log in but was refused on 32 routes, which is not a usable account either. Both halves
    have to hold for the business rule to be satisfied.
    """
    make_user(db_session, 33, "E33", "Sam NoAccess", "Supervisor", hash_password("secret123"))
    login = client.post("/api/auth/login", json={"employee_id": "E33", "password": "secret123"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/shed-visits/current", headers=headers).status_code == 200


def test_that_same_supervisor_is_still_refused_admin_only_routes(client, db_session):
    """Base access must not have leaked anything narrower."""
    make_user(db_session, 34, "E34", "Sam NoAccess", "Supervisor", hash_password("secret123"))
    login = client.post("/api/auth/login", json={"employee_id": "E34", "password": "secret123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.get("/api/admin/dashboard-access/users", headers=headers).status_code == 403


def test_inactive_user_rejected(client, db_session):
    make_user(db_session, 5, "E5", "Inactive Guy", "Admin", hash_password("secret123"), is_active=False)

    resp = client.post("/api/auth/login", json={"employee_id": "E5", "password": "secret123"})

    assert resp.status_code == 401


def test_technician_rejected(client, db_session):
    make_user(db_session, 6, "E6", "Tom Technician", "Technician", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E6", "password": "secret123"})

    assert resp.status_code == 403


def test_wrong_password_rejected(client, db_session):
    make_user(db_session, 7, "E7", "Alice Admin", "Admin", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E7", "password": "wrong"})

    assert resp.status_code == 401


def test_unknown_user_rejected(client, db_session):
    resp = client.post("/api/auth/login", json={"employee_id": "NOBODY", "password": "x"})

    assert resp.status_code == 401


def test_me_response_shape_for_admin(client, db_session):
    make_user(db_session, 8, "E8", "Alice Admin", "Admin", hash_password("secret123"))

    resp = client.get("/api/auth/me", headers=auth_header("E8", "Admin", 8))

    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "Admin"
    assert body["dashboard_access"] is True
    assert body["permissions"]["can_manage_equipment_mapping"] is True
    assert body["permissions"]["can_add_booking_sections"] is True


def test_me_response_shape_for_supervisor_with_section_and_permissions(client, db_session):
    make_section(db_session, 8, "M2-HR")
    make_user(
        db_session, 9, "E9", "Sam Supervisor", "Supervisor", hash_password("secret123"), section_id=8
    )
    grant_access(db_session, 9, is_enabled=True, can_manage_equipment_mapping=True)

    resp = client.get("/api/auth/me", headers=auth_header("E9", "Supervisor", 9))

    assert resp.status_code == 200
    body = resp.json()
    assert body["section"] == {"id": 8, "code": "M2-HR", "name": "M2-HR"}
    assert body["dashboard_access"] is True
    assert body["permissions"] == {
        "can_add_booking_sections": False,
        # Migration 015. False for this Supervisor, and false for every pre-existing account -
        # routing is granted explicitly, never inherited. Asserted as an exact dict on purpose:
        # a new permission appearing here should fail this test until someone states the value it
        # is meant to have.
        "can_route_bookings": False,
        "can_manage_equipment_mapping": True,
    }


def test_me_requires_authentication(client):
    resp = client.get("/api/auth/me")

    assert resp.status_code == 401


# ==================================================================== the login rule ==
#
# Business rule (2026-09-30): every ACTIVE Supervisor may sign in. Admin may sign in.
# Technicians, inactive accounts and unrecognised roles are denied. dashboard_access decides
# what a signed-in Supervisor may DO, never whether they may sign in.


def test_a_supervisor_with_a_section_can_log_in(client, db_session):
    section = make_section(db_session, 4, "M4-HR")
    make_user(db_session, 10, "E10", "Sectioned Sup", "Supervisor", hash_password("secret123"),
              section_id=section.id)

    resp = client.post("/api/auth/login", json={"employee_id": "E10", "password": "secret123"})

    assert resp.status_code == 200, resp.text


def test_a_supervisor_with_no_section_can_still_log_in(client, db_session):
    """section_id has never gated login and must not start to. A Supervisor whose section has
    not been set yet should reach the app and be told what is missing, not be turned away at
    the door with a permission error."""
    make_user(db_session, 11, "E11", "Sectionless Sup", "Supervisor", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E11", "password": "secret123"})

    assert resp.status_code == 200, resp.text


def test_the_admin_only_mapping_permission_is_not_required_to_log_in(client, db_session):
    """can_manage_equipment_mapping is an Admin-page permission. It restricts one page; it has
    nothing to do with holding a session, and a Supervisor without it must log in normally."""
    make_user(db_session, 12, "E12", "No Mapping Perm", "Supervisor", hash_password("secret123"))
    grant_access(db_session, 12, is_enabled=True, can_manage_equipment_mapping=False)

    resp = client.post("/api/auth/login", json={"employee_id": "E12", "password": "secret123"})

    assert resp.status_code == 200, resp.text


def test_the_add_booking_sections_permission_is_not_required_to_log_in(client, db_session):
    make_user(db_session, 13, "E13", "No Booking Perm", "Supervisor", hash_password("secret123"))
    grant_access(db_session, 13, is_enabled=True, can_add_booking_sections=False)

    resp = client.post("/api/auth/login", json={"employee_id": "E13", "password": "secret123"})

    assert resp.status_code == 200, resp.text


def test_shift_membership_is_not_required_to_log_in(client, db_session):
    """Movement/SHIFT privileges are decided by section membership on the routes that need them.
    A Supervisor in an ordinary maintenance section must log in exactly like a SHIFT one."""
    section = make_section(db_session, 6, "M6-HR")
    make_user(db_session, 14, "E14", "Non SHIFT Sup", "Supervisor", hash_password("secret123"),
              section_id=section.id)

    resp = client.post("/api/auth/login", json={"employee_id": "E14", "password": "secret123"})

    assert resp.status_code == 200, resp.text


# ============================================================== role normalization ==
#
# users.role is a free VARCHAR with no CHECK constraint, written by BL-DCMS and by admin
# tooling. It used to be compared with exact case-sensitive equality, which was fail-OPEN: a row
# spelled "supervisor" matched neither the Technician branch nor the Supervisor branch and so
# had the FEWEST checks applied to it.


def test_a_lowercase_supervisor_role_is_recognised_and_normalized(client, db_session):
    make_user(db_session, 15, "E15", "lower sup", "supervisor", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E15", "password": "secret123"})
    assert resp.status_code == 200, resp.text

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"})
    assert me.json()["role"] == "Supervisor"


def test_an_uppercase_admin_role_is_recognised_and_normalized(client, db_session):
    make_user(db_session, 16, "E16", "upper admin", "ADMIN", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E16", "password": "secret123"})
    assert resp.status_code == 200, resp.text

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"})
    assert me.json()["role"] == "Admin"


def test_a_mis_cased_technician_is_still_denied(client, db_session):
    """The fail-open case that mattered most: before normalization, "technician" skipped the
    Technician refusal entirely and was issued a token."""
    make_user(db_session, 17, "E17", "lower tech", "technician", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E17", "password": "secret123"})

    assert resp.status_code == 403, resp.text


def test_a_role_with_surrounding_whitespace_is_recognised(client, db_session):
    make_user(db_session, 18, "E18", "padded", " Supervisor ", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E18", "password": "secret123"})

    assert resp.status_code == 200, resp.text


def test_an_unknown_role_fails_closed(client, db_session):
    """Not recognised means denied - never "unrecognised, therefore unrestricted"."""
    make_user(db_session, 19, "E19", "Mystery", "Foreman", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E19", "password": "secret123"})

    assert resp.status_code == 403, resp.text


def test_a_blank_role_fails_closed(client, db_session):
    make_user(db_session, 20, "E20", "Blank", "", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E20", "password": "secret123"})

    assert resp.status_code == 403, resp.text


def test_an_unknown_role_is_also_refused_on_an_existing_token(client, db_session):
    """A token outlives the decision that minted it, so the rule is re-applied per request. Here
    the account is renamed to an unrecognised role AFTER signing in."""
    from app.db.models import User

    make_user(db_session, 21, "E21", "Was Supervisor", "Supervisor", hash_password("secret123"))
    token = client.post(
        "/api/auth/login", json={"employee_id": "E21", "password": "secret123"}
    ).json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    db_session.query(User).filter(User.id == 21).update({"role": "Foreman"})
    db_session.commit()

    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_normalization_never_writes_the_corrected_role_back_to_the_database(client, db_session):
    """Recognition, not repair. A badly spelled row stays badly spelled until someone fixes the
    data - this code must never quietly UPDATE the shared users table during a login."""
    from app.db.models import User

    make_user(db_session, 22, "E22", "lower sup", "supervisor", hash_password("secret123"))

    resp = client.post("/api/auth/login", json={"employee_id": "E22", "password": "secret123"})
    assert resp.status_code == 200
    client.get("/api/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"})

    db_session.expire_all()
    assert db_session.query(User).filter(User.id == 22).one().role == "supervisor"
