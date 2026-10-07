"""M1-HR Minor Inspection in the frozen work package: one requirement per equipment even when BL-DCMS
returns alternative performas (pantograph PT-1/PT-2 x FTRTIL/Schunk), optional HLC-1/HLC-2 via the
generic is_required=false semantics, additive refresh on top of TB/TA + M2-HR, and stage/Shed Out
behaviour with optional work left untouched.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_true_admin_headers

ARRIVAL = datetime(2026, 9, 14, 7, 30, tzinfo=timezone.utc)
STARTED = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
PANEL = "/api/admin/pending-checksheet-requirements"
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
M1, M2 = 9, 8
# (minor equipment id, label, required, [template ids - alternatives])
M1HR = [
    (101, "FB PANEL/ CUBICLE", True, [601]), (102, "HB CUBICLE-1", True, [602]), (103, "HB CUBICLE-2", True, [603]),
    (104, "SB CUBICLE-1", True, [604]), (105, "SB CUBICLE-2", True, [605]), (106, "DRIVER DESK PANELS: CAB-1", True, [606]),
    (107, "DRIVER DESK PANELS: CAB-2", True, [607]), (108, "SIGNAL LIGHT FOR EXCHANGE SYSTEM", True, [608]),
    (109, "CAB HEATER & CREW FAN: CAB-1", True, [609]), (110, "CAB HEATER & CREW FAN: CAB-2", True, [610]),
    (111, "HLC-1", False, [611]), (112, "HLC-2", False, [612]), (113, "TRACTION CONVERTER", True, [613]),
    (114, "Auxiliary Converter", True, [614]),
    (115, "HR Pantograph PT-1", True, [615, 616]), (116, "HR Pantograph PT-2", True, [617, 618]),
]
M2HR = [(i, chr(64 + i), True, [500 + i]) for i in range(1, 21)]


def _add(mock, variant, section_id, section_name, rows, label_fn, loco_type="WAP7"):
    target = mock.applicability.setdefault((loco_type, "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for order, (eq_id, label, required, templates) in enumerate(rows, start=1):
        for tpl in templates:
            target.append({
                "applicability_id": tpl * 10 + MINOR.index(variant), "template_id": tpl, "template_name": f"{section_name} - {label}",
                "technology": "3_PHASE", "section_id": section_id, "section_name": section_name,
                "equipment_id": None, "equipment_name": None, "maintenance_type": None, "is_required": required,
                "minor_inspection_equipment_id": eq_id, "minor_inspection_equipment_code": f"CODE_{eq_id}",
                "minor_inspection_equipment_name": label, "minor_inspection_equipment_label": label_fn(label),
                "minor_inspection_performa_variant": (f"make-{tpl}" if len(templates) > 1 else None),
                "minor_inspection_equipment_display_order": order,
            })


def _m1(mock, variant):
    _add(mock, variant, M1, "M1-HR", M1HR, lambda label: label)


def _m2(mock, variant):
    _add(mock, variant, M2, "M2-HR", M2HR, lambda code: f"{code} - equipment")


def _tbta(mock, variant):
    for stage, appl in (("TEST_BEFORE", 1), ("TEST_AFTER", 2)):
        mock.applicability.setdefault(("WAP7", "MINOR", variant, stage), []).append({
            "applicability_id": appl * 100 + MINOR.index(variant), "template_id": 179, "template_name": "TB/TA",
            "technology": "3_PHASE", "section_id": 18, "section_name": "SHIFT", "equipment_id": None,
            "equipment_name": None, "maintenance_type": None, "is_required": True})


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    for sid, code in ((18, "SHIFT"), (M1, "M1-HR"), (M2, "M2-HR")):
        ensure_section(db_session, id=sid, code=code, name=code)
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("30476", loco_type="WAP7")
    mock_loco_client.add_locomotive("22560", loco_type="WAP4")
    for v in MINOR:
        _tbta(mock_bldcms_client, v)
    return headers


def _shed_in(client, headers, variant="IB", loco="30476"):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _reqs(db_session, visit_id, section=None):
    rows = (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id).all())
    return [r for r in rows if section is None or r.section_name_snapshot == section]


def _submit(mock, visit_id, variant, stage, template_id, minor_id=None, section=18, status="SUBMITTED"):
    mock.add_checksheet(visit_id, checksheet_id=len(mock.visits.get(visit_id, [])) + 1, template_id=template_id,
                        section_id=section, schedule_variant=variant, workflow_stage_type=stage, status=status,
                        minor_inspection_equipment_id=minor_id)


def _group(client, headers, visit_id):
    return next(g for g in client.get(PANEL, headers=headers).json()["groups"] if g["shed_visit_id"] == visit_id)


# ============================================================================= package ==

@pytest.mark.parametrize("variant", MINOR)
def test_one_requirement_per_m1hr_equipment_with_optional_hlc(client, db_session, env, mock_bldcms_client, variant):
    _m1(mock_bldcms_client, variant)
    visit_id = _shed_in(client, env, variant)
    m1 = sorted(_reqs(db_session, visit_id, "M1-HR"), key=lambda r: r.minor_inspection_equipment_id)
    assert [(r.minor_inspection_equipment_label_snapshot, r.is_required) for r in m1] == [(l, req) for _, l, req, _ in M1HR]
    pt1 = next(r for r in m1 if r.minor_inspection_equipment_id == 115)
    assert pt1.template_id == 615                                     # reference performa, one row for the slot
    assert all(r.equipment_id_snapshot is None and r.workflow_stage_type == "SCHEDULE_INSPECTION" for r in m1)


def test_conventional_locomotive_gets_no_m1hr_requirements(client, db_session, env, mock_bldcms_client):
    _m1(mock_bldcms_client, "IA0")
    assert _reqs(db_session, _shed_in(client, env, "IA0", loco="22560")) == []


def test_refresh_never_adds_m1hr_to_a_frozen_package_and_preserves_its_flags(client, db_session, env, mock_bldcms_client):
    _m2(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")                            # package: TB, TA, 20 M2-HR
    existing = _reqs(db_session, visit_id)
    assert len(existing) == 22
    tb = next(r for r in existing if r.workflow_stage_type == "TEST_BEFORE")
    tb.is_required = False
    m2_a = next(r for r in existing if r.minor_inspection_equipment_id == 1)
    m2_a.is_active = False
    db_session.commit()
    snapshot = {r.id: (r.template_id, r.is_required, r.is_active, r.minor_inspection_equipment_id) for r in _reqs(db_session, visit_id)}

    _m1(mock_bldcms_client, "IB")
    body = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/refresh", headers=env).json()
    assert body["retained"] == 22 and body["added"] == []
    db_session.expire_all()
    after = _reqs(db_session, visit_id)
    assert {r.id: (r.template_id, r.is_required, r.is_active, r.minor_inspection_equipment_id) for r in after} == snapshot


# ======================================================================= optional HLC ==

def test_pending_panel_shows_hlc_as_optional_and_lets_it_be_completed(client, db_session, env, mock_bldcms_client):
    _m1(mock_bldcms_client, "IC")
    visit_id = _shed_in(client, env, "IC")
    group = _group(client, make_true_admin_headers(db_session), visit_id)   # all-sections view = Admin
    hlc = [r for r in group["requirements"] if r["minor_inspection_equipment_label"] in ("HLC-1", "HLC-2")]
    assert len(hlc) == 2 and all(r["is_required"] is False and r["is_active"] for r in hlc)
    assert group["counts"]["optional"] == 2

    _submit(mock_bldcms_client, visit_id, "IC", "SCHEDULE_INSPECTION", 611, minor_id=111, section=M1)
    group = _group(client, make_true_admin_headers(db_session), visit_id)   # all-sections view = Admin
    assert [r["minor_inspection_equipment_label"] for r in group["requirements"]].count("HLC-1") == 0   # satisfied -> leaves queue
    assert group["counts"]["satisfied"] == 1 and group["counts"]["optional"] == 1


def _reconcile(client, headers, visit_id, times=3):
    for _ in range(times):
        resp = client.post(f"/api/shed-visits/{visit_id}/reconcile-checksheet-stages", headers=headers)
        assert resp.status_code == 200, resp.text
    return {s["workflow_stage_type"]: s for s in resp.json()["stages"]}


def _submit_required_m1(mock, visit_id, variant, pt1=616, pt2=617, skip=()):
    for eq_id, label, required, templates in M1HR:
        if not required or eq_id in skip:
            continue
        template = {115: pt1, 116: pt2}.get(eq_id, templates[0])
        _submit(mock, visit_id, variant, "SCHEDULE_INSPECTION", template, minor_id=eq_id, section=M1)


@pytest.mark.parametrize("variant", ["IB", "IA0"])
def test_untouched_optional_hlc_never_blocks_inspection_ta_or_shed_out(client, db_session, env, mock_bldcms_client, variant):
    _m1(mock_bldcms_client, variant)
    _m2(mock_bldcms_client, variant)
    mock_bldcms_client.minor_inspection_configuration_complete = True       # isolated fixture: full configuration
    visit_id = _shed_in(client, env, variant)
    client.post(f"/api/shed-visits/{visit_id}/start-schedule", json={"started_at": STARTED.isoformat()}, headers=env)

    _submit(mock_bldcms_client, visit_id, variant, "TEST_BEFORE", 179)
    _submit_required_m1(mock_bldcms_client, visit_id, variant)             # pantographs via Schunk / FTRTIL alternatives
    for eq_id, _code, _req, templates in M2HR:
        _submit(mock_bldcms_client, visit_id, variant, "SCHEDULE_INSPECTION", templates[0], minor_id=eq_id, section=M2)
    _submit(mock_bldcms_client, visit_id, variant, "SCHEDULE_INSPECTION", 611, minor_id=111, section=M1, status="DRAFT")  # HLC-1 pending
    _submit(mock_bldcms_client, visit_id, variant, "TEST_AFTER", 179)                                                  # HLC-2 never opened

    stages = _reconcile(client, env, visit_id)
    assert stages["SCHEDULE_INSPECTION"]["current_status"] == "COMPLETED"
    assert stages["TEST_AFTER"]["current_status"] == "COMPLETED"
    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=env).json()
    assert elig["checksheet_blockers"] == [] and elig["stage_blockers"] == []


@pytest.mark.parametrize("missing", [116, 102])
def test_a_missing_required_m1hr_equipment_still_blocks(client, db_session, env, mock_bldcms_client, missing):
    _m1(mock_bldcms_client, "IB")
    mock_bldcms_client.minor_inspection_configuration_complete = True
    visit_id = _shed_in(client, env, "IB")
    _submit(mock_bldcms_client, visit_id, "IB", "TEST_BEFORE", 179)
    _submit_required_m1(mock_bldcms_client, visit_id, "IB", skip=(missing,))
    stages = _reconcile(client, env, visit_id)
    assert stages["SCHEDULE_INSPECTION"]["reason"] == "REQUIRED_CHECKSHEETS_PENDING"


def test_production_like_incomplete_configuration_keeps_inspection_open(client, db_session, env, mock_bldcms_client):
    _m1(mock_bldcms_client, "IC0")
    _m2(mock_bldcms_client, "IC0")
    visit_id = _shed_in(client, env, "IC0")
    _submit(mock_bldcms_client, visit_id, "IC0", "TEST_BEFORE", 179)
    _submit_required_m1(mock_bldcms_client, visit_id, "IC0")
    for eq_id, _code, _req, templates in M2HR:
        _submit(mock_bldcms_client, visit_id, "IC0", "SCHEDULE_INSPECTION", templates[0], minor_id=eq_id, section=M2)
    assert _reconcile(client, env, visit_id)["SCHEDULE_INSPECTION"]["reason"] == "STAGE_CONFIGURATION_INCOMPLETE"
