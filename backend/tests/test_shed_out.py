from datetime import datetime, timedelta, timezone

from app.db import models
from tests.conftest import (
    make_checksheet_booking,
    make_movement_supervisor_headers,
    auth_header,
    grant_access,
    make_assignment,
    make_booking,
    make_minor_stages,
    make_non_blocking_checksheet_package,
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


def _minor_visit_with_stages(db_session, visit_id=900, schedule_variant="IA", loco_number="39126", **kwargs):
    visit = make_shed_visit(db_session, visit_id, loco_number, schedule_variant=schedule_variant, created_by=None, **kwargs)
    stages = make_minor_stages(db_session, visit.id, base_id=visit_id * 10)
    # Shed Out now also gates on checksheet requirements. These tests are about the stage and
    # booking gates, so the visit gets a generated snapshot with no blocking rows - the neutral
    # state. ("No package at all" is deliberately NOT neutral: it is its own blocker.) The
    # checksheet gate itself is covered by tests/test_shed_out_checksheet_gate.py.
    make_non_blocking_checksheet_package(db_session, visit.id)
    return visit, stages


def _complete_all_stages(db_session, visit_id):
    stages = db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit_id).all()
    for s in stages:
        if s.stage_type in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
            s.status = "COMPLETED"
    db_session.commit()


def _fully_attended_booking(db_session, visit_id, booking_id, section_id, booking_source="LOG_BOOK"):
    """Business Rule Alignment: the Shed Out gate is assignment-based again - Booking.status is
    kept in sync (by booking_assignment_service.recompute_booking_status on every real
    mutation), but the gate itself always reads the assignment row directly."""
    make_section(db_session, section_id, f"S{section_id}")
    booking = make_booking(
        db_session, booking_id, visit_id, created_by=None, booking_source=booking_source, status="ATTENDED"
    )
    make_assignment(db_session, booking_id, booking.id, section_id=section_id, status="ATTENDED")
    return booking


# --------------------------------------------------------------- eligibility --


def test_eligible_when_all_stages_completed_and_all_attended(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    _fully_attended_booking(db_session, visit.id, 1, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["eligible"] is True
    assert body["stage_blockers"] == []
    assert body["booking_blockers"] == []


def test_zero_bookings_and_completed_stages_is_eligible(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["eligible"] is True


def test_test_before_pending_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _minor_visit_with_stages(db_session)
    stages[1].status = "COMPLETED"
    stages[2].status = "COMPLETED"
    db_session.commit()

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is False
    assert {"stage_type": "TEST_BEFORE", "status": "PENDING"} in body["stage_blockers"]


def test_schedule_inspection_pending_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _minor_visit_with_stages(db_session)
    stages[0].status = "COMPLETED"
    stages[2].status = "COMPLETED"
    db_session.commit()

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is False
    assert {"stage_type": "SCHEDULE_INSPECTION", "status": "PENDING"} in body["stage_blockers"]


def test_test_after_pending_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _minor_visit_with_stages(db_session)
    stages[0].status = "COMPLETED"
    stages[1].status = "COMPLETED"
    db_session.commit()

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is False
    assert {"stage_type": "TEST_AFTER", "status": "PENDING"} in body["stage_blockers"]


def test_missing_required_stage_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = make_shed_visit(db_session, 900, "39126", schedule_variant="IA", created_by=None)
    from tests.conftest import make_stage

    make_stage(db_session, 9001, visit.id, "TEST_BEFORE", 1, status="COMPLETED")
    make_stage(db_session, 9002, visit.id, "SCHEDULE_INSPECTION", 2, status="COMPLETED")
    # TEST_AFTER row deliberately never created.

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is False
    assert {"stage_type": "TEST_AFTER", "status": "MISSING"} in body["stage_blockers"]


def test_legacy_special_checking_does_not_block(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    from tests.conftest import make_minor_stages_with_legacy_special_checking

    visit = make_shed_visit(db_session, 900, "39126", schedule_variant="IA", created_by=None)
    stages = make_minor_stages_with_legacy_special_checking(db_session, visit.id, base_id=9000)
    for s in stages:
        if s.stage_type != "SPECIAL_CHECKING":
            s.status = "COMPLETED"
    # SPECIAL_CHECKING stays PENDING — must not affect eligibility.
    db_session.commit()
    make_non_blocking_checksheet_package(db_session, visit.id)

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is True
    assert body["stage_blockers"] == []


def test_open_booking_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "S1")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="LOG_BOOK", status="OPEN")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is False
    assert body["booking_blockers"][0]["booking_id"] == booking.id
    assert body["booking_blockers"][0]["status"] == "OPEN"


def test_in_progress_booking_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "S1")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="TEST_BEFORE", status="IN_PROGRESS")
    make_assignment(db_session, 1, booking.id, section_id=1, status="IN_PROGRESS")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is False
    assert body["booking_blockers"][0]["status"] == "IN_PROGRESS"


