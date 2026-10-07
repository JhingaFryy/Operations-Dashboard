"""Phase 3E.1, restored by Business Rule Alignment.

Once a shed_visit is CLOSED, no assignment mutation may occur - the assignment-level API
(section start/attend/reopen) is authoritative again, so its own CLOSED-visit guard
(app.services.booking_service.assert_booking_visit_open(), reused by
section_dashboard_service.start_assignment/attend_assignment/reopen_assignment) is what's under
test here. The booking-level POST /api/bookings/{id}/start|attend|reopen routes are frozen
(always 410) instead - see test_booking_pool.py.

manual section addition (POST /api/bookings/{id}/sections) remains frozen/legacy regardless of
visit status - unaffected by this phase, see app.services.booking_service.add_section().

Reads (booking history/detail, section-dashboard listing) remain available on a CLOSED visit
either way.
"""

from app.db import models
from tests.conftest import (
    make_true_admin_headers,
    ensure_section,
    make_movement_supervisor_headers,
    auth_header,
    grant_access,
    make_assignment,
    make_booking,
    make_defect_type,
    make_section,
    make_shed_visit,
    make_user,
)


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)



def _section_owner_headers(db_session, section_id, user_id=50, employee_id="OWNSUP"):
    """Assignment mutations are section-scoped: only the owning section's Supervisor may act.
    Movement Supervisors get locomotive movement, never other sections' booking work."""
    ensure_section(db_session, section_id, f"M{section_id}-HR")
    existing = db_session.get(models.User, user_id)
    if existing is None:
        make_user(db_session, user_id, employee_id, "Owner Sup", "Supervisor", "hash",
                  section_id=section_id)
        grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)

def _supervisor_headers(db_session, user_id, section_id, employee_id="SUP1", can_add_booking_sections=True):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash", section_id=section_id)
    grant_access(db_session, user_id, is_enabled=True, can_add_booking_sections=can_add_booking_sections)
    return auth_header(employee_id, "Supervisor", user_id)


def _setup(db_session, mock_loco_client, visit_status="IN_SHED", assignment_status="OPEN", id=900):
    if db_session.query(models.Section).filter_by(id=1).first() is None:
        ensure_section(db_session, 1, "M1-HR")
    if db_session.query(models.Section).filter_by(id=2).first() is None:
        ensure_section(db_session, 2, "M2-HR")
    if db_session.query(models.BookingDefectType).filter_by(id=1).first() is None:
        make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, id, f"391{id}", status=visit_status, created_by=None)
    booking = make_booking(db_session, id, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None)
    assignment = make_assignment(db_session, id, booking.id, section_id=1, status=assignment_status)
    return visit, booking, assignment


def _event_count(db_session, booking_id):
    return db_session.query(models.BookingEvent).filter_by(booking_id=booking_id).count()


# --------------------------------------------- legacy mutations: always 410 --


def test_start_assignment_allowed_when_open_blocked_when_closed(client, db_session, mock_loco_client):
    headers = _section_owner_headers(db_session, 1)
    for visit_status, expect_ok in (("IN_SHED", True), ("CLOSED", False)):
        idx = 900 if visit_status == "IN_SHED" else 901
        _visit, booking, assignment = _setup(db_session, mock_loco_client, visit_status=visit_status, id=idx)
        events_before = _event_count(db_session, booking.id)

        resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

        if expect_ok:
            assert resp.status_code == 200, resp.text
            db_session.refresh(assignment)
            assert assignment.status == "IN_PROGRESS"
            assert _event_count(db_session, booking.id) == events_before + 1
        else:
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["code"] == "VISIT_CLOSED"
            db_session.refresh(assignment)
            assert assignment.status == "OPEN"
            assert _event_count(db_session, booking.id) == events_before


