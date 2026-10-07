"""A MINOR visit's work package is frozen at Shed In and is its authoritative scope.

Configuration added or changed in BL-DCMS afterwards - a new section (M6-HR CONVENTIONAL), a
template deactivated by hand (the 2026-09-22 template-324 incident) - never changes an existing
visit: not its package, not its requirement identities, not what refresh does. BL-DCMS reads the
frozen scope from GET /api/internal/shed-visits/{id}/checksheet-work-package/frozen. New visits are
generated from the current configuration and frozen in turn.
"""

from datetime import datetime, timezone

import pytest

from app.core import dependencies as dependencies_module
from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers
from tests.test_minor_inspection_m35_conventional_package import (
    AUX, CP, M1, M2, MINOR, SHIFT, TM, _configure, _row)
from tests.test_minor_inspection_m4hr_conventional_package import M4, _configure_m4

INTERNAL_KEY = "test-internal-key-not-for-production"
ARRIVAL = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
M6 = 11
IA_IC = ("IA", "IA0", "IC", "IC0")
M6_SLOTS = [(1, "FUSE", IA_IC), (2, "SB_1_2", IA_IC), (3, "SB_CAB", IA_IC), (4, "HEAD_LIGHT_DC_DC_CONVERTER", IA_IC),
            (5, "CAB_CORRIDOR_LIGHT", IA_IC), (7, "PNEUMATIC_VALVE", IA_IC), (8, "CAB_WORK", MINOR)]
BEFORE = {"IA": 51, "IA0": 53, "IB": 45, "IC": 53, "IC0": 53}
AFTER = {"IA": 58, "IA0": 60, "IB": 46, "IC": 60, "IC0": 60}
LOCOS = iter(f"2{n:04d}" for n in range(2400, 2600))


class _StubSettings:
    operations_internal_api_key = INTERNAL_KEY


@pytest.fixture()
def internal(monkeypatch):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings())
    return {"X-Internal-API-Key": INTERNAL_KEY}


@pytest.fixture()
def env(db_session, mock_loco_client):
    for sid, code in ((SHIFT, "SHIFT"), (M1, "M1-HR"), (M2, "M2-HR"), (AUX, "M35-Aux"), (CP, "M35-CP"),
                      (TM, "M35-TM"), (M4, "M4-HR"), (M6, "M6-HR")):
        ensure_section(db_session, id=sid, code=code, name=code)
    return {"headers": make_movement_supervisor_headers(db_session), "locos": mock_loco_client}


def _configure_m6(mock, variant):
    """What BL-DCMS resolves for a WAP-4 after the M6-HR CONVENTIONAL seed (TM DDS held: no row)."""
    target = mock.applicability.setdefault(("WAP4", "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for order, code, variants in M6_SLOTS:
        if variant in variants:
            target.append(_row(variant, 8000 + order, 400 + order, code, code.replace("_", " "), M6, "M6-HR", order))


def _baseline(mock, variant):
    _configure(mock, variant, after_049=True)
    _configure_m4(mock, variant)


def _shed_in(client, env, variant):
    loco = next(LOCOS)
    env["locos"].add_locomotive(loco, loco_type="WAP4")
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []}, headers=env["headers"])
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _fingerprint(db_session, visit_id):
    """Every stored column of every requirement row, plus the package's own frozen fields."""
    db_session.expire_all()
    package = db_session.query(models.ShedVisitChecksheetPackage).filter_by(shed_visit_id=visit_id).one()
    cols = [c.key for c in models.ShedVisitChecksheetRequirement.__table__.columns]
    rows = sorted(tuple(getattr(r, c) for c in cols) for r in package.requirements)
    return (package.id, package.generated_at, package.minor_inspection_configuration_complete, rows)


def _frozen(client, internal, visit_id):
    resp = client.get(f"/api/internal/shed-visits/{visit_id}/checksheet-work-package/frozen", headers=internal)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sections(body):
    return {r["section_id"] for r in body["requirements"]}


# ============================================================================ the endpoint ==

def test_frozen_endpoint_returns_the_package_with_per_visit_overrides(client, db_session, env, internal,
                                                                      mock_bldcms_client):
    _baseline(mock_bldcms_client, "IC")
    visit_id = _shed_in(client, env, "IC")
    row = db_session.query(models.ShedVisitChecksheetRequirement).first()
    row.is_active = False
    db_session.commit()

    body = _frozen(client, internal, visit_id)

    assert body["frozen"] is True and body["schedule_family"] == "MINOR" and len(body["requirements"]) == 53
    pinned = next(r for r in body["requirements"] if r["requirement_id"] == row.id)
    assert pinned["is_active"] is False and pinned["template_id"] == row.template_id
    assert pinned["minor_inspection_equipment_id"] == row.minor_inspection_equipment_id


def test_frozen_endpoint_without_a_package_and_for_an_unknown_visit(client, db_session, env, internal):
    visit_id = _shed_in(client, env, "IC")                     # nothing configured: no package
    body = _frozen(client, internal, visit_id)
    assert body["frozen"] is False and body["requirements"] == []
    assert client.get("/api/internal/shed-visits/999999/checksheet-work-package/frozen", headers=internal).status_code == 404


