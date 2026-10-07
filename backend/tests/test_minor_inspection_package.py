"""Minor Inspection equipment (BL-DCMS directory, migration 028 / OD migration 010) in the frozen
work package: per-equipment requirement identity, refresh that never expands a frozen package,
equipment-exact correlation, and the "partially configured Inspection never completes" rule.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_true_admin_headers

ARRIVAL = datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)
STARTED = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
PANEL = "/api/admin/pending-checksheet-requirements"
CODES = "ABCDEFGHIJKLMNOPQRST"
NAMES = ["VCD", "FL UNIT & Marker Light", "GAUGES AND PRESSURE SWITCHES", "Meter panel", "Driver Display Unit & Buzzer",
         "TMDDS", "CENTRAL ELECTRONICS RACK", "AUXILIARY CONVERTER 1, 2 & 3", "TRACTION CONVERTER (SR)",
         "FIRE DETECTION UNIT", "FB PANEL / CUBICLE", "HB Cubicle", "SB Cubicle", "QFL relay & Voltage transducer",
         "OCR relay", "MEMOTEL (SPEEDOMETER)", "X'Mer Temp & pressure sensor", "RMS (REMOTE MONITORING SYSTEM)",
         "CVVRS", "HOTEL LOAD CONVERTOR (HLC)"]
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
M2HR_SECTION = 8


def _tbta(mock, variant, loco_type="WAP7"):
    for stage, appl in (("TEST_BEFORE", 1), ("TEST_AFTER", 2)):
        mock.applicability.setdefault((loco_type, "MINOR", variant, stage), []).append({
            "applicability_id": appl * 100 + MINOR.index(variant), "template_id": 179,
            "template_name": "3 Phase Loco Test Before / Test After", "technology": "3_PHASE",
            "section_id": 18, "section_name": "SHIFT", "equipment_id": None, "equipment_name": None,
            "maintenance_type": None, "is_required": True,
        })


def _m2hr(mock, variant, loco_type="WAP7"):
    rows = mock.applicability.setdefault((loco_type, "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for i, (code, name) in enumerate(zip(CODES, NAMES), start=1):
        rows.append({
            "applicability_id": 1000 + i * 10 + MINOR.index(variant), "template_id": 500 + i,
            "template_name": f"M2-HR Minor Inspection - {code} - {name}", "technology": "3_PHASE",
            "section_id": M2HR_SECTION, "section_name": "M2-HR", "equipment_id": None, "equipment_name": None,
            "maintenance_type": None, "is_required": True,
            "minor_inspection_equipment_id": i, "minor_inspection_equipment_code": code,
            "minor_inspection_equipment_name": name, "minor_inspection_equipment_display_order": i,
        })


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=18, code="SHIFT", name="SHIFT")
    ensure_section(db_session, id=M2HR_SECTION, code="M2-HR", name="M2-HR")
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39015", loco_type="WAP7")
    mock_loco_client.add_locomotive("22560", loco_type="WAP4")
    for v in MINOR:
        _tbta(mock_bldcms_client, v)
    return headers


def _shed_in(client, headers, variant="IA0", loco="39015"):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": [],
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _reqs(db_session, visit_id):
    return (db_session.query(models.ShedVisitChecksheetRequirement)
            .join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id).all())


def _package(db_session, visit_id):
    return db_session.query(models.ShedVisitChecksheetPackage).filter_by(shed_visit_id=visit_id).one()


def _submit(mock, visit_id, variant, stage, template_id, minor_id=None, status="SUBMITTED"):
    mock.add_checksheet(visit_id, checksheet_id=len(mock.visits.get(visit_id, [])) + 1, template_id=template_id,
                        section_id=M2HR_SECTION if minor_id else 18, schedule_variant=variant,
                        workflow_stage_type=stage, status=status, minor_inspection_equipment_id=minor_id)


# ============================================================================ generation ==

@pytest.mark.parametrize("variant", MINOR)
def test_fresh_package_snapshots_each_m2hr_equipment_as_its_own_requirement(client, db_session, env, mock_bldcms_client, variant):
    _m2hr(mock_bldcms_client, variant)
    visit_id = _shed_in(client, env, variant)
    reqs = _reqs(db_session, visit_id)
    si = sorted((r for r in reqs if r.workflow_stage_type == "SCHEDULE_INSPECTION"),
                key=lambda r: r.minor_inspection_equipment_code_snapshot)
    assert [(r.minor_inspection_equipment_code_snapshot, r.minor_inspection_equipment_name_snapshot) for r in si] == list(zip(CODES, NAMES))
    assert len({r.template_id for r in si}) == 20 and len({r.minor_inspection_equipment_id for r in si}) == 20
    assert all(r.equipment_id_snapshot is None for r in si)             # legacy id never overloaded
    assert sorted(r.workflow_stage_type for r in reqs if r.template_id == 179) == ["TEST_AFTER", "TEST_BEFORE"]
    assert _package(db_session, visit_id).minor_inspection_configuration_complete is False


def test_conventional_locomotive_receives_no_m2hr_requirements(client, db_session, env, mock_bldcms_client):
    _m2hr(mock_bldcms_client, "IA0")       # configured for WAP7 (3_PHASE) only
    visit_id = _shed_in(client, env, "IA0", loco="22560")
    assert _reqs(db_session, visit_id) == []


def test_requirement_model_rejects_mixing_minor_and_legacy_equipment(client, db_session, env, mock_bldcms_client):
    _m2hr(mock_bldcms_client, "IA0")
    visit_id = _shed_in(client, env, "IA0")
    row = next(r for r in _reqs(db_session, visit_id) if r.minor_inspection_equipment_id)
    row.equipment_id_snapshot = 26
    with pytest.raises(IntegrityError):
        db_session.commit()


# ============================================================================== refresh ==

def test_refresh_never_adds_m2hr_to_an_existing_tbta_package(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env, "IA0")          # only TB/TA configured at Shed In
    before = {r.id: (r.template_id, r.workflow_stage_type, r.is_required, r.is_active, r.applicability_id)
              for r in _reqs(db_session, visit_id)}
    assert len(before) == 2
    tb = next(r for r in _reqs(db_session, visit_id) if r.workflow_stage_type == "TEST_BEFORE")
    tb.is_required = False                            # a per-visit decision that must survive
    db_session.commit()
    before[tb.id] = (tb.template_id, tb.workflow_stage_type, False, tb.is_active, tb.applicability_id)

    _m2hr(mock_bldcms_client, "IA0")
    resp = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/refresh", headers=env)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["generated"] is False and body["frozen"] is True and body["retained"] == 2 and body["added"] == []
    assert body["minor_inspection_configuration_complete_after"] is False

    db_session.expire_all()
    after = _reqs(db_session, visit_id)
    assert {r.id: (r.template_id, r.workflow_stage_type, r.is_required, r.is_active, r.applicability_id) for r in after} == before


def test_refresh_never_attaches_new_work_to_any_stage(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env, "IC")
    stage = db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit_id, stage_type="SCHEDULE_INSPECTION").one()
    stage.status = "COMPLETED"
    stage.completed_at = datetime.now(timezone.utc)
    stage.started_at = stage.completed_at
    db_session.commit()
    _m2hr(mock_bldcms_client, "IC")
    body = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/refresh", headers=env).json()
    assert body["added"] == [] and body["retained"] == 2


def test_refresh_refuses_closed_and_major_visits(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env, "IA0")
    visit = db_session.get(models.ShedVisit, visit_id)
    visit.schedule_family, visit.schedule_variant = "MAJOR", "IOH"
    db_session.commit()
    assert client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/refresh", headers=env).status_code == 409


# ========================================================================= correlation ==

def test_a_checksheet_only_satisfies_its_own_equipment_requirement(client, db_session, env, mock_bldcms_client):
    """Equipment-level identity: B's checksheet never satisfies A, and a checksheet for A satisfies A
    whichever of A's performas (templates) it used - one instance per (visit, stage, equipment)."""
    _m2hr(mock_bldcms_client, "IA0")
    visit_id = _shed_in(client, env, "IA0")

    def pending():
        group = next(g for g in client.get(PANEL, headers=make_true_admin_headers(db_session)).json()["groups"] if g["shed_visit_id"] == visit_id)
        return [r["minor_inspection_equipment_code"] for r in group["requirements"] if r["workflow_stage_type"] == "SCHEDULE_INSPECTION"]

    _submit(mock_bldcms_client, visit_id, "IA0", "SCHEDULE_INSPECTION", template_id=502, minor_id=2)   # B
    assert pending() == [c for c in CODES if c != "B"]
    _submit(mock_bldcms_client, visit_id, "IA0", "SCHEDULE_INSPECTION", template_id=9999, minor_id=1)  # A via another performa
    assert pending() == list(CODES[2:])


