"""Operations Dashboard Integration Phase 5B.1: materialized checksheet work packages."""

from app.db import models
from tests.conftest import auth_header, grant_access, make_assignment, make_booking, make_section, make_shed_visit, make_user, make_movement_supervisor_headers


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _supervisor_headers(db_session, user_id, section_id, employee_id="SUP1"):
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


# --------------------------------------------------------------------------- generation --


def test_admin_can_generate(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated"] is True
    assert body["shed_visit_id"] == visit.id
    assert len(body["stages"]) == 3


def test_supervisor_cannot_generate(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _minor_visit(db_session)
    make_section(db_session, 1, "M1-HR")
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 403
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_nonexistent_visit_404(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/9999/checksheet-work-package", headers=headers)

    assert resp.status_code == 404


def test_unsupported_schedule_rejected(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = make_shed_visit(db_session, 900, "39126", schedule_family=None, schedule_variant=None, created_by=None)

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "WORKFLOW_NOT_APPLICABLE"
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_technology_resolution_failure_creates_nothing(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    # Locomotive deliberately never registered with mock_loco_client.

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "LOCOMOTIVE_TECHNOLOGY_UNRESOLVED"
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_bldcms_unavailable_creates_nothing(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.unavailable = True

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "BLDCMS_UNAVAILABLE"
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_bldcms_auth_failure_creates_nothing(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.auth_error = True

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 502
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_malformed_bldcms_response_creates_nothing(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.malformed = True

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 502
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_all_three_stages_queried(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200, resp.text
    stage_types = {s["workflow_stage_type"] for s in resp.json()["stages"]}
    assert stage_types == {"TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"}


def test_required_and_optional_snapshotted(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage, is_required=True)
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_BEFORE", is_required=False)

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200, resp.text
    tb = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    required_flags = {r["is_required"] for r in tb["requirements"]}
    assert required_flags == {True, False}


def test_human_readable_metadata_snapshot_preserved(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    for stage in ("SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type=stage)
    mock_bldcms_client.add_applicability(
        technology="3_PHASE",
        workflow_stage_type="TEST_BEFORE",
        template_name="Aux Converter Checksheet",
        section_name="M4-HR",
        equipment_name="Aux Converter",
        maintenance_type="GC",
    )

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200, resp.text
    tb = next(s for s in resp.json()["stages"] if s["workflow_stage_type"] == "TEST_BEFORE")
    req = tb["requirements"][0]
    assert req["template_name"] == "Aux Converter Checksheet"
    assert req["section_name"] == "M4-HR"
    assert req["equipment_name"] == "Aux Converter"
    assert req["maintenance_type"] == "GC"
    assert req["technology"] == "3_PHASE"


def test_repeated_generation_returns_existing_snapshot_unchanged(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)

    first = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    second = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 1


def test_later_applicability_change_does_not_mutate_existing_package(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)

    first = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()

    # Simulate a BL-DCMS applicability edit happening after generation - a whole new mapping
    # for TEST_BEFORE, mocked as if freshly configured.
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_BEFORE", template_id=999)

    second = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert second.status_code == 200, second.text
    assert second.json() == first_body  # unchanged, the new mapping never got picked up


def test_zero_mappings_rejected(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    # No applicability configured for any stage.

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "NO_APPLICABILITY_CONFIGURED"
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_partial_stage_configuration_is_accepted(client, db_session, mock_loco_client, mock_bldcms_client):
    """Staged Minor Schedule rollout: TEST_BEFORE/SCHEDULE_INSPECTION/TEST_AFTER may each be
    independently configured now - a visit with only TEST_BEFORE and SCHEDULE_INSPECTION
    configured (TEST_AFTER deliberately left unconfigured) must still generate a package, with
    TEST_AFTER simply carrying zero requirements. This replaces the old all-or-none
    APPLICABILITY_CONFIGURATION_INCOMPLETE rejection."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_BEFORE")
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="SCHEDULE_INSPECTION")
    # TEST_AFTER deliberately left unconfigured.

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated"] is True
    stages_by_type = {s["workflow_stage_type"]: s for s in body["stages"]}
    assert len(stages_by_type["TEST_BEFORE"]["requirements"]) == 1
    assert len(stages_by_type["SCHEDULE_INSPECTION"]["requirements"]) == 1
    assert stages_by_type["TEST_AFTER"]["requirements"] == []
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 1
    # No fake TEST_AFTER requirement was fabricated for the unconfigured stage.
    assert (
        db_session.query(models.ShedVisitChecksheetRequirement)
        .filter(models.ShedVisitChecksheetRequirement.workflow_stage_type == "TEST_AFTER")
        .count()
        == 0
    )


def test_only_test_before_configured_generates_package_with_others_empty(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """The staged-rollout case this whole change exists for: only TEST_BEFORE is configured in
    BL-DCMS (SCHEDULE_INSPECTION and TEST_AFTER are not configured at all yet). Generation must
    still succeed."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_BEFORE")

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    stages_by_type = {s["workflow_stage_type"]: s for s in body["stages"]}
    assert len(stages_by_type["TEST_BEFORE"]["requirements"]) == 1
    assert stages_by_type["SCHEDULE_INSPECTION"]["requirements"] == []
    assert stages_by_type["TEST_AFTER"]["requirements"] == []


def test_transaction_rollback_when_later_stage_fails(client, db_session, mock_loco_client, mock_bldcms_client):
    """Even though the current implementation resolves all 3 stages before writing anything (so
    a mid-write failure is unlikely), this proves the invariant holds: if the read phase for any
    stage fails, nothing from an earlier stage's successful resolution is ever persisted."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_BEFORE")
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="SCHEDULE_INSPECTION")
    mock_bldcms_client.add_applicability(technology="3_PHASE", workflow_stage_type="TEST_AFTER")
    # Force unavailability partway through by flipping it on before the call - simplest reliable
    # simulation available through the mock's public surface.
    mock_bldcms_client.unavailable = True

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 502
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0
    assert db_session.query(models.ShedVisitChecksheetRequirement).count() == 0


# -------------------------------------------------------------------------------- read --


def test_read_admin_allowed(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["generated"] is False


def test_read_before_generation_shows_not_generated(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["generated"] is False
    assert body["stages"] == []


def test_read_after_generation_shows_generated(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)
    client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["generated"] is True


def test_read_supervisor_can_view_any_visit(client, db_session, mock_loco_client, mock_bldcms_client):
    """Common Booking Pool reform: visibility is global for any authenticated Dashboard user."""
    visit = _minor_visit(db_session)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200


def test_read_supervisor_with_relevant_booking_allowed(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _minor_visit(db_session)
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None)
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)

    assert resp.status_code == 200


def test_read_nonexistent_visit_404(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/shed-visits/9999/checksheet-work-package", headers=headers)

    assert resp.status_code == 404


# ------------------------------------------------------------------------- no side effects --


def test_generation_does_not_mutate_stages_or_bookings(client, db_session, mock_loco_client, mock_bldcms_client):
    from tests.conftest import make_minor_stages

    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    _configure_all_stages(mock_bldcms_client)
    stages = make_minor_stages(db_session, visit.id, base_id=9000)
    snapshot = [(s.id, s.status) for s in stages]
    bookings_before = db_session.query(models.Booking).count()

    resp = client.post(f"/api/shed-visits/{visit.id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 200, resp.text

    for s in stages:
        db_session.refresh(s)
    assert [(s.id, s.status) for s in stages] == snapshot
    assert db_session.query(models.Booking).count() == bookings_before
