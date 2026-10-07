"""Creating equipment, and adding sections to equipment that already exists.

CREATING EQUIPMENT IS ADMIN-ONLY, AND HAS ONE ENTRY POINT. A Shed In Log Book booking used to be
able to create equipment from its "no equipment found" dead end, for an Admin or a SHIFT
Supervisor. That is withdrawn: creating equipment is master-data administration and now belongs
solely to the Equipment Responsibility Mapping page (POST /api/equipment/admin/nodes,
require_admin). The old route was DELETED, not re-gated - the first tests below prove a SHIFT
client cannot reach it at all, because hiding a button while the endpoint keeps answering is not
an authorization change.

Two things are under test and they are separate concerns:

  AUTHORIZATION - Admin and the SHIFT section only. Enforced by app/core/authz.py's
  require_equipment_creation, server-side, on the endpoint. Hiding the button in React
  is a usability decision and is not tested here, because it is not the boundary.

  OWNERSHIP - Loco Master owns equipment_nodes and equipment_section_map. This app has
  no equipment table and no grant on the `loco` database; every write crosses the
  boundary over HTTP. These tests assert the calls this app makes, through the same
  mock client that stands in for Loco Master everywhere else, including its duplicate
  rule (unique among siblings, case-insensitively).
"""

import pytest

from tests.conftest import (
    auth_header,
    ensure_section,
    grant_access,
    make_movement_supervisor_headers,
    make_section,
    make_section_supervisor_headers,
    make_true_admin_headers,
    make_user,
)

#: Every section that must NOT be able to create equipment. These are the real
#: production section codes, checked by name rather than by "not SHIFT" so a future
#: section added to this list is a deliberate act.
NON_SHIFT_SECTIONS = [
    "M1-HR",
    "M2-HR",
    "M4-HR",
    "M6-HR",
    "M7-HR",
    "M8-HR",
    "M9-HR",
    "M35-Aux",
    "M35-CP",
    "M35-TM",
]


def _body(**overrides):
    """The body the withdrawn Shed In route used to take, kept only to prove it is refused."""
    body = {
        "loco_number": "39126",
        "name": "Traction Motor Blower",
        "section_codes": ["SHIFT"],
    }
    body.update(overrides)
    return body


@pytest.fixture()
def loco(mock_loco_client, mock_bldcms_client):
    """Both sides of the boundary. BL-DCMS owns a locomotive's TECHNOLOGY (which decides
    the equipment family); Loco Master owns the equipment itself."""
    mock_loco_client.add_locomotive("39126", loco_type="WAG9HC")
    mock_bldcms_client.locomotive_technologies["39126"] = "3_PHASE"
    return mock_loco_client


def _shift_headers(db_session):
    """An entitled Supervisor of SHIFT - the account that runs Shed In."""
    return make_movement_supervisor_headers(db_session)


def _other_section_headers(db_session, code, user_id):
    section = ensure_section(db_session, id=50 + user_id, code=code, name=code)
    return make_section_supervisor_headers(
        db_session, user_id=user_id, section_id=section.id, employee_id=f"SUP{user_id}"
    )



# ============================ the Shed In creation route no longer exists ==================


def test_the_shed_in_creation_route_is_gone(client, db_session, loco):
    """Not 403 - absent. FastAPI answers 405 for a path that exists with other methods, which is
    what a removed route on /api/equipment/nodes looks like. Either way it never creates."""
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.post("/api/equipment/nodes", json=_body(), headers=_shift_headers(db_session))

    assert resp.status_code in (404, 405), resp.text
    assert loco.nodes == {}
    assert loco.created_nodes == []


def test_no_route_exposes_equipment_creation_outside_the_admin_one():
    """The whole published surface, asserted from the OpenAPI schema rather than by reading
    the source: exactly one POST creates equipment, and it is the Admin one. Adding a second
    creation route anywhere under /api/equipment fails this test."""
    from app.main import app

    paths = app.openapi()["paths"]
    creators = sorted(
        path
        for path, operations in paths.items()
        if path.startswith("/api/equipment")
        and "post" in operations
        and not path.endswith("/sections")
    )
    assert creators == ["/api/equipment/admin/nodes"]


