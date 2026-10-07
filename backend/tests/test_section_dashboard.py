from app.db import models
from tests.conftest import (
    make_movement_supervisor_headers,
    auth_header,
    grant_access,
    make_assignment,
    make_booking,
    make_defect_type,
    ensure_section,
    make_section,
    make_shed_visit,
    make_user,
)


def _admin_headers(db_session, section_id=1):
    """Historically an Admin operating ANY section. Under the Supervisor-only policy that is
    exactly what is forbidden, so this is now the entitled Supervisor OF the section these tests
    operate on (section 1 / M1-HR by default) - which is the identity the endpoints are designed
    for. Cross-section refusal is covered separately in test_operations_authorization.py."""
    ensure_section(db_session, section_id, f"M{section_id}-HR")
    return _supervisor_headers(db_session, 1, section_id=section_id, employee_id="OWNSUP")


def _supervisor_headers(db_session, user_id, section_id, employee_id="SUP1", grant=True):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash", section_id=section_id)
    if grant:
        grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)


def _true_admin_headers(db_session, user_id=99, employee_id="TRUEADM"):
    """Reopen of an ATTENDED assignment stays Admin-only under the new policy - Supervisors,
    including movement Supervisors, never inherit it. These tests therefore need a genuine Admin,
    which is now an explicit, separate technical path rather than the default test identity."""
    make_user(db_session, user_id, employee_id, "Admin", "Admin", "hash")
    return auth_header(employee_id, "Admin", user_id)


def _setup_one_assignment(db_session, mock_loco_client, status="OPEN", section_id=1, id=900):
    if db_session.query(models.Section).filter_by(id=section_id).first() is None:
        make_section(db_session, section_id, f"M{section_id}-HR")
    if db_session.query(models.BookingDefectType).count() == 0:
        make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    loco_number = "39126" if id == 900 else f"391{id}"
    visit = make_shed_visit(db_session, id, loco_number, created_by=None)
    booking = make_booking(db_session, id, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None)
    assignment = make_assignment(db_session, id, booking.id, section_id=section_id, status=status)
    return assignment


# ------------------------------------------------------------- listing --


def test_list_assignments_for_section(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    _setup_one_assignment(db_session, mock_loco_client)

    resp = client.get("/api/sections/M1-HR/assignments", headers=headers)

    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "OPEN"
    assert item["section_code"] == "M1-HR"
    assert item["booking"]["status"] == "OPEN"  # parent booking status, shown separately from assignment status
    assert item["booking"]["equipment_node_name"] == "Aux Converter"
    assert item["booking"]["defect_type"]["code"] == "DEFECTIVE"
    assert item["booking"]["shed_visit"]["loco_number"] == "39126"


def test_list_assignments_shows_schedule_inspection_source(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 900, "39126", created_by=None)
    booking = make_booking(
        db_session, 900, visit.id, defect_type_id=1, created_by=None, booking_source="SCHEDULE_INSPECTION"
    )
    make_assignment(db_session, 900, booking.id, section_id=1, status="OPEN")

    resp = client.get("/api/sections/M1-HR/assignments", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["booking"]["booking_source"] == "SCHEDULE_INSPECTION"


def test_list_assignments_shows_test_after_source(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 901, "39127", created_by=None)
    booking = make_booking(
        db_session, 901, visit.id, defect_type_id=1, created_by=None, booking_source="TEST_AFTER"
    )
    make_assignment(db_session, 901, booking.id, section_id=1, status="OPEN")

    resp = client.get("/api/sections/M1-HR/assignments", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["booking"]["booking_source"] == "TEST_AFTER"


def test_list_assignments_filters_by_status(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    b1 = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None)
    b2 = make_booking(db_session, 2, visit.id, defect_type_id=1, created_by=None)
    make_assignment(db_session, 1, b1.id, section_id=1, status="OPEN")
    make_assignment(db_session, 2, b2.id, section_id=1, status="ATTENDED")

    resp = client.get("/api/sections/M1-HR/assignments?status=OPEN", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["status"] == "OPEN"


def test_unknown_section_code_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/sections/NOPE/assignments", headers=headers)

    assert resp.status_code == 404


def test_equipment_name_missing_when_loco_master_unavailable(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    _setup_one_assignment(db_session, mock_loco_client)
    mock_loco_client.unavailable = True

    resp = client.get("/api/sections/M1-HR/assignments", headers=headers)

    assert resp.status_code == 200
    assert resp.json()[0]["booking"]["equipment_node_name"] is None


# --------------------------------------------------- assignment mutations (authoritative) --
#
# Business Rule Alignment: start/attend/reopen on a section-assignment are the sole
# authoritative booking lifecycle operations again -
#   OPEN/REOPENED -> IN_PROGRESS (start), IN_PROGRESS -> ATTENDED (attend, remarks),
#   ATTENDED -> REOPENED (reopen, Admin only). Direct OPEN/REOPENED -> ATTENDED remains
# forbidden. Every mutation writes a booking_events row and recomputes parent Booking.status.


def test_start_open_assignment_succeeds(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN")

    resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "IN_PROGRESS"
    assert body["started_at"] is not None


def test_start_reopened_assignment_succeeds(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="REOPENED")

    resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "IN_PROGRESS"


def test_open_to_attended_directly_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN")

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "done"}, headers=headers
    )

    assert resp.status_code == 409


def test_reopened_to_attended_directly_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="REOPENED")

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "done"}, headers=headers
    )

    assert resp.status_code == 409


def test_attend_in_progress_assignment_succeeds(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="IN_PROGRESS")

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "fixed"}, headers=headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ATTENDED"
    assert body["attendance_remarks"] == "fixed"