def test_pending_panel_shows_equipment_identity_and_partial_configuration(client, db_session, env, mock_bldcms_client):
    _m2hr(mock_bldcms_client, "IA0")
    visit_id = _shed_in(client, env, "IA0")
    group = next(g for g in client.get(PANEL, headers=make_true_admin_headers(db_session)).json()["groups"] if g["shed_visit_id"] == visit_id)
    assert group["work_package_generated"] is True
    assert group["unconfigured_stages"] == []
    assert group["partially_configured_stages"] == ["SCHEDULE_INSPECTION"]
    m2 = [r for r in group["requirements"] if r["section_name"] == "M2-HR"]
    assert [(r["minor_inspection_equipment_code"], r["minor_inspection_equipment_name"]) for r in m2] == list(zip(CODES, NAMES))
    assert all(r["equipment_id"] is None for r in m2)
    assert group["schedule_variant"] == "IA0"


# ============================================================ Inspection stage / Shed Out ==

def _reconcile(client, headers, visit_id, times=3):
    for _ in range(times):
        resp = client.post(f"/api/shed-visits/{visit_id}/reconcile-checksheet-stages", headers=headers)
        assert resp.status_code == 200, resp.text
    return {s["workflow_stage_type"]: s for s in resp.json()["stages"]}


def _submit_everything(mock, visit_id, variant):
    _submit(mock, visit_id, variant, "TEST_BEFORE", 179)
    for i in range(1, 21):
        _submit(mock, visit_id, variant, "SCHEDULE_INSPECTION", 500 + i, minor_id=i)
    _submit(mock, visit_id, variant, "TEST_AFTER", 179)


