"""Operations Dashboard Phase 5B.3: Authoritative Checksheet Stage Reconciliation.

Exercises POST /api/shed-visits/{visit_id}/reconcile-checksheet-stages directly - the sole
mutation path that may transition a shed_visit_stage to COMPLETED. See
app/services/checksheet_stage_reconciliation_service.py for the rule this file verifies.
"""

from app.db import models
from tests.conftest import auth_header, grant_access, make_booking, make_minor_stages, make_shed_visit, make_user, make_movement_supervisor_headers


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

def _supervisor_headers(db_session, user_id=2, section_id=None, employee_id="SUP1"):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash", section_id=section_id)
    grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)


def _minor_visit(db_session, visit_id=900, loco_number="39126", schedule_variant="IA", status="IN_SHED"):
    visit = make_shed_visit(
        db_session, visit_id, loco_number, schedule_variant=schedule_variant, status=status, created_by=None
    )
    make_minor_stages(db_session, visit.id, base_id=visit_id * 10)
    return visit


def _configure_all_stages(mock_bldcms_client, technology="3_PHASE", schedule_variant="IA", **kwargs):
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(
            technology=technology, schedule_variant=schedule_variant, workflow_stage_type=stage, **kwargs
        )


def _generate_package(
    client, headers, visit, mock_loco_client, mock_bldcms_client, technology="3_PHASE", **applicability_kwargs
):
    mock_loco_client.add_locomotive(visit.loco_number, loco_type=technology)
    _configure_all_stages(mock_bldcms_client, technology=technology, **applicability_kwargs)
    # These tests model a FULLY configured Minor schedule: BL-DCMS declares every required Inspection
    # section configured (migration 010). Partial configuration is covered in test_minor_inspection_package.py.
    mock_bldcms_client.minor_inspection_configuration_complete = True
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _requirement_for_stage(body, stage_type):
    stage = next(s for s in body["stages"] if s["workflow_stage_type"] == stage_type)
    return stage["requirements"][0]


def _approve(mock_bldcms_client, visit, req, stage_type, checksheet_id=1, status="APPROVED"):
    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=checksheet_id,
        template_id=req["template_id"],
        workflow_stage_type=stage_type,
        status=status,
        is_final=(status == "APPROVED"),
    )


