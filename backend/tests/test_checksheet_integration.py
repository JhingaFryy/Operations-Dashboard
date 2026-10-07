"""Operations Dashboard Integration Phase 4: read-only BL-DCMS checksheet
status/progress. Route-level tests against the FastAPI TestClient, using
MockBLDCMSClient (see tests/conftest.py) to control BL-DCMS's simulated
behavior without any real HTTP call."""

from app.clients.bldcms import BLDCMSClient
from app.db import models
from app.services.bldcms_client import get_bldcms_client
from tests.conftest import (
    make_movement_supervisor_headers,
    auth_header,
    grant_access,
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


def _minor_visit(db_session, visit_id=900, loco_number="39126"):
    return make_shed_visit(db_session, visit_id, loco_number, schedule_variant="IA", created_by=None)


# -------------------------------------------------------------------- client factory --


def test_get_bldcms_client_returns_none_when_not_configured(monkeypatch):
    import app.services.bldcms_client as module

    class _StubSettings:
        bldcms_base_url = None
        bldcms_timeout_seconds = 10.0
        bldcms_internal_api_key = None

    monkeypatch.setattr(module, "get_settings", lambda: _StubSettings())
    module.get_bldcms_client.cache_clear()
    try:
        assert module.get_bldcms_client() is None
    finally:
        module.get_bldcms_client.cache_clear()


def test_get_bldcms_client_returns_client_when_configured(monkeypatch):
    import app.services.bldcms_client as module

    class _StubSettings:
        bldcms_base_url = "http://bldcms.invalid"
        bldcms_timeout_seconds = 7.5
        bldcms_internal_api_key = "some-key"

    monkeypatch.setattr(module, "get_settings", lambda: _StubSettings())
    module.get_bldcms_client.cache_clear()
    try:
        result = get_bldcms_client()
        assert isinstance(result, BLDCMSClient)
        assert result._base_url == "http://bldcms.invalid"
        assert result._timeout == 7.5
    finally:
        module.get_bldcms_client.cache_clear()


# --------------------------------------------------------------- visit endpoint --


def test_checksheets_requires_existing_visit(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/shed-visits/9999/checksheets", headers=headers)

    assert resp.status_code == 404


def test_checksheets_correct_shed_visit_id_forwarded(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=101)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shed_visit_id"] == visit.id
    assert body["available"] is True
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == 101


def test_checksheets_workflow_stage_type_forwarded(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=1, workflow_stage_type="TEST_BEFORE")
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=2, workflow_stage_type="TEST_AFTER")

    resp = client.get(
        f"/api/shed-visits/{visit.id}/checksheets",
        params={"workflow_stage_type": "TEST_AFTER"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == 2


def test_checksheets_empty_list_handled(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["items"] == []


def test_checksheets_same_visit_data_returned_only(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit_a = _minor_visit(db_session, visit_id=501, loco_number="39126")
    visit_b = _minor_visit(db_session, visit_id=502, loco_number="39127")
    mock_bldcms_client.add_checksheet(501, checksheet_id=1)
    mock_bldcms_client.add_checksheet(502, checksheet_id=2)

    resp_a = client.get(f"/api/shed-visits/{visit_a.id}/checksheets", headers=headers)
    resp_b = client.get(f"/api/shed-visits/{visit_b.id}/checksheets", headers=headers)

    assert [i["id"] for i in resp_a.json()["items"]] == [1]
    assert [i["id"] for i in resp_b.json()["items"]] == [2]


def test_checksheets_upstream_unavailable_distinguished_from_empty(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.unavailable = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["items"] == []


def test_checksheets_auth_error_treated_as_unavailable(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.auth_error = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["available"] is False


def test_checksheets_malformed_response_treated_as_unavailable(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.malformed = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["available"] is False


def test_checksheets_normalizes_approval_and_signature_fields(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.add_checksheet(
        visit.id,
        checksheet_id=1,
        status="APPROVED",
        is_final=True,
        digital_signature={
            "signed": True,
            "signing_timestamp": "2026-06-01T00:00:00",
            "verification_status": "VALID",
        },
    )

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    item = resp.json()["items"][0]
    assert item["status"] == "APPROVED"
    assert item["approved"] is True
    assert item["signed"] is True
    assert item["verification_status"] == "VALID"
    # No certificate/hash fields exist on the Dashboard response schema at all.
    assert "certificate_subject" not in item
    assert "signature_hash" not in item


def test_checksheets_response_never_includes_internal_api_key(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.add_checksheet(visit.id)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert "BLDCMS_INTERNAL_API_KEY" not in resp.text
    assert "X-Internal-API-Key" not in resp.text


# ------------------------------------------------------------------- authorization --


def test_checksheets_admin_allowed(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200


def test_checksheets_supervisor_can_view_any_visit(client, db_session, mock_bldcms_client):
    """Common Booking Pool reform: visit-workflow visibility is global for any authenticated
    Dashboard user - a Supervisor no longer needs a relevant booking/assignment to view a
    visit's checksheet integration data."""
    visit = _minor_visit(db_session)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200


def test_checksheets_supervisor_with_relevant_booking_allowed(client, db_session, mock_bldcms_client):
    from tests.conftest import make_assignment, make_booking

    visit = _minor_visit(db_session)
    make_section(db_session, 1, "M1-HR")
    booking = make_booking(db_session, 1, visit.id, created_by=None)
    make_assignment(db_session, 1, booking.id, section_id=1, status="OPEN")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)

    assert resp.status_code == 200


def test_checksheets_unauthenticated_rejected(client, db_session, mock_bldcms_client):
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheets")

    assert resp.status_code == 401


# ------------------------------------------------------------------------- summary --


def test_summary_requires_existing_visit(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)

    resp = client.get("/api/shed-visits/9999/checksheet-summary", headers=headers)

    assert resp.status_code == 404


def test_summary_stage_counts_normalized(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=1, workflow_stage_type="TEST_BEFORE", is_final=True)
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=2, workflow_stage_type="SCHEDULE_INSPECTION", is_final=True)
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=3, workflow_stage_type="SCHEDULE_INSPECTION", is_final=False)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-summary", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shed_visit_id"] == visit.id
    assert body["available"] is True
    stages_by_type = {s["workflow_stage_type"]: s for s in body["stages"]}
    assert stages_by_type["TEST_BEFORE"] == {
        "workflow_stage_type": "TEST_BEFORE", "total_checksheets": 1, "approved_checksheets": 1, "pending_checksheets": 0,
    }
    assert stages_by_type["SCHEDULE_INSPECTION"] == {
        "workflow_stage_type": "SCHEDULE_INSPECTION", "total_checksheets": 2, "approved_checksheets": 1, "pending_checksheets": 1,
    }


def test_summary_empty_stages_accepted(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-summary", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["stages"] == []


def test_summary_bldcms_unavailable_handled(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.unavailable = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-summary", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["stages"] == []


def test_summary_malformed_response_handled(client, db_session, mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    mock_bldcms_client.malformed = True

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-summary", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["available"] is False


def test_summary_supervisor_can_view_any_visit(client, db_session, mock_bldcms_client):
    """Common Booking Pool reform: see test_checksheets_supervisor_can_view_any_visit."""
    visit = _minor_visit(db_session)
    make_section(db_session, 1, "M1-HR")
    headers = _supervisor_headers(db_session, 2, section_id=1)

    resp = client.get(f"/api/shed-visits/{visit.id}/checksheet-summary", headers=headers)

    assert resp.status_code == 200


# ------------------------------------------------------------- no side effects --


def test_checksheets_endpoint_does_not_touch_stages_or_bookings(client, db_session, mock_bldcms_client):
    """Observation only: reading checksheet integration data must never create/modify a
    shed_visit_stage row or a booking."""
    visit = _minor_visit(db_session)
    from tests.conftest import make_minor_stages

    make_minor_stages(db_session, visit.id, base_id=9000)
    stages_before = [
        (s.id, s.status) for s in db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).all()
    ]
    bookings_before = db_session.query(models.Booking).count()

    headers = _admin_headers(db_session)
    mock_bldcms_client.add_checksheet(visit.id)
    client.get(f"/api/shed-visits/{visit.id}/checksheets", headers=headers)
    client.get(f"/api/shed-visits/{visit.id}/checksheet-summary", headers=headers)

    stages_after = [
        (s.id, s.status) for s in db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).all()
    ]
    bookings_after = db_session.query(models.Booking).count()
    assert stages_after == stages_before
    assert bookings_after == bookings_before
