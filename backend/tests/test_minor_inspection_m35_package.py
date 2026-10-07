"""M35-Aux (A-E), M35-CP (Compressor) and M35-TM (TM 1, 2 & 3 / TM 4, 5 & 6) Minor Inspection in the
frozen work package: eight new requirements, one per equipment slot, in printed order; nothing added
to a package that already exists unless an Admin refreshes it; Conventional locomotives unaffected.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers

ARRIVAL = datetime(2026, 9, 17, 7, 30, tzinfo=timezone.utc)
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
AUX, TM, CP, M1 = 5, 6, 15, 9
# (section id, section name, minor equipment id, label, template id)
M35 = [
    (AUX, "M35-Aux", 201, "A - CPA + MCPA", 314),
    (AUX, "M35-Aux", 202, "B - OIL CIRCULATING PUMPS (SR & TFP)", 315),
    (AUX, "M35-Aux", 203, "C - AUXILIARY MOTOR", 316),
    (AUX, "M35-Aux", 204, "D - OIL COOLING UNIT RADIATOR", 317),
    (AUX, "M35-Aux", 205, "E - EARTHING POINTS", 318),
    (CP, "M35-CP", 206, "Compressor", 319),
    (TM, "M35-TM", 207, "TM 1, 2 & 3", 320),
    (TM, "M35-TM", 208, "TM 4, 5 & 6", 321),
]


def _tbta(mock, variant):
    for stage, appl in (("TEST_BEFORE", 1), ("TEST_AFTER", 2)):
        mock.applicability.setdefault(("WAP7", "MINOR", variant, stage), []).append({
            "applicability_id": appl * 100 + MINOR.index(variant), "template_id": 179, "template_name": "TB/TA",
            "technology": "3_PHASE", "section_id": 18, "section_name": "SHIFT", "equipment_id": None,
            "equipment_name": None, "maintenance_type": None, "is_required": True})


def _m1_single(mock, variant):
    mock.applicability.setdefault(("WAP7", "MINOR", variant, "SCHEDULE_INSPECTION"), []).append({
        "applicability_id": 6010 + MINOR.index(variant), "template_id": 601, "template_name": "M1-HR - FB PANEL",
        "technology": "3_PHASE", "section_id": M1, "section_name": "M1-HR", "equipment_id": None,
        "equipment_name": None, "maintenance_type": None, "is_required": True,
        "minor_inspection_equipment_id": 101, "minor_inspection_equipment_code": "FB_PANEL_CUBICLE",
        "minor_inspection_equipment_name": "FB PANEL/ CUBICLE", "minor_inspection_equipment_label": "FB PANEL/ CUBICLE",
        "minor_inspection_performa_variant": None, "minor_inspection_equipment_display_order": 1})


def _m35(mock, variant):
    """Exactly what BL-DCMS's /integration/applicability/resolve returns after migrations 033-035."""
    target = mock.applicability.setdefault(("WAP7", "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    orders = {}
    for section_id, section_name, eq_id, label, tpl in M35:
        orders[section_id] = orders.get(section_id, 0) + 1
        target.append({
            "applicability_id": tpl * 10 + MINOR.index(variant), "template_id": tpl,
            "template_name": f"{section_name} Minor Inspection - {label}", "technology": "3_PHASE",
            "section_id": section_id, "section_name": section_name, "equipment_id": None, "equipment_name": None,
            "maintenance_type": None, "is_required": True, "minor_inspection_equipment_id": eq_id,
            "minor_inspection_equipment_code": f"CODE_{eq_id}", "minor_inspection_equipment_name": label,
            "minor_inspection_equipment_label": label, "minor_inspection_performa_variant": None,
            "minor_inspection_equipment_display_order": orders[section_id],
        })


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    for sid, code in ((18, "SHIFT"), (M1, "M1-HR"), (AUX, "M35-Aux"), (TM, "M35-TM"), (CP, "M35-CP")):
        ensure_section(db_session, id=sid, code=code, name=code)
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("30476", loco_type="WAP7")
    mock_loco_client.add_locomotive("30477", loco_type="WAP7")
    mock_loco_client.add_locomotive("22560", loco_type="WAP4")
    for v in MINOR:
        _tbta(mock_bldcms_client, v)
        _m1_single(mock_bldcms_client, v)
    return headers


def _shed_in(client, headers, variant="IB", loco="30476"):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _reqs(db_session, visit_id):
    return (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id)
            .order_by(models.ShedVisitChecksheetRequirement.id).all())


def _m35_rows(rows):
    return [r for r in rows if r.section_name_snapshot in ("M35-Aux", "M35-CP", "M35-TM")]


@pytest.mark.parametrize("variant", MINOR)
def test_a_new_visit_gets_exactly_eight_m35_requirements_in_printed_order(client, db_session, env, mock_bldcms_client, variant):
    _m35(mock_bldcms_client, variant)
    rows = _m35_rows(_reqs(db_session, _shed_in(client, env, variant)))

    assert [(r.section_name_snapshot, r.minor_inspection_equipment_label_snapshot, r.template_id) for r in rows] == \
        [(name, label, tpl) for _, name, _, label, tpl in M35]
    assert all(r.is_required and r.is_active and r.workflow_stage_type == "SCHEDULE_INSPECTION" for r in rows)
    assert all(r.equipment_id_snapshot is None for r in rows)
    # M35-Aux has five slots with E last; M35-TM has exactly two - never six TM rows.
    assert [r.minor_inspection_equipment_label_snapshot for r in rows if r.section_name_snapshot == "M35-Aux"][-1] == "E - EARTHING POINTS"
    assert len([r for r in rows if r.section_name_snapshot == "M35-TM"]) == 2


def test_an_existing_frozen_package_is_never_rewritten_by_new_configuration(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env, "IC")
    before = [(r.id, r.template_id, r.is_required, r.is_active) for r in _reqs(db_session, visit_id)]
    assert len(before) == 3 and _m35_rows(_reqs(db_session, visit_id)) == []

    _m35(mock_bldcms_client, "IC")          # the seeds are applied while the visit is open
    for path in (f"/api/shed-visits/{visit_id}/checksheet-work-package",
                 f"/api/shed-visits/{visit_id}/workflow",
                 f"/api/shed-visits/{visit_id}/shed-out-eligibility"):
        assert client.get(path, headers=env).status_code == 200, path
    db_session.expire_all()
    assert [(r.id, r.template_id, r.is_required, r.is_active) for r in _reqs(db_session, visit_id)] == before

    # Refresh never expands a frozen package: the new sections apply to visits shed in from now on.
    body = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/refresh", headers=env).json()
    assert body["retained"] == 3 and body["added"] == [] and body["frozen"] is True
    db_session.expire_all()
    assert [(r.id, r.template_id, r.is_required, r.is_active) for r in _reqs(db_session, visit_id)] == before
    assert len(_m35_rows(_reqs(db_session, _shed_in(client, env, "IC", loco="30477")))) == 8


def test_a_conventional_locomotive_gets_no_m35_requirements(client, db_session, env, mock_bldcms_client):
    _m35(mock_bldcms_client, "IB")          # 3_PHASE-only content, keyed to WAP7
    assert _m35_rows(_reqs(db_session, _shed_in(client, env, "IB", loco="22560"))) == []
