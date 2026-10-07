from app.db import models
from tests.conftest import (
    make_checksheet_booking,
    make_movement_supervisor_headers,
    set_assignment_status,
    auth_header,
    grant_access,
    make_assignment,
    make_booking,
    make_defect_type,
    make_minor_stages,
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


def _minor_visit_with_stages(db_session, visit_id=900, schedule_variant="IA"):
    visit = make_shed_visit(db_session, visit_id, "39126", schedule_variant=schedule_variant, created_by=None)
    stages = make_minor_stages(db_session, visit.id, base_id=visit_id * 10)
    return visit, stages


def _stage(db_session, visit_id, stage_type):
    return (
        db_session.query(models.ShedVisitStage)
        .filter_by(shed_visit_id=visit_id, stage_type=stage_type)
        .one()
    )


def _complete_test_before(db_session, visit_id, admin_id=1):
    tb = _stage(db_session, visit_id, "TEST_BEFORE")
    tb.status = "COMPLETED"
    db_session.commit()


# --------------------------------------------------------------- start --


def test_cannot_start_before_test_before_complete(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PREVIOUS_STAGE_NOT_COMPLETED"
    assert db_session.get(models.ShedVisitStage, _stage(db_session, visit.id, "SCHEDULE_INSPECTION").id).status == "PENDING"


def test_can_start_after_test_before_complete(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "IN_PROGRESS"
    assert body["started_by"] == 1
    assert body["started_at"] is not None


def test_repeated_start_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "STAGE_NOT_PENDING"


def test_start_admin_only(client, db_session, mock_loco_client):
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    assert resp.status_code == 403


def test_start_requires_authentication(client, db_session, mock_loco_client):
    resp = client.post("/api/shed-visits/1/stages/schedule-inspection/start")
    assert resp.status_code == 401


def test_start_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/9999/stages/schedule-inspection/start", headers=headers)

    assert resp.status_code == 404


def test_start_non_minor_visit_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = make_shed_visit(db_session, 1, "39126", schedule_family=None, schedule_variant=None, created_by=None)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "WORKFLOW_NOT_APPLICABLE"


def test_start_when_test_before_still_in_progress_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    tb = _stage(db_session, visit.id, "TEST_BEFORE")
    tb.status = "IN_PROGRESS"
    db_session.commit()

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PREVIOUS_STAGE_NOT_COMPLETED"


# -------------------------------------------------------- create findings --


def _si_setup(db_session, mock_loco_client):
    visit, stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_defect_type(db_session, 2, "BROKEN")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    mock_loco_client.set_mapping(1843, ["M1-HR", "M2-HR"])
    return visit, stages


def _start_si(client, headers, visit_id):
    resp = client.post(f"/api/shed-visits/{visit_id}/stages/schedule-inspection/start", headers=headers)
    assert resp.status_code == 200, resp.text


def _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client):
    """Phase 5B.3: completion is now evidence-driven, so any test exercising a stage-completion
    path needs a real work package + BL-DCMS applicability behind it."""
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage_type in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage_type)
    # These tests model a FULLY configured Minor schedule: BL-DCMS declares every required Inspection
    # section configured (migration 010). Partial configuration is covered in test_minor_inspection_package.py.
    mock_bldcms_client.minor_inspection_configuration_complete = True
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _approve_stage_checksheets(mock_bldcms_client, visit, package_body, stage_type, start_checksheet_id=1000):
    stage_body = next(s for s in package_body["stages"] if s["workflow_stage_type"] == stage_type)
    for i, req in enumerate(stage_body["requirements"]):
        if not req["is_required"]:
            continue
        mock_bldcms_client.add_checksheet(
            visit.id,
            checksheet_id=start_checksheet_id + i,
            template_id=req["template_id"],
            workflow_stage_type=stage_type,
            status="APPROVED",
            is_final=True,
        )


def test_dashboard_cannot_create_schedule_inspection_findings(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _si_setup(db_session, mock_loco_client)
    _start_si(client, headers, visit.id)

    resp = client.post(
        f"/api/shed-visits/{visit.id}/schedule-inspection/bookings",
        json={"bookings": [{"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "x"}]},
        headers=headers,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "BOOKING_ORIGIN_NOT_ALLOWED"
    assert db_session.query(models.Booking).count() == 0


def test_list_si_findings_only_returns_schedule_inspection(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _si_setup(db_session, mock_loco_client)
    _start_si(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "SCHEDULE_INSPECTION", equipment_node_id=1843, defect_type_id=1, remarks='si finding')
    # A Log Book booking and a Test Before booking on the same visit must
    # never appear in this list (workflow separation, requirement 9).
    make_booking(db_session, 9999, visit.id, defect_type_id=1, created_by=None, booking_source="LOG_BOOK")
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    tb_stage = _stage(db_session, visit.id, "TEST_BEFORE")
    make_booking(
        db_session, 9998, visit.id, defect_type_id=1, created_by=None, booking_source="TEST_BEFORE", stage_id=tb_stage.id
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/schedule-inspection/bookings", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["description"] == "si finding"
    # 1843 is mapped to both M1-HR and M2-HR - one assignment per resolved section.
    assert len(items[0]["assignments"]) == 2
    assert items[0]["equipment_node_name"] == "Aux Converter"
    for booking_id in (9999, 9998):
        assert booking_id not in [i["id"] for i in items]
    assert si_stage.id is not None  # sanity: fixture wired up correctly


def test_list_si_findings_empty_when_none_created(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _si_setup(db_session, mock_loco_client)
    _start_si(client, headers, visit.id)

    resp = client.get(f"/api/shed-visits/{visit.id}/schedule-inspection/bookings", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_si_findings_reflects_booking_status_directly(client, db_session, mock_loco_client):
    """Common Booking Pool reform: booking.status itself is authoritative and reflected in the
    list - no more per-section assignment statuses."""
    headers = _admin_headers(db_session)
    visit, _stages = _si_setup(db_session, mock_loco_client)
    _start_si(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "SCHEDULE_INSPECTION", equipment_node_id=1843, defect_type_id=1, remarks='x')
    booking = db_session.query(models.Booking).filter_by(booking_source="SCHEDULE_INSPECTION").one()
    booking.status = "IN_PROGRESS"
    db_session.commit()

    resp = client.get(f"/api/shed-visits/{visit.id}/schedule-inspection/bookings", headers=headers)

    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "IN_PROGRESS"


# ------------------------------------------------------------ completion --


def test_zero_booking_completion_allowed(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    # Phase 5B.3: completion is system/evidence-attributed, never the triggering Admin.
    assert body["completed_by"] is None
    assert body["completed_at"] is not None


def test_complete_pending_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "WORK_PACKAGE_MISSING"


def test_complete_admin_only(client, db_session, mock_loco_client):
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    admin_headers = _admin_headers(db_session)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=admin_headers)
    make_section(db_session, 1, "M1-HR")
    sup_headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=sup_headers)

    assert resp.status_code == 403


def test_complete_blocked_by_open_booking(client, db_session, mock_loco_client, mock_bldcms_client):
    """Seeds a SCHEDULE_INSPECTION-stage booking directly rather than through the findings API,
    purely to keep this completion-gate test isolated from booking creation (which has its own
    dedicated tests below). booking_source is deliberately SCHEDULE_INSPECTION - the exact
    booking-source-to-stage mapping (Phase 5B.3), not stage_id/MANUAL."""
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    booking = make_booking(
        db_session, 1, visit.id, created_by=None, stage_id=si_stage.id, booking_source="SCHEDULE_INSPECTION"
    )
    # booking.status stays OPEN (default)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "BOOKINGS_PENDING"
    assert detail["checksheets_ready"] is True
    assert booking.id is not None  # sanity: fixture wired up correctly


def test_complete_blocked_by_in_progress_booking(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    booking = make_booking(
        db_session, 1, visit.id, created_by=None, stage_id=si_stage.id, booking_source="SCHEDULE_INSPECTION"
    )
    booking.status = "IN_PROGRESS"
    db_session.commit()
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "BOOKINGS_PENDING"


def test_complete_blocked_by_reopened_booking(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    booking = make_booking(
        db_session, 1, visit.id, created_by=None, stage_id=si_stage.id, booking_source="SCHEDULE_INSPECTION"
    )
    booking.status = "REOPENED"
    db_session.commit()
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "BOOKINGS_PENDING"


def test_manual_source_booking_does_not_block_schedule_inspection(client, db_session, mock_loco_client, mock_bldcms_client):
    """Phase 5B.3: a MANUAL-source booking must never determine SCHEDULE_INSPECTION's own
    completion - only booking_source == SCHEDULE_INSPECTION bookings do."""
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    make_booking(db_session, 1, visit.id, created_by=None, stage_id=si_stage.id, booking_source="MANUAL")
    # MANUAL booking stays OPEN (default) - must not block.
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_complete_allowed_when_all_attended(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(
        db_session, 1, visit.id, created_by=None, stage_id=si_stage.id, booking_source="SCHEDULE_INSPECTION"
    )
    # Business Rule Alignment: the gate is assignment-based - a booking with zero assignments
    # blocks regardless of booking.status, so create and attend a real assignment.
    make_assignment(db_session, 1, booking.id, section_id=1, status="ATTENDED")
    booking.status = "ATTENDED"
    db_session.commit()
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_complete_sets_next_stage_pending(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)

    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    test_after = _stage(db_session, visit.id, "TEST_AFTER")
    assert test_after.status == "PENDING"


def test_repeated_completion_rejected_409(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")
    first = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)
    assert first.status_code == 200, first.text

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "ALREADY_COMPLETED"


def test_complete_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/9999/stages/schedule-inspection/complete", headers=headers)

    assert resp.status_code == 404


def test_complete_row_lock_recheck_uses_fresh_state(client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch):
    """Regression guard for the row-lock/recheck pattern: complete() must
    re-query booking status from the DB inside its own transaction, not
    trust anything computed earlier — simulate a status flip happening
    between the initial fetch and the completion call."""
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/start", headers=headers)
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(
        db_session, 1, visit.id, created_by=None, stage_id=si_stage.id, booking_source="SCHEDULE_INSPECTION"
    )
    assignment = make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    # First attempt: correctly blocked (assignment is OPEN).
    resp1 = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)
    assert resp1.status_code == 409

    # Mutate directly in the DB (simulating a concurrent Section Dashboard attend action) and
    # confirm the next completion call sees it fresh.
    set_assignment_status(db_session, assignment, "ATTENDED")
    booking.status = "ATTENDED"
    db_session.commit()

    resp2 = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status"] == "COMPLETED"


def test_complete_blocked_then_allowed_via_real_findings_api(client, db_session, mock_loco_client, mock_bldcms_client):
    """End-to-end: create a real finding through the findings API, confirm it blocks
    completion, attend the booking, confirm completion then succeeds and TEST_AFTER stays
    PENDING."""
    headers = _admin_headers(db_session)
    visit, _stages = _si_setup(db_session, mock_loco_client)
    _start_si(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "SCHEDULE_INSPECTION", equipment_node_id=1843, defect_type_id=1, remarks='x')
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "SCHEDULE_INSPECTION")

    blocked = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)
    assert blocked.status_code == 409
    blocked_detail = blocked.json()["detail"]
    assert blocked_detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert blocked_detail["reason"] == "BOOKINGS_PENDING"

    for a in db_session.query(models.BookingSectionAssignment).all():
        set_assignment_status(db_session, a, "ATTENDED")
    for b in db_session.query(models.Booking).filter_by(booking_source="SCHEDULE_INSPECTION").all():
        b.status = "ATTENDED"
    db_session.commit()

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/schedule-inspection/complete", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"

    test_after = _stage(db_session, visit.id, "TEST_AFTER")
    assert test_after.status == "PENDING"


# ------------------------------------------------------------ regression --


def test_workflow_shows_schedule_inspection_stage(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 200
    stage_types = [s["stage_type"] for s in resp.json()["stages"]]
    assert "SCHEDULE_INSPECTION" in stage_types
