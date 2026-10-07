"""Business Rule Alignment: the global Booking Pool (GET /api/bookings) is Admin-only
operational visibility now, not the primary Supervisor workflow. Its booking-level lifecycle
mutations (POST /api/bookings/{id}/start|attend|reopen) are retired/frozen - always 410 - in
favor of assignment-level mutations (see test_section_dashboard.py). See
app/services/booking_pool_service.py's module docstring for the full design rationale."""

from app.db import models
from tests.conftest import (
    make_true_admin_headers,
    make_movement_supervisor_headers,
    auth_header,
    grant_access,
    make_assignment,
    make_booking,
    make_section,
    make_shed_visit,
    make_user,
)


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _supervisor_headers(db_session, user_id, section_id=None, employee_id="SUP1"):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash", section_id=section_id)
    grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)


def _visit(db_session, visit_id=700, loco_number="39126"):
    return make_shed_visit(db_session, visit_id, loco_number, created_by=None)


# ------------------------------------------------------------------- visibility --


def test_admin_sees_bookings(client, db_session, mock_loco_client):
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_booking(db_session, 1, visit.id, created_by=None)

    resp = client.get("/api/bookings", headers=headers)

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


def test_supervisor_cannot_access_the_global_booking_pool(client, db_session, mock_loco_client):
    """Business Rule Alignment: the global pool is Admin-only now - a Supervisor's operational
    visibility is the Section Dashboard instead (see test_section_dashboard.py)."""
    visit = _visit(db_session)
    make_booking(db_session, 1, visit.id, created_by=None)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get("/api/bookings", headers=headers)

    assert resp.status_code == 403


