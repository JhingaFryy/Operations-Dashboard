from app.db import models
from tests.conftest import (
    ensure_section,
    make_true_admin_headers,
    make_movement_supervisor_headers,
    set_assignment_status,
    auth_header,
    grant_access,
    make_assignment,
    make_booking,
    make_defect_type,
    make_section,
    make_shed_visit,
    make_user,
)


def _admin_headers(db_session, section_id=1):
    """Historically an Admin operating any section. Under the Supervisor-only policy the actor is
    the entitled Supervisor OF the section these bookings are assigned to."""
    ensure_section(db_session, section_id, f"M{section_id}-HR")
    return _supervisor_headers(db_session, 1, section_id=section_id, employee_id="OWNSUP")


def _supervisor_headers(
    db_session, user_id, section_id, employee_id="SUP1", can_add_booking_sections=False
):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash", section_id=section_id)
    grant_access(db_session, user_id, is_enabled=True, can_add_booking_sections=can_add_booking_sections)
    return auth_header(employee_id, "Supervisor", user_id)


def _setup_booking(db_session, mock_loco_client):
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 900, "39126", created_by=None)
    booking = make_booking(db_session, 900, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None)
    make_assignment(db_session, 900, booking.id, section_id=1, status="OPEN")
    return booking


# ------------------------------------------------- add section (FROZEN) --
#
# Booking Pool Hardening: POST /api/bookings/{id}/sections is retired the same way the
# section-dashboard start/attend/reopen mutations are - it must never create a new
# booking_section_assignments row again, since that would be an alternate, disconnected way to
# put "operational-looking" state on a booking outside booking_pool_service.


def test_add_section_always_returns_410(client, db_session, mock_loco_client):
    ensure_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(
        db_session, 1, section_id=1, employee_id="OWNSUP", can_add_booking_sections=True
    )
    booking = _setup_booking(db_session, mock_loco_client)

    resp = client.post(
        f"/api/bookings/{booking.id}/sections",
        json={"section_id": 2, "reason": "Capacitor inspection also required."},
        headers=headers,
    )

    assert resp.status_code == 410, resp.text
    assert "legacy historical records" in resp.json()["detail"]


def test_add_section_creates_no_new_assignment(client, db_session, mock_loco_client):
    """The frozen add-section route must never become an alternate booking workflow - confirm no
    new booking_section_assignments row is created."""
    headers = _admin_headers(db_session)
    booking = _setup_booking(db_session, mock_loco_client)
    before = db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id).count()

    client.post(
        f"/api/bookings/{booking.id}/sections", json={"section_id": 2, "reason": "x"}, headers=headers
    )

    after = db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id).count()
    assert after == before


def test_add_section_creates_no_booking_event(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    booking = _setup_booking(db_session, mock_loco_client)

    client.post(
        f"/api/bookings/{booking.id}/sections", json={"section_id": 2, "reason": "x"}, headers=headers
    )

    assert db_session.query(models.BookingEvent).filter_by(booking_id=booking.id).count() == 0


def test_add_section_returns_410_even_for_supervisor_with_permission(client, db_session, mock_loco_client):
    """Even a Supervisor who holds can_add_booking_sections gets the same 410 - the permission
    flag no longer unlocks a live mutation, since the route writes nothing for anyone."""
    booking = _setup_booking(db_session, mock_loco_client)
    headers = _supervisor_headers(db_session, 2, section_id=1, can_add_booking_sections=True)

    resp = client.post(
        f"/api/bookings/{booking.id}/sections", json={"section_id": 2, "reason": "x"}, headers=headers
    )

    assert resp.status_code == 410


def test_add_section_without_permission_still_403_before_reaching_deprecation(
    client, db_session, mock_loco_client
):
    """Permission is still checked at the route layer (require_add_booking_sections_permission)
    ahead of the service call, so a Supervisor without the flag gets the pre-existing 403, not
    410 - the deprecation doesn't change who may attempt the call, only what happens once they
    do."""
    booking = _setup_booking(db_session, mock_loco_client)
    headers = _supervisor_headers(db_session, 2, section_id=1, can_add_booking_sections=False)

    resp = client.post(
        f"/api/bookings/{booking.id}/sections", json={"section_id": 2, "reason": "x"}, headers=headers
    )

    assert resp.status_code == 403


# -------------------------------------------------------------- history --


def test_section_supervisor_can_view_booking_history(client, db_session, mock_loco_client):
    """History setup uses the authoritative assignment-level start API (not the frozen
    booking-level route) - that's the only thing that still writes booking_events.

    The actor is the owning section's Supervisor: Operations Dashboard is Supervisor-only, and an
    Admin can no longer reach these operational endpoints at all.
    """
    headers = _admin_headers(db_session)
    booking = _setup_booking(db_session, mock_loco_client)
    client.post("/api/section-assignments/900/start", headers=headers)

    resp = client.get(f"/api/bookings/{booking.id}/history", headers=headers)

    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "STARTED"
    assert events[0]["actor_name"] == "Sup"


def test_supervisor_can_view_history_for_own_section_booking(client, db_session, mock_loco_client):
    booking = _setup_booking(db_session, mock_loco_client)
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/bookings/{booking.id}/history", headers=headers)

    assert resp.status_code == 200


def test_supervisor_can_view_history_for_any_booking(client, db_session, mock_loco_client):
    """Common Booking Pool reform: booking history is visible to any authenticated Dashboard
    user, not gated by an assignment in the Supervisor's own section."""
    booking = _setup_booking(db_session, mock_loco_client)
    headers = _supervisor_headers(db_session, 2, section_id=2)

    resp = client.get(f"/api/bookings/{booking.id}/history", headers=headers)

    assert resp.status_code == 200


def test_history_missing_booking_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/bookings/9999/history", headers=headers)

    assert resp.status_code == 404


# -------------------------------------------------------------- summary --


def test_summary_is_scoped_to_the_callers_own_section(client, db_session, mock_loco_client):
    """The section_id query parameter was REMOVED, not merely ignored - a caller can no longer
    name a section at all, so there is nothing left to tamper with."""
    sec1 = ensure_section(db_session, 1, "M1-HR")
    sec2 = ensure_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    booking = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None, status="OPEN")
    # One OPEN assignment in the caller's section, three in the other section.
    # (booking_id, section_id) is unique, so the other section's work lives on its own bookings.
    make_assignment(db_session, 1, booking.id, section_id=sec1.id, status="OPEN")
    for idx in (2, 3, 4):
        other_booking = make_booking(
            db_session, idx, visit.id, defect_type_id=1, created_by=None, status="OPEN"
        )
        make_assignment(db_session, idx, other_booking.id, section_id=sec2.id, status="OPEN")

    headers = _supervisor_headers(db_session, 5, section_id=sec1.id, employee_id="SUPA")

    own = client.get("/api/bookings/summary", headers=headers)
    assert own.status_code == 200
    assert own.json()["open"] == 1  # only this section's assignment, never section 2's three

    # Naming another section changes nothing: the parameter is not read at all.
    tampered = client.get(f"/api/bookings/summary?section_id={sec2.id}", headers=headers)
    assert tampered.status_code == 200
    assert tampered.json() == own.json()


