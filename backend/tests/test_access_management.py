from tests.conftest import auth_header, grant_access, hash_password, make_user


def _make_admin(db_session, id=1, employee_id="ADMIN1"):
    make_user(db_session, id, employee_id, "Admin One", "Admin", hash_password("x"))
    return auth_header(employee_id, "Admin", id)


def test_non_admin_cannot_list_access(client, db_session):
    make_user(db_session, 2, "SUP1", "Sup One", "Supervisor", hash_password("x"))
    grant_access(db_session, 2, is_enabled=True)

    resp = client.get(
        "/api/admin/dashboard-access/users", headers=auth_header("SUP1", "Supervisor", 2)
    )

    assert resp.status_code == 403


def test_non_admin_cannot_update_access(client, db_session):
    make_user(db_session, 2, "SUP1", "Sup One", "Supervisor", hash_password("x"))
    grant_access(db_session, 2, is_enabled=True)
    make_user(db_session, 3, "SUP2", "Sup Two", "Supervisor", hash_password("x"))

    resp = client.put(
        "/api/admin/dashboard-access/users/3",
        json={"is_enabled": True},
        headers=auth_header("SUP1", "Supervisor", 2),
    )

    assert resp.status_code == 403


def test_admin_can_enable_access(client, db_session):
    admin_headers = _make_admin(db_session)
    make_user(db_session, 10, "SUP3", "Sup Three", "Supervisor", hash_password("x"))

    resp = client.put(
        "/api/admin/dashboard-access/users/10",
        json={
            "is_enabled": True,
            "can_add_booking_sections": False,
            "can_manage_equipment_mapping": True,
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["dashboard_access_enabled"] is True
    assert body["can_manage_equipment_mapping"] is True
    assert body["granted_at"] is not None
    assert body["revoked_at"] is None


def test_admin_can_disable_access(client, db_session):
    admin_headers = _make_admin(db_session)
    make_user(db_session, 11, "SUP4", "Sup Four", "Supervisor", hash_password("x"))
    grant_access(db_session, 11, is_enabled=True)

    resp = client.put(
        "/api/admin/dashboard-access/users/11",
        json={"is_enabled": False},
        headers=admin_headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["dashboard_access_enabled"] is False
    assert body["revoked_at"] is not None


def test_permission_flags_update_independent_of_enable_state(client, db_session):
    admin_headers = _make_admin(db_session)
    make_user(db_session, 12, "SUP5", "Sup Five", "Supervisor", hash_password("x"))
    grant_access(db_session, 12, is_enabled=True, can_add_booking_sections=False)

    resp = client.put(
        "/api/admin/dashboard-access/users/12",
        json={
            "is_enabled": True,
            "can_add_booking_sections": True,
            "can_manage_equipment_mapping": False,
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["can_add_booking_sections"] is True


def test_inactive_target_cannot_be_enabled(client, db_session):
    admin_headers = _make_admin(db_session)
    make_user(db_session, 13, "SUP6", "Sup Six", "Supervisor", hash_password("x"), is_active=False)

    resp = client.put(
        "/api/admin/dashboard-access/users/13",
        json={"is_enabled": True},
        headers=admin_headers,
    )

    assert resp.status_code == 422


def test_update_unknown_user_404(client, db_session):
    admin_headers = _make_admin(db_session)

    resp = client.put(
        "/api/admin/dashboard-access/users/9999",
        json={"is_enabled": True},
        headers=admin_headers,
    )

    assert resp.status_code == 404


def test_list_users_search(client, db_session):
    admin_headers = _make_admin(db_session)
    make_user(db_session, 20, "FINDME", "Findable Person", "Supervisor", hash_password("x"))
    make_user(db_session, 21, "OTHER", "Other Person", "Supervisor", hash_password("x"))

    resp = client.get("/api/admin/dashboard-access/users?q=Findable", headers=admin_headers)

    assert resp.status_code == 200
    employee_ids = [u["employee_id"] for u in resp.json()]
    assert employee_ids == ["FINDME"]


def test_password_hash_never_returned(client, db_session):
    admin_headers = _make_admin(db_session)
    make_user(db_session, 30, "SECRETUSR", "Secret User", "Supervisor", hash_password("x"))

    resp = client.get("/api/admin/dashboard-access/users", headers=admin_headers)

    assert resp.status_code == 200
    for row in resp.json():
        assert "password_hash" not in row
        assert "password" not in row