def test_reopened_booking_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "S1")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="SCHEDULE_INSPECTION", status="REOPENED")
    make_assignment(db_session, 1, booking.id, section_id=1, status="REOPENED")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is False
    assert body["booking_blockers"][0]["status"] == "REOPENED"


def test_all_attended_allows(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    _fully_attended_booking(db_session, visit.id, 1, section_id=1, booking_source="TEST_AFTER")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.json()["eligible"] is True


def test_zero_assignment_booking_blocks(client, db_session, mock_loco_client):
    """Business Rule Alignment: a booking with zero booking_section_assignments rows blocks
    Shed Out - booking creation always creates at least one, so zero here means a genuine
    routing gap, not an expected state."""
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="LOG_BOOK", status="ATTENDED")
    # No assignments created at all for this booking.

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    body = resp.json()
    assert body["eligible"] is False
    assert body["booking_blockers"][0]["booking_id"] == booking.id
    assert body["booking_blockers"][0]["status"] == "NO_ASSIGNMENTS"


def test_booking_blockers_include_log_book(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="LOG_BOOK")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.json()["booking_blockers"][0]["booking_source"] == "LOG_BOOK"


def test_booking_blockers_include_test_before(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="TEST_BEFORE")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.json()["booking_blockers"][0]["booking_source"] == "TEST_BEFORE"


def test_booking_blockers_include_schedule_inspection(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="SCHEDULE_INSPECTION")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.json()["booking_blockers"][0]["booking_source"] == "SCHEDULE_INSPECTION"


def test_booking_blockers_include_test_after(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="TEST_AFTER")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.json()["booking_blockers"][0]["booking_source"] == "TEST_AFTER"


def test_eligibility_admin_only(client, db_session, mock_loco_client):
    visit, _stages = _minor_visit_with_stages(db_session)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)

    assert resp.status_code == 403


def test_eligibility_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/shed-visits/9999/shed-out-eligibility", headers=headers)

    assert resp.status_code == 404


# ------------------------------------------------------------------ shed out --


def _shed_out_payload(departed_at_iso, remarks="Loco released after IA schedule"):
    return {"departed_at": departed_at_iso, "remarks": remarks}


def test_shed_out_admin_only(client, db_session, mock_loco_client):
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_all_stages(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload("2026-09-01T12:00:00+00:00"),
        headers=headers,
    )

    assert resp.status_code == 403


def test_shed_out_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/9999/out",
        json=_shed_out_payload("2026-09-01T12:00:00+00:00"),
        headers=headers,
    )

    assert resp.status_code == 404


