from tests.conftest import auth_header, make_defect_type, make_user, make_movement_supervisor_headers


def _headers(db_session):
    """Reference reads now require an entitled Supervisor, not merely an authenticated user."""
    return make_movement_supervisor_headers(db_session)


def _unused_headers(db_session):
    make_user(db_session, 1, "ADM", "Admin", "Admin", "hash")
    return auth_header("ADM", "Admin", 1)


def test_list_active_defect_types_only(client, db_session, mock_loco_client):
    make_defect_type(db_session, 1, "DEFECTIVE", sort_order=10)
    make_defect_type(db_session, 2, "BROKEN", sort_order=20)
    make_defect_type(db_session, 3, "RETIRED", is_active=False, sort_order=30)
    headers = _headers(db_session)

    resp = client.get("/api/booking-defect-types", headers=headers)

    assert resp.status_code == 200
    codes = [d["code"] for d in resp.json()]
    assert codes == ["DEFECTIVE", "BROKEN"]


def test_defect_types_require_dashboard_access(client, db_session, mock_loco_client):
    resp = client.get("/api/booking-defect-types")
    assert resp.status_code == 401


def test_locomotive_search_proxy(client, db_session, mock_loco_client):
    mock_loco_client.add_locomotive("39126", "WAG9HC")
    mock_loco_client.add_locomotive("39127", "WAG9HC")
    headers = _headers(db_session)

    resp = client.get("/api/locomotives/search?q=391", headers=headers)

    assert resp.status_code == 200
    numbers = [l["loco_number"] for l in resp.json()]
    assert numbers == ["39126", "39127"]


def test_locomotive_lookup_not_found(client, db_session, mock_loco_client):
    headers = _headers(db_session)

    resp = client.get("/api/locomotives/NOPE", headers=headers)

    assert resp.status_code == 404


def test_locomotive_search_unavailable_returns_502(client, db_session, mock_loco_client):
    mock_loco_client.unavailable = True
    headers = _headers(db_session)

    resp = client.get("/api/locomotives/search?q=391", headers=headers)

    assert resp.status_code == 502
