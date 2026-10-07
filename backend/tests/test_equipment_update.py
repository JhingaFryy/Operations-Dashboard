"""Editing EXISTING equipment from the Equipment Responsibility Mapping page. ADMIN ONLY.

Two concerns, kept separate:

  AUTHORIZATION - Admin and nobody else, enforced server-side by require_admin on the route.
  A Supervisor with can_manage_equipment_mapping may still edit MAPPINGS through the mapping
  routes, but equipment master data (name, description, active state) is Admin-only, and a
  SHIFT Supervisor regains nothing here. Hiding the button is not the boundary and is not what
  these tests assert.

  OWNERSHIP - Loco Master owns equipment_nodes. This app has no grant on that database; the
  browser never talks to Loco Master, and actor_employee_id is derived from the authenticated
  Admin rather than accepted from the request body.
"""

from tests.conftest import (
    ensure_section,
    make_section,
    make_section_supervisor_headers,
    make_true_admin_headers,
)


def _node(mock_loco_client, node_id=10, name="Aux Converter", parent_id=None, family_id=1):
    mock_loco_client.add_node(node_id, family_id, parent_id, name)
    mock_loco_client.set_mapping(node_id, ["M4-HR"])
    return node_id


def _sections(db_session):
    make_section(db_session, 4, "M4-HR")
    make_section(db_session, 2, "M2-HR")
    make_section(db_session, 6, "M6-HR")


def _patch(client, headers, node_id, **body):
    return client.patch(f"/api/equipment/admin/nodes/{node_id}", headers=headers, json=body)


# ------------------------------------------------------------- authorization --


def test_an_admin_may_edit_equipment(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, name="Auxiliary Converter")

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Auxiliary Converter"


def test_a_supervisor_is_refused_server_side(client, db_session, mock_loco_client):
    """Not by a hidden button - by the endpoint. A Supervisor who crafts the request is refused."""
    _sections(db_session)
    _node(mock_loco_client)
    ensure_section(db_session, 4, "M4-HR")
    headers = make_section_supervisor_headers(db_session, 50, 4, employee_id="SUP4")

    assert _patch(client, headers, 10, name="Sneaky").status_code == 403


def test_a_supervisor_with_the_mapping_capability_is_still_refused(
    client, db_session, mock_loco_client
):
    """can_manage_equipment_mapping governs MAPPINGS. Equipment master data is Admin-only, and
    the two must not be conflated - one is "who maintains this", the other is "what this is"."""
    _sections(db_session)
    _node(mock_loco_client)
    ensure_section(db_session, 4, "M4-HR")
    headers = make_section_supervisor_headers(
        db_session, 51, 4, employee_id="SUPMAP", can_manage_equipment_mapping=True
    )

    assert _patch(client, headers, 10, name="Sneaky").status_code == 403


def test_an_unauthenticated_request_is_refused(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client)

    assert client.patch("/api/equipment/admin/nodes/10", json={"name": "X"}).status_code == 401


def test_the_actor_comes_from_the_authenticated_admin_not_the_body(
    client, db_session, mock_loco_client
):
    """actor_employee_id is never browser-authoritative. Sending one changes nothing."""
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, name="Renamed", actor_employee_id="SOMEONE_ELSE")

    # Accepted and ignored - the schema has no such field, so it cannot reach Loco Master.
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------ renaming --


def test_a_duplicate_rename_is_refused_with_a_usable_message(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client, 10, "Aux Converter")
    _node(mock_loco_client, 11, "Pantograph")
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 11, name="Aux Converter")

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "DUPLICATE_EQUIPMENT"
    assert detail["existing_node_id"] == 10


def test_a_case_only_duplicate_rename_is_refused(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client, 10, "Aux Converter")
    _node(mock_loco_client, 11, "Pantograph")
    headers = make_true_admin_headers(db_session)

    assert _patch(client, headers, 11, name="AUX CONVERTER").status_code == 409


def test_a_whitespace_only_duplicate_rename_is_refused(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client, 10, "Aux Converter")
    _node(mock_loco_client, 11, "Pantograph")
    headers = make_true_admin_headers(db_session)

    assert _patch(client, headers, 11, name="Aux   Converter").status_code == 409


def test_the_same_name_under_a_different_parent_is_allowed(client, db_session, mock_loco_client):
    _sections(db_session)
    mock_loco_client.add_node(1, 1, None, "Bogie")
    mock_loco_client.add_node(2, 1, None, "Roof")
    _node(mock_loco_client, 10, "Others", parent_id=1)
    _node(mock_loco_client, 11, "Spares", parent_id=2)
    headers = make_true_admin_headers(db_session)

    assert _patch(client, headers, 11, name="Others").status_code == 200


# -------------------------------------------------------------- mapping edits --


