from tests.conftest import auth_header, grant_access, make_section, make_user, make_movement_supervisor_headers


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _supervisor_headers(db_session, user_id=2, can_manage_equipment_mapping=False):
    make_user(db_session, user_id, f"SUP{user_id}", "Supervisor", "Supervisor", "hash")
    grant_access(
        db_session,
        user_id,
        is_enabled=True,
        can_manage_equipment_mapping=can_manage_equipment_mapping,
    )
    return auth_header(f"SUP{user_id}", "Supervisor", user_id)


def test_families_proxy(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/equipment/families", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == mock_loco_client.families


def test_child_node_listing(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "Auxiliary Converter")
    mock_loco_client.add_node(2, 1, 1, "Contactor")

    resp = client.get("/api/equipment/nodes?family=3PHASE&parent_id=1", headers=headers)

    assert resp.status_code == 200
    ids = [n["id"] for n in resp.json()]
    assert ids == [2]


def test_child_node_listing_requires_family(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/equipment/nodes?parent_id=1", headers=headers)

    assert resp.status_code == 422  # family is a required query param


def test_child_node_listing_unknown_family_code_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/equipment/nodes?family=NOPE", headers=headers)

    assert resp.status_code == 404


def test_search_nodes(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "IGBT Module")
    mock_loco_client.add_node(2, 1, None, "Fan Motor")

    resp = client.get("/api/equipment/nodes/search?q=igbt", headers=headers)

    assert resp.status_code == 200
    names = [n["name"] for n in resp.json()]
    assert names == ["IGBT Module"]


def test_search_nodes_includes_ancestor_path(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "Auxiliary Converter")
    mock_loco_client.add_node(2, 1, 1, "ABB")
    mock_loco_client.add_node(3, 1, 2, "Converter Module")
    mock_loco_client.add_node(4, 1, 3, "IGBT")

    resp = client.get("/api/equipment/nodes/search?q=igbt", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert [p["name"] for p in items[0]["path"]] == [
        "Auxiliary Converter",
        "ABB",
        "Converter Module",
        "IGBT",
    ]


def test_search_nodes_resolves_family_code_to_id(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "IGBT Module")

    resp = client.get("/api/equipment/nodes/search?q=igbt&family=3PHASE", headers=headers)

    assert resp.status_code == 200
    assert [n["id"] for n in resp.json()] == [1]


def test_loco_master_unavailable_returns_502(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.unavailable = True

    resp = client.get("/api/equipment/families", headers=headers)

    assert resp.status_code == 502


def test_loco_master_auth_error_returns_502(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.auth_error = True

    resp = client.get("/api/equipment/families", headers=headers)

    assert resp.status_code == 502


def test_direct_mapping_read(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "Contactor")
    mock_loco_client.set_mapping(1, ["M1-HR", "M2-HR"])

    resp = client.get("/api/equipment/nodes/1/mapping", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"equipment_node_id": 1, "section_codes": ["M1-HR", "M2-HR"]}


def test_resolved_mapping_exact(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "Contactor")
    mock_loco_client.set_mapping(1, ["M1-HR", "M2-HR"])

    resp = client.get("/api/equipment/nodes/1/resolved-sections", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "EXACT"
    assert body["resolved_from_node_id"] == 1
    assert set(body["section_codes"]) == {"M1-HR", "M2-HR"}


def test_resolved_mapping_ancestor_most_specific_wins(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "Auxiliary Converter")
    mock_loco_client.set_mapping(1, ["M35-Aux"])
    mock_loco_client.add_node(2, 1, 1, "Contactor")
    mock_loco_client.set_mapping(2, ["M1-HR", "M2-HR"])
    mock_loco_client.add_node(3, 1, 2, "Contact Tip")  # no direct mapping

    resp = client.get("/api/equipment/nodes/3/resolved-sections", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "ANCESTOR"
    assert body["resolved_from_node_id"] == 2
    assert set(body["section_codes"]) == {"M1-HR", "M2-HR"}
    assert "M35-Aux" not in body["section_codes"]  # union of all ancestors is wrong


def test_resolved_mapping_none(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    mock_loco_client.add_node(1, 1, None, "Unmapped Thing")

    resp = client.get("/api/equipment/nodes/1/resolved-sections", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "NONE"
    assert body["section_codes"] == []


def test_unknown_section_code_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    mock_loco_client.add_node(5, 1, None, "Some Node")

    resp = client.put(
        "/api/equipment/nodes/5/mapping",
        json={"section_codes": ["M1-HR", "NOT-A-REAL-SECTION"]},
        headers=headers,
    )

    assert resp.status_code == 422


def test_duplicate_section_request_normalized(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    mock_loco_client.add_node(5, 1, None, "Some Node")

    resp = client.put(
        "/api/equipment/nodes/5/mapping",
        json={"section_codes": ["M1-HR", "M1-HR"]},
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()["section_codes"] == ["M1-HR"]


def test_unauthorized_mapping_update_rejected_for_supervisor_without_permission(
    client, db_session, mock_loco_client
):
    headers = _supervisor_headers(db_session, can_manage_equipment_mapping=False)
    make_section(db_session, 1, "M1-HR")
    mock_loco_client.add_node(5, 1, None, "Some Node")

    resp = client.put(
        "/api/equipment/nodes/5/mapping",
        json={"section_codes": ["M1-HR"]},
        headers=headers,
    )

    assert resp.status_code == 403


def test_admin_mapping_update_allowed(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    mock_loco_client.add_node(5, 1, None, "Some Node")

    resp = client.put(
        "/api/equipment/nodes/5/mapping",
        json={"section_codes": ["M1-HR"]},
        headers=headers,
    )

    assert resp.status_code == 200


def test_permitted_supervisor_mapping_update_allowed(client, db_session, mock_loco_client):
    headers = _supervisor_headers(db_session, can_manage_equipment_mapping=True)
    make_section(db_session, 1, "M1-HR")
    mock_loco_client.add_node(5, 1, None, "Some Node")

    resp = client.put(
        "/api/equipment/nodes/5/mapping",
        json={"section_codes": ["M1-HR"]},
        headers=headers,
    )

    assert resp.status_code == 200


def test_mapping_write_passes_actor_employee_id(client, db_session, mock_loco_client, monkeypatch):
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    mock_loco_client.add_node(5, 1, None, "Some Node")

    captured = {}
    original = mock_loco_client.update_equipment_mapping

    def spy(node_id, section_codes, actor_employee_id):
        captured["actor_employee_id"] = actor_employee_id
        return original(node_id, section_codes, actor_employee_id)

    mock_loco_client.update_equipment_mapping = spy

    client.put(
        "/api/equipment/nodes/5/mapping",
        json={"section_codes": ["M1-HR"]},
        headers=headers,
    )

    assert captured["actor_employee_id"] == "SHIFTSUP"
