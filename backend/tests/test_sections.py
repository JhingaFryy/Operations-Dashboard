from tests.conftest import auth_header, make_section, make_user, make_movement_supervisor_headers


def _headers(db_session):
    """Reference reads now require an entitled Supervisor."""
    return make_movement_supervisor_headers(db_session)


def _unused_headers(db_session):
    make_user(db_session, 1, "U1", "Some Admin", "Admin", "irrelevant-hash-not-used")
    return auth_header("U1", "Admin", 1)


def test_list_sections(client, db_session):
    make_section(db_session, 1, "M2-HR")
    make_section(db_session, 2, "M1-HR")
    headers = _headers(db_session)

    resp = client.get("/api/sections", headers=headers)

    assert resp.status_code == 200
    codes = {s["code"] for s in resp.json()}
    # Superset, not equality: the authenticated Supervisor fixture necessarily belongs to a
    # section of its own (Operations Dashboard is Supervisor-only), which also appears here.
    assert {"M2-HR", "M1-HR"} <= codes


def test_search_sections(client, db_session):
    make_section(db_session, 1, "M2-HR")
    make_section(db_session, 2, "M35-Aux")
    headers = _headers(db_session)

    resp = client.get("/api/sections?q=M2", headers=headers)

    assert resp.status_code == 200
    codes = [s["code"] for s in resp.json()]
    assert codes == ["M2-HR"]


def test_sections_requires_auth(client, db_session):
    make_section(db_session, 1, "M2-HR")

    resp = client.get("/api/sections")

    assert resp.status_code == 401