@pytest.mark.parametrize("variant", ["IA0", "IC0", "IA"])
def test_completing_all_m2hr_never_completes_a_partially_configured_inspection_stage(client, db_session, env, mock_bldcms_client, variant):
    _m2hr(mock_bldcms_client, variant)
    visit_id = _shed_in(client, env, variant)
    _submit_everything(mock_bldcms_client, visit_id, variant)

    stages = _reconcile(client, env, visit_id)
    assert stages["TEST_BEFORE"]["current_status"] == "COMPLETED"
    # Migration 012: the inspection can only start once Test Before is satisfied, and not before it.
    started = datetime.now(timezone.utc)
    assert client.post(f"/api/shed-visits/{visit_id}/start-schedule", json={"started_at": started.isoformat()},
                       headers=env).status_code == 200
    assert stages["SCHEDULE_INSPECTION"]["reason"] == "STAGE_CONFIGURATION_INCOMPLETE"
    assert stages["SCHEDULE_INSPECTION"]["current_status"] != "COMPLETED"
    assert stages["TEST_AFTER"]["current_status"] != "COMPLETED"

    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=env).json()
    assert elig["eligible"] is False
    assert {b["stage_type"] for b in elig["stage_blockers"]} >= {"SCHEDULE_INSPECTION", "TEST_AFTER"}
    completed = started
    assert client.post(f"/api/shed-visits/{visit_id}/complete-schedule", json={"completed_at": completed.isoformat()},
                       headers=env).status_code == 200
    out = client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": started.isoformat()},
                      headers=env)
    assert out.status_code == 409 and out.json()["detail"]["code"] == "SHED_OUT_BLOCKED"


def test_inspection_completes_only_after_configuration_is_declared_complete_and_refreshed(client, db_session, env, mock_bldcms_client):
    _m2hr(mock_bldcms_client, "IA0")
    visit_id = _shed_in(client, env, "IA0")
    _submit_everything(mock_bldcms_client, visit_id, "IA0")
    assert _reconcile(client, env, visit_id)["SCHEDULE_INSPECTION"]["reason"] == "STAGE_CONFIGURATION_INCOMPLETE"

    mock_bldcms_client.minor_inspection_configuration_complete = True
    body = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/refresh", headers=env).json()
    assert body["minor_inspection_configuration_complete_before"] is False
    assert body["minor_inspection_configuration_complete_after"] is True
    stages = _reconcile(client, env, visit_id)
    assert stages["SCHEDULE_INSPECTION"]["current_status"] == "COMPLETED"
    assert stages["TEST_AFTER"]["current_status"] == "COMPLETED"


def test_unreachable_configuration_answer_is_recorded_as_incomplete(client, db_session, env, mock_bldcms_client, monkeypatch):
    _m2hr(mock_bldcms_client, "IA0")

    def boom(**kwargs):
        from app.clients.bldcms import BLDCMSUnavailableError
        raise BLDCMSUnavailableError("down")

    monkeypatch.setattr(mock_bldcms_client, "get_minor_inspection_configuration", boom)
    visit_id = _shed_in(client, env, "IA0")
    assert len(_reqs(db_session, visit_id)) == 22
    assert _package(db_session, visit_id).minor_inspection_configuration_complete is False
