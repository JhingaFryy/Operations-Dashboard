"""Every Shed In must leave the visit with its own checksheet work package.

Shed In snapshots the visit's requirements automatically (shed_visit_service.shed_in ->
checksheet_work_package_service.generate_requirements_snapshot), best-effort: the physical fact of
a locomotive entering the shed stays recordable even when BL-DCMS or Loco Master is unreachable,
and the visit then carries no package - a state Pending Checksheets reports explicitly rather than
as "nothing pending". These tests pin both halves: the normal path always produces a package, and
the failure path produces a RECOVERABLE visit - which for MAJOR it previously was not, because
manual generation was reachable only through the Minor-only route.

The real-world defect these cover (2026-09-18): BL-DCMS did not recognise Loco Master's "WAG9H" /
"WAP5AB" spellings, answered 400 on every resolution for them, and 125 of the shed's 238
locomotives therefore shed in with no package at all. That root cause is a BL-DCMS mapping and is
tested there (backend/tests/test_loco_master_vocabulary.py); what belongs here is the Dashboard
behaviour around it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from tests.conftest import (
    ensure_section,
    make_movement_supervisor_headers,
    make_true_admin_headers,
)

SHIFT, M1 = 18, 9
MINOR_VARIANTS = ("IA", "IA0", "IB", "IC", "IC0")
MAJOR_VARIANTS = ("IOH", "TOH")


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=SHIFT, code="SHIFT", name="SHIFT")
    ensure_section(db_session, id=M1, code="M1-HR", name="M1-HR")
    # Loco Master's own vocabulary, verbatim - the Dashboard never translates it.
    mock_loco_client.add_locomotive("41771", loco_type="WAG9H")
    mock_loco_client.add_locomotive("43710", loco_type="WAG9H")
    mock_loco_client.add_locomotive("39078", loco_type="WAP7")
    for variant in MINOR_VARIANTS:
        for stage, applicability_id in (("TEST_BEFORE", 1), ("SCHEDULE_INSPECTION", 2), ("TEST_AFTER", 3)):
            mock_bldcms_client.add_applicability(
                technology="WAG9H", schedule_family="MINOR", schedule_variant=variant,
                workflow_stage_type=stage, template_id=170 + applicability_id,
                section_id=SHIFT if stage != "SCHEDULE_INSPECTION" else M1)
    mock_bldcms_client.add_major_requirement(loco_type="WAG9H", template_id=85, section_id=M1,
                                             template_name="Bogie Frame-1", equipment_id=60)
    mock_bldcms_client.add_major_requirement(loco_type="WAG9H", template_id=86, section_id=M1,
                                             template_name="Bogie Frame-2", equipment_id=61)
    return {"movement": make_movement_supervisor_headers(db_session),
            "admin": make_true_admin_headers(db_session)}


def _shed_in(client, headers, loco="41771", family="MINOR", variant="IA", arrival=None):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": (arrival or _now() - timedelta(hours=6)).isoformat(),
        "schedule_family": family, "schedule_variant": variant,
        "arrival_condition": "WORKING", "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _package(db_session, visit_id):
    db_session.expire_all()
    return (db_session.query(models.ShedVisitChecksheetPackage)
            .filter_by(shed_visit_id=visit_id).one_or_none())


def _requirements(db_session, visit_id):
    package = _package(db_session, visit_id)
    return list(package.requirements) if package else []


# ------------------------------------------------------------------ the normal path ----------

def test_first_shed_in_on_an_empty_operational_database_generates_a_package(client, db_session, env):
    """Nothing in the database assumes an earlier visit, package or stage row exists."""
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0

    visit_id = _shed_in(client, env["movement"])

    package = _package(db_session, visit_id)
    assert package is not None and package.generated_at is not None
    assert len(package.requirements) > 0


def test_second_shed_in_generates_its_own_independent_package(client, db_session, env):
    first = _shed_in(client, env["movement"], loco="41771")
    second = _shed_in(client, env["movement"], loco="43710")

    first_package, second_package = _package(db_session, first), _package(db_session, second)

    assert first_package.id != second_package.id
    assert {r.package_id for r in first_package.requirements} == {first_package.id}
    assert {r.package_id for r in second_package.requirements} == {second_package.id}


@pytest.mark.parametrize("variant", MINOR_VARIANTS)
def test_every_minor_variant_generates_its_package(client, db_session, env, variant):
    visit_id = _shed_in(client, env["movement"], variant=variant)

    requirements = _requirements(db_session, visit_id)

    assert requirements, f"no requirements generated for MINOR/{variant}"
    assert {r.workflow_stage_type for r in requirements} == {
        "TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"}


@pytest.mark.parametrize("variant", MAJOR_VARIANTS)
def test_every_major_variant_generates_its_package(client, db_session, env, variant):
    visit_id = _shed_in(client, env["movement"], family="MAJOR", variant=variant)

    requirements = _requirements(db_session, visit_id)

    assert {r.template_id for r in requirements} == {85, 86}
    # Major is equipment-wise and stageless.
    assert {r.workflow_stage_type for r in requirements} == {None}
    assert {r.applicability_id for r in requirements} == {None}


def test_the_locomotives_own_loco_type_is_what_reaches_the_resolver(client, db_session, env,
                                                                    mock_bldcms_client):
    """Loco Master's spelling is forwarded verbatim; the Dashboard never invents a technology."""
    seen = []
    original = mock_bldcms_client.resolve_major_requirements
    mock_bldcms_client.resolve_major_requirements = lambda loco_type: (
        seen.append(loco_type) or original(loco_type))

    _shed_in(client, env["movement"], loco="41771", family="MAJOR", variant="TOH")

    assert seen == ["WAG9H"]


