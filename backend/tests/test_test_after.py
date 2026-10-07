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


def _minor_visit_with_stages(db_session, visit_id=900, schedule_variant="IA", loco_number="39126"):
    visit = make_shed_visit(db_session, visit_id, loco_number, schedule_variant=schedule_variant, created_by=None)
    stages = make_minor_stages(db_session, visit.id, base_id=visit_id * 10)
    return visit, stages


def _stage(db_session, visit_id, stage_type):
    return (
        db_session.query(models.ShedVisitStage)
        .filter_by(shed_visit_id=visit_id, stage_type=stage_type)
        .one()
    )


def _complete_test_before(db_session, visit_id):
    tb = _stage(db_session, visit_id, "TEST_BEFORE")
    tb.status = "COMPLETED"
    db_session.commit()


def _complete_schedule_inspection(db_session, visit_id):
    si = _stage(db_session, visit_id, "SCHEDULE_INSPECTION")
    si.status = "COMPLETED"
    db_session.commit()


def _prereqs_done(db_session, visit_id):
    _complete_test_before(db_session, visit_id)
    _complete_schedule_inspection(db_session, visit_id)


# --------------------------------------------------------------- start --


def test_cannot_start_before_schedule_inspection_completed(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _complete_test_before(db_session, visit.id)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PREVIOUS_STAGE_NOT_COMPLETED"
    assert _stage(db_session, visit.id, "TEST_AFTER").status == "PENDING"


def test_cannot_start_before_test_before_completed(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PREVIOUS_STAGE_NOT_COMPLETED"


def test_can_start_after_prerequisites_completed(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _prereqs_done(db_session, visit.id)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/start", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "IN_PROGRESS"
    assert body["started_by"] == 1
    assert body["started_at"] is not None


def test_repeated_start_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    _prereqs_done(db_session, visit.id)
    client.post(f"/api/shed-visits/{visit.id}/stages/test-after/start", headers=headers)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "STAGE_NOT_PENDING"


def test_start_admin_only(client, db_session, mock_loco_client):
    visit, _stages = _minor_visit_with_stages(db_session)
    _prereqs_done(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/start", headers=headers)

    assert resp.status_code == 403


def test_start_non_minor_visit_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = make_shed_visit(db_session, 1, "39126", schedule_family=None, schedule_variant=None, created_by=None)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "WORKFLOW_NOT_APPLICABLE"


def test_start_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/9999/stages/test-after/start", headers=headers)

    assert resp.status_code == 404


# -------------------------------------------------------- create findings --


def _ta_setup(db_session, mock_loco_client):
    visit, stages = _minor_visit_with_stages(db_session)
    _prereqs_done(db_session, visit.id)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_defect_type(db_session, 2, "BROKEN")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    mock_loco_client.set_mapping(1843, ["M1-HR", "M2-HR"])
    return visit, stages


def _start_ta(client, headers, visit_id):
    resp = client.post(f"/api/shed-visits/{visit_id}/stages/test-after/start", headers=headers)
    assert resp.status_code == 200, resp.text


def _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client):
    """Phase 5B.3: completion is now evidence-driven, so any test exercising a stage-completion
    path needs a real work package + BL-DCMS applicability behind it."""
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage_type in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage_type)
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


def test_list_ta_findings_only_returns_test_after(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_AFTER", equipment_node_id=1843, defect_type_id=1, remarks='ta finding')
    make_booking(db_session, 9999, visit.id, defect_type_id=1, created_by=None, booking_source="LOG_BOOK")

    resp = client.get(f"/api/shed-visits/{visit.id}/test-after/bookings", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["description"] == "ta finding"
    # 1843 is mapped to both M1-HR and M2-HR - one assignment per resolved section.
    assert len(items[0]["assignments"]) == 2


def test_list_ta_findings_empty_when_none_created(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)

    resp = client.get(f"/api/shed-visits/{visit.id}/test-after/bookings", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == []


# ------------------------------------------------------- reference bookings --


def test_reference_bookings_returns_only_test_before(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _ta_setup(db_session, mock_loco_client)
    tb_stage = _stage(db_session, visit.id, "TEST_BEFORE")
    si_stage = _stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    make_booking(
        db_session, 100, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="TEST_BEFORE", stage_id=tb_stage.id,
    )
    make_booking(
        db_session, 101, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="SCHEDULE_INSPECTION", stage_id=si_stage.id,
    )
    make_booking(
        db_session, 102, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="LOG_BOOK",
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/test-after/reference-bookings", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == 100


def test_reference_bookings_excludes_test_after_source(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_AFTER", equipment_node_id=1843, defect_type_id=1, remarks='x')
    tb_stage = _stage(db_session, visit.id, "TEST_BEFORE")
    make_booking(
        db_session, 200, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="TEST_BEFORE", stage_id=tb_stage.id,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/test-after/reference-bookings", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert [i["id"] for i in items] == [200]


def test_reference_bookings_only_same_shed_visit(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    tb_stage = _stage(db_session, visit.id, "TEST_BEFORE")
    make_booking(
        db_session, 300, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="TEST_BEFORE", stage_id=tb_stage.id,
    )

    other_visit, other_stages = _minor_visit_with_stages(db_session, visit_id=901, loco_number="40007")
    other_tb_stage = _stage(db_session, other_visit.id, "TEST_BEFORE")
    make_booking(
        db_session, 301, other_visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="TEST_BEFORE", stage_id=other_tb_stage.id,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/test-after/reference-bookings", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert [i["id"] for i in items] == [300]


def test_reference_bookings_includes_section_statuses(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    tb_stage = _stage(db_session, visit.id, "TEST_BEFORE")
    booking = make_booking(
        db_session, 400, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="TEST_BEFORE", stage_id=tb_stage.id,
    )
    make_assignment(db_session, 1, booking.id, section_id=1, status="ATTENDED")
    make_assignment(db_session, 2, booking.id, section_id=2, status="IN_PROGRESS")

    resp = client.get(f"/api/shed-visits/{visit.id}/test-after/reference-bookings", headers=headers)

    assert resp.status_code == 200
    statuses = {a["section_code"]: a["status"] for a in resp.json()[0]["assignments"]}
    assert statuses == {"M1-HR": "ATTENDED", "M2-HR": "IN_PROGRESS"}
    assert resp.json()[0]["equipment_node_name"] == "Aux Converter"


def test_reference_bookings_do_not_mutate_on_read(client, db_session, mock_loco_client):
    """Reading the reference list must never write anything — no reopen,
    no verification record, no linked booking."""
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    tb_stage = _stage(db_session, visit.id, "TEST_BEFORE")
    booking = make_booking(
        db_session, 500, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="TEST_BEFORE", stage_id=tb_stage.id,
    )
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")
    before_booking_count = db_session.query(models.Booking).count()
    before_assignment_count = db_session.query(models.BookingSectionAssignment).count()

    client.get(f"/api/shed-visits/{visit.id}/test-after/reference-bookings", headers=headers)
    client.get(f"/api/shed-visits/{visit.id}/test-after/reference-bookings", headers=headers)

    assert db_session.query(models.Booking).count() == before_booking_count
    assert db_session.query(models.BookingSectionAssignment).count() == before_assignment_count
    refreshed = db_session.get(models.BookingSectionAssignment, 1)
    assert refreshed.status == "OPEN"


# ------------------------------------------------------------ completion --


def test_zero_finding_completion_allowed(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["completed_by"] is None
    assert body["completed_at"] is not None


def test_complete_pending_rejected(client, db_session, mock_loco_client):
    """Phase 5B.3: completion is evidence-driven now - a PENDING stage with no work package at
    all is blocked with WORK_PACKAGE_MISSING (via the shared reconciliation gate), not a
    stage-status precondition."""
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "WORK_PACKAGE_MISSING"


def test_complete_admin_only(client, db_session, mock_loco_client):
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    admin_headers = _admin_headers(db_session)
    _start_ta(client, admin_headers, visit.id)
    sup_headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=sup_headers)

    assert resp.status_code == 403


def test_complete_blocked_by_open_ta_booking(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_AFTER", equipment_node_id=1843, defect_type_id=1, remarks='x')
    # booking.status stays OPEN (default)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "BOOKINGS_PENDING"
    assert detail["stage"] == "TEST_AFTER"


def test_complete_blocked_by_in_progress_ta_booking(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_AFTER", equipment_node_id=1843, defect_type_id=1, remarks='x')
    booking = db_session.query(models.Booking).filter_by(booking_source="TEST_AFTER").one()
    booking.status = "IN_PROGRESS"
    db_session.commit()

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "BOOKINGS_PENDING"


def test_complete_blocked_by_reopened_ta_booking(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_AFTER", equipment_node_id=1843, defect_type_id=1, remarks='x')
    booking = db_session.query(models.Booking).filter_by(booking_source="TEST_AFTER").one()
    booking.status = "REOPENED"
    db_session.commit()

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "BOOKINGS_PENDING"


def test_complete_allowed_when_all_ta_attended(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_AFTER", equipment_node_id=1843, defect_type_id=1, remarks='x')
    for a in db_session.query(models.BookingSectionAssignment).all():
        set_assignment_status(db_session, a, "ATTENDED")
    for b in db_session.query(models.Booking).filter_by(booking_source="TEST_AFTER").all():
        b.status = "ATTENDED"
    db_session.commit()

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_unresolved_reference_tb_booking_does_not_block_completion(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """The critical rule from the brief: an unattended TEST_BEFORE booking
    referenced during Test After must never block TEST_AFTER completion —
    it's informational only in this workflow. Under the new exact
    booking_source -> stage mapping this is doubly guaranteed: the
    reconciliation service only ever inspects bookings whose
    booking_source == TEST_AFTER."""
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    tb_stage = _stage(db_session, visit.id, "TEST_BEFORE")
    tb_booking = make_booking(
        db_session, 600, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="TEST_BEFORE", stage_id=tb_stage.id,
    )
    make_assignment(db_session, 1, tb_booking.id, section_id=1, status="OPEN")
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    # Zero TEST_AFTER findings — the only "open" thing in this visit is the
    # unrelated TEST_BEFORE reference booking above.

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_complete_is_final_active_stage_and_does_not_touch_visit_status(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """TEST_AFTER is now the last stage in the active Minor workflow
    (Phase 3D scope correction removed SPECIAL_CHECKING) — completing it
    must succeed on its own, must not create/touch any later stage, and
    must not auto-mark the visit READY or Shed Out (that's a future
    phase)."""
    headers = _admin_headers(db_session)
    visit, stages = _ta_setup(db_session, mock_loco_client)
    original_visit_status = visit.status
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"
    stage_types = {
        s.stage_type for s in db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).all()
    }
    assert stage_types == {"TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"}
    db_session.refresh(visit)
    assert visit.status == original_visit_status


def test_repeated_completion_rejected_409(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "ALREADY_COMPLETED"


def test_complete_row_lock_recheck_uses_fresh_state(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_AFTER", equipment_node_id=1843, defect_type_id=1, remarks='x')
    bookings = db_session.query(models.Booking).filter_by(booking_source="TEST_AFTER").all()

    resp1 = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)
    assert resp1.status_code == 409

    for a in db_session.query(models.BookingSectionAssignment).all():
        set_assignment_status(db_session, a, "ATTENDED")
    for b in bookings:
        b.status = "ATTENDED"
    db_session.commit()

    resp2 = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status"] == "COMPLETED"


def test_manual_source_booking_does_not_block_test_after(client, db_session, mock_loco_client, mock_bldcms_client):
    """Exact booking_source -> stage mapping rule: a MANUAL-source booking left OPEN must never
    block TEST_AFTER completion, even though it belongs to the same visit."""
    headers = _admin_headers(db_session)
    visit, _stages = _ta_setup(db_session, mock_loco_client)
    _start_ta(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_AFTER")
    make_booking(
        db_session, 700, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None,
        booking_source="MANUAL",
    )

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-after/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_complete_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/9999/stages/test-after/complete", headers=headers)

    assert resp.status_code == 404


# ------------------------------------------------------------ regression --


def test_workflow_shows_test_after_stage(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 200
    stage_types = [s["stage_type"] for s in resp.json()["stages"]]
    assert "TEST_AFTER" in stage_types
