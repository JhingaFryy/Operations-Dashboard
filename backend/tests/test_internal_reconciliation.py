"""Operations Dashboard Phase 5B.4A: internal service-to-service reconciliation trigger.

Exercises POST /api/internal/shed-visits/{visit_id}/reconcile-checksheet-stages - the endpoint
BL-DCMS calls after a checksheet becomes APPROVED. Auth is X-Internal-API-Key only, never a human
JWT; the route calls the exact same reconcile_visit_checksheet_stages() the Admin-triggered
POST /api/shed-visits/{visit_id}/reconcile-checksheet-stages route uses (see
tests/test_checksheet_stage_reconciliation.py for the full evidence-rule test matrix - this file
focuses on what's specific to the internal trigger: auth, payload-trust, and that it's the same
underlying engine)."""

import app.core.dependencies as dependencies_module
from app.db import models
from tests.conftest import auth_header, grant_access, make_minor_stages, make_shed_visit, make_user, make_movement_supervisor_headers

INTERNAL_KEY = "test-internal-key-not-for-production"


class _StubSettings:
    def __init__(self, operations_internal_api_key):
        self.operations_internal_api_key = operations_internal_api_key


def _set_internal_key(monkeypatch, key):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings(key))


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _supervisor_headers(db_session, user_id=2, employee_id="SUP1"):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash")
    grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)


def _minor_visit(db_session, visit_id=900, loco_number="39126", status="IN_SHED"):
    visit = make_shed_visit(
        db_session, visit_id, loco_number, schedule_variant="IA", status=status, created_by=None
    )
    make_minor_stages(db_session, visit.id, base_id=visit_id * 10)
    return visit


def _configure_all_stages(mock_bldcms_client, technology="3_PHASE", schedule_variant="IA"):
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(
            technology=technology, schedule_variant=schedule_variant, workflow_stage_type=stage
        )


def _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client):
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _requirement_for_stage(body, stage_type):
    stage = next(s for s in body["stages"] if s["workflow_stage_type"] == stage_type)
    return stage["requirements"][0]


def _approve(mock_bldcms_client, visit, req, stage_type, checksheet_id=1):
    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=checksheet_id,
        template_id=req["template_id"],
        workflow_stage_type=stage_type,
        status="APPROVED",
        is_final=True,
    )


def _internal_reconcile(client, visit_id, key=INTERNAL_KEY, extra_headers=None):
    headers = dict(extra_headers or {})
    if key is not None:
        headers["X-Internal-API-Key"] = key
    return client.post(f"/api/internal/shed-visits/{visit_id}/reconcile-checksheet-stages", headers=headers)


def _db_stage(db_session, visit_id, stage_type):
    return (
        db_session.query(models.ShedVisitStage)
        .filter_by(shed_visit_id=visit_id, stage_type=stage_type)
        .one()
    )


# ------------------------------------------------------------------------------------- auth --


def test_missing_key_401(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)

    resp = _internal_reconcile(client, visit.id, key=None)

    assert resp.status_code == 401


def test_wrong_key_401(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)

    resp = _internal_reconcile(client, visit.id, key="totally-wrong-key")

    assert resp.status_code == 401


def test_key_not_configured_401(client, db_session, monkeypatch):
    """When OPERATIONS_INTERNAL_API_KEY isn't set at all, every request fails closed - even one
    that happens to send a plausible-looking key - and the response looks identical to a wrong
    key so a caller can never learn "not configured" from the outside."""
    _set_internal_key(monkeypatch, None)
    visit = _minor_visit(db_session)

    resp = _internal_reconcile(client, visit.id, key="anything")

    assert resp.status_code == 401


