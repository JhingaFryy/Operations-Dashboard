"""BL-DCMS Pending Checksheets' Admin retry for a visit with no work package.

Shed In generates every visit's package automatically and best-effort; when that attempt cannot
complete (an unreachable resolver, or - as on 2026-09-18 - a locomotive model BL-DCMS did not yet
recognise), the visit stays package-less and the panel says so. This internal route is how an
Admin retries from BL-DCMS, mirroring the Skip Test Before route beside it: the internal key alone
authorises nothing, the named actor is re-verified here as an active Admin, and Operations
Dashboard picks the Minor or Major generator from the visit's own schedule family.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.core import dependencies as dependencies_module
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_shed_visit

INTERNAL_KEY = "test-internal-key-not-for-production"
SHIFT, M1 = 18, 9


class _StubSettings:
    operations_internal_api_key = INTERNAL_KEY


@pytest.fixture()
def internal(monkeypatch):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings())
    return {"X-Internal-API-Key": INTERNAL_KEY}


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=SHIFT, code="SHIFT", name="SHIFT")
    ensure_section(db_session, id=M1, code="M1-HR", name="M1-HR")
    mock_loco_client.add_locomotive("41771", loco_type="WAG9H")
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(
            technology="WAG9H", schedule_family="MINOR", schedule_variant="IA0",
            workflow_stage_type=stage, template_id=179,
            section_id=SHIFT if stage != "SCHEDULE_INSPECTION" else M1)
    mock_bldcms_client.add_major_requirement(loco_type="WAG9H", template_id=85, section_id=M1)
    make_movement_supervisor_headers(db_session)  # user 1, the Admin re-verified below
    admin = db_session.get(models.User, 1)
    admin.role = "Admin"
    db_session.commit()
    return admin


def _visit(db_session, visit_id, family="MINOR", variant="IA0"):
    return make_shed_visit(db_session, visit_id, "41771", schedule_family=family,
                           schedule_variant=variant,
                           arrival_at=datetime.now(timezone.utc) - timedelta(hours=6))


def _generate(client, internal, visit_id, actor_user_id=1):
    return client.post(f"/api/internal/shed-visits/{visit_id}/checksheet-work-package/generate",
                       json={"actor_user_id": actor_user_id}, headers=internal)


def _requirements(db_session, visit_id):
    package = (db_session.query(models.ShedVisitChecksheetPackage)
               .filter_by(shed_visit_id=visit_id).one_or_none())
    return list(package.requirements) if package else []


def test_a_minor_visit_without_a_package_gets_one(client, db_session, env, internal):
    _visit(db_session, 501)

    resp = _generate(client, internal, 501)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schedule_family"] == "MINOR" and body["generated"] is True
    assert body["requirement_count"] == len(_requirements(db_session, 501)) > 0


def test_a_major_visit_without_a_package_gets_one(client, db_session, env, internal):
    _visit(db_session, 502, family="MAJOR", variant="TOH")

    resp = _generate(client, internal, 502)

    assert resp.status_code == 200, resp.text
    assert resp.json()["schedule_family"] == "MAJOR"
    assert {r.template_id for r in _requirements(db_session, 502)} == {85}


def test_the_retry_is_idempotent_and_never_rewrites_an_existing_package(client, db_session, env,
                                                                        internal):
    _visit(db_session, 503, family="MAJOR", variant="TOH")
    assert _generate(client, internal, 503).status_code == 200
    before = {(r.id, r.template_id) for r in _requirements(db_session, 503)}

    resp = _generate(client, internal, 503)

    assert resp.status_code == 200, resp.text
    assert {(r.id, r.template_id) for r in _requirements(db_session, 503)} == before


def test_generation_never_touches_the_visits_workflow_state(client, db_session, env, internal):
    """The production repair of 2026-09-18: visits whose Test Before was already Admin-skipped and
    whose schedule had started kept every one of those facts."""
    visit = _visit(db_session, 504)
    visit.schedule_started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    stage = models.ShedVisitStage(
        shed_visit_id=504, stage_type="TEST_BEFORE", stage_order=1, status="SKIPPED",
        skipped_at=datetime.now(timezone.utc) - timedelta(hours=2), skipped_by=1,
        skip_reason="For testing", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc))
    db_session.add(stage)
    db_session.commit()
    before = (visit.arrival_at, visit.schedule_started_at, visit.status,
              stage.status, stage.skipped_at, stage.skipped_by, stage.skip_reason)

    assert _generate(client, internal, 504).status_code == 200

    db_session.expire_all()
    visit, stage = db_session.get(models.ShedVisit, 504), db_session.query(
        models.ShedVisitStage).filter_by(shed_visit_id=504, stage_type="TEST_BEFORE").one()
    assert (visit.arrival_at, visit.schedule_started_at, visit.status, stage.status,
            stage.skipped_at, stage.skipped_by, stage.skip_reason) == before
    assert db_session.query(models.ShedVisitEvent).filter_by(shed_visit_id=504).count() == 0


def test_a_non_admin_actor_is_refused(client, db_session, env, internal):
    _visit(db_session, 505)
    env.role = "Supervisor"
    db_session.commit()

    resp = _generate(client, internal, 505)

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "ADMIN_REQUIRED"
    assert _requirements(db_session, 505) == []


def test_an_unknown_actor_is_refused(client, db_session, env, internal):
    _visit(db_session, 506)

    resp = _generate(client, internal, 506, actor_user_id=424242)

    assert resp.status_code == 403
    assert _requirements(db_session, 506) == []


def test_the_internal_key_is_required(client, db_session, env):
    _visit(db_session, 507)

    resp = client.post("/api/internal/shed-visits/507/checksheet-work-package/generate",
                       json={"actor_user_id": 1})

    assert resp.status_code in (401, 403)
    assert _requirements(db_session, 507) == []


def test_a_resolver_failure_is_reported_not_swallowed(client, db_session, env, internal,
                                                      mock_bldcms_client):
    _visit(db_session, 508, family="MAJOR", variant="TOH")
    mock_bldcms_client.unavailable = True

    resp = _generate(client, internal, 508)

    assert resp.status_code >= 400
    assert _requirements(db_session, 508) == []
