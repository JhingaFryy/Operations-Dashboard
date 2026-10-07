"""Operations Dashboard Phase 5B.2, refactored by Business Rule Alignment: Required Checksheet vs
BL-DCMS submission (SUBMITTED/UNDER_REVIEW/APPROVED) correlation."""

import pytest

from app.db import models
from tests.conftest import auth_header, grant_access, make_section, make_shed_visit, make_user, make_movement_supervisor_headers


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _supervisor_headers(db_session, user_id, section_id=None, employee_id="SUP1"):
    make_user(db_session, user_id, employee_id, "Sup", "Supervisor", "hash", section_id=section_id)
    grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)


def _minor_visit(db_session, visit_id=900, loco_number="39126", schedule_variant="IA"):
    return make_shed_visit(db_session, visit_id, loco_number, schedule_variant=schedule_variant, created_by=None)


def _configure_all_stages(mock_bldcms_client, technology="3_PHASE", schedule_variant="IA"):
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(
            technology=technology, schedule_variant=schedule_variant, workflow_stage_type=stage
        )


def _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client, **applicability_kwargs):
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client, **applicability_kwargs)
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _requirement_for_stage(body, stage_type):
    stage = next(s for s in body["stages"] if s["workflow_stage_type"] == stage_type)
    return stage["requirements"][0]


# --------------------------------------------------------------------------- matching --