def test_attend_blank_remarks_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="IN_PROGRESS")

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "   "}, headers=headers
    )

    assert resp.status_code == 422


def test_admin_reopen_works(client, db_session, mock_loco_client):
    headers = _true_admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="ATTENDED")

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "still broken"}, headers=headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REOPENED"


def test_supervisor_reopen_rejected(client, db_session, mock_loco_client):
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="ATTENDED", section_id=1)
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "still broken"}, headers=headers
    )

    assert resp.status_code == 403


def test_reopen_non_attended_rejected(client, db_session, mock_loco_client):
    headers = _true_admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN")

    resp = client.post(f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "x"}, headers=headers)

    assert resp.status_code == 409


def test_reopen_blank_reason_rejected(client, db_session, mock_loco_client):
    headers = _true_admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="ATTENDED")

    resp = client.post(f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "  "}, headers=headers)

    assert resp.status_code == 422


def test_start_missing_assignment_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/section-assignments/9999/start", headers=headers)

    assert resp.status_code == 404


def test_double_start_second_request_gets_controlled_conflict(client, db_session, mock_loco_client):
    """First successful transition wins; a second start attempt on the now-IN_PROGRESS
    assignment must receive a controlled 409, never a silent double-start - exercises the same
    row-lock/recheck code path a true concurrent second request would hit."""
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN")

    first = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)
    second = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409


def test_start_on_closed_visit_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN")
    booking = db_session.get(models.Booking, assignment.booking_id)
    visit = db_session.get(models.ShedVisit, booking.shed_visit_id)
    visit.status = "CLOSED"
    db_session.commit()

    resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_CLOSED"


def test_attend_on_closed_visit_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="IN_PROGRESS")
    booking = db_session.get(models.Booking, assignment.booking_id)
    visit = db_session.get(models.ShedVisit, booking.shed_visit_id)
    visit.status = "CLOSED"
    db_session.commit()

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "x"}, headers=headers
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_CLOSED"


def test_reopen_on_closed_visit_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="ATTENDED")
    booking = db_session.get(models.Booking, assignment.booking_id)
    visit = db_session.get(models.ShedVisit, booking.shed_visit_id)
    visit.status = "CLOSED"
    db_session.commit()

    resp = client.post(f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "x"}, headers=_true_admin_headers(db_session))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_CLOSED"


