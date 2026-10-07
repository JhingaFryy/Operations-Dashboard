"""MINOR workflow refinement (migration 012).

  * Test Before: always required; its ONLY exception is an audited, Admin-only Skip (status SKIPPED,
    never COMPLETED). Test After: always required, no exception.
  * Start Schedule needs Test Before COMPLETED or SKIPPED; Complete Schedule (MINOR) writes
    inspection_completed_at, not ready_at; Mark Ready needs Test After COMPLETED.
  * Stage timing: first real start wins and is never reset; a skipped Test Before has a NULL duration.
  * Pending Checksheets: Supervisors see every locomotive but only their own section's rows; Test
    Before / Test After rows are not configurable; Skipped / Awaiting Inspection are not pending.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

import app.core.dependencies as dependencies_module
from app.db import models
from app.services.shed_visit_phase import compute_minor_workflow_analytics
from tests.conftest import (
    auth_header,
    ensure_section,
    make_movement_supervisor_headers,
    make_section_supervisor_headers,
    make_shed_visit,
    make_stage,
    make_true_admin_headers,
    make_user,
)

INTERNAL_KEY = "test-internal-key-not-for-production"
PANEL = "/api/admin/pending-checksheet-requirements"
SHIFT, M1 = 18, 9
MINOR = ("IA", "IA0", "IB", "IC", "IC0")


class _StubSettings:
    operations_internal_api_key = INTERNAL_KEY


@pytest.fixture()
def internal(monkeypatch):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings())
    return {"X-Internal-API-Key": INTERNAL_KEY}


def _now():
    return datetime.now(timezone.utc)


def _configure(mock, variant="IB"):
    """TB/TA owned by SHIFT, one required M1-HR Inspection requirement, configuration complete."""
    for stage, appl in (("TEST_BEFORE", 1), ("TEST_AFTER", 2)):
        mock.applicability.setdefault(("WAP7", "MINOR", variant, stage), []).append({
            "applicability_id": appl * 100 + MINOR.index(variant), "template_id": 179, "template_name": "TB/TA",
            "technology": "3_PHASE", "section_id": SHIFT, "section_name": "SHIFT", "equipment_id": None,
            "equipment_name": None, "maintenance_type": None, "is_required": True})
    mock.applicability.setdefault(("WAP7", "MINOR", variant, "SCHEDULE_INSPECTION"), []).append({
        "applicability_id": 900 + MINOR.index(variant), "template_id": 601, "template_name": "M1-HR - FB PANEL",
        "technology": "3_PHASE", "section_id": M1, "section_name": "M1-HR", "equipment_id": None,
        "equipment_name": None, "maintenance_type": None, "is_required": True})
    mock.minor_inspection_configuration_complete = True


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=SHIFT, code="SHIFT", name="SHIFT")
    ensure_section(db_session, id=M1, code="M1-HR", name="M1-HR")
    mock_loco_client.add_locomotive("30476", loco_type="WAP7")
    mock_loco_client.add_locomotive("30757", loco_type="WAP7")
    for v in MINOR:
        _configure(mock_bldcms_client, v)
    return SimpleNamespace(
        movement=make_movement_supervisor_headers(db_session),          # SHIFT Supervisor, user 1
        admin=make_true_admin_headers(db_session),                       # Admin, user 99
        m1=make_section_supervisor_headers(db_session, user_id=2, section_id=M1, employee_id="M1SUP"),
    )


def _shed_in(client, headers, loco="30476", variant="IB", arrival=None):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": (arrival or _now() - timedelta(hours=6)).isoformat(),
        "schedule_family": "MINOR", "schedule_variant": variant, "arrival_condition": "WORKING",
        "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _stage(db_session, visit_id, stage_type):
    db_session.expire_all()
    return db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit_id, stage_type=stage_type).one()


def _visit(db_session, visit_id):
    db_session.expire_all()
    return db_session.get(models.ShedVisit, visit_id)


def _submit(mock, visit_id, stage, template_id, section, status="SUBMITTED", checksheet_id=None):
    mock.add_checksheet(visit_id, checksheet_id=checksheet_id or len(mock.visits.get(visit_id, [])) + 1,
                        template_id=template_id, section_id=section, schedule_variant="IB",
                        workflow_stage_type=stage, status=status)


def _reconcile(client, headers, visit_id):
    resp = client.post(f"/api/shed-visits/{visit_id}/reconcile-checksheet-stages", headers=headers)
    assert resp.status_code == 200, resp.text
    return {s["workflow_stage_type"]: s for s in resp.json()["stages"]}


def _post(client, headers, visit_id, action, field, at):
    return client.post(f"/api/shed-visits/{visit_id}/{action}", json={field: at.isoformat()}, headers=headers)


def _skip(client, headers, visit_id, reason=None):
    return client.post(f"/api/shed-visits/{visit_id}/stages/test-before/skip",
                       json={"reason": reason} if reason is not None else None, headers=headers)


def _requirement_ids(db_session, visit_id):
    rows = (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id).all())
    return {r.workflow_stage_type: r.id for r in rows}


# ===================================================================== authorization ==

def test_supervisor_sees_every_locomotive_but_only_own_section_rows_and_counts(client, db_session, env):
    first = _shed_in(client, env.movement, loco="30476")
    second = _shed_in(client, env.movement, loco="30757")

    body = client.get(PANEL, headers=env.m1).json()
    assert body["scope_section_id"] == M1
    assert {g["shed_visit_id"] for g in body["groups"]} == {first, second}
    for group in body["groups"]:
        assert {r["section_id"] for r in group["requirements"]} == {M1}
        assert group["counts"]["total"] == 1            # never SHIFT's TB/TA rows
        assert group["test_before"] is None             # visit-level controls are Admin-only

    # A filter cannot widen the scope.
    widened = client.get(PANEL, params={"section_id": SHIFT}, headers=env.m1).json()
    assert all(g["requirements"] == [] and g["counts"]["total"] == 1 for g in widened["groups"])

    shift = client.get(PANEL, headers=env.movement).json()
    assert {r["workflow_stage_type"] for g in shift["groups"] for r in g["requirements"]} == {"TEST_BEFORE", "TEST_AFTER"}

    admin = client.get(PANEL, headers=env.admin).json()
    assert admin["scope_section_id"] is None
    group = next(g for g in admin["groups"] if g["shed_visit_id"] == first)
    assert {r["section_id"] for r in group["requirements"]} == {SHIFT, M1}
    assert group["test_before"]["can_skip"] is True


def test_technician_has_no_panel_access(client, db_session, env):
    make_user(db_session, 3, "TECH1", "Tech", "Technician", "hash", section_id=M1)
    assert client.get(PANEL, headers=auth_header("TECH1", "Technician", 3)).status_code in (401, 403)


def test_supervisors_cannot_change_requirement_configuration(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    inspection = _requirement_ids(db_session, visit_id)["SCHEDULE_INSPECTION"]
    for headers in (env.movement, env.m1):
        assert client.patch(f"{PANEL}/{inspection}/required?is_required=false", headers=headers).status_code == 403
        assert client.patch(f"{PANEL}/{inspection}/active?is_active=false", headers=headers).status_code == 403


def test_admin_still_configures_normal_inspection_equipment(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    inspection = _requirement_ids(db_session, visit_id)["SCHEDULE_INSPECTION"]
    resp = client.patch(f"{PANEL}/{inspection}/required?is_required=false", headers=env.admin)
    assert resp.status_code == 200 and resp.json()["is_required"] is False and resp.json()["configurable"] is True
    assert client.patch(f"{PANEL}/{inspection}/active?is_active=false", headers=env.admin).status_code == 200
    assert client.patch(f"{PANEL}/{inspection}/active?is_active=true", headers=env.admin).status_code == 200


@pytest.mark.parametrize("stage,code", [("TEST_BEFORE", "TEST_BEFORE_NOT_CONFIGURABLE"),
                                        ("TEST_AFTER", "TEST_AFTER_ALWAYS_REQUIRED")])
@pytest.mark.parametrize("change", ["required?is_required=false", "required?is_required=true",
                                    "active?is_active=false", "active?is_active=true"])
def test_test_before_and_test_after_rows_reject_every_override(client, db_session, env, internal, stage, code, change):
    visit_id = _shed_in(client, env.movement)
    req_id = _requirement_ids(db_session, visit_id)[stage]

    human = client.patch(f"{PANEL}/{req_id}/{change}", headers=env.admin)
    assert human.status_code == 409 and human.json()["detail"]["code"] == code
    service = client.patch(f"/api/internal/pending-checksheet-requirements/{req_id}/{change}", headers=internal)
    assert service.status_code == 409 and service.json()["detail"]["code"] == code

    row = db_session.get(models.ShedVisitChecksheetRequirement, req_id)
    db_session.refresh(row)
    assert row.is_required is True and row.is_active is True and row.changed_by is None


def test_test_before_and_test_after_rows_are_reported_as_not_configurable(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    group = next(g for g in client.get(PANEL, headers=env.admin).json()["groups"] if g["shed_visit_id"] == visit_id)
    flags = {r["workflow_stage_type"]: r["configurable"] for r in group["requirements"]}
    assert flags == {"TEST_BEFORE": False, "TEST_AFTER": False, "SCHEDULE_INSPECTION": True}


def test_legacy_optional_flag_on_test_before_no_longer_applies(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    row = db_session.get(models.ShedVisitChecksheetRequirement, _requirement_ids(db_session, visit_id)["TEST_BEFORE"])
    row.is_required = False          # a pre-012 per-visit override, preserved in the table
    db_session.commit()

    group = next(g for g in client.get(PANEL, headers=env.admin).json()["groups"] if g["shed_visit_id"] == visit_id)
    tb = next(r for r in group["requirements"] if r["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["is_required"] is True and group["counts"]["optional"] == 0
    assert _reconcile(client, env.movement, visit_id)["TEST_BEFORE"]["reason"] == "REQUIRED_CHECKSHEETS_PENDING"


# ======================================================================== TB skip ==

def test_only_admin_can_skip_test_before(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    assert _skip(client, env.movement, visit_id).status_code == 403
    assert _skip(client, env.m1, visit_id).status_code == 403
    assert _stage(db_session, visit_id, "TEST_BEFORE").status == "PENDING"

    resp = _skip(client, env.admin, visit_id, reason="  Loco needed urgently  ")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SKIPPED" and body["skipped_by"] == 99 and body["skip_reason"] == "Loco needed urgently"
    assert body["completed_at"] is None


def test_skip_is_a_distinct_audited_state_never_a_completed_test_before(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env.movement)
    assert _skip(client, env.admin, visit_id, reason="urgent").status_code == 200

    stage = _stage(db_session, visit_id, "TEST_BEFORE")
    assert stage.status == "SKIPPED" and stage.completed_at is None and stage.skipped_at is not None
    event = db_session.query(models.ShedVisitEvent).filter_by(event_type="TEST_BEFORE_SKIPPED").one()
    assert event.shed_visit_id == visit_id and event.created_by == 99 and event.remarks == "urgent"
    assert event.event_data["stage"] == "TEST_BEFORE"
    assert event.event_data["channel"] == "OPERATIONS_DASHBOARD"
    assert event.event_data["previous_stage_status"] == "PENDING"
    assert mock_bldcms_client.visits.get(visit_id, []) == []          # no checksheet fabricated

    # Stays SKIPPED even if a Test Before checksheet is later submitted and reconciled.
    _submit(mock_bldcms_client, visit_id, "TEST_BEFORE", 179, SHIFT)
    stages = _reconcile(client, env.movement, visit_id)
    assert stages["TEST_BEFORE"]["current_status"] == "SKIPPED" and stages["TEST_BEFORE"]["reason"] == "SKIPPED"
    assert _stage(db_session, visit_id, "TEST_BEFORE").completed_at is None


def test_skip_is_idempotent(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    first = _skip(client, env.admin, visit_id).json()
    second = _skip(client, env.admin, visit_id, reason="again")
    assert second.status_code == 200 and second.json()["skipped_at"] == first["skipped_at"]
    assert second.json()["skip_reason"] is None
    assert db_session.query(models.ShedVisitEvent).filter_by(event_type="TEST_BEFORE_SKIPPED").count() == 1


def test_cannot_skip_a_completed_test_before_or_a_closed_visit(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env.movement)
    _submit(mock_bldcms_client, visit_id, "TEST_BEFORE", 179, SHIFT)
    assert _reconcile(client, env.movement, visit_id)["TEST_BEFORE"]["current_status"] == "COMPLETED"
    resp = _skip(client, env.admin, visit_id)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "STAGE_ALREADY_COMPLETED"

    closed = make_shed_visit(db_session, 777, "11111", status="CLOSED", departed_at=_now(), departure_source="DASHBOARD")
    make_stage(db_session, 7771, closed.id, "TEST_BEFORE", 1)
    resp = _skip(client, env.admin, closed.id)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "VISIT_CLOSED"
    assert _stage(db_session, closed.id, "TEST_BEFORE").status == "PENDING"


def test_internal_skip_requires_a_real_active_admin_actor(client, db_session, env, internal):
    visit_id = _shed_in(client, env.movement)
    url = f"/api/internal/shed-visits/{visit_id}/stages/test-before/skip"
    assert client.post(url, json={"actor_user_id": 1}).status_code == 401                     # no key
    assert client.post(url, json={"actor_user_id": 1}, headers=internal).status_code == 403  # Supervisor
    assert client.post(url, json={"actor_user_id": 12345}, headers=internal).status_code == 403
    make_user(db_session, 98, "OLDADMIN", "Old", "Admin", "hash", is_active=False)
    assert client.post(url, json={"actor_user_id": 98}, headers=internal).status_code == 403
    assert _stage(db_session, visit_id, "TEST_BEFORE").status == "PENDING"

    resp = client.post(url, json={"actor_user_id": 99, "reason": "from BL-DCMS"}, headers=internal)
    assert resp.status_code == 200, resp.text
    event = db_session.query(models.ShedVisitEvent).filter_by(event_type="TEST_BEFORE_SKIPPED").one()
    assert event.created_by == 99 and event.event_data["channel"] == "BLDCMS_DASHBOARD"


def test_test_after_can_never_be_skipped(client, db_session, env):
    # No API exists for it ...
    visit_id = _shed_in(client, env.admin)
    assert client.post(f"/api/shed-visits/{visit_id}/stages/test-after/skip", headers=env.admin).status_code in (404, 405)
    # ... and the database refuses it too (chk_stage_skipped, mirrored from migration 012).
    stage = _stage(db_session, visit_id, "TEST_AFTER")
    stage.status, stage.skipped_at = "SKIPPED", _now()
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# =================================================================== workflow gates ==

@pytest.mark.parametrize("tb_status", ["PENDING", "IN_PROGRESS"])
def test_start_schedule_is_blocked_until_test_before_is_satisfied(client, db_session, env, mock_bldcms_client, tb_status):
    visit_id = _shed_in(client, env.movement)
    if tb_status == "IN_PROGRESS":
        assert client.post(f"/api/shed-visits/{visit_id}/stages/test-before/start", headers=env.movement).status_code == 200
    for checksheet_status in (None, "DRAFT", "REJECTED"):
        if checksheet_status:
            _submit(mock_bldcms_client, visit_id, "TEST_BEFORE", 179, SHIFT, status=checksheet_status, checksheet_id=1)
            _reconcile(client, env.movement, visit_id)
        resp = _post(client, env.movement, visit_id, "start-schedule", "started_at", _now())
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "TEST_BEFORE_NOT_SATISFIED"
    assert _visit(db_session, visit_id).schedule_started_at is None


def test_start_schedule_cannot_be_dated_before_test_before_was_satisfied(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    assert _skip(client, env.admin, visit_id).status_code == 200
    resp = _post(client, env.movement, visit_id, "start-schedule", "started_at", _now() - timedelta(hours=1))
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "SCHEDULE_START_BEFORE_TEST_BEFORE"


def test_minor_normal_path_end_to_end(client, db_session, env, internal, mock_bldcms_client):
    visit_id = _shed_in(client, env.movement)
    tb_start = _now() - timedelta(hours=5)
    started = client.post(f"/api/internal/shed-visits/{visit_id}/stages/TEST_BEFORE/started",
                          json={"started_at": tb_start.isoformat(), "checksheet_id": 1}, headers=internal)
    assert started.status_code == 200 and started.json()["status"] == "IN_PROGRESS"

    _submit(mock_bldcms_client, visit_id, "TEST_BEFORE", 179, SHIFT)
    assert _reconcile(client, env.movement, visit_id)["TEST_BEFORE"]["current_status"] == "COMPLETED"

    assert _post(client, env.movement, visit_id, "start-schedule", "started_at", _now()).status_code == 200
    _submit(mock_bldcms_client, visit_id, "SCHEDULE_INSPECTION", 601, M1)
    assert _reconcile(client, env.movement, visit_id)["SCHEDULE_INSPECTION"]["current_status"] == "COMPLETED"

    # Ready is refused before the inspection is complete and before Test After.
    early = _post(client, env.movement, visit_id, "mark-ready", "ready_at", _now())
    assert early.status_code == 409 and early.json()["detail"]["code"] == "INSPECTION_NOT_COMPLETED"

    complete = _post(client, env.movement, visit_id, "complete-schedule", "completed_at", _now())
    assert complete.status_code == 200 and complete.json()["operational_phase"] == "INSPECTION_COMPLETED"
    visit = _visit(db_session, visit_id)
    assert visit.ready_at is None and visit.status == "IN_SHED" and visit.inspection_completed_at is not None

    blocked = _post(client, env.movement, visit_id, "mark-ready", "ready_at", _now())
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "TEST_AFTER_NOT_SATISFIED"
    out = client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": _now().isoformat()}, headers=env.movement)
    assert out.status_code == 409 and out.json()["detail"]["code"] == "SHED_OUT_BLOCKED"

    ta_start = _now()
    assert client.post(f"/api/internal/shed-visits/{visit_id}/stages/TEST_AFTER/started",
                       json={"started_at": ta_start.isoformat()}, headers=internal).status_code == 200
    _submit(mock_bldcms_client, visit_id, "TEST_AFTER", 179, SHIFT)
    assert _reconcile(client, env.movement, visit_id)["TEST_AFTER"]["current_status"] == "COMPLETED"

    not_ready = client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": _now().isoformat()}, headers=env.movement)
    assert not_ready.status_code == 409 and not_ready.json()["detail"]["code"] == "VISIT_NOT_READY"

    ready = _post(client, env.movement, visit_id, "mark-ready", "ready_at", _now())
    assert ready.status_code == 200 and ready.json()["operational_phase"] == "READY"
    assert db_session.query(models.ShedVisitEvent).filter_by(shed_visit_id=visit_id, event_type="MARK_READY").count() == 1

    out = client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": _now().isoformat()}, headers=env.movement)
    assert out.status_code == 200, out.text

    workflow = client.get(f"/api/shed-visits/{visit_id}/workflow", headers=env.admin).json()
    analytics = workflow["analytics"]
    assert analytics["test_before_skipped"] is False
    assert analytics["arrival_to_test_before_start_seconds"] is not None
    assert analytics["test_before_seconds"] >= 5 * 3600 - 5
    for key in ("test_before_to_schedule_start_seconds", "inspection_seconds", "inspection_to_test_after_start_seconds",
                "test_after_seconds", "test_after_to_ready_seconds", "ready_to_shed_out_seconds", "total_dwell_seconds"):
        assert analytics[key] is not None, key


def test_minor_skip_path_end_to_end(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env.movement)
    assert _skip(client, env.admin, visit_id).status_code == 200
    assert _post(client, env.movement, visit_id, "start-schedule", "started_at", _now()).status_code == 200

    _submit(mock_bldcms_client, visit_id, "SCHEDULE_INSPECTION", 601, M1)
    assert _reconcile(client, env.movement, visit_id)["SCHEDULE_INSPECTION"]["current_status"] == "COMPLETED"
    assert _post(client, env.movement, visit_id, "complete-schedule", "completed_at", _now()).status_code == 200
    _submit(mock_bldcms_client, visit_id, "TEST_AFTER", 179, SHIFT)
    assert _reconcile(client, env.movement, visit_id)["TEST_AFTER"]["current_status"] == "COMPLETED"
    assert _post(client, env.movement, visit_id, "mark-ready", "ready_at", _now()).status_code == 200

    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=env.movement).json()
    assert elig["eligible"] is True, elig
    out = client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": _now().isoformat()}, headers=env.movement)
    assert out.status_code == 200, out.text

    analytics = client.get(f"/api/shed-visits/{visit_id}/workflow", headers=env.admin).json()["analytics"]
    assert analytics["test_before_skipped"] is True and analytics["test_before_skipped_at"] is not None
    assert analytics["test_before_seconds"] is None                   # NULL, never 0
    assert analytics["arrival_to_test_before_start_seconds"] is None
    assert analytics["test_before_to_schedule_start_seconds"] is not None


def test_skipping_test_before_does_not_relax_test_after(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env.movement)
    _skip(client, env.admin, visit_id)
    _post(client, env.movement, visit_id, "start-schedule", "started_at", _now())
    _submit(mock_bldcms_client, visit_id, "SCHEDULE_INSPECTION", 601, M1)
    _reconcile(client, env.movement, visit_id)
    _post(client, env.movement, visit_id, "complete-schedule", "completed_at", _now())

    assert _reconcile(client, env.movement, visit_id)["TEST_AFTER"]["reason"] == "REQUIRED_CHECKSHEETS_PENDING"
    assert _post(client, env.movement, visit_id, "mark-ready", "ready_at", _now()).json()["detail"]["code"] == "TEST_AFTER_NOT_SATISFIED"
    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=env.movement).json()
    assert elig["eligible"] is False
    assert [b["stage_type"] for b in elig["stage_blockers"]] == ["TEST_AFTER"]
    assert [b["workflow_stage_type"] for b in elig["checksheet_blockers"]] == ["TEST_AFTER"]


def test_mark_ready_is_minor_only(client, db_session, env):
    visit = make_shed_visit(db_session, 555, "39160", schedule_family="MAJOR", schedule_variant="IOH",
                            arrival_at=_now() - timedelta(hours=3))
    resp = _post(client, env.movement, visit.id, "mark-ready", "ready_at", _now())
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "MARK_READY_NOT_APPLICABLE"


# ========================================================================== timing ==

def test_stage_start_is_recorded_once_and_never_reset(client, db_session, env, internal, mock_bldcms_client):
    visit_id = _shed_in(client, env.movement)
    url = f"/api/internal/shed-visits/{visit_id}/stages/TEST_BEFORE/started"
    first = _now() - timedelta(hours=2)
    assert client.post(url, json={"started_at": first.isoformat()}, headers=internal).status_code == 200
    # A second creation (e.g. after a rejection) or an explicit Start never moves it.
    assert client.post(url, json={"started_at": (_now() - timedelta(hours=3)).isoformat()}, headers=internal).status_code == 200
    assert client.post(url, json={"started_at": _now().isoformat()}, headers=internal).status_code == 200
    assert client.post(f"/api/shed-visits/{visit_id}/stages/test-before/start", headers=env.movement).status_code == 409
    stage = _stage(db_session, visit_id, "TEST_BEFORE")
    assert stage.started_at.replace(tzinfo=timezone.utc) == first

    # Submission, rejection, resubmission: completion follows the evidence, the start stays put.
    _submit(mock_bldcms_client, visit_id, "TEST_BEFORE", 179, SHIFT, status="REJECTED", checksheet_id=1)
    assert _reconcile(client, env.movement, visit_id)["TEST_BEFORE"]["current_status"] == "IN_PROGRESS"
    mock_bldcms_client.visits[visit_id][0]["status"] = "SUBMITTED"
    assert _reconcile(client, env.movement, visit_id)["TEST_BEFORE"]["current_status"] == "COMPLETED"
    stage = _stage(db_session, visit_id, "TEST_BEFORE")
    completed_at = stage.completed_at
    assert stage.started_at.replace(tzinfo=timezone.utc) == first
    _reconcile(client, env.movement, visit_id)
    assert _stage(db_session, visit_id, "TEST_BEFORE").completed_at == completed_at   # idempotent


def test_stage_start_rejects_out_of_range_times_other_stages_and_closed_visits(client, db_session, env, internal):
    visit_id = _shed_in(client, env.movement, arrival=_now() - timedelta(hours=1))
    base = f"/api/internal/shed-visits/{visit_id}/stages"
    assert client.post(f"{base}/TEST_BEFORE/started", json={"started_at": (_now() - timedelta(hours=2)).isoformat()},
                       headers=internal).status_code == 422
    assert client.post(f"{base}/TEST_BEFORE/started", json={"started_at": (_now() + timedelta(hours=2)).isoformat()},
                       headers=internal).status_code == 422
    assert client.post(f"{base}/SCHEDULE_INSPECTION/started", json={"started_at": _now().isoformat()},
                       headers=internal).status_code == 422
    assert _stage(db_session, visit_id, "TEST_BEFORE").status == "PENDING"

    closed = make_shed_visit(db_session, 778, "22222", status="CLOSED", departed_at=_now(), departure_source="DASHBOARD")
    make_stage(db_session, 7781, closed.id, "TEST_BEFORE", 1)
    resp = client.post(f"/api/internal/shed-visits/{closed.id}/stages/TEST_BEFORE/started",
                       json={"started_at": (_now() - timedelta(minutes=5)).isoformat()}, headers=internal)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "VISIT_CLOSED"


def test_analytics_formulas_exclude_test_before_and_test_after_from_inspection():
    t0 = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)
    h = lambda n: t0 + timedelta(hours=n)  # noqa: E731
    visit = SimpleNamespace(arrival_at=t0, schedule_started_at=h(3), inspection_completed_at=h(8),
                            ready_at=h(11), departed_at=h(12))
    stages = {
        "TEST_BEFORE": SimpleNamespace(status="COMPLETED", started_at=h(1), completed_at=h(2), skipped_at=None),
        "TEST_AFTER": SimpleNamespace(status="COMPLETED", started_at=h(9), completed_at=h(10), skipped_at=None),
    }
    a = compute_minor_workflow_analytics(visit, stages)
    assert (a.arrival_to_test_before_start_seconds, a.test_before_seconds, a.test_before_to_schedule_start_seconds) == (3600, 3600, 3600)
    assert a.inspection_seconds == 5 * 3600                      # 03:00 -> 08:00 only
    assert (a.inspection_to_test_after_start_seconds, a.test_after_seconds, a.test_after_to_ready_seconds) == (3600, 3600, 3600)
    assert (a.ready_to_shed_out_seconds, a.total_dwell_seconds) == (3600, 12 * 3600)

    skipped = dict(stages, TEST_BEFORE=SimpleNamespace(status="SKIPPED", started_at=None, completed_at=None, skipped_at=h(2)))
    s = compute_minor_workflow_analytics(visit, skipped)
    assert s.test_before_skipped and s.test_before_seconds is None and s.test_before_to_schedule_start_seconds == 3600

    unrecorded = dict(stages, TEST_BEFORE=SimpleNamespace(status="COMPLETED", started_at=None, completed_at=h(2), skipped_at=None))
    assert compute_minor_workflow_analytics(visit, unrecorded).test_before_seconds is None


# ================================================================ pending semantics ==

def test_skipped_test_before_shows_as_skipped_and_is_not_pending(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    _skip(client, env.admin, visit_id)
    group = next(g for g in client.get(PANEL, headers=env.admin).json()["groups"] if g["shed_visit_id"] == visit_id)
    tb = next(r for r in group["requirements"] if r["workflow_stage_type"] == "TEST_BEFORE")
    assert tb["display_status"] == "Skipped"
    assert group["counts"]["skipped"] == 1
    assert group["test_before"]["stage_status"] == "SKIPPED" and group["test_before"]["can_skip"] is False
    assert group["test_before"]["skipped_by"] == 99

    shift = next(g for g in client.get(PANEL, headers=env.movement).json()["groups"] if g["shed_visit_id"] == visit_id)
    assert next(r for r in shift["requirements"] if r["workflow_stage_type"] == "TEST_BEFORE")["display_status"] == "Skipped"


def test_test_after_awaits_the_inspection_then_becomes_pending(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env.movement)

    def ta_row():
        group = next(g for g in client.get(PANEL, headers=env.admin).json()["groups"] if g["shed_visit_id"] == visit_id)
        return group, next(r for r in group["requirements"] if r["workflow_stage_type"] == "TEST_AFTER")

    group, ta = ta_row()
    assert ta["display_status"] == "Awaiting Inspection" and ta["is_required"] is True
    assert group["counts"]["awaiting_inspection"] == 1 and group["inspection_completed"] is False
    pending_before = group["counts"]["pending"]

    _skip(client, env.admin, visit_id)
    _post(client, env.movement, visit_id, "start-schedule", "started_at", _now())
    _post(client, env.movement, visit_id, "complete-schedule", "completed_at", _now())

    group, ta = ta_row()
    assert ta["display_status"] == "Pending"
    assert group["counts"]["awaiting_inspection"] == 0 and group["inspection_completed"] is True
    assert group["counts"]["pending"] == pending_before + 1 - 1   # TA now pending; TB no longer (skipped)


def test_active_visit_feed_exposes_test_before_and_inspection_state(client, db_session, env, internal):
    visit_id = _shed_in(client, env.movement)
    feed = lambda: next(v for v in client.get("/api/internal/shed-visits/active", headers=internal).json()  # noqa: E731
                        if v["shed_visit_id"] == visit_id)
    row = feed()
    assert (row["test_before_status"], row["test_before_skipped"], row["inspection_completed"]) == ("PENDING", False, False)
    _skip(client, env.admin, visit_id)
    _post(client, env.movement, visit_id, "start-schedule", "started_at", _now())
    _post(client, env.movement, visit_id, "complete-schedule", "completed_at", _now())
    row = feed()
    assert (row["test_before_status"], row["test_before_skipped"], row["inspection_completed"]) == ("SKIPPED", True, True)
    assert row["inspection_completed_at"] is not None


# =============================================== post-deployment decisions (grandfathering etc.) ==

def _legacy_start(db_session, visit_id, started_at=None):
    """What a schedule started under the previous workflow looks like: schedule_started_at set while
    Test Before was not completed/skipped at that moment. Written directly, as the old code did -
    the current API can no longer produce this state."""
    visit = db_session.get(models.ShedVisit, visit_id)
    visit.schedule_started_at = started_at or (visit.arrival_at.replace(tzinfo=timezone.utc) + timedelta(hours=1))
    db_session.commit()
    return visit


def _tb_snapshot(db_session, visit_id):
    tb = _stage(db_session, visit_id, "TEST_BEFORE")
    return (tb.status, tb.started_at, tb.completed_at, tb.skipped_at, tb.skipped_by, tb.skip_reason)


def test_grandfathered_legacy_start_proceeds_without_a_retroactive_test_before(
    client, db_session, env, internal, mock_bldcms_client
):
    visit_id = _shed_in(client, env.movement)
    _legacy_start(db_session, visit_id)
    before = _tb_snapshot(db_session, visit_id)
    assert before[0] == "PENDING"

    feed = next(v for v in client.get("/api/internal/shed-visits/active", headers=internal).json()
                if v["shed_visit_id"] == visit_id)
    assert feed["test_before_legacy_waived"] is True and feed["test_before_skipped"] is False

    group = next(g for g in client.get(PANEL, headers=env.admin).json()["groups"] if g["shed_visit_id"] == visit_id)
    tb_row = next(r for r in group["requirements"] if r["workflow_stage_type"] == "TEST_BEFORE")
    assert tb_row["display_status"] == "Not Required (Legacy Start)"
    assert group["counts"]["legacy_waived"] == 1
    assert group["test_before"]["legacy_waived"] is True and group["test_before"]["can_skip"] is False
    # No forced skip after the start either.
    skip = _skip(client, env.admin, visit_id)
    assert skip.status_code == 409 and skip.json()["detail"]["code"] == "SCHEDULE_ALREADY_STARTED"

    _submit(mock_bldcms_client, visit_id, "SCHEDULE_INSPECTION", 601, M1)
    stages = _reconcile(client, env.movement, visit_id)
    assert stages["TEST_BEFORE"]["current_status"] == "PENDING"
    assert stages["SCHEDULE_INSPECTION"]["current_status"] == "COMPLETED"
    assert _post(client, env.movement, visit_id, "complete-schedule", "completed_at", _now()).status_code == 200
    _submit(mock_bldcms_client, visit_id, "TEST_AFTER", 179, SHIFT)
    assert _reconcile(client, env.movement, visit_id)["TEST_AFTER"]["current_status"] == "COMPLETED"

    # Mark Ready stays explicit and required.
    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=env.movement).json()
    assert elig["eligible"] is True, elig
    not_ready = client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": _now().isoformat()}, headers=env.movement)
    assert not_ready.status_code == 409 and not_ready.json()["detail"]["code"] == "VISIT_NOT_READY"
    assert _post(client, env.movement, visit_id, "mark-ready", "ready_at", _now()).status_code == 200
    out = client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": _now().isoformat()}, headers=env.movement)
    assert out.status_code == 200, out.text

    # Nothing about Test Before was fabricated, and its analytics stay unknown.
    assert _tb_snapshot(db_session, visit_id) == before
    assert db_session.query(models.ShedVisitEvent).filter_by(shed_visit_id=visit_id, event_type="TEST_BEFORE_SKIPPED").count() == 0
    analytics = client.get(f"/api/shed-visits/{visit_id}/workflow", headers=env.admin).json()
    assert analytics["test_before_legacy_waived"] is True
    assert analytics["analytics"]["test_before_seconds"] is None
    assert analytics["analytics"]["test_before_to_schedule_start_seconds"] is None
    assert analytics["analytics"]["inspection_seconds"] is not None


def test_a_test_before_completed_after_a_legacy_start_is_still_grandfathered(client, db_session, env):
    visit_id = _shed_in(client, env.movement)
    visit = _legacy_start(db_session, visit_id)
    tb = _stage(db_session, visit_id, "TEST_BEFORE")
    tb.status, tb.started_at, tb.completed_at = "COMPLETED", _now() - timedelta(minutes=5), _now()
    db_session.commit()

    from app.services.workflow_common import test_before_legacy_waived
    assert test_before_legacy_waived(_visit(db_session, visit_id), _stage(db_session, visit_id, "TEST_BEFORE")) is True
    # The later completion does not become a TB -> Schedule Start span (it would be negative).
    analytics = client.get(f"/api/shed-visits/{visit_id}/workflow", headers=env.admin).json()["analytics"]
    assert analytics["test_before_to_schedule_start_seconds"] is None
    assert visit.schedule_started_at is not None


def test_grandfathering_never_applies_to_a_visit_started_through_the_gate(client, db_session, env, internal):
    from app.services.workflow_common import test_before_legacy_waived

    # Not started: no waiver, and the new gate still refuses Start Schedule.
    visit_id = _shed_in(client, env.movement)
    assert test_before_legacy_waived(_visit(db_session, visit_id), _stage(db_session, visit_id, "TEST_BEFORE")) is False
    resp = _post(client, env.movement, visit_id, "start-schedule", "started_at", _now())
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "TEST_BEFORE_NOT_SATISFIED"

    # Started through the gate (after a skip): never waived.
    assert _skip(client, env.admin, visit_id).status_code == 200
    assert _post(client, env.movement, visit_id, "start-schedule", "started_at", _now()).status_code == 200
    assert test_before_legacy_waived(_visit(db_session, visit_id), _stage(db_session, visit_id, "TEST_BEFORE")) is False
    feed = next(v for v in client.get("/api/internal/shed-visits/active", headers=internal).json()
                if v["shed_visit_id"] == visit_id)
    assert feed["test_before_legacy_waived"] is False


def test_test_before_completed_with_outstanding_findings_can_start_the_schedule(
    client, db_session, env, mock_bldcms_client
):
    from tests.conftest import make_assignment, make_booking

    visit_id = _shed_in(client, env.movement)
    finding = make_booking(db_session, 5100, visit_id, booking_source="TEST_BEFORE", status="OPEN")
    make_assignment(db_session, 5100, finding.id, section_id=M1, status="OPEN")

    _submit(mock_bldcms_client, visit_id, "TEST_BEFORE", 179, SHIFT)
    tb = _reconcile(client, env.movement, visit_id)["TEST_BEFORE"]
    assert tb["current_status"] == "COMPLETED" and tb["bookings_ready"] is False

    assert _post(client, env.movement, visit_id, "start-schedule", "started_at", _now()).status_code == 200

    # The finding is neither lost nor auto-attended, and still holds Shed Out.
    db_session.expire_all()
    assert db_session.get(models.Booking, 5100).status == "OPEN"
    assert db_session.get(models.BookingSectionAssignment, 5100).status == "OPEN"
    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=env.movement).json()
    assert [b["booking_id"] for b in elig["booking_blockers"]] == [5100]


def test_test_after_findings_still_hold_test_after_open(client, db_session, env, mock_bldcms_client):
    from tests.conftest import make_assignment, make_booking

    visit_id = _shed_in(client, env.movement)
    _skip(client, env.admin, visit_id)
    _post(client, env.movement, visit_id, "start-schedule", "started_at", _now())
    _submit(mock_bldcms_client, visit_id, "SCHEDULE_INSPECTION", 601, M1)
    _reconcile(client, env.movement, visit_id)
    _post(client, env.movement, visit_id, "complete-schedule", "completed_at", _now())
    _submit(mock_bldcms_client, visit_id, "TEST_AFTER", 179, SHIFT)
    finding = make_booking(db_session, 5200, visit_id, booking_source="TEST_AFTER", status="ATTENDED")
    make_assignment(db_session, 5200, finding.id, section_id=M1, status="OPEN")   # assignment is authoritative

    ta = _reconcile(client, env.movement, visit_id)["TEST_AFTER"]
    assert ta["current_status"] != "COMPLETED" and ta["reason"] == "BOOKINGS_PENDING"


def test_conventional_minor_without_test_before_needs_an_admin_skip_to_start(
    client, db_session, env, mock_loco_client
):
    # WAP4 (Conventional) has no Test Before template configured anywhere in this fixture.
    mock_loco_client.add_locomotive("22560", loco_type="WAP4")
    visit_id = _shed_in(client, env.movement, loco="22560", variant="IA0")
    assert _stage(db_session, visit_id, "TEST_BEFORE").status == "PENDING"

    resp = _post(client, env.movement, visit_id, "start-schedule", "started_at", _now())
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "TEST_BEFORE_NOT_SATISFIED"

    # Never auto-satisfied, and only an Admin may skip.
    assert _skip(client, env.movement, visit_id).status_code == 403
    assert _skip(client, env.m1, visit_id).status_code == 403
    assert _post(client, env.movement, visit_id, "start-schedule", "started_at", _now()).status_code == 409

    assert _skip(client, env.admin, visit_id, reason="Conventional Test Before template not yet available").status_code == 200
    assert _post(client, env.movement, visit_id, "start-schedule", "started_at", _now()).status_code == 200
    tb = _stage(db_session, visit_id, "TEST_BEFORE")
    assert tb.status == "SKIPPED" and tb.completed_at is None and tb.started_at is None
    assert db_session.query(models.ShedVisitEvent).filter_by(shed_visit_id=visit_id, event_type="TEST_BEFORE_SKIPPED").count() == 1
    analytics = client.get(f"/api/shed-visits/{visit_id}/workflow", headers=env.admin).json()["analytics"]
    assert analytics["test_before_skipped"] is True and analytics["test_before_seconds"] is None
