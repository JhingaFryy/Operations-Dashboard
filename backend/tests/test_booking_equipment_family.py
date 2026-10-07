"""Phase H: a booking's equipment must belong to its locomotive's technology - enforced by this
server, for the Dashboard's own Log Book channel, and for the family-scoped equipment reads both
booking forms browse.

The Android/BL-DCMS channel's half of the same matrix lives in tests/test_internal_bookings.py.
Common to both: the client sends no technology and no family. It cannot: the value is derived from
the visit's locomotive (BL-DCMS owns technology) and compared against Loco Master's family_id.
"""

from app.db import models
from tests.conftest import (
    make_defect_type,
    make_movement_supervisor_headers,
    make_section,
)

THREE_PHASE_NODE = 1843
CONVENTIONAL_NODE = 2901


def _setup(db_session, mock_loco_client, loco_type="WAG9HC"):
    """One locomotive and BOTH equipment trees, so nothing but the family check can decide which
    node is acceptable."""
    mock_loco_client.add_locomotive("39126", loco_type)
    make_section(db_session, 1, "M1-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_defect_type(db_session, 9, "RETIRED", is_active=False)
    mock_loco_client.add_node(THREE_PHASE_NODE, 1, None, "Aux Converter")
    mock_loco_client.add_node(1844, 1, THREE_PHASE_NODE, "BUR-1")
    mock_loco_client.add_node(1845, 1, 1844, "Control Unit")
    mock_loco_client.set_mapping(THREE_PHASE_NODE, ["M1-HR"])
    mock_loco_client.set_mapping(1845, ["M1-HR"])
    mock_loco_client.add_node(CONVENTIONAL_NODE, 2, None, "Tap Changer")
    mock_loco_client.add_node(2902, 2, CONVENTIONAL_NODE, "GR Contactor")
    mock_loco_client.set_mapping(CONVENTIONAL_NODE, ["M1-HR"])


def _shed_in(client, db_session, bookings):
    return client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126",
            "schedule_family": "MINOR",
            "schedule_variant": "IA",
            "arrival_condition": "WORKING",
            "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": bookings,
        },
        headers=make_movement_supervisor_headers(db_session),
    )


def _booking(node_id=THREE_PHASE_NODE, defect_type_id=1, remarks="Isolated after fault"):
    return {"equipment_node_id": node_id, "defect_type_id": defect_type_id, "remarks": remarks}


# ----------------------------------------------------- Log Book creation: the family matrix --