def test_frozen_endpoint_requires_the_internal_key(client, db_session, env, internal):
    visit_id = _shed_in(client, env, "IC")
    assert client.get(f"/api/internal/shed-visits/{visit_id}/checksheet-work-package/frozen").status_code in (401, 403)


# ============================================================ OLD VISIT: M6-HR added later ==

@pytest.mark.parametrize("variant", MINOR)
def test_old_visit_never_acquires_m6hr_and_its_package_is_byte_identical(client, db_session, env, internal,
                                                                          mock_bldcms_client, variant):
    _baseline(mock_bldcms_client, variant)
    old = _shed_in(client, env, variant)
    fingerprint = _fingerprint(db_session, old)
    frozen_before = _frozen(client, internal, old)
    assert len(frozen_before["requirements"]) == BEFORE[variant] and M6 not in _sections(frozen_before)

    _configure_m6(mock_bldcms_client, variant)                 # the M6-HR seed lands while the visit is open

    headers = env["headers"]
    for path in (f"/api/shed-visits/{old}/checksheet-work-package", f"/api/shed-visits/{old}/workflow",
                 f"/api/shed-visits/{old}/shed-out-eligibility", f"/api/shed-visits/{old}/checksheet-requirement-progress"):
        assert client.get(path, headers=headers).status_code == 200, path
    assert client.post(f"/api/shed-visits/{old}/checksheet-work-package", headers=headers).status_code == 200
    refreshed = client.post(f"/api/shed-visits/{old}/checksheet-work-package/refresh", headers=headers).json()
    assert refreshed["added"] == [] and refreshed["retained"] == BEFORE[variant]

    assert _fingerprint(db_session, old) == fingerprint
    assert _frozen(client, internal, old) == frozen_before
    assert db_session.query(models.ShedVisitChecksheetRequirement).filter_by(section_id_snapshot=M6).count() == 0


# ============================================================= NEW VISIT: M6-HR included ==

@pytest.mark.parametrize("variant,added", [("IA", 7), ("IA0", 7), ("IB", 1), ("IC", 7), ("IC0", 7)])
def test_new_visit_receives_m6hr(client, db_session, env, internal, mock_bldcms_client, variant, added):
    _baseline(mock_bldcms_client, variant)
    _configure_m6(mock_bldcms_client, variant)

    body = _frozen(client, internal, _shed_in(client, env, variant))

    assert len(body["requirements"]) == AFTER[variant]
    m6 = [r for r in body["requirements"] if r["section_id"] == M6]
    assert len(m6) == added and all(r["is_required"] and r["is_active"] for r in m6)
    assert {r["workflow_stage_type"] for r in body["requirements"]} == {"SCHEDULE_INSPECTION"}   # still no TB / TA


# ============================================ CONFIG CHANGE AFTER SHED-IN: template deactivated ==

def test_a_template_deactivated_after_shed_in_stays_pinned_to_the_frozen_visit_only(client, db_session, env, internal,
                                                                                    mock_bldcms_client):
    """The template-324 shape: a frozen visit requires a template that is later deactivated."""
    _baseline(mock_bldcms_client, "IB")
    old = _shed_in(client, env, "IB")
    rows = mock_bldcms_client.applicability[("WAP4", "MINOR", "IB", "SCHEDULE_INSPECTION")]
    victim = next(r for r in rows if r["section_name"] == "M4-HR")
    rows.remove(victim)                                        # BL-DCMS stops resolving it (template inactive)

    pinned = [r for r in _frozen(client, internal, old)["requirements"] if r["template_id"] == victim["template_id"]]
    assert len(pinned) == 1 and pinned[0]["is_required"] and pinned[0]["is_active"]

    new = _frozen(client, internal, _shed_in(client, env, "IB"))
    assert victim["template_id"] not in {r["template_id"] for r in new["requirements"]}
    assert len(new["requirements"]) == BEFORE["IB"] - 1


# ======================================================================== REFRESH never expands ==

def test_refresh_appends_nothing_but_still_recovers_a_missing_package(client, db_session, env, internal,
                                                                      mock_bldcms_client):
    missing = _shed_in(client, env, "IC")                      # no configuration at Shed In: no package
    assert _frozen(client, internal, missing)["frozen"] is False

    _baseline(mock_bldcms_client, "IC")
    recovered = client.post(f"/api/shed-visits/{missing}/checksheet-work-package/refresh", headers=env["headers"]).json()
    assert recovered["generated"] is True and len(recovered["added"]) == BEFORE["IC"]

    _configure_m6(mock_bldcms_client, "IC")
    again = client.post(f"/api/shed-visits/{missing}/checksheet-work-package/refresh", headers=env["headers"]).json()
    assert again["generated"] is False and again["added"] == [] and again["retained"] == BEFORE["IC"]
    assert M6 not in _sections(_frozen(client, internal, missing))