def test_supervisor_without_a_section_is_refused_a_summary(client, db_session, mock_loco_client):
    headers = _supervisor_headers(db_session, 6, section_id=None, employee_id="NOSEC")

    resp = client.get("/api/bookings/summary", headers=headers)

    assert resp.status_code == 403



def test_supervisor_summary_auto_scoped_to_own_section(client, db_session, mock_loco_client):
    _setup_booking(db_session, mock_loco_client)
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get("/api/bookings/summary", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["open"] == 1


def test_supervisor_summary_ignores_other_section_id_param(client, db_session, mock_loco_client):
    _setup_booking(db_session, mock_loco_client)
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get("/api/bookings/summary?section_id=2", headers=headers)

    assert resp.status_code == 200
    # still scoped to the supervisor's own section (1), not section 2
    assert resp.json()["open"] == 1


def test_supervisor_summary_without_section_id_403(client, db_session, mock_loco_client):
    make_user(db_session, 2, "SUP1", "Sup", "Supervisor", "hash", section_id=None)
    grant_access(db_session, 2, is_enabled=True)
    headers = auth_header("SUP1", "Supervisor", 2)

    resp = client.get("/api/bookings/summary", headers=headers)

    assert resp.status_code == 403


def test_summary_counts_from_assignments_not_booking_status(client, db_session, mock_loco_client):
    """Two sections on one booking: one ATTENDED, one OPEN, while the parent booking.status is
    independently OPEN (booking.status is no longer derived from assignments at all - see
    booking_pool_service.py). The summary must still show the ATTENDED assignment's section
    count correctly rather than deriving anything from booking.status."""
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    booking = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None, status="OPEN")
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    a1 = make_assignment(db_session, 1, booking.id, section_id=1, status="ATTENDED")
    a1.attended_at = now
    make_assignment(db_session, 2, booking.id, section_id=2, status="OPEN")
    db_session.commit()
    assert db_session.get(models.Booking, booking.id).status == "OPEN"

    resp = client.get("/api/bookings/summary?section_id=1", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["attended_today"] == 1


# --------------------------------------------------------- booking detail --


def test_admin_can_view_booking_detail(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    booking = _setup_booking(db_session, mock_loco_client)

    resp = client.get(f"/api/bookings/{booking.id}", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "OPEN"
    assert body["equipment_node_name"] == "Aux Converter"
    assert body["shed_visit"]["loco_number"] == "39126"
    assert len(body["assignments"]) == 1
    assert body["assignments"][0]["section_code"] == "M1-HR"
    assert body["assignments"][0]["assignment_source"] == "AUTO_MAPPING"


def test_booking_detail_add_section_attempt_does_not_add_a_section(client, db_session, mock_loco_client):
    """The frozen add-section route must not silently succeed from the detail view's
    perspective either - detail still shows only the original, pre-existing assignment."""
    headers = _admin_headers(db_session)
    booking = _setup_booking(db_session, mock_loco_client)
    client.post(
        f"/api/bookings/{booking.id}/sections", json={"section_id": 2, "reason": "x"}, headers=headers
    )

    resp = client.get(f"/api/bookings/{booking.id}", headers=headers)

    assert resp.status_code == 200
    codes = {a["section_code"] for a in resp.json()["assignments"]}
    assert codes == {"M1-HR"}


def test_supervisor_can_view_detail_for_any_booking(client, db_session, mock_loco_client):
    """Common Booking Pool reform: booking detail is visible to any authenticated Dashboard
    user, not gated by an assignment in the Supervisor's own section."""
    booking = _setup_booking(db_session, mock_loco_client)
    headers = _supervisor_headers(db_session, 2, section_id=2)

    resp = client.get(f"/api/bookings/{booking.id}", headers=headers)

    assert resp.status_code == 200


def test_booking_detail_missing_booking_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/bookings/9999", headers=headers)

    assert resp.status_code == 404


def test_summary_route_not_shadowed_by_booking_id_route(client, db_session, mock_loco_client):
    """Regression guard for the /summary vs /{booking_id} route-ordering
    trap — /summary must resolve to the summary handler, not a 422 from
    trying to parse "summary" as an integer booking_id."""
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")

    resp = client.get("/api/bookings/summary?section_id=1", headers=headers)

    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"open", "in_progress", "attended_today", "reopened"}