def test_starter_user_recorded(client, db_session, mock_loco_client):
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN", section_id=9)
    headers = _supervisor_headers(db_session, 4, section_id=9, employee_id="SUPM1")

    resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["started_by_name"] == "Sup"

    db_session.refresh(assignment)
    assert assignment.started_by == 4
    assert assignment.started_at is not None


def test_attendee_user_and_remarks_recorded(client, db_session, mock_loco_client):
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="IN_PROGRESS", section_id=9)
    headers = _supervisor_headers(db_session, 4, section_id=9, employee_id="SUPM1")

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "Replaced part."}, headers=headers
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attended_by_name"] == "Sup"
    assert body["attendance_remarks"] == "Replaced part."

    db_session.refresh(assignment)
    assert assignment.attended_by == 4
    assert assignment.attendance_remarks == "Replaced part."


def test_booking_events_written_for_each_mutation(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN")

    client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)
    client.post(f"/api/section-assignments/{assignment.id}/attend", json={"remarks": "done"}, headers=headers)
    client.post(f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "recurred"}, headers=_true_admin_headers(db_session))

    event_types = [
        e.event_type
        for e in db_session.query(models.BookingEvent)
        .filter_by(booking_id=assignment.booking_id)
        .order_by(models.BookingEvent.id)
        .all()
    ]
    assert event_types == ["STARTED", "ATTENDED", "REOPENED"]