def test_same_visit_stage_template_approved_is_satisfied(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE",
        status="APPROVED",
        is_final=True,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bldcms_available"] is True
    tb_stage = next(s for s in body["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "SATISFIED"
    assert req["satisfied"] is True
    assert tb_stage["required_satisfied"] == 1
    assert tb_stage["ready_for_completion"] is True


def test_same_loco_different_visit_not_matched(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session, visit_id=900, loco_number="39126")
    other_visit = make_shed_visit(db_session, 901, "39126", schedule_variant="IA", status="CLOSED", created_by=None)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    # Checksheet exists for the SAME loco number but a DIFFERENT shed_visit_id.
    mock_bldcms_client.add_checksheet(
        other_visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE",
        status="APPROVED",
        is_final=True,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text
    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_stage["requirements"][0]["progress_state"] == "NOT_STARTED"


def test_same_visit_template_different_stage_not_matched(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    # Same template_id, but filed against SCHEDULE_INSPECTION, not TEST_BEFORE.
    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="SCHEDULE_INSPECTION",
        status="APPROVED",
        is_final=True,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_stage["requirements"][0]["progress_state"] == "NOT_STARTED"


def test_same_visit_stage_different_template_not_matched(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"] + 999,
        workflow_stage_type="TEST_BEFORE",
        status="APPROVED",
        is_final=True,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_stage["requirements"][0]["progress_state"] == "NOT_STARTED"


def test_draft_status_is_in_progress_not_satisfied(client, db_session, mock_loco_client, mock_bldcms_client):
    """Business Rule Alignment: DRAFT is the only non-terminal, non-satisfying status left -
    technician work hasn't even been submitted yet."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE",
        status="DRAFT",
        is_final=False,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "IN_PROGRESS"
    assert req["satisfied"] is False


@pytest.mark.parametrize("status", ["SUBMITTED", "UNDER_REVIEW", "APPROVED"])
def test_submission_satisfying_statuses_are_satisfied(
    status, client, db_session, mock_loco_client, mock_bldcms_client
):
    """Business Rule Alignment: maintenance completion is technician submission, not Supervisor
    DSC approval - SUBMITTED/UNDER_REVIEW/APPROVED all satisfy a requirement."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE",
        status=status,
        is_final=(status == "APPROVED"),
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "SATISFIED"
    assert req["satisfied"] is True


def test_rejected_status_is_flagged_rejected_not_satisfied(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """REJECTED never satisfies, and is distinct from ordinary IN_PROGRESS so the UI can flag it
    rather than presenting it as routine progress."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE",
        status="REJECTED",
        is_final=False,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "REJECTED"
    assert req["satisfied"] is False


def test_rejected_plus_active_draft_resubmission_is_in_progress(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Multiple matching rows: an earlier REJECTED attempt alongside an active DRAFT
    resubmission for the same requirement reads as ordinary IN_PROGRESS, not REJECTED - the
    technician is actively redoing the work."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=1, template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="REJECTED", is_final=False,
    )
    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=2, template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="DRAFT", is_final=False,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "IN_PROGRESS"
    assert req["satisfied"] is False
    assert len(req["matching_checksheets"]) == 2


def test_rejected_plus_later_submitted_is_satisfied(client, db_session, mock_loco_client, mock_bldcms_client):
    """Multiple matching rows: an earlier REJECTED attempt alongside a later SUBMITTED
    resubmission for the same requirement is satisfied - ANY matching row satisfying is enough,
    the REJECTED row never overrides it."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=1, template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="REJECTED", is_final=False,
    )
    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=2, template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="SUBMITTED", is_final=False,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "SATISFIED"
    assert req["satisfied"] is True


def test_no_checksheet_is_not_started(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "NOT_STARTED"
    assert req["matching_checksheets"] == []


def test_multiple_matching_rows_one_approved_is_satisfied(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    tb_req = _requirement_for_stage(package, "TEST_BEFORE")

    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=1, template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="REJECTED", is_final=False,
    )
    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=2, template_id=tb_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="APPROVED", is_final=True,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb_stage["requirements"][0]
    assert req["progress_state"] == "SATISFIED"
    assert len(req["matching_checksheets"]) == 2
    statuses = {c["status"] for c in req["matching_checksheets"]}
    assert statuses == {"REJECTED", "APPROVED"}


# ---------------------------------------------------------------- required/optional --


def test_required_totals_correct(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage, is_required=True)
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage, is_required=False)
    client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_stage["required_total"] == 1
    assert tb_stage["optional_total"] == 1


def test_optional_rows_do_not_block_ready_for_completion(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage, is_required=True)
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage, is_required=False)
    body = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers).json()
    tb_stage_pkg = next(s for s in body["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    required_req = next(r for r in tb_stage_pkg["requirements"] if r["is_required"])

    # Only the required row gets approved; the optional row is left with no checksheet at all.
    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=1, template_id=required_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="APPROVED", is_final=True,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_stage["ready_for_completion"] is True
    assert tb_stage["optional_satisfied"] == 0


def test_optional_satisfied_count_works(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage, is_required=True)
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage, is_required=False)
    body = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers).json()
    tb_stage_pkg = next(s for s in body["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    optional_req = next(r for r in tb_stage_pkg["requirements"] if not r["is_required"])

    mock_bldcms_client.add_checksheet(
        visit.id, checksheet_id=1, template_id=optional_req["template_id"],
        workflow_stage_type="TEST_BEFORE", status="APPROVED", is_final=True,
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_stage["optional_satisfied"] == 1
    # The required row is still unmet, so the stage is still not ready.
    assert tb_stage["ready_for_completion"] is False


def test_zero_required_does_not_become_ready(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    # TEST_BEFORE gets only an optional row; SI/TA get a required row each so the package still
    # generates (at least one stage must be non-empty).
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_BEFORE", is_required=False)
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="SCHEDULE_INSPECTION")
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_AFTER")
    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text

    progress = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    tb_stage = next(s for s in progress.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_stage["required_total"] == 0
    assert tb_stage["ready_for_completion"] is False


# ------------------------------------------------------------------------- package --


def test_no_package_returns_no_package_state(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["work_package_generated"] is False
    assert body["bldcms_available"] is False
    assert body["stages"] == []


def test_frozen_snapshot_used_not_current_applicability(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    package = _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    original_template_ids = {
        r["template_id"] for s in package["stages"] for r in s["requirements"]
    }

    # A later applicability change (a brand-new template added to BL-DCMS) must never appear.
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_BEFORE")

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    body = resp.json()
    seen_template_ids = {r["template_id"] for s in body["stages"] for r in s["requirements"]}
    assert seen_template_ids == original_template_ids


def test_current_applicability_never_queried(client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)

    def _boom(*args, **kwargs):
        raise AssertionError("resolve_applicability must never be called by the progress endpoint")

    monkeypatch.setattr(mock_bldcms_client, "resolve_applicability", _boom)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------- integration failure --


def test_bldcms_unavailable(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    mock_bldcms_client.unavailable = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bldcms_available"] is False
    assert body["work_package_generated"] is True
    assert body["stages"] == []


def test_bldcms_auth_error(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    mock_bldcms_client.auth_error = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["bldcms_available"] is False


def test_bldcms_malformed_response(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    mock_bldcms_client.malformed = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["bldcms_available"] is False


def test_unavailable_never_masquerades_as_zero_progress(client, db_session, mock_loco_client, mock_bldcms_client):
    """The critical rule from the brief: bldcms_available=False must be clearly distinguishable
    from "checked, and genuinely zero progress" - stages must be empty, not populated with a
    fabricated required_satisfied=0."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    mock_bldcms_client.unavailable = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    body = resp.json()
    assert body["bldcms_available"] is False
    assert body["stages"] == []


# -------------------------------------------------------------------------------- auth --


def test_admin_allowed(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200


def test_supervisor_allowed_any_visit(client, db_session, mock_loco_client, mock_bldcms_client):
    """Common Booking Pool reform: visit visibility is global for any authenticated Dashboard
    user - a Supervisor needs no relevant booking/assignment to read this endpoint."""
    admin_headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, admin_headers, visit, mock_loco_client, mock_bldcms_client)
    make_section(db_session, 1, "M1-HR")
    sup_headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=sup_headers)

    assert resp.status_code == 200


def test_unauthenticated_rejected(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress")

    assert resp.status_code == 401


def test_a_supervisor_without_dashboard_access_may_read_progress(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Base operational access is a matter of ROLE now. An active Supervisor with no
    dashboard_access row is still a Supervisor and may use the ordinary operational pages."""
    visit = _minor_visit(db_session)
    make_user(db_session, 2, "SUP1", "Sup", "Supervisor", "hash")
    # No grant_access call - no dashboard_access row at all.
    headers = auth_header("SUP1", "Supervisor", 2)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 200, resp.text


def test_missing_visit_404(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/shed-visits/9999/checksheet-requirement-progress", headers=headers)

    assert resp.status_code == 404


# --------------------------------------------------------------------- no side effects --


def test_no_shed_visit_stage_mutation(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    before = [
        (s.id, s.status, s.completed_at)
        for s in db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).all()
    ]

    client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    after = [
        (s.id, s.status, s.completed_at)
        for s in db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).all()
    ]
    assert after == before


def test_no_booking_mutation(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    before = db_session.query(models.Booking).count()

    client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert db_session.query(models.Booking).count() == before


def test_no_work_package_mutation(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)
    before_packages = db_session.query(models.ShedVisitChecksheetPackage).count()
    before_requirements = db_session.query(models.ShedVisitChecksheetRequirement).count()

    client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert db_session.query(models.ShedVisitChecksheetPackage).count() == before_packages
    assert db_session.query(models.ShedVisitChecksheetRequirement).count() == before_requirements


def test_endpoint_safe_to_call_repeatedly(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _generate_package(client, headers, visit, mock_loco_client, mock_bldcms_client)

    first = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)
    second = client.get(f"/api/shed-visits/{visit.id}/checksheet-requirement-progress", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