def test_correct_key_accepted(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text


def test_key_never_appears_in_error_response(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)

    resp = _internal_reconcile(client, visit.id, key="totally-wrong-key")

    assert INTERNAL_KEY not in resp.text
    assert "totally-wrong-key" not in resp.text


def test_admin_jwt_alone_does_not_authorize_internal_route(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)
    admin_headers = _admin_headers(db_session)

    resp = client.post(
        f"/api/internal/shed-visits/{visit.id}/reconcile-checksheet-stages", headers=admin_headers
    )

    assert resp.status_code == 401


def test_supervisor_jwt_alone_does_not_authorize_internal_route(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)
    sup_headers = _supervisor_headers(db_session)

    resp = client.post(
        f"/api/internal/shed-visits/{visit.id}/reconcile-checksheet-stages", headers=sup_headers
    )

    assert resp.status_code == 401


def test_internal_key_alone_does_not_authorize_the_admin_route(client, db_session, monkeypatch):
    """The internal key is a separate trust domain - it must not double as a bypass for the
    Admin-only human-facing reconciliation route."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)

    resp = client.post(
        f"/api/shed-visits/{visit.id}/reconcile-checksheet-stages",
        headers={"X-Internal-API-Key": INTERNAL_KEY},
    )

    assert resp.status_code == 401


# ---------------------------------------------------------------------------------- trigger --


def test_calls_central_reconciliation_service_and_completes_eligible_stage(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    tb = next(s for s in body["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["current_status"] == "COMPLETED"
    assert tb["reason"] == "COMPLETED"
    assert body["changed"] is True

    db_stage = _db_stage(db_session, visit.id, "TEST_BEFORE")
    assert db_stage.status == "COMPLETED"
    # Same system-attribution rule as the Admin route - never a fake human user.
    assert db_stage.completed_by is None
    assert db_stage.started_by is None


def test_draft_requirement_still_blocks(client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch):
    """Business Rule Alignment: DRAFT (technician hasn't submitted yet) is the only ordinary
    non-satisfying status left - the internal callback inherits the same submission-satisfying
    rule as the Admin-triggered reconciliation, never a second completion rule."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE",
        status="DRAFT",
        is_final=False,
    )

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text
    tb = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["reason"] == "REQUIRED_CHECKSHEETS_PENDING"


def test_submitted_requirement_completes_without_approval(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    """Business Rule Alignment: SUBMITTED (technician has submitted, no Supervisor DSC approval
    yet) is enough to complete the stage via the internal callback - digital-signature/DSC
    approval is never required for maintenance-workflow completion."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE",
        status="SUBMITTED",
        is_final=False,
    )

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text
    tb = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["current_status"] == "COMPLETED"
    assert tb["reason"] == "COMPLETED"


def test_pending_test_before_booking_no_longer_blocks_test_before(client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch):
    from tests.conftest import make_booking

    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    make_booking(db_session, 100, visit.id, booking_source="TEST_BEFORE", status="OPEN")

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text
    # Decision 2 (post-012): Test Before findings no longer hold Test Before open - the stage completes
    # on its checksheet evidence and the findings stay outstanding (Shed Out's booking gate still
    # enforces them; the booking gate itself is covered for Test After in test_minor_workflow_refinement.py).
    tb = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["current_status"] == "COMPLETED"
    assert tb["bookings_ready"] is False


def test_previous_stage_sequencing_still_enforced(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    si_req = _requirement_for_stage(package, "SCHEDULE_INSPECTION")
    _approve(mock_bldcms_client, visit, si_req, "SCHEDULE_INSPECTION")

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text
    si = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "SCHEDULE_INSPECTION")
    assert si["current_status"] == "PENDING"
    assert si["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"


def test_test_before_with_only_optional_rows_still_blocks(client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch):
    """Mirrors test_test_before_stage_whose_rows_are_all_optional_still_blocks over the
    service-to-service trigger: both entry points call the same reconciler (migration 012: Test
    Before rows are always required)."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.add_applicability(
        technology="3_PHASE", schedule_variant="IA", workflow_stage_type="TEST_BEFORE", is_required=False
    )
    for stage in ("SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", schedule_variant="IA", workflow_stage_type=stage)
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    resp2 = _internal_reconcile(client, visit.id)

    assert resp2.status_code == 200, resp2.text
    tb = next(s for s in resp2.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["reason"] == "REQUIRED_CHECKSHEETS_PENDING"


def test_no_work_package_still_blocks(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text
    tb = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["reason"] == "WORK_PACKAGE_MISSING"
    assert resp.json()["changed"] is False


def test_bldcms_unavailable_during_callback_changes_nothing(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")
    mock_bldcms_client.unavailable = True

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    tb = next(s for s in body["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["reason"] == "BLDCMS_UNAVAILABLE"
    assert body["changed"] is False
    assert _db_stage(db_session, visit.id, "TEST_BEFORE").status == "PENDING"


# --------------------------------------------------------------------------- payload trust --


def test_body_cannot_force_stage_completion(client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch):
    """A caller cannot smuggle fabricated 'evidence' through the request body - Dashboard only
    ever recomputes from its own frozen work package plus a fresh BL-DCMS query, never from
    anything the caller sent."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session)

    resp = client.post(
        f"/api/internal/shed-visits/{visit.id}/reconcile-checksheet-stages",
        headers={"X-Internal-API-Key": INTERNAL_KEY},
        json={
            "stage_type": "TEST_BEFORE",
            "completed": True,
            "template_id": 17,
            "status": "APPROVED",
            "override": True,
        },
    )

    assert resp.status_code == 200, resp.text
    tb = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["current_status"] == "PENDING"
    assert tb["reason"] == "WORK_PACKAGE_MISSING"
    assert _db_stage(db_session, visit.id, "TEST_BEFORE").status == "PENDING"


# ------------------------------------------------------------------------------------- CLOSED --


def test_closed_visit_remains_immutable(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    visit = _minor_visit(db_session, status="CLOSED")

    resp = _internal_reconcile(client, visit.id)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_CLOSED"
    assert _db_stage(db_session, visit.id, "TEST_BEFORE").status == "PENDING"


# --------------------------------------------------------------------------------- idempotency --


def test_repeated_callback_is_safe_and_does_not_rewrite_timestamps(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")
    _approve(mock_bldcms_client, visit, tb_req, "TEST_BEFORE")

    resp1 = _internal_reconcile(client, visit.id)
    assert resp1.status_code == 200, resp1.text
    first_completed_at = _db_stage(db_session, visit.id, "TEST_BEFORE").completed_at

    # Simulate a retry after a timeout, or two APPROVEs notifying close together.
    resp2 = _internal_reconcile(client, visit.id)
    resp3 = _internal_reconcile(client, visit.id)

    for resp in (resp2, resp3):
        assert resp.status_code == 200, resp.text
        body = resp.json()
        tb = next(s for s in body["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
        assert tb["reason"] == "ALREADY_COMPLETED"
        assert body["changed"] is False

    assert _db_stage(db_session, visit.id, "TEST_BEFORE").completed_at == first_completed_at