def test_reopen_preserves_prior_attendance_remarks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="ATTENDED")
    assignment.attendance_remarks = "Replaced part."
    db_session.commit()

    resp = client.post(
        f"/api/section-assignments/{assignment.id}/reopen", json={"reason": "recurred"}, headers=_true_admin_headers(db_session)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["attendance_remarks"] == "Replaced part."


def test_start_returns_404_for_nonexistent_assignment(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/section-assignments/9999/start", headers=headers)

    assert resp.status_code == 404


def test_supervisor_gets_403_on_another_sections_assignment_start(client, db_session, mock_loco_client):
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN", section_id=1)
    ensure_section(db_session, 2, "M2-HR")
    headers = _supervisor_headers(db_session, 2, section_id=2)

    resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert resp.status_code == 403


def test_supervisor_can_start_own_sections_assignment(client, db_session, mock_loco_client):
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN", section_id=1)
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert resp.status_code == 200, resp.text


# ------------------------------------------------------- authorization --


def test_list_requires_authentication(client, db_session, mock_loco_client):
    resp = client.get("/api/sections/M1-HR/assignments")
    assert resp.status_code == 401


def test_transition_requires_authentication(client, db_session, mock_loco_client):
    resp = client.post("/api/section-assignments/1/start")
    assert resp.status_code == 401


def test_a_supervisor_without_dashboard_access_reaches_their_section(client, db_session, mock_loco_client):
    """Base access is by role. Section SCOPE is unchanged and still enforced by the service -
    this Supervisor reaches the endpoint, and would be refused another section's data."""
    ensure_section(db_session, 1, "M1-HR")
    make_user(db_session, 2, "SUP1", "Sup One", "Supervisor", "hash", section_id=1)
    headers = auth_header("SUP1", "Supervisor", 2)

    resp = client.get("/api/sections/M1-HR/assignments", headers=headers)
    assert resp.status_code == 200, resp.text


def test_admin_may_view_every_section(client, db_session, mock_loco_client):
    """The Superadmin has cross-section operational visibility - authorised by role alone, with
    no dashboard_access row required."""
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    headers = _true_admin_headers(db_session)

    assert client.get("/api/sections/M1-HR/assignments", headers=headers).status_code == 200
    assert client.get("/api/sections/M2-HR/assignments", headers=headers).status_code == 200


def test_supervisor_cannot_view_another_section(client, db_session, mock_loco_client):
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    headers = _supervisor_headers(db_session, 5, section_id=1, employee_id="SUPA")

    assert client.get("/api/sections/M1-HR/assignments", headers=headers).status_code == 200
    other = client.get("/api/sections/M2-HR/assignments", headers=headers)
    assert other.status_code == 403
    assert other.json() == {"detail": "You may only operate your own section's assignments."}


def test_admin_may_mutate_any_sections_assignment(client, db_session, mock_loco_client):
    """Cross-section operational control, which a Supervisor never gets (see the test above it)."""
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN", section_id=1)
    headers = _true_admin_headers(db_session)

    resp = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(models.BookingSectionAssignment, assignment.id).status == "IN_PROGRESS"



def test_supervisor_can_view_own_section(client, db_session, mock_loco_client):
    _setup_one_assignment(db_session, mock_loco_client, section_id=1)
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get("/api/sections/M1-HR/assignments", headers=headers)

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_supervisor_cannot_view_another_section(client, db_session, mock_loco_client):
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get("/api/sections/M2-HR/assignments", headers=headers)

    assert resp.status_code == 403


def test_supervisor_without_section_id_gets_403_on_both_list_and_start(client, db_session, mock_loco_client):
    assignment = _setup_one_assignment(db_session, mock_loco_client, status="OPEN", section_id=1)
    headers = _supervisor_headers(db_session, 2, section_id=None)

    resp_list = client.get("/api/sections/M1-HR/assignments", headers=headers)
    assert resp_list.status_code == 403

    resp_start = client.post(f"/api/section-assignments/{assignment.id}/start", headers=headers)
    assert resp_start.status_code == 403


# ------------------------------------------------ parent booking status (recompute_booking_status) --


def test_starting_one_of_two_assignments_moves_booking_to_in_progress(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    booking = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None)
    a1 = make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")
    make_assignment(db_session, 2, booking.id, section_id=2, status="OPEN")

    client.post(f"/api/section-assignments/{a1.id}/start", headers=headers)

    assert db_session.get(models.Booking, booking.id).status == "IN_PROGRESS"


def test_booking_status_some_attended_some_open_stays_open(client, db_session, mock_loco_client):
    """all ATTENDED is required for the parent to read ATTENDED - one lagging OPEN assignment
    keeps the booking at OPEN (no IN_PROGRESS assignment exists here to bump it to IN_PROGRESS)."""
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    booking = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None)
    a1 = make_assignment(db_session, 1, booking.id, section_id=1, status="IN_PROGRESS")
    make_assignment(db_session, 2, booking.id, section_id=2, status="OPEN")

    client.post(f"/api/section-assignments/{a1.id}/attend", json={"remarks": "done"}, headers=headers)

    assert db_session.get(models.Booking, booking.id).status == "OPEN"


def test_booking_status_all_attended_when_last_assignment_attended(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    booking = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None)
    make_assignment(db_session, 1, booking.id, section_id=1, status="ATTENDED")
    a2 = make_assignment(db_session, 2, booking.id, section_id=2, status="IN_PROGRESS")

    # a2 belongs to section 2, so only section 2's Supervisor may attend it - cross-section
    # mutation is exactly what the Supervisor-only policy forbids.
    sec2 = _supervisor_headers(db_session, 7, section_id=2, employee_id="SUP2")
    client.post(f"/api/section-assignments/{a2.id}/attend", json={"remarks": "done"}, headers=sec2)

    assert db_session.get(models.Booking, booking.id).status == "ATTENDED"


def test_booking_status_reopened_wins_over_attended(client, db_session, mock_loco_client):
    """any REOPENED wins outright, even with every other assignment ATTENDED."""
    headers = _admin_headers(db_session)
    ensure_section(db_session, 1, "M1-HR")
    ensure_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    booking = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None)
    a1 = make_assignment(db_session, 1, booking.id, section_id=1, status="ATTENDED")
    make_assignment(db_session, 2, booking.id, section_id=2, status="ATTENDED")

    client.post(
        f"/api/section-assignments/{a1.id}/reopen", json={"reason": "still broken"}, headers=_true_admin_headers(db_session)
    )

    assert db_session.get(models.Booking, booking.id).status == "REOPENED"