def test_attend_assignment_allowed_when_open_blocked_when_closed(client, db_session, mock_loco_client):
    headers = _section_owner_headers(db_session, 1)
    for visit_status, expect_ok in (("IN_SHED", True), ("CLOSED", False)):
        idx = 900 if visit_status == "IN_SHED" else 901
        _visit, _booking, assignment = _setup(
            db_session, mock_loco_client, visit_status=visit_status, assignment_status="IN_PROGRESS", id=idx
        )

        resp = client.post(
            f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "done"}, headers=headers
        )

        if expect_ok:
            assert resp.status_code == 200, resp.text
            db_session.refresh(assignment)
            assert assignment.status == "ATTENDED"
            assert assignment.attended_at is not None
        else:
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["code"] == "VISIT_CLOSED"
            db_session.refresh(assignment)
            assert assignment.status == "IN_PROGRESS"
            assert assignment.attended_at is None


def test_reopen_assignment_allowed_when_open_blocked_when_closed(client, db_session, mock_loco_client):
    headers = _section_owner_headers(db_session, 1)
    for visit_status, expect_ok in (("IN_SHED", True), ("CLOSED", False)):
        idx = 900 if visit_status == "IN_SHED" else 901
        _visit, _booking, assignment = _setup(
            db_session, mock_loco_client, visit_status=visit_status, assignment_status="ATTENDED", id=idx
        )

        resp = client.post(
            f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "needs recheck"}, headers=make_true_admin_headers(db_session)
        )

        if expect_ok:
            assert resp.status_code == 200, resp.text
            db_session.refresh(assignment)
            assert assignment.status == "REOPENED"
        else:
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["code"] == "VISIT_CLOSED"
            db_session.refresh(assignment)
            assert assignment.status == "ATTENDED"


def test_add_section_always_410_regardless_of_visit_status(client, db_session, mock_loco_client):
    # The route is frozen (410) but still behind the add-booking-sections permission, which an
    # ordinary section Supervisor does not hold - grant it so the 410 itself is what is asserted.
    ensure_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(
        db_session, 60, section_id=1, employee_id="ADDSEC", can_add_booking_sections=True
    )
    for i, visit_status in enumerate(("IN_SHED", "CLOSED")):
        _visit, booking, _assignment = _setup(db_session, mock_loco_client, visit_status=visit_status, id=900 + i)
        events_before = _event_count(db_session, booking.id)

        resp = client.post(
            f"/api/bookings/{booking.id}/sections",
            json={"section_id": 2, "reason": "Also needs checking"},
            headers=headers,
        )

        assert resp.status_code == 410, resp.text
        assert (
            db_session.query(models.BookingSectionAssignment)
            .filter_by(booking_id=booking.id, section_id=2)
            .count()
            == 0
        )
        assert _event_count(db_session, booking.id) == events_before


def test_supervisor_add_section_also_always_410(client, db_session, mock_loco_client):
    _visit, booking, _assignment = _setup(db_session, mock_loco_client, visit_status="CLOSED")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(
        f"/api/bookings/{booking.id}/sections",
        json={"section_id": 2, "reason": "Also needs checking"},
        headers=headers,
    )

    assert resp.status_code == 410


# ---------------------------------------------------------------- reads --


def test_closed_visit_booking_history_still_readable(client, db_session, mock_loco_client):
    headers = _section_owner_headers(db_session, 1)
    _visit, booking, _assignment = _setup(db_session, mock_loco_client, visit_status="CLOSED")

    resp = client.get(f"/api/bookings/{booking.id}/history", headers=headers)

    assert resp.status_code == 200


def test_closed_visit_booking_detail_still_readable(client, db_session, mock_loco_client):
    headers = _section_owner_headers(db_session, 1)
    _visit, booking, _assignment = _setup(db_session, mock_loco_client, visit_status="CLOSED")

    resp = client.get(f"/api/bookings/{booking.id}", headers=headers)

    assert resp.status_code == 200


def test_closed_visit_section_dashboard_listing_still_readable(client, db_session, mock_loco_client):
    headers = _section_owner_headers(db_session, 1)
    _visit, _booking, _assignment = _setup(db_session, mock_loco_client, visit_status="CLOSED")

    resp = client.get("/api/sections/M1-HR/assignments", headers=headers)

    assert resp.status_code == 200
    assert len(resp.json()) == 1