def test_the_shed_in_creation_path_is_absent_from_the_api_schema():
    from app.main import app

    operations = app.openapi()["paths"].get("/api/equipment/nodes", {})
    assert "post" not in operations
    # Search and browse on that same path are untouched.
    assert "get" in operations


def test_an_admin_cannot_use_the_old_route_either(client, db_session, loco):
    """It is gone for everyone - this was a route removal, not a permission tightening."""
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.post(
        "/api/equipment/nodes", json=_body(), headers=make_true_admin_headers(db_session)
    )

    assert resp.status_code in (404, 405)


def test_no_account_reports_an_equipment_creation_capability(client, db_session):
    """The capability itself is withdrawn, so no client can present a create affordance."""
    ensure_section(db_session, id=18, code="SHIFT")
    for headers in (_shift_headers(db_session), make_true_admin_headers(db_session)):
        caps = client.get("/api/auth/me", headers=headers).json()["capabilities"]
        assert "can_create_equipment" not in caps


def test_shed_in_equipment_search_and_selection_still_work_for_shift(client, db_session, loco):
    """The point of the change is to remove CREATION, not to break the workflow. A SHIFT
    Supervisor must still be able to find and select equipment for a Log Book booking."""
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor Blower")
    loco.mappings[10] = ["SHIFT"]

    found = client.get(
        "/api/equipment/nodes/search?q=blower&loco_number=39126", headers=headers
    ).json()
    assert [n["id"] for n in found] == [10]

    roots = client.get("/api/equipment/nodes?loco_number=39126", headers=headers)
    assert roots.status_code == 200
    resolved = client.get("/api/equipment/nodes/10/resolved-sections", headers=headers).json()
    assert resolved["section_codes"] == ["SHIFT"]


# =========================================================== existing equipment ==
# Equipment that already exists must not be duplicated just because it needs another
# responsible section. equipment_section_map is already many-to-many, so the node is
# reused and mappings are ADDED - no schema change, no second node.


def _match(client, headers, name, loco_number="39126"):
    return client.get(
        f"/api/equipment/nodes/match?loco_number={loco_number}&name={name}", headers=headers
    )