def test_the_snapshot_carries_the_resolved_requirement_content(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], family="MAJOR", variant="TOH")

    rows = {r.template_id: r for r in _requirements(db_session, visit_id)}

    assert rows[85].template_name_snapshot == "Bogie Frame-1"
    assert rows[85].section_id_snapshot == M1
    assert rows[85].equipment_id_snapshot == 60
    assert rows[85].is_required is True and rows[85].is_active is True


# ------------------------------------------------------------- the fail-closed path -----------

def test_a_resolution_failure_leaves_a_recoverable_visit_not_a_silent_one(client, db_session, env,
                                                                          mock_bldcms_client):
    """Shed In still records the locomotive, and the missing package is explicit, never implied."""
    mock_bldcms_client.unavailable = True

    visit_id = _shed_in(client, env["movement"], family="MAJOR", variant="TOH")

    assert db_session.get(models.ShedVisit, visit_id) is not None
    assert _package(db_session, visit_id) is None
    body = client.get(f"/api/shed-visits/{visit_id}/checksheet-work-package",
                      headers=env["movement"]).json()
    assert body["generated"] is False


def test_a_major_visit_that_missed_its_package_can_be_repaired(client, db_session, env,
                                                               mock_bldcms_client):
    """The regression this route exists for: manual generation used to be MINOR-only, so a Major
    visit whose automatic snapshot failed could never get one."""
    mock_bldcms_client.unavailable = True
    visit_id = _shed_in(client, env["movement"], family="MAJOR", variant="TOH")
    assert _package(db_session, visit_id) is None
    mock_bldcms_client.unavailable = False

    resp = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/major",
                       headers=env["movement"])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"shed_visit_id": visit_id, "generated": True, "requirement_count": 2}
    assert {r.template_id for r in _requirements(db_session, visit_id)} == {85, 86}


def test_repairing_an_already_generated_major_package_changes_nothing(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], family="MAJOR", variant="TOH")
    before = {(r.id, r.template_id) for r in _requirements(db_session, visit_id)}
    generated_at = _package(db_session, visit_id).generated_at

    resp = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/major",
                       headers=env["movement"])

    assert resp.status_code == 200, resp.text
    assert {(r.id, r.template_id) for r in _requirements(db_session, visit_id)} == before
    assert _package(db_session, visit_id).generated_at == generated_at


def test_a_minor_visit_is_refused_by_the_major_repair_route(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], family="MINOR", variant="IA")

    resp = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/major",
                       headers=env["movement"])

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "NOT_A_MAJOR_VISIT"


def test_an_earlier_visits_package_is_never_reused_or_rewritten(client, db_session, env):
    """A new visit's package is its own frozen snapshot; the previous visit's stays exactly as it
    was, even for the same locomotive."""
    first = _shed_in(client, env["movement"], loco="41771", family="MAJOR", variant="TOH")
    frozen = {(r.id, r.template_id, r.is_active) for r in _requirements(db_session, first)}
    db_session.get(models.ShedVisit, first).status = "CLOSED"
    db_session.get(models.ShedVisit, first).departed_at = _now()
    db_session.get(models.ShedVisit, first).departure_source = "DASHBOARD"
    db_session.commit()

    second = _shed_in(client, env["movement"], loco="41771", family="MAJOR", variant="TOH")

    assert _package(db_session, second).id != _package(db_session, first).id
    assert {(r.id, r.template_id, r.is_active) for r in _requirements(db_session, first)} == frozen
