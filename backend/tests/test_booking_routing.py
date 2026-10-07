"""Business Rule Alignment: equipment-section-mapping-based booking routing, exercised through
the real booking-creation flow (not just equipment_service.resolve_sections() in isolation - see
test_equipment.py for that). Confirms booking_creation_service.create_booking() actually applies
most-specific-mapping-wins when creating booking_section_assignments rows, and that Section
Dashboard visibility follows those assignments correctly."""

from app.db import models
from tests.conftest import (
    make_true_admin_headers,
    make_movement_supervisor_headers,
    auth_header,
    grant_access,
    make_defect_type,
    make_section,
    make_shed_visit,
    make_user,
)


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _supervisor_headers(db_session, user_id, section_id, employee_id="SUP1"):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash", section_id=section_id)
    grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)


def _visit(db_session, visit_id=700, loco_number="39126"):
    return make_shed_visit(db_session, visit_id, loco_number, created_by=None)


# ------------------------------------------------------------ most-specific-wins hierarchy --
#
# Matches the brief's own example exactly:
#   Auxiliary Converter -> M35-Aux
#       Contactor -> M1-HR + M2-HR
# A booking against Contactor must go to M1-HR + M2-HR, never M35-Aux.


def _setup_hierarchy(mock_loco_client):
    mock_loco_client.add_locomotive("39126", "WAG9HC")
    mock_loco_client.add_node(1, 1, None, "Auxiliary Converter")
    mock_loco_client.set_mapping(1, ["M35-Aux"])
    mock_loco_client.add_node(2, 1, 1, "Contactor")
    mock_loco_client.set_mapping(2, ["M1-HR", "M2-HR"])
    mock_loco_client.add_node(3, 1, 2, "Contact Tip")  # no direct mapping - falls to Contactor