def test_assignment_absence_does_not_hide_booking(client, db_session, mock_loco_client):
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_booking(db_session, 1, visit.id, created_by=None)
    # Deliberately no BookingSectionAssignment row created at all.
    assert db_session.query(models.BookingSectionAssignment).count() == 0

    resp = client.get("/api/bookings", headers=headers)

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_equipment_mapping_absence_does_not_hide_booking_from_admin_pool(client, db_session, mock_loco_client):
    """The global Admin pool lists every booking regardless of routing outcome - equipment
    mapping only affects Section Dashboard visibility, never this listing."""
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    make_booking(db_session, 1, visit.id, created_by=None, equipment_node_id=1843)

    resp = client.get("/api/bookings", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["equipment_node_name"] == "Aux Converter"


def test_supervisor_without_dashboard_access_rejected(client, db_session, mock_loco_client):
    make_user(db_session, 2, "SUP1", "Sup", "Supervisor", "hash")
    headers = auth_header("SUP1", "Supervisor", 2)

    resp = client.get("/api/bookings", headers=headers)

    assert resp.status_code == 403


# ------------------------------------------------------------------------ filters --


def test_list_filters_by_status(client, db_session, mock_loco_client):
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_booking(db_session, 1, visit.id, created_by=None, status="OPEN")
    make_booking(db_session, 2, visit.id, created_by=None, status="ATTENDED")

    resp = client.get("/api/bookings?status=ATTENDED", headers=headers)

    assert resp.status_code == 200
    assert [b["id"] for b in resp.json()] == [2]


def test_list_filters_by_source(client, db_session, mock_loco_client):
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_booking(db_session, 1, visit.id, created_by=None, booking_source="LOG_BOOK")
    make_booking(db_session, 2, visit.id, created_by=None, booking_source="TEST_BEFORE")

    resp = client.get("/api/bookings?source=TEST_BEFORE", headers=headers)

    assert resp.status_code == 200
    assert [b["id"] for b in resp.json()] == [2]


def test_list_filters_by_equipment(client, db_session, mock_loco_client):
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_booking(db_session, 1, visit.id, created_by=None, equipment_node_id=1843)
    make_booking(db_session, 2, visit.id, created_by=None, equipment_node_id=200)

    resp = client.get("/api/bookings?equipment_node_id=200", headers=headers)

    assert resp.status_code == 200
    assert [b["id"] for b in resp.json()] == [2]


def test_list_filters_by_shed_visit(client, db_session, mock_loco_client):
    headers = make_true_admin_headers(db_session)
    visit1 = _visit(db_session, visit_id=700, loco_number="39126")
    visit2 = _visit(db_session, visit_id=701, loco_number="39127")
    make_booking(db_session, 1, visit1.id, created_by=None)
    make_booking(db_session, 2, visit2.id, created_by=None)

    resp = client.get(f"/api/bookings?shed_visit_id={visit1.id}", headers=headers)

    assert resp.status_code == 200
    assert [b["id"] for b in resp.json()] == [1]


# ---------------------------------------------------- booking-level mutations (FROZEN) --


def test_start_always_returns_410(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _visit(db_session)
    booking = make_booking(db_session, 1, visit.id, created_by=None, status="OPEN")

    resp = client.post(f"/api/bookings/{booking.id}/start", headers=headers)

    assert resp.status_code == 410, resp.text
    assert "assignment-level" in resp.json()["detail"]


def test_attend_always_returns_410(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _visit(db_session)
    booking = make_booking(db_session, 1, visit.id, created_by=None, status="IN_PROGRESS")

    resp = client.post(f"/api/bookings/{booking.id}/attend", json={"remarks": "done"}, headers=headers)

    assert resp.status_code == 410, resp.text


def test_reopen_always_returns_410(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _visit(db_session)
    booking = make_booking(db_session, 1, visit.id, created_by=None, status="ATTENDED")

    resp = client.post(f"/api/bookings/{booking.id}/reopen", json={"reason": "still broken"}, headers=headers)

    assert resp.status_code == 410, resp.text


def test_start_returns_410_even_for_nonexistent_booking(client, db_session, mock_loco_client):
    """FROZEN means FROZEN regardless of whether the target even exists - 410, never 404, so a
    caller never mistakes "gone" for "not found"."""
    headers = _admin_headers(db_session)

    resp = client.post("/api/bookings/9999/start", headers=headers)

    assert resp.status_code == 410


def test_frozen_mutations_never_write_booking_events(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _visit(db_session)
    booking = make_booking(db_session, 1, visit.id, created_by=None, status="OPEN")

    client.post(f"/api/bookings/{booking.id}/start", headers=headers)

    assert db_session.query(models.BookingEvent).filter_by(booking_id=booking.id).count() == 0


def test_frozen_mutations_do_not_affect_booking_status(client, db_session, mock_loco_client):
    """The booking-level route must never write bookings.status directly again - that status is
    derived from booking_section_assignments (see section_dashboard_service.recompute_booking_
    status), and a writable booking-level path would silently create a second, disconnected
    state machine."""
    admin_headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None, status="OPEN")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    # The frozen booking-level route lives on the Supervisor-facing router, so a Supervisor is
    # what proves it still answers 410 (an Admin is simply not accepted there at all now).
    sup_headers = _supervisor_headers(db_session, 70, section_id=1, employee_id="FROZENSUP")
    resp = client.post(f"/api/bookings/{booking.id}/start", headers=sup_headers)
    assert resp.status_code == 410

    # The global pool read remains Admin-only.
    pool_resp = client.get("/api/bookings", headers=admin_headers)
    assert pool_resp.status_code == 200
    body = pool_resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "OPEN"
    assert body[0]["started_at"] is None


# ------------------------------------------------- routed sections + source (2026-09-30) --
#
# The Admin pool must show WHICH SECTION a booking went to and WHERE IT CAME FROM. The section
# is read from booking_section_assignments - the booking's own historical routing record - and
# never from the equipment's CURRENT mapping, which changes.


def test_a_booking_reports_the_section_it_was_routed_to(client, db_session, mock_loco_client):
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_section(db_session, 4, "M4-HR")
    make_booking(db_session, 1, visit.id, created_by=None)
    make_assignment(db_session, 10, 1, 4)

    item = client.get("/api/bookings", headers=headers).json()[0]

    assert [s["section_code"] for s in item["routed_sections"]] == ["M4-HR"]
    assert item["routed_sections"][0]["section_id"] == 4
    assert item["routed_sections"][0]["assignment_source"] == "AUTO_MAPPING"


def test_multi_section_routing_reports_every_section(client, db_session, mock_loco_client):
    """uq_booking_section is unique per (booking, section), not per booking - several sections
    for one booking is a supported shape, not an anomaly, and all of them must be shown."""
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_section(db_session, 4, "M4-HR")
    make_section(db_session, 2, "M2-HR")
    make_booking(db_session, 1, visit.id, created_by=None)
    make_assignment(db_session, 10, 1, 4)
    make_assignment(db_session, 11, 1, 2)

    item = client.get("/api/bookings", headers=headers).json()[0]

    # Ordered by section code, so the same booking renders identically every time.
    assert [s["section_code"] for s in item["routed_sections"]] == ["M2-HR", "M4-HR"]


def test_the_routed_section_is_history_not_the_equipments_current_mapping(
    client, db_session, mock_loco_client
):
    """THE point of reading booking_section_assignments.

    The booking was routed to M4-HR. The equipment is then re-mapped to M2-HR - which is exactly
    what the Admin equipment-mapping page does. The booking must still report M4-HR, because
    that is where the work actually went.
    """
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_section(db_session, 4, "M4-HR")
    make_section(db_session, 2, "M2-HR")
    make_booking(db_session, 1, visit.id, equipment_node_id=1843, created_by=None)
    make_assignment(db_session, 10, 1, 4)

    # The equipment's mapping changes after the fact. Loco Master owns that mapping; nothing
    # about it is copied onto the booking, and nothing here re-reads it.
    mock_loco_client.set_node_sections(1843, ["M2-HR"]) if hasattr(
        mock_loco_client, "set_node_sections"
    ) else None

    item = client.get("/api/bookings", headers=headers).json()[0]

    assert [s["section_code"] for s in item["routed_sections"]] == ["M4-HR"]


def test_a_booking_with_no_assignment_rows_reports_no_routed_sections(
    client, db_session, mock_loco_client
):
    """Reported as empty rather than guessed at. The creation path always writes at least one
    assignment, so this is a data-integrity signal, not a normal state to paper over."""
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_booking(db_session, 1, visit.id, created_by=None)

    item = client.get("/api/bookings", headers=headers).json()[0]

    assert item["routed_sections"] == []


def test_every_canonical_booking_source_round_trips(client, db_session, mock_loco_client):
    """All eight values the CHECK constraint permits, and no invented ones."""
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    sources = [
        "LOG_BOOK", "TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER",
        "SPECIAL_CHECKING", "MANUAL", "TRIP_INSPECTION", "GENERAL_CHECKING",
    ]
    for index, source in enumerate(sources, start=1):
        make_booking(db_session, index, visit.id, created_by=None, booking_source=source)

    items = client.get("/api/bookings", headers=headers).json()

    assert sorted(i["booking_source"] for i in items) == sorted(sources)


def test_the_vestigial_section_columns_are_not_used_for_routing(client, db_session, mock_loco_client):
    """started_by_section_id / attended_by_section_id are written by nothing. The routed section
    must come from the assignment rows, so it is present even though those stay NULL."""
    headers = make_true_admin_headers(db_session)
    visit = _visit(db_session)
    make_section(db_session, 4, "M4-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None)
    make_assignment(db_session, 10, 1, 4)

    assert booking.started_by_section_id is None
    assert booking.attended_by_section_id is None

    item = client.get("/api/bookings", headers=headers).json()[0]

    assert item["started_by_section_code"] is None
    assert [s["section_code"] for s in item["routed_sections"]] == ["M4-HR"]