def test_shed_out_already_closed_409(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(
        db_session, status="CLOSED", arrival_at=now - timedelta(days=1), departed_at=now, departure_source="DASHBOARD"
    )

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload((now + timedelta(hours=1)).isoformat()),
        headers=headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_ALREADY_CLOSED"


def test_shed_out_departure_before_arrival_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(db_session, arrival_at=now)
    _complete_all_stages(db_session, visit.id)

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload((now - timedelta(hours=1)).isoformat()),
        headers=headers,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "DEPARTURE_BEFORE_ARRIVAL"


def test_shed_out_blocked_returns_structured_409(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _minor_visit_with_stages(db_session)
    stages[0].status = "COMPLETED"
    stages[1].status = "COMPLETED"
    # TEST_AFTER stays PENDING.
    db_session.commit()

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
        headers=headers,
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "SHED_OUT_BLOCKED"
    assert {"stage_type": "TEST_AFTER", "status": "PENDING"} in detail["stage_blockers"]


def test_shed_out_eligible_visit_closes(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    _complete_all_stages(db_session, visit.id)
    _fully_attended_booking(db_session, visit.id, 1, section_id=1)

    departed_at = now.isoformat()
    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(departed_at, remarks="all clear"),
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "CLOSED"
    assert body["departure_source"] == "DASHBOARD"

    db_session.refresh(visit)
    assert visit.status == "CLOSED"
    assert visit.departed_at is not None
    assert visit.departure_source == "DASHBOARD"
    assert visit.updated_by == 1


def test_shed_out_creates_shed_out_event(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    _complete_all_stages(db_session, visit.id)
    _fully_attended_booking(db_session, visit.id, 1, section_id=1)

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(now.isoformat(), remarks="all clear"),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    events = db_session.query(models.ShedVisitEvent).filter_by(shed_visit_id=visit.id, event_type="SHED_OUT").all()
    assert len(events) == 1
    event = events[0]
    assert event.source == "DASHBOARD"
    assert event.remarks == "all clear"
    assert event.created_by == 1
    assert event.event_data["schedule_family"] == "MINOR"
    assert event.event_data["schedule_variant"] == "IA"
    assert event.event_data["booking_count"] == 1


def test_shed_out_does_not_mutate_bookings_or_assignments(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    _complete_all_stages(db_session, visit.id)
    booking = _fully_attended_booking(db_session, visit.id, 1, section_id=1)
    assignment = db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id).one()
    booking_status_before = booking.status
    assignment_status_before = assignment.status

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(now.isoformat()),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(booking)
    db_session.refresh(assignment)
    assert booking.status == booking_status_before
    assert assignment.status == assignment_status_before


def test_shed_out_does_not_touch_stage_rows(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    _complete_all_stages(db_session, visit.id)
    snapshot = {s.id: (s.status, s.completed_at) for s in stages}

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(now.isoformat()),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    for s in stages:
        db_session.refresh(s)
        assert (s.status, s.completed_at) == snapshot[s.id]


def test_repeated_shed_out_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    _complete_all_stages(db_session, visit.id)

    first = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(now.isoformat()),
        headers=headers,
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload((now + timedelta(minutes=1)).isoformat()),
        headers=headers,
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "VISIT_ALREADY_CLOSED"


# --------------------------------------------------------- race / transaction --


def test_gate_reevaluated_fresh_inside_transaction(client, db_session, mock_loco_client):
    """A blocking assignment created directly in the DB after any earlier
    read must still be seen by Shed Out — the gate is re-run from scratch
    inside the request, never trusting a prior GET."""
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    _complete_all_stages(db_session, visit.id)

    # Confirm eligible first (simulating a prior GET the client might have made).
    eligible_check = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)
    assert eligible_check.json()["eligible"] is True

    # Simulate a concurrent reopen/new-open-assignment landing after that GET.
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None, booking_source="LOG_BOOK")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(now.isoformat()),
        headers=headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SHED_OUT_BLOCKED"

    db_session.refresh(visit)
    assert visit.status != "CLOSED"


def test_simulated_stage_reopen_before_close_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    _complete_all_stages(db_session, visit.id)

    # Simulate a stage getting reopened/un-completed concurrently.
    ta_stage = next(s for s in stages if s.stage_type == "TEST_AFTER")
    ta_stage.status = "IN_PROGRESS"
    db_session.commit()

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(now.isoformat()),
        headers=headers,
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "SHED_OUT_BLOCKED"
    assert {"stage_type": "TEST_AFTER", "status": "IN_PROGRESS"} in detail["stage_blockers"]


def test_no_partially_closed_visit_after_blocked_attempt(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(hours=6))
    # Stages left PENDING — guaranteed block.

    resp = client.post(
        f"/api/shed-visits/{visit.id}/out",
        json=_shed_out_payload(now.isoformat()),
        headers=headers,
    )

    assert resp.status_code == 409
    db_session.refresh(visit)
    assert visit.status == "IN_SHED"
    assert visit.departed_at is None
    assert visit.departure_source is None


# ---------------------------------------------------------------- closed visit --


def test_closed_visit_blocks_start_test_before(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(
        db_session, status="CLOSED", arrival_at=now - timedelta(days=1), departed_at=now, departure_source="DASHBOARD"
    )

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/start", headers=headers)

    assert resp.status_code == 409


def test_closed_visit_blocks_schedule_inspection_finding_creation(client, db_session, mock_loco_client):
    """Schedule Inspection findings can no longer be created by anyone (booking ownership), so the
    route refuses before the visit's state even matters - open or closed."""
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, stages = _minor_visit_with_stages(db_session, arrival_at=now - timedelta(days=1))
    si_stage = next(s for s in stages if s.stage_type == "SCHEDULE_INSPECTION")
    si_stage.status = "IN_PROGRESS"
    visit.status = "CLOSED"
    visit.departed_at = now
    visit.departure_source = "DASHBOARD"
    db_session.commit()

    resp = client.post(
        f"/api/shed-visits/{visit.id}/schedule-inspection/bookings",
        json={"bookings": [{"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "x"}]},
        headers=headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "BOOKING_ORIGIN_NOT_ALLOWED"
    assert db_session.query(models.Booking).count() == 0


def test_closed_visit_still_readable(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    now = datetime.now(timezone.utc)
    visit, _stages = _minor_visit_with_stages(
        db_session, status="CLOSED", arrival_at=now - timedelta(days=1), departed_at=now, departure_source="DASHBOARD"
    )
    _fully_attended_booking(db_session, visit.id, 1, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "CLOSED"


# ------------------------------------------------------------------ regression --


def test_shed_in_still_works(client, db_session, mock_loco_client):
    make_section(db_session, 1, "M1-HR")
    from tests.conftest import make_defect_type

    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_locomotive("39126", "WAG9HC")
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json={
            "loco_number": "39126",
            "schedule_family": "MINOR",
            "schedule_variant": "IA",
            "arrival_condition": "WORKING",
            "arrival_at": "2026-08-31T09:35:00+05:30",
            "log_book_bookings": [],
        },
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["stages_created"] == 3