def test_match_finds_an_existing_node_with_its_path_and_sections(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Bogie")
    loco.add_node(11, 1, 10, "Traction Motor")
    loco.mappings[11] = ["M35-TM"]

    items = _match(client, headers, "traction motor").json()

    assert [n["id"] for n in items] == [11]
    assert [p["name"] for p in items[0]["path"]] == ["Bogie", "Traction Motor"]
    assert items[0]["section_codes"] == ["M35-TM"]


def test_match_is_insensitive_to_case_and_whitespace(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")

    assert [n["id"] for n in _match(client, headers, "  tRaCtIoN   motor  ").json()] == [10]


def test_match_returns_every_namesake_and_guesses_none(client, db_session, loco):
    """Production has 1,129 reused names across 7,423 nodes - 'Split Pin' appears 149
    times under different parents. Picking one would attach a booking to the wrong
    equipment silently, so all of them come back and the user chooses."""
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Bogie")
    loco.add_node(11, 1, None, "Pantograph")
    loco.add_node(12, 1, 10, "Split Pin")
    loco.add_node(13, 1, 11, "Split Pin")
    loco.mappings[12] = ["M1-HR"]
    loco.mappings[13] = ["M2-HR"]

    items = _match(client, headers, "split pin").json()

    assert sorted(n["id"] for n in items) == [12, 13]
    # Each is distinguishable by path AND by who maintains it.
    paths = {tuple(p["name"] for p in n["path"]) for n in items}
    assert paths == {("Bogie", "Split Pin"), ("Pantograph", "Split Pin")}
    assert {n["section_codes"][0] for n in items} == {"M1-HR", "M2-HR"}


def test_match_never_crosses_into_the_other_technology(client, db_session, loco, mock_bldcms_client):
    """12 root names exist in BOTH production families, e.g. 'Traction motor' (3-phase)
    and 'Traction Motor' (conventional)."""
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _shift_headers(db_session)
    loco.add_locomotive("22345", loco_type="WAG7")
    mock_bldcms_client.locomotive_technologies["22345"] = "CONVENTIONAL"
    loco.add_node(10, 1, None, "Traction motor")
    loco.add_node(20, 2, None, "Traction Motor")

    assert [n["id"] for n in _match(client, headers, "traction motor").json()] == [10]
    assert [
        n["id"] for n in _match(client, headers, "traction motor", "22345").json()
    ] == [20]


def test_match_is_empty_when_the_equipment_is_genuinely_new(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")

    assert _match(client, _shift_headers(db_session), "Oil Cooler Fan").json() == []


# ------------------------------------------------- adding sections to an existing node --


def _add_sections(client, headers, node_id, codes):
    return client.post(
        f"/api/equipment/nodes/{node_id}/sections",
        json={"section_codes": codes},
        headers=headers,
    )


def test_adding_a_section_reuses_the_node_and_creates_nothing(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")
    loco.mappings[10] = ["SHIFT"]
    before = len(loco.nodes)

    body = _add_sections(client, headers, 10, ["M7-HR"]).json()

    assert body["equipment_node_id"] == 10
    assert sorted(body["section_codes"]) == ["M7-HR", "SHIFT"]
    assert body["newly_added"] == ["M7-HR"]
    assert body["already_present"] == []
    assert len(loco.nodes) == before  # no second equipment node


def test_adding_sections_is_idempotent(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")
    loco.mappings[10] = ["SHIFT"]

    first = _add_sections(client, headers, 10, ["SHIFT", "M7-HR"]).json()
    second = _add_sections(client, headers, 10, ["SHIFT", "M7-HR"]).json()

    assert first["already_present"] == ["SHIFT"]
    assert first["newly_added"] == ["M7-HR"]
    assert second["newly_added"] == []
    assert sorted(second["already_present"]) == ["M7-HR", "SHIFT"]
    assert first["section_codes"] == second["section_codes"]


def test_adding_a_section_never_removes_the_existing_ones(client, db_session, loco):
    """The reason this is POST /sections and not PUT /mapping."""
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    make_section(db_session, id=6, code="M35-TM", name="M35-TM")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")
    loco.mappings[10] = ["M35-TM", "SHIFT"]

    body = _add_sections(client, headers, 10, ["M7-HR"]).json()

    assert sorted(body["section_codes"]) == ["M35-TM", "M7-HR", "SHIFT"]


def test_adding_several_sections_at_once(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    make_section(db_session, id=6, code="M35-TM", name="M35-TM")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")

    body = _add_sections(client, headers, 10, ["M7-HR", "M35-TM", "SHIFT"]).json()

    assert sorted(body["section_codes"]) == ["M35-TM", "M7-HR", "SHIFT"]


def test_a_reused_node_can_be_booked_against(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")

    _add_sections(client, headers, 10, ["M7-HR"])

    resolved = client.get("/api/equipment/nodes/10/resolved-sections", headers=headers).json()
    assert resolved["resolution"] == "EXACT"
    assert resolved["section_codes"] == ["M7-HR"]


def test_an_unknown_section_is_refused_and_nothing_is_mapped(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")

    resp = _add_sections(client, headers, 10, ["NOT-A-SECTION"])

    assert resp.status_code == 422
    assert loco.mappings.get(10, []) == []


def test_an_empty_section_list_is_refused(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")

    assert _add_sections(client, headers, 10, []).status_code == 422


def test_adding_to_an_unknown_node_is_404(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")

    assert _add_sections(client, _shift_headers(db_session), 999, ["SHIFT"]).status_code == 404


def test_the_actor_is_recorded_when_sections_are_added(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    headers = _shift_headers(db_session)
    loco.add_node(10, 1, None, "Traction Motor")

    _add_sections(client, headers, 10, ["M7-HR"])

    assert loco.section_additions[0]["actor_employee_id"] == "SHIFTSUP"


# --------------------------------------------- authorization on the mapping path --
# POST /nodes/{id}/sections is gated by the EQUIPMENT MAPPING permission, the same one as
# PUT /nodes/{id}/mapping beside it - not by the Shed In creation rule. It changes who is
# responsible for equipment, which is the mapping capability.


@pytest.mark.parametrize("code", NON_SHIFT_SECTIONS)
def test_adding_sections_is_forbidden_without_the_mapping_permission(
    client, db_session, loco, code
):
    ensure_section(db_session, id=18, code="SHIFT")
    loco.add_node(10, 1, None, "Traction Motor")
    headers = _other_section_headers(db_session, code, user_id=7)

    resp = _add_sections(client, headers, 10, ["SHIFT"])

    assert resp.status_code == 403
    assert loco.section_additions == []


def test_a_supervisor_with_the_mapping_permission_may_add_sections(client, db_session, loco):
    """Not a SHIFT-only capability: it matches PUT /mapping, which this account may
    already use on the same page."""
    ensure_section(db_session, id=18, code="SHIFT")
    section = make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    loco.add_node(10, 1, None, "Traction Motor")
    headers = make_section_supervisor_headers(
        db_session, user_id=31, section_id=section.id, employee_id="MAPSUP",
        can_manage_equipment_mapping=True,
    )

    assert _add_sections(client, headers, 10, ["M7-HR"]).status_code == 200


def test_adding_sections_requires_authentication(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    loco.add_node(10, 1, None, "Traction Motor")

    resp = client.post("/api/equipment/nodes/10/sections", json={"section_codes": ["SHIFT"]})

    assert resp.status_code == 401
    assert loco.section_additions == []


def test_admin_may_add_sections(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    loco.add_node(10, 1, None, "Traction Motor")

    resp = _add_sections(client, make_true_admin_headers(db_session), 10, ["M7-HR"])

    assert resp.status_code == 200


# ===================================================== POST /api/equipment/admin/nodes ==
# The Equipment Responsibility Mapping page's create action. ADMIN ONLY, and a different
# endpoint from the Shed In one on purpose: it accepts an explicit family and parent, which
# is administration of the master data rather than recording a defect.


def _admin_body(**overrides):
    body = {"family_code": "3PHASE", "name": "Traction Motor", "section_codes": ["SHIFT"]}
    body.update(overrides)
    return body


def test_admin_may_create_with_an_explicit_family(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(),
        headers=make_true_admin_headers(db_session),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["family_id"] == 1
    assert resp.json()["parent_id"] is None
    assert resp.json()["section_codes"] == ["SHIFT"]


def test_admin_may_create_under_a_chosen_parent(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    loco.add_node(10, 1, None, "Bogie")

    resp = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(parent_id=10, name="Axle Box"),
        headers=make_true_admin_headers(db_session),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["parent_id"] == 10


def test_admin_may_map_several_sections_at_creation(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    make_section(db_session, id=6, code="M35-TM", name="M35-TM")

    body = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(section_codes=["M35-TM", "M7-HR", "SHIFT"]),
        headers=make_true_admin_headers(db_session),
    ).json()

    assert sorted(body["section_codes"]) == ["M35-TM", "M7-HR", "SHIFT"]


def test_a_child_may_be_created_without_a_mapping_and_inherits_its_parents(
    client, db_session, loco
):
    """Ancestor resolution routes it, so it is bookable without a mapping of its own."""
    ensure_section(db_session, id=18, code="SHIFT")
    loco.add_node(10, 1, None, "Bogie")
    loco.mappings[10] = ["SHIFT"]
    headers = make_true_admin_headers(db_session)

    created = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(parent_id=10, name="Axle Box", section_codes=[]),
        headers=headers,
    ).json()

    resolved = client.get(
        f"/api/equipment/nodes/{created['id']}/resolved-sections", headers=headers
    ).json()
    assert resolved["resolution"] == "ANCESTOR"
    assert resolved["section_codes"] == ["SHIFT"]


def test_a_root_still_cannot_be_created_unmapped(client, db_session, loco):
    """No ancestor to inherit from, so it would be permanently unbookable."""
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(section_codes=[]),
        headers=make_true_admin_headers(db_session),
    )

    assert resp.status_code == 422
    assert loco.nodes == {}


def test_admin_creation_prevents_a_canonical_duplicate(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    headers = make_true_admin_headers(db_session)
    client.post("/api/equipment/admin/nodes", json=_admin_body(), headers=headers)

    resp = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(name="  traction   MOTOR  "),
        headers=headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DUPLICATE_EQUIPMENT"
    assert len(loco.nodes) == 1


def test_admin_creation_allows_the_same_name_under_a_different_parent(client, db_session, loco):
    """Identity is family + parent + canonical name. Production reuses 1,129 names."""
    ensure_section(db_session, id=18, code="SHIFT")
    loco.add_node(10, 1, None, "Bogie")
    loco.add_node(11, 1, None, "Pantograph")
    headers = make_true_admin_headers(db_session)

    first = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(parent_id=10, name="Split Pin", section_codes=[]),
        headers=headers,
    )
    second = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(parent_id=11, name="Split Pin", section_codes=[]),
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_a_shift_supervisor_may_not_use_the_admin_create_endpoint(client, db_session, loco):
    """SHIFT may create from Shed In. It may NOT place equipment anywhere in the
    hierarchy - that is administration of the master data."""
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.post(
        "/api/equipment/admin/nodes", json=_admin_body(), headers=_shift_headers(db_session)
    )

    assert resp.status_code == 403
    assert loco.nodes == {}


def test_a_supervisor_with_the_mapping_permission_may_not_use_it_either(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")
    section = make_section(db_session, id=12, code="M7-HR", name="M7-HR")
    headers = make_section_supervisor_headers(
        db_session, user_id=32, section_id=section.id, employee_id="MAPSUP2",
        can_manage_equipment_mapping=True,
    )

    resp = client.post("/api/equipment/admin/nodes", json=_admin_body(), headers=headers)

    assert resp.status_code == 403
    assert loco.nodes == {}


@pytest.mark.parametrize("code", NON_SHIFT_SECTIONS)
def test_admin_create_is_forbidden_for_every_other_section(client, db_session, loco, code):
    ensure_section(db_session, id=18, code="SHIFT")
    headers = _other_section_headers(db_session, code, user_id=7)

    assert client.post(
        "/api/equipment/admin/nodes", json=_admin_body(), headers=headers
    ).status_code == 403


def test_admin_create_requires_authentication(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")

    assert client.post("/api/equipment/admin/nodes", json=_admin_body()).status_code == 401
    assert loco.nodes == {}


def test_admin_create_rejects_an_unknown_family(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(family_code="NOPE"),
        headers=make_true_admin_headers(db_session),
    )

    assert resp.status_code == 404


def test_admin_create_rejects_an_unknown_section(client, db_session, loco):
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.post(
        "/api/equipment/admin/nodes",
        json=_admin_body(section_codes=["NOT-A-SECTION"]),
        headers=make_true_admin_headers(db_session),
    )

    assert resp.status_code == 422
    assert loco.nodes == {}


def test_match_can_be_scoped_by_family_for_the_mapping_page(client, db_session, loco):
    """The mapping page has no locomotive - it browses a family directly."""
    ensure_section(db_session, id=18, code="SHIFT")
    loco.add_node(10, 1, None, "Traction motor")
    loco.add_node(20, 2, None, "Traction Motor")
    headers = make_true_admin_headers(db_session)

    assert [
        n["id"]
        for n in client.get(
            "/api/equipment/nodes/match?family=3PHASE&name=traction+motor", headers=headers
        ).json()
    ] == [10]
    assert [
        n["id"]
        for n in client.get(
            "/api/equipment/nodes/match?family=CONVENTIONAL&name=traction+motor", headers=headers
        ).json()
    ] == [20]


def test_match_refuses_both_a_family_and_a_locomotive(client, db_session, loco):
    """Same rule as browse and search: the server decides, or the caller does, never both."""
    ensure_section(db_session, id=18, code="SHIFT")

    resp = client.get(
        "/api/equipment/nodes/match?family=3PHASE&loco_number=39126&name=x",
        headers=make_true_admin_headers(db_session),
    )

    assert resp.status_code == 422
