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
    make_stage,
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


# ---------------------------------------------------------------- workflow --


def test_workflow_returned_for_minor_visit(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shed_visit_id"] == visit.id
    assert body["loco_number"] == "39126"
    assert body["schedule_variant"] == "IA"
    assert [s["stage_type"] for s in body["stages"]] == [
        "TEST_BEFORE",
        "SCHEDULE_INSPECTION",
        "TEST_AFTER",
    ]
    assert [s["stage_order"] for s in body["stages"]] == [1, 2, 3]


def test_workflow_with_legacy_special_checking_row_still_reads(client, db_session, mock_loco_client):
    """Historical visits may still carry a SPECIAL_CHECKING stage row from
    before the Phase 3D scope correction. Reading their workflow must not
    crash, even though new visits no longer get this stage."""
    from tests.conftest import make_minor_stages_with_legacy_special_checking, make_shed_visit

    headers = _admin_headers(db_session)
    visit = make_shed_visit(db_session, 950, "39199", schedule_variant="IA", created_by=None)
    make_minor_stages_with_legacy_special_checking(db_session, visit.id, base_id=9500)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 200, resp.text
    stage_types = [s["stage_type"] for s in resp.json()["stages"]]
    assert stage_types == ["TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER", "SPECIAL_CHECKING"]


def test_workflow_non_minor_visit_returns_409(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = make_shed_visit(db_session, 1, "39126", schedule_family=None, schedule_variant=None, created_by=None)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "WORKFLOW_NOT_APPLICABLE"


def test_workflow_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/shed-visits/9999/workflow", headers=headers)

    assert resp.status_code == 404


def test_workflow_requires_authentication(client, db_session, mock_loco_client):
    resp = client.get("/api/shed-visits/1/workflow")
    assert resp.status_code == 401


def test_supervisor_can_view_workflow_for_relevant_visit(client, db_session, mock_loco_client):
    visit, stages = _minor_visit_with_stages(db_session)
    make_section(db_session, 1, "M1-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    booking = make_booking(db_session, 1, visit.id, defect_type_id=1, created_by=None)
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 200


def test_supervisor_can_view_workflow_for_any_visit(client, db_session, mock_loco_client):
    """Common Booking Pool reform: workflow/booking visibility is global - a Supervisor may view
    any shed visit's workflow regardless of whether their own section has any assignment on it
    (assignments no longer gate visibility at all)."""
    visit, _ = _minor_visit_with_stages(db_session)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/workflow", headers=headers)

    assert resp.status_code == 200


# --------------------------------------------------------------- start TB --


def test_admin_can_start_test_before(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/start", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "IN_PROGRESS"
    assert body["started_by"] == 1
    assert body["started_at"] is not None

    stage = db_session.get(models.ShedVisitStage, body["id"])
    assert stage.status == "IN_PROGRESS"


def test_supervisor_cannot_start_test_before(client, db_session, mock_loco_client):
    visit, _stages = _minor_visit_with_stages(db_session)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/start", headers=headers)

    assert resp.status_code == 403


def test_start_already_in_progress_rejected_409(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _minor_visit_with_stages(db_session)
    client.post(f"/api/shed-visits/{visit.id}/stages/test-before/start", headers=headers)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "STAGE_NOT_PENDING"


def test_start_completed_rejected_409(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _minor_visit_with_stages(db_session)
    tb = stages[0]
    tb.status = "COMPLETED"
    db_session.commit()

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/start", headers=headers)

    assert resp.status_code == 409


def test_start_non_minor_visit_rejected(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = make_shed_visit(db_session, 1, "39126", schedule_family=None, schedule_variant=None, created_by=None)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/start", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "WORKFLOW_NOT_APPLICABLE"


# --------------------------------------------------------- create TB bookings --


def _start_tb(client, headers, visit_id):
    resp = client.post(f"/api/shed-visits/{visit_id}/stages/test-before/start", headers=headers)
    assert resp.status_code == 200, resp.text


def _tb_setup(db_session, mock_loco_client):
    visit, stages = _minor_visit_with_stages(db_session)
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_defect_type(db_session, 2, "BROKEN")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    mock_loco_client.set_mapping(1843, ["M1-HR", "M2-HR"])
    return visit, stages


def _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client):
    """Phase 5B.3: completion is now evidence-driven, so any test exercising a stage-completion
    path needs a real work package + BL-DCMS applicability behind it. Generates one covering all
    3 stages and returns the response body (each stage's requirements, with their template_ids)."""
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage_type in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage_type)
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _approve_stage_checksheets(mock_bldcms_client, visit, package_body, stage_type, start_checksheet_id=1000):
    """Marks every REQUIRED requirement for `stage_type` (from an already-generated work
    package) as APPROVED in BL-DCMS, so that stage's checksheet-evidence gate passes."""
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


def test_tb_booking_events_created(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)

    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='x')

    booking = db_session.query(models.Booking).filter_by(booking_source="TEST_BEFORE").one()
    event_types = [e.event_type for e in db_session.query(models.BookingEvent).filter_by(booking_id=booking.id).all()]
    # CREATED, then one AUTO_ROUTED per resolved section (1843 -> M1-HR + M2-HR).
    assert event_types == ["CREATED", "AUTO_ROUTED", "AUTO_ROUTED"]


def test_tb_booking_correct_actor(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)

    # Attribution is the technician BL-DCMS relays (users.employee_id), never a Dashboard session.
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843,
                            defect_type_id=1, remarks='x', technician_employee_id="SHIFTSUP")

    booking = db_session.query(models.Booking).filter_by(booking_source="TEST_BEFORE").one()
    assert booking.created_by == 1


def test_checksheet_tb_booking_is_accepted_after_test_before_completed(client, db_session, mock_loco_client, mock_bldcms_client):
    """Android sends Test Before bookings only after the checksheet is submitted, and that submission
    can already have completed the stage - so the checksheet channel never requires IN_PROGRESS."""
    headers = _admin_headers(db_session)
    visit, stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_BEFORE")
    complete_resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)
    assert complete_resp.status_code == 200, complete_resp.text

    result = make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='x')

    assert result.created is True and result.status == "OPEN"


def test_list_tb_bookings_only_returns_test_before(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='tb finding')
    # a Log Book booking on the same visit should never appear in this list
    make_booking(db_session, 9999, visit.id, defect_type_id=1, created_by=None, booking_source="LOG_BOOK")

    resp = client.get(f"/api/shed-visits/{visit.id}/test-before/bookings", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["description"] == "tb finding"
    # 1843 is mapped to both M1-HR and M2-HR - one assignment per resolved section.
    assert len(items[0]["assignments"]) == 2
    assert {a["section_code"] for a in items[0]["assignments"]} == {"M1-HR", "M2-HR"}
    assert {a["status"] for a in items[0]["assignments"]} == {"OPEN"}
    assert items[0]["equipment_node_name"] == "Aux Converter"


def test_list_tb_bookings_empty_when_none_created(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)

    resp = client.get(f"/api/shed-visits/{visit.id}/test-before/bookings", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_tb_bookings_loco_master_unavailable_degrades_gracefully(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='x')
    mock_loco_client.unavailable = True

    resp = client.get(f"/api/shed-visits/{visit.id}/test-before/bookings", headers=headers)

    assert resp.status_code == 200
    assert resp.json()[0]["equipment_node_name"] is None


# --------------------------------------------------------------- complete TB --


def test_admin_only_can_complete(client, db_session, mock_loco_client):
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    admin_headers = _admin_headers(db_session)
    _start_tb(client, admin_headers, visit.id)
    sup_headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=sup_headers)

    assert resp.status_code == 403


def test_complete_pending_rejected(client, db_session, mock_loco_client, mock_bldcms_client):
    """Phase 5B.3: completion is evidence-driven now - a PENDING stage with no work package at
    all is blocked with WORK_PACKAGE_MISSING (via the shared reconciliation gate), not a
    stage-status precondition."""
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "WORK_PACKAGE_MISSING"


def test_complete_with_zero_bookings_allowed(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_BEFORE")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    # Phase 5B.3: completion is system/evidence-attributed, never the triggering Admin.
    assert body["completed_by"] is None
    assert body["completed_at"] is not None


def test_complete_all_attended_allowed(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='x')
    # Business Rule Alignment: the stage-booking gate is assignment-based again - attend every
    # real assignment (recompute_booking_status then derives Booking.status itself).
    for a in db_session.query(models.BookingSectionAssignment).all():
        set_assignment_status(db_session, a, "ATTENDED")
    for b in db_session.query(models.Booking).filter_by(booking_source="TEST_BEFORE").all():
        b.status = "ATTENDED"
    db_session.commit()
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_BEFORE")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_complete_allowed_with_open_test_before_finding(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='x')
    # Leave the booking OPEN (default state)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_BEFORE")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    # Decision 2 (post-012): Test Before findings no longer hold Test Before open - the stage completes
    # on its checksheet evidence and the findings stay outstanding (Shed Out's booking gate still
    # enforces them; the booking gate itself is covered for Test After in test_minor_workflow_refinement.py).
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"
    assert db_session.query(models.Booking).filter_by(booking_source="TEST_BEFORE").one().status == "OPEN"


def test_complete_allowed_with_in_progress_test_before_finding(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='x')
    booking = db_session.query(models.Booking).filter_by(booking_source="TEST_BEFORE").one()
    booking.status = "IN_PROGRESS"
    db_session.commit()
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_BEFORE")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    # Decision 2 (post-012): Test Before findings no longer hold Test Before open - the stage completes
    # on its checksheet evidence and the findings stay outstanding (Shed Out's booking gate still
    # enforces them; the booking gate itself is covered for Test After in test_minor_workflow_refinement.py).
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_complete_allowed_with_reopened_test_before_finding(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    make_checksheet_booking(db_session, mock_loco_client, visit.id, "TEST_BEFORE", equipment_node_id=1843, defect_type_id=1, remarks='x')
    booking = db_session.query(models.Booking).filter_by(booking_source="TEST_BEFORE").one()
    booking.status = "REOPENED"
    db_session.commit()
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_BEFORE")

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    # Decision 2 (post-012): Test Before findings no longer hold Test Before open - the stage completes
    # on its checksheet evidence and the findings stay outstanding (Shed Out's booking gate still
    # enforces them; the booking gate itself is covered for Test After in test_minor_workflow_refinement.py).
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "COMPLETED"


def test_complete_sets_next_stage_pending(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit, stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)

    client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    next_stage = db_session.get(models.ShedVisitStage, stages[1].id)
    assert next_stage.stage_type == "SCHEDULE_INSPECTION"
    assert next_stage.status == "PENDING"


def test_repeated_completion_rejected_409(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit, _stages = _tb_setup(db_session, mock_loco_client)
    _start_tb(client, headers, visit.id)
    package = _generate_package_all_stages(client, headers, visit, mock_loco_client, mock_bldcms_client)
    _approve_stage_checksheets(mock_bldcms_client, visit, package, "TEST_BEFORE")
    first = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)
    assert first.status_code == 200, first.text

    resp = client.post(f"/api/shed-visits/{visit.id}/stages/test-before/complete", headers=headers)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "STAGE_NOT_READY_FOR_COMPLETION"
    assert detail["reason"] == "ALREADY_COMPLETED"


def test_complete_missing_visit_404(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/9999/stages/test-before/complete", headers=headers)

    assert resp.status_code == 404