def test_log_book_accepts_equipment_of_the_locomotives_own_family(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")

    resp = _shed_in(client, db_session, [_booking(THREE_PHASE_NODE)])

    assert resp.status_code == 200, resp.text
    assert resp.json()["bookings_created"] == 1


def test_log_book_refuses_conventional_equipment_on_a_3_phase_locomotive(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")

    resp = _shed_in(client, db_session, [_booking(CONVENTIONAL_NODE)])

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "EQUIPMENT_FAMILY_MISMATCH"
    # The whole Shed In rolls back: no visit, no booking.
    assert db_session.query(models.Booking).count() == 0
    assert db_session.query(models.ShedVisit).count() == 0


def test_log_book_accepts_conventional_equipment_on_a_conventional_locomotive(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client, loco_type="WAG7")
    mock_bldcms_client.set_locomotive_technology("39126", "CONVENTIONAL")

    resp = _shed_in(client, db_session, [_booking(CONVENTIONAL_NODE)])

    assert resp.status_code == 200, resp.text
    assert resp.json()["bookings_created"] == 1


def test_log_book_refuses_3_phase_equipment_on_a_conventional_locomotive(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client, loco_type="WAG7")
    mock_bldcms_client.set_locomotive_technology("39126", "CONVENTIONAL")

    resp = _shed_in(client, db_session, [_booking(THREE_PHASE_NODE)])

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "EQUIPMENT_FAMILY_MISMATCH"


def test_log_book_refuses_an_unknown_node_and_an_inactive_defect_type(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client)

    unknown_node = _shed_in(client, db_session, [_booking(node_id=987654)])
    assert unknown_node.status_code == 422
    assert unknown_node.json()["detail"]["code"] == "EQUIPMENT_NOT_FOUND"

    inactive = _shed_in(client, db_session, [_booking(defect_type_id=9)])
    assert inactive.status_code == 422
    assert inactive.json()["detail"]["code"] == "UNKNOWN_DEFECT_TYPE"


def test_log_book_refuses_blank_remarks(client, db_session, mock_loco_client, mock_bldcms_client):
    _setup(db_session, mock_loco_client)

    for blank in ("", "   ", "\n\t"):
        resp = _shed_in(client, db_session, [_booking(remarks=blank)])
        assert resp.status_code == 422, blank
    assert db_session.query(models.Booking).count() == 0


def test_a_deeper_node_is_bookable_not_only_a_leaf_or_a_root(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Any node of the right family may be the booking target - root (1843) or a level-3 node
    (1845). Depth is not a rule here; family is."""
    _setup(db_session, mock_loco_client)

    resp = _shed_in(client, db_session, [_booking(1845)])

    assert resp.status_code == 200, resp.text
    assert db_session.query(models.Booking).one().equipment_node_id == 1845


def test_a_shed_in_with_no_bookings_never_needs_the_technology(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """A locomotive BL-DCMS has no record of can still be shedded in; only bookings need a family."""
    _setup(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", None)

    resp = _shed_in(client, db_session, [])

    assert resp.status_code == 200, resp.text


# ------------------------------------------- the reference reads both booking forms browse --


def _headers(db_session):
    return make_movement_supervisor_headers(db_session)


def test_nodes_are_listed_in_the_locomotives_family_at_every_depth(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")
    headers = _headers(db_session)

    roots = client.get("/api/equipment/nodes", params={"loco_number": "39126"}, headers=headers)
    assert [n["id"] for n in roots.json()] == [THREE_PHASE_NODE]  # the Conventional root is absent

    children = client.get(
        "/api/equipment/nodes",
        params={"loco_number": "39126", "parent_id": THREE_PHASE_NODE},
        headers=headers,
    )
    assert [n["id"] for n in children.json()] == [1844]

    grandchildren = client.get(
        "/api/equipment/nodes", params={"loco_number": "39126", "parent_id": 1844}, headers=headers
    )
    assert [n["id"] for n in grandchildren.json()] == [1845]
    assert grandchildren.json()[0]["has_children"] is False  # the cascade ends here, by data


def test_a_conventional_locomotive_sees_only_conventional_roots(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client, loco_type="WAG7")
    mock_bldcms_client.set_locomotive_technology("39126", "CONVENTIONAL")

    roots = client.get(
        "/api/equipment/nodes", params={"loco_number": "39126"}, headers=_headers(db_session)
    )

    assert [n["id"] for n in roots.json()] == [CONVENTIONAL_NODE]


def test_search_cannot_return_another_technologys_equipment(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    _setup(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")
    mock_loco_client.add_node(2903, 2, CONVENTIONAL_NODE, "Aux Converter (conventional)")

    results = client.get(
        "/api/equipment/nodes/search",
        params={"loco_number": "39126", "q": "aux converter"},
        headers=_headers(db_session),
    )

    assert [n["id"] for n in results.json()] == [THREE_PHASE_NODE]


def test_a_locomotive_and_a_family_together_are_refused(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """The one way a client could still smuggle a family in - it cannot."""
    _setup(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")

    resp = client.get(
        "/api/equipment/nodes",
        params={"loco_number": "39126", "family": "CONVENTIONAL"},
        headers=_headers(db_session),
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "FAMILY_NOT_ACCEPTED"


def test_the_equipment_mapping_screen_still_browses_by_family(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Equipment Responsibility Mapping is not a booking form: it legitimately browses a family
    directly, and this change must not take that away."""
    _setup(db_session, mock_loco_client)

    resp = client.get(
        "/api/equipment/nodes", params={"family": "CONVENTIONAL"}, headers=_headers(db_session)
    )

    assert resp.status_code == 200
    assert [n["id"] for n in resp.json()] == [CONVENTIONAL_NODE]