def _reconcile(client, headers, visit_id):
    resp = client.post(f"/api/shed-visits/{visit_id}/reconcile-checksheet-stages", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _stage_result(body, stage_type):
    return next(s for s in body["stages"] if s["workflow_stage_type"] == stage_type)


def _db_stage(db_session, visit_id, stage_type):
    return (
        db_session.query(models.ShedVisitStage)
        .filter_by(shed_visit_id=visit_id, stage_type=stage_type)
        .one()
    )


# --------------------------------------------------------------------- checksheet evidence --


def test_all_required_approved_completes_stage(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")

    body = _reconcile(client, headers, visit.id)

    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["previous_status"] == "PENDING"
    assert tb["current_status"] == "COMPLETED"
    assert tb["checksheets_ready"] is True
    assert tb["bookings_ready"] is True
    assert tb["reason"] == "COMPLETED"
    assert body["changed"] is True

    db_stage = _db_stage(db_session, visit.id, "TEST_BEFORE")
    assert db_stage.completed_by is None
    assert db_stage.completed_at is not None
    assert db_stage.started_by is None
    # Migration 012: reconciliation no longer stamps the completion instant as the start - no
    # start was recorded for this stage, so its start (and duration) stays unknown, never 0.
    assert db_stage.started_at is None


def test_non_approved_checksheet_status_blocks(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    for i, blocking_status in enumerate(("DRAFT", "SUBMITTED", "UNDER_REVIEW", "REJECTED")):
        visit = _minor_visit(db_session, visit_id=920 + i, loco_number=f"3912{i}")
        package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
        tb_req = _requirement_for_stage(package, "TEST_BEFORE")
        _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE", status=blocking_status)

        body = _reconcile(client, headers, visit.id)

        tb = _stage_result(body, "TEST_BEFORE")
        assert tb["current_status"] == "PENDING"
        assert tb["checksheets_ready"] is False
        assert tb["reason"] == "REQUIRED_CHECKSHEETS_PENDING"


def test_optional_flag_on_a_test_before_row_no_longer_exempts_it(client, db_session, mock_loco_client, mock_bldcms_client):
    """Migration 012: Test Before is always required - a (legacy) Optional flag on one of its rows
    is ignored. The only Test Before exception is the explicit Admin skip."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.add_applicability(
        technology="3_PHASE", schedule_variant="IA", workflow_stage_type="TEST_BEFORE", is_required=True
    )
    mock_bldcms_client.add_applicability(
        technology="3_PHASE", schedule_variant="IA", workflow_stage_type="TEST_BEFORE", is_required=False
    )
    for stage in ("SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", schedule_variant="IA", workflow_stage_type=stage)
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text
    package = resp.json()

    tb_stage_body = next(s for s in package["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    required_req = next(r for r in tb_stage_body["requirements"] if r["is_required"])
    optional_req = next(r for r in tb_stage_body["requirements"] if not r["is_required"])
    assert optional_req["template_id"] != required_req["template_id"]

    _approve(mock_bldcms_client, visit, required_req, "TEST_BEFORE", checksheet_id=1)

    body = _reconcile(client, headers, visit.id)
    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["reason"] == "REQUIRED_CHECKSHEETS_PENDING"

    _approve(mock_bldcms_client, visit, optional_req, "TEST_BEFORE", checksheet_id=2)
    body = _reconcile(client, headers, visit.id)
    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["current_status"] == "COMPLETED"
    assert tb["reason"] == "COMPLETED"


def test_test_before_stage_whose_rows_are_all_optional_still_blocks(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Migration 012 supersedes the Operational Control behaviour below for TEST_BEFORE/TEST_AFTER:
    their rows are always required, so an all-Optional Test Before no longer completes vacuously.
    (Normal Inspection equipment keeps the NO_BLOCKING_REQUIREMENTS behaviour.)

    Original Operational Control note, still true for normal Inspection rows:

    This stage IS configured (a requirement row exists); its single row is simply not required.
    There is therefore genuinely nothing outstanding, so it completes and reports
    NO_BLOCKING_REQUIREMENTS rather than COMPLETED - the distinction between "finished the work"
    and "had no work to finish" stays visible.

    This is NOT the vacuous-truth bug: the unconfigured case (zero rows at all) is a separate
    branch that still blocks forever - see
    test_unconfigured_inspection_stage_never_completes_and_blocks_test_after below.
    """
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.add_applicability(
        technology="3_PHASE", schedule_variant="IA", workflow_stage_type="TEST_BEFORE", is_required=False
    )
    for stage in ("SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", schedule_variant="IA", workflow_stage_type=stage)
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text

    body = _reconcile(client, headers, visit.id)

    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["checksheets_ready"] is False
    assert tb["reason"] == "REQUIRED_CHECKSHEETS_PENDING"


def test_unconfigured_inspection_stage_never_completes_and_blocks_test_after(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Staged Minor Schedule rollout regression test: TEST_BEFORE is configured (and submitted/
    approved) but SCHEDULE_INSPECTION is not configured in BL-DCMS at all yet (no applicability
    rows -> the generated package carries zero requirements for that stage, same shape as
    "configured with zero required rows" today - see checksheet_work_package_service.py). This
    must NOT be treated as satisfied: SCHEDULE_INSPECTION must stay PENDING with
    NO_REQUIRED_CHECKSHEETS, and TEST_AFTER - which is blocked behind it in
    MINOR_STAGE_SEQUENCE - must never complete either, even though TEST_AFTER itself has no
    requirements to satisfy. The Minor work package therefore stays incomplete and Shed Out
    stays blocked purely from an unconfigured stage."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.add_applicability(
        technology="3_PHASE", schedule_variant="IA", workflow_stage_type="TEST_BEFORE"
    )
    # SCHEDULE_INSPECTION and TEST_AFTER deliberately left unconfigured in BL-DCMS.
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text
    package = resp.json()
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")

    # First call: TEST_BEFORE completes in this same call, so SCHEDULE_INSPECTION/TEST_AFTER are
    # evaluated against TEST_BEFORE's status as loaded at the START of the call (still PENDING) -
    # see the module's "Sequencing" docstring - and are correctly held back a call behind.
    body = _reconcile(client, headers, visit.id)

    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["current_status"] == "COMPLETED"

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "PENDING"
    assert si["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"

    ta = _stage_result(body, "TEST_AFTER")
    assert ta["current_status"] == "PENDING"
    assert ta["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"

    # Second call: TEST_BEFORE is now COMPLETED as-loaded, so SCHEDULE_INSPECTION is finally
    # evaluated on its own merits - and, having zero applicability configured in BL-DCMS (and
    # therefore zero requirements in the package), it is correctly held at PENDING forever, not
    # vacuously completed. TEST_AFTER stays blocked behind it.
    body2 = _reconcile(client, headers, visit.id)

    si2 = _stage_result(body2, "SCHEDULE_INSPECTION")
    assert si2["current_status"] == "PENDING"
    assert si2["reason"] == "STAGE_NOT_CONFIGURED"

    ta2 = _stage_result(body2, "TEST_AFTER")
    assert ta2["current_status"] == "PENDING"
    assert ta2["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"

    db_si = _db_stage(db_session, visit.id, "SCHEDULE_INSPECTION")
    db_ta = _db_stage(db_session, visit.id, "TEST_AFTER")
    assert db_si.status == "PENDING"
    assert db_ta.status == "PENDING"


def test_no_work_package_blocked(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    body = _reconcile(client, headers, visit.id)

    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["reason"] == "WORK_PACKAGE_MISSING"
    assert tb["checksheets_ready"] is None
    assert tb["bookings_ready"] is None
    assert body["changed"] is False


def test_bldcms_unavailable_no_mutation(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    mock_bldcms_client.unavailable = True

    body = _reconcile(client, headers, visit.id)

    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["reason"] == "BLDCMS_UNAVAILABLE"
    assert tb["checksheets_ready"] is None
    assert body["changed"] is False
    db_stage = _db_stage(db_session, visit.id, "TEST_BEFORE")
    assert db_stage.status == "PENDING"
    assert db_stage.completed_at is None


# --------------------------------------------------------------------------------- bookings --


def test_zero_bookings_ready(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")

    body = _reconcile(client, headers, visit.id)

    assert _stage_result(body, "TEST_BEFORE")["reason"] == "COMPLETED"


def test_all_attended_bookings_ready(client, db_session, mock_loco_client, mock_bldcms_client):
    from tests.conftest import make_assignment, ensure_section

    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    ensure_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 100, visit.id, booking_source="TEST_BEFORE", status="ATTENDED")
    make_assignment(db_session, 1, booking.id, section_id=1, status="ATTENDED")

    body = _reconcile(client, headers, visit.id)

    assert _stage_result(body, "TEST_BEFORE")["reason"] == "COMPLETED"


def test_open_test_before_findings_do_not_hold_test_before_open(client, db_session, mock_loco_client, mock_bldcms_client):
    from tests.conftest import make_assignment, ensure_section

    headers = _admin_headers(db_session)
    for i, blocking_status in enumerate(("OPEN", "IN_PROGRESS", "REOPENED")):
        visit = _minor_visit(db_session, visit_id=930 + i, loco_number=f"3913{i}")
        package = _generate_package(
            client, headers, visit, mock_loco_client, mock_bldcms_client, technology=f"3_PHASE_{i}"
        )
        tb_req = _requirement_for_stage(package, "TEST_BEFORE")
        _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
        ensure_section(db_session, 900 + i, f"M{i}-HR")
        booking = make_booking(db_session, 200 + i, visit.id, booking_source="TEST_BEFORE", status="OPEN")
        make_assignment(db_session, 200 + i, booking.id, section_id=900 + i, status=blocking_status)

        body = _reconcile(client, headers, visit.id)

        # Decision 2 (post-012): Test Before findings no longer hold Test Before open - the stage completes
        # on its checksheet evidence and the findings stay outstanding (Shed Out's booking gate still
        # enforces them; the booking gate itself is covered for Test After in test_minor_workflow_refinement.py).
        tb = _stage_result(body, "TEST_BEFORE")
        assert tb["current_status"] == "COMPLETED"
        assert tb["checksheets_ready"] is True
        assert tb["bookings_ready"] is False
        assert tb["reason"] == "COMPLETED"
        db_session.expire_all()
        assert db_session.get(models.BookingSectionAssignment, 200 + i).status == blocking_status


def test_zero_assignment_test_before_booking_is_reported_but_does_not_block(client, db_session, mock_loco_client, mock_bldcms_client):
    """A booking with zero booking_section_assignments rows blocks the stage - booking creation
    always creates at least one, so zero here means a genuine routing gap."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    make_booking(db_session, 200, visit.id, booking_source="TEST_BEFORE", status="OPEN")

    body = _reconcile(client, headers, visit.id)

    # Decision 2 (post-012): Test Before findings no longer hold Test Before open - the stage completes
    # on its checksheet evidence and the findings stay outstanding (Shed Out's booking gate still
    # enforces them; the booking gate itself is covered for Test After in test_minor_workflow_refinement.py).
    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["bookings_ready"] is False
    assert tb["reason"] == "COMPLETED"


def test_log_book_and_manual_bookings_do_not_block_stage(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    make_booking(db_session, 300, visit.id, booking_source="LOG_BOOK", status="OPEN")
    make_booking(db_session, 301, visit.id, booking_source="MANUAL", status="OPEN")

    body = _reconcile(client, headers, visit.id)

    assert _stage_result(body, "TEST_BEFORE")["reason"] == "COMPLETED"


# --------------------------------------------------------------------------------- sequencing --


def test_schedule_inspection_blocked_until_test_before_completed(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    si_req = _requirement_for_stage(package, "SCHEDULE_INSPECTION")
    _approve(mock_bldcms_client, visit, si_req, "SCHEDULE_INSPECTION")
    # TEST_BEFORE has no checksheet at all - still PENDING.

    body = _reconcile(client, headers, visit.id)

    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["current_status"] == "PENDING"
    assert si["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"
    assert si["checksheets_ready"] is None
    assert si["bookings_ready"] is None


def test_test_after_blocked_until_schedule_inspection_completed(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    ta_req = _requirement_for_stage(package, "TEST_AFTER")
    _approve(mock_bldcms_client, visit, ta_req, "TEST_AFTER", checksheet_id=2)

    body = _reconcile(client, headers, visit.id)

    # In a single call, TEST_BEFORE completes but SCHEDULE_INSPECTION has not (evaluated against
    # its PRE-call status) - so TEST_AFTER, despite already-APPROVED evidence, must not bypass
    # ordering in this same call.
    assert _stage_result(body, "TEST_BEFORE")["reason"] == "COMPLETED"
    si = _stage_result(body, "SCHEDULE_INSPECTION")
    assert si["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"
    ta = _stage_result(body, "TEST_AFTER")
    assert ta["current_status"] == "PENDING"
    assert ta["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"


def test_downstream_approved_does_not_bypass_ordering_across_calls(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Even once TEST_BEFORE genuinely completes on a later call, already-APPROVED TEST_AFTER
    evidence from earlier still requires its own reconciliation call to take effect - the engine
    does not cascade multiple stages through in one shot."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    si_req = _requirement_for_stage(package, "SCHEDULE_INSPECTION")
    _approve(mock_bldcms_client, visit, si_req, "SCHEDULE_INSPECTION", checksheet_id=2)
    ta_req = _requirement_for_stage(package, "TEST_AFTER")
    _approve(mock_bldcms_client, visit, ta_req, "TEST_AFTER", checksheet_id=3)

    body1 = _reconcile(client, headers, visit.id)
    assert _stage_result(body1, "TEST_BEFORE")["reason"] == "COMPLETED"
    assert _stage_result(body1, "SCHEDULE_INSPECTION")["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"
    assert _stage_result(body1, "TEST_AFTER")["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"

    body2 = _reconcile(client, headers, visit.id)
    assert _stage_result(body2, "TEST_BEFORE")["reason"] == "ALREADY_COMPLETED"
    assert _stage_result(body2, "SCHEDULE_INSPECTION")["reason"] == "COMPLETED"
    assert _stage_result(body2, "TEST_AFTER")["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"

    body3 = _reconcile(client, headers, visit.id)
    assert _stage_result(body3, "TEST_AFTER")["reason"] == "COMPLETED"


# --------------------------------------------------------------------------------- idempotency --


def test_repeated_reconciliation_does_not_rewrite_completed_stage(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")

    body1 = _reconcile(client, headers, visit.id)
    assert _stage_result(body1, "TEST_BEFORE")["reason"] == "COMPLETED"
    first_completed_at = _db_stage(db_session, visit.id, "TEST_BEFORE").completed_at

    body2 = _reconcile(client, headers, visit.id)
    tb2 = _stage_result(body2, "TEST_BEFORE")
    assert tb2["reason"] == "ALREADY_COMPLETED"
    assert tb2["current_status"] == "COMPLETED"
    assert body2["changed"] is False

    second_completed_at = _db_stage(db_session, visit.id, "TEST_BEFORE").completed_at
    assert second_completed_at == first_completed_at


# ------------------------------------------------------------------------------------- CLOSED --


def test_closed_visit_cannot_be_reconciled(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, status="CLOSED")

    resp = client.post(f"/api/shed-visits/{visit.id}/reconcile-checksheet-stages", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_CLOSED"
    db_stage = _db_stage(db_session, visit.id, "TEST_BEFORE")
    assert db_stage.status == "PENDING"


# ------------------------------------------------------------------------------------- access --


def test_reconcile_admin_only(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _minor_visit(db_session)
    sup_headers = _supervisor_headers(db_session)

    resp = client.post(f"/api/shed-visits/{visit.id}/reconcile-checksheet-stages", headers=sup_headers)

    assert resp.status_code == 403


def test_reconcile_is_not_reachable_via_get(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/reconcile-checksheet-stages", headers=headers)

    assert resp.status_code == 405


# ------------------------------------------------------------------ no assignment dependency --


def test_booking_status_alone_cannot_bypass_the_assignment_gate(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Business Rule Alignment: booking_section_assignments is authoritative again - a booking
    whose own Booking.status says ATTENDED, but whose real assignment is still OPEN, must still
    block. Booking.status is never trusted directly, even though
    booking_assignment_service.recompute_booking_status keeps it in sync on every real
    mutation."""
    from tests.conftest import make_assignment, ensure_section

    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    ensure_section(db_session, 1, "M1-HR")
    # Booking.status directly claims ATTENDED, but its one real assignment is still OPEN - this
    # can't happen through the real mutation API (recompute_booking_status keeps them in sync),
    # but proves the gate reads assignments directly rather than trusting the cached status.
    booking = make_booking(db_session, 400, visit.id, booking_source="TEST_BEFORE", status="ATTENDED")
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")

    body = _reconcile(client, headers, visit.id)

    # Decision 2 (post-012): Test Before findings no longer hold Test Before open - the stage completes
    # on its checksheet evidence and the findings stay outstanding (Shed Out's booking gate still
    # enforces them; the booking gate itself is covered for Test After in test_minor_workflow_refinement.py).
    tb = _stage_result(body, "TEST_BEFORE")
    assert tb["bookings_ready"] is False            # still read from the assignment, not Booking.status
    assert tb["reason"] == "COMPLETED"