def test_exact_mapping_routes_correctly(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_section(db_session, 3, "M35-Aux")
    make_defect_type(db_session, 1, "DEFECTIVE")
    _setup_hierarchy(mock_loco_client)

    resp = client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126",
            "schedule_family": "MINOR", "schedule_variant": "IA", "arrival_condition": "WORKING",
            "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": [{"equipment_node_id": 2, "defect_type_id": 1, "remarks": "Contactor fault"}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["section_assignments_created"] == 2

    booking = db_session.query(models.Booking).one()
    sections = {
        db_session.get(models.Section, a.section_id).code
        for a in db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id).all()
    }
    assert sections == {"M1-HR", "M2-HR"}


def test_ancestor_fallback_routes_correctly_and_is_not_unioned(client, db_session, mock_loco_client):
    """Contact Tip (no direct mapping) falls back to its parent Contactor's mapping (M1-HR,
    M2-HR) - the grandparent Auxiliary Converter's M35-Aux must never appear."""
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_section(db_session, 3, "M35-Aux")
    make_defect_type(db_session, 1, "DEFECTIVE")
    _setup_hierarchy(mock_loco_client)

    resp = client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126", "schedule_family": "MINOR", "schedule_variant": "IA",
            "arrival_condition": "WORKING", "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": [{"equipment_node_id": 3, "defect_type_id": 1, "remarks": "Contact tip worn"}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["section_assignments_created"] == 2

    booking = db_session.query(models.Booking).one()
    sections = {
        db_session.get(models.Section, a.section_id).code
        for a in db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id).all()
    }
    assert sections == {"M1-HR", "M2-HR"}
    assert "M35-Aux" not in sections


def test_no_duplicate_assignment_rows_per_booking(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    _setup_hierarchy(mock_loco_client)

    resp = client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126", "schedule_family": "MINOR", "schedule_variant": "IA",
            "arrival_condition": "WORKING", "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": [{"equipment_node_id": 2, "defect_type_id": 1, "remarks": "x"}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    booking = db_session.query(models.Booking).one()
    rows = db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id).all()
    section_ids = [a.section_id for a in rows]
    assert len(section_ids) == len(set(section_ids))


# --------------------------------------------------------------- per-booking-source routing --


def test_manual_source_booking_routes_via_the_shared_create_booking_function(client, db_session, mock_loco_client):
    """MANUAL has no dedicated live creation endpoint yet, but routing lives in the one shared
    create_booking() function - call it directly to prove MANUAL is routed identically to every
    other source."""
    from datetime import datetime, timezone

    from app.services.booking_creation_service import create_booking

    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    _setup_hierarchy(mock_loco_client)
    visit = _visit(db_session)
    admin = make_user(db_session, 1, "ADM", "Admin", "Admin", "hash")

    booking, assignments_created = create_booking(
        db_session, mock_loco_client,
        shed_visit_id=visit.id, stage_id=None, booking_source="MANUAL",
        equipment_node_id=2, defect_type_id=1, description="manual entry",
        actor=admin, now=datetime.now(timezone.utc),
    )
    db_session.commit()

    assert assignments_created == 2
    sections = {
        db_session.get(models.Section, a.section_id).code
        for a in db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id).all()
    }
    assert sections == {"M1-HR", "M2-HR"}


# ------------------------------------------------------------------------------- visibility --


def test_shared_mapped_booking_visible_to_both_mapped_sections(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    _setup_hierarchy(mock_loco_client)

    resp = client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126", "schedule_family": "MINOR", "schedule_variant": "IA",
            "arrival_condition": "WORKING", "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": [{"equipment_node_id": 2, "defect_type_id": 1, "remarks": "Contactor fault"}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    booking_id = db_session.query(models.Booking).one().id

    sup1_headers = _supervisor_headers(db_session, 2, section_id=1, employee_id="SUP1")
    sup2_headers = _supervisor_headers(db_session, 3, section_id=2, employee_id="SUP2")

    resp1 = client.get("/api/sections/M1-HR/assignments", headers=sup1_headers)
    resp2 = client.get("/api/sections/M2-HR/assignments", headers=sup2_headers)

    assert resp1.status_code == 200 and resp2.status_code == 200
    assert [a["booking_id"] for a in resp1.json()] == [booking_id]
    assert [a["booking_id"] for a in resp2.json()] == [booking_id]


def test_unmapped_section_does_not_see_booking(client, db_session, mock_loco_client):
    """A Supervisor whose own section was never resolved for this booking's equipment sees
    nothing for it, even though the booking exists."""
    headers = _admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_section(db_session, 99, "UNRELATED")
    make_defect_type(db_session, 1, "DEFECTIVE")
    _setup_hierarchy(mock_loco_client)

    resp = client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126", "schedule_family": "MINOR", "schedule_variant": "IA",
            "arrival_condition": "WORKING", "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": [{"equipment_node_id": 2, "defect_type_id": 1, "remarks": "Contactor fault"}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    unrelated_headers = _supervisor_headers(db_session, 4, section_id=99, employee_id="SUPU")

    resp_unrelated = client.get("/api/sections/UNRELATED/assignments", headers=unrelated_headers)

    assert resp_unrelated.status_code == 200
    assert resp_unrelated.json() == []


def test_admin_sees_all_via_global_pool(client, db_session, mock_loco_client):
    # Shed In is a movement-section action; the global pool read is Admin-only. Two identities,
    # because under the Supervisor-only policy no single account holds both.
    headers = _admin_headers(db_session)
    admin_headers = make_true_admin_headers(db_session)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    _setup_hierarchy(mock_loco_client)

    client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126", "schedule_family": "MINOR", "schedule_variant": "IA",
            "arrival_condition": "WORKING", "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": [{"equipment_node_id": 2, "defect_type_id": 1, "remarks": "Contactor fault"}],
        },
        headers=headers,
    )

    resp = client.get("/api/bookings", headers=admin_headers)

    assert resp.status_code == 200
    assert len(resp.json()) == 1