def test_an_admin_can_replace_the_section_mapping(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, section_codes=["M2-HR", "M6-HR"])

    assert resp.status_code == 200, resp.text
    assert sorted(resp.json()["section_codes"]) == ["M2-HR", "M6-HR"]
    # Replace-set: the previous section is gone, not merged.
    assert "M4-HR" not in mock_loco_client.mappings[10]


def test_an_admin_can_map_equipment_to_several_sections(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, section_codes=["M4-HR", "M2-HR", "M6-HR"])

    assert sorted(resp.json()["section_codes"]) == ["M2-HR", "M4-HR", "M6-HR"]


def test_resaving_the_same_mapping_is_idempotent(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    first = _patch(client, headers, 10, section_codes=["M4-HR"])
    second = _patch(client, headers, 10, section_codes=["M4-HR"])

    assert first.json()["section_codes"] == second.json()["section_codes"] == ["M4-HR"]


def test_omitting_section_codes_leaves_the_mapping_untouched(client, db_session, mock_loco_client):
    """A rename must not wipe a mapping the dialog did not send."""
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, name="Renamed")

    assert resp.json()["section_codes"] == ["M4-HR"]
    assert mock_loco_client.mappings[10] == ["M4-HR"]


def test_clearing_every_section_is_refused(client, db_session, mock_loco_client):
    """Equipment with no mapping and no mapped ancestor is unbookable."""
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, section_codes=[])

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SECTION_REQUIRED"


def test_an_unknown_section_code_is_rejected_before_anything_is_written(
    client, db_session, mock_loco_client
):
    _sections(db_session)
    _node(mock_loco_client, 10, "Aux Converter")
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, name="Renamed", section_codes=["NOT-A-SECTION"])

    assert resp.status_code == 422
    # Validated FIRST, so the rename did not land either - no half-done edit.
    assert mock_loco_client.nodes[10]["name"] == "Aux Converter"


# ------------------------------------------------------- partial failure --


def test_a_mapping_failure_after_a_successful_rename_is_reported_as_partial(
    client, db_session, mock_loco_client
):
    """Two systems, no shared transaction. The one thing that must never happen is telling the
    Admin everything saved when only half of it did."""
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)
    mock_loco_client.fail_mapping_updates()

    resp = _patch(client, headers, 10, name="Renamed", section_codes=["M2-HR"])

    assert resp.status_code == 502, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "MAPPING_UPDATE_FAILED"
    assert detail["partial"] is True
    assert "saved" in detail["message"]
    # The metadata really did land, which is exactly why the response must not claim failure
    # of the whole operation either.
    assert mock_loco_client.nodes[10]["name"] == "Renamed"


def test_a_mapping_only_failure_is_not_reported_as_partial(client, db_session, mock_loco_client):
    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)
    mock_loco_client.fail_mapping_updates()

    resp = _patch(client, headers, 10, section_codes=["M2-HR"])

    assert resp.status_code == 502
    assert resp.json()["detail"]["partial"] is False


# ---------------------------------------------------- what it cannot do --


def test_equipment_can_never_be_moved_through_this_route(client, db_session, mock_loco_client):
    _sections(db_session)
    mock_loco_client.add_node(1, 1, None, "Bogie")
    _node(mock_loco_client, 10, "Aux Converter")
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, name="Renamed", parent_id=1, family_code="WAP7")

    assert resp.status_code == 200, resp.text
    # Neither field exists on the request schema, so neither can reach Loco Master.
    assert mock_loco_client.nodes[10]["parent_id"] is None
    assert mock_loco_client.nodes[10]["family_id"] == 1


def test_a_rename_does_not_change_the_node_id_historical_bookings_point_at(
    client, db_session, mock_loco_client
):
    """Bookings store equipment_node_id. A rename must leave that identity alone - the booking
    stays attached to the same node, and simply displays the new name."""
    _sections(db_session)
    _node(mock_loco_client, 10, "Aux Converter")
    headers = make_true_admin_headers(db_session)

    resp = _patch(client, headers, 10, name="Auxiliary Converter")

    assert resp.json()["id"] == 10
    assert 10 in mock_loco_client.nodes


def test_the_edit_is_audit_logged_with_actor_and_changed_fields(
    client, db_session, mock_loco_client, caplog
):
    import logging

    _sections(db_session)
    _node(mock_loco_client)
    headers = make_true_admin_headers(db_session)

    with caplog.at_level(logging.INFO):
        _patch(client, headers, 10, name="Renamed", section_codes=["M2-HR"])

    updated = next(r for r in caplog.records if getattr(r, "event", None) == "EQUIPMENT_UPDATED")
    assert updated.equipment_node_id == 10
    assert "name" in updated.changed_fields
    assert updated.actor_employee_id

    mapped = next(
        r for r in caplog.records if getattr(r, "event", None) == "EQUIPMENT_MAPPING_UPDATED"
    )
    assert mapped.previous_section_codes == ["M4-HR"]
    assert mapped.final_section_codes == ["M2-HR"]
