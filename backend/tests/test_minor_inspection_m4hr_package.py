"""M4-HR Minor Inspection (BL-DCMS migration 038) in the frozen work package.

Seventeen new requirements, one per printed block of the "WAG-9 & WAP-7" performa, in printed
order, for every MINOR variant. BOGIE CLEARANCE is deliberately NOT an eighteenth requirement (its
48 readings belong to WHEEL PROFILE MEASUREMENT's checkpoint 1.4), and the two damper blocks stay
two separate requirements. A visit that is already open keeps its frozen package until an Admin
refreshes it - which is why the seed waits for a quiet window.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers

ARRIVAL = datetime(2026, 9, 19, 7, 30, tzinfo=timezone.utc)
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
M4, SHIFT = 10, 18
# (minor equipment id, source code, printed heading, template id) in printed order.
M4HR = [
    (301, "HELICAL_SPRING", "HELICAL SPRING", 401),
    (302, "DAMPERS_AND_SPHERI_BLOCK", "DAMPERS AND SPHERI BLOCK", 402),
    (303, "TM_BOGIE", "TM (BOGIE)", 403),
    (304, "BUFFERS", "BUFFERS", 404),
    (305, "BOGIE_FRAME", "BOGIE FRAME", 405),
    (306, "CBC", "CBC", 406),
    (307, "SCREW_COUPLING", "SCREW COUPLING", 407),
    (308, "DRAFT_GEAR", "DRAFT GEAR", 408),
    (309, "RAIL_GUARD_AND_CATTLE_GUARD", "RAIL GUARD AND CATTLE GUARD", 409),
    (310, "BREAK_RIGGING", "BREAK RIGGING", 410),
    (311, "HAND_BREAK", "HAND BREAK", 411),
    (312, "DAMPER_AND_SPHERIBLOCKS", "DAMPER AND SPHERIBLOCKS", 412),
    (313, "SANDER", "SANDER", 413),
    (314, "TRACTION_BAR", "TRACTION BAR", 414),
    (315, "GEAR_CASE_OILING", "GEAR CASE (OILING)", 415),
    (316, "AXLE_BOX_UST", "AXLE BOX (UST)", 416),
    (317, "WHEEL_PROFILE_MEASUREMENT", "WHEEL PROFILE MEASUREMENT", 417),
]


def _tbta(mock, variant):
    for stage, appl in (("TEST_BEFORE", 1), ("TEST_AFTER", 2)):
        mock.applicability.setdefault(("WAG9H", "MINOR", variant, stage), []).append({
            "applicability_id": appl * 100 + MINOR.index(variant), "template_id": 179,
            "template_name": "TB/TA", "technology": "3_PHASE", "section_id": SHIFT,
            "section_name": "SHIFT", "equipment_id": None, "equipment_name": None,
            "maintenance_type": None, "is_required": True})


def _m4hr(mock, variant):
    """Exactly what BL-DCMS's /integration/applicability/resolve returns after migration 038."""
    target = mock.applicability.setdefault(("WAG9H", "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for order, (eq_id, code, label, tpl) in enumerate(M4HR, start=1):
        target.append({
            "applicability_id": tpl * 10 + MINOR.index(variant), "template_id": tpl,
            "template_name": f"M4-HR Minor Inspection - {label}", "technology": "3_PHASE",
            "section_id": M4, "section_name": "M4-HR", "equipment_id": None, "equipment_name": None,
            "maintenance_type": None, "is_required": True, "minor_inspection_equipment_id": eq_id,
            "minor_inspection_equipment_code": code, "minor_inspection_equipment_name": label,
            "minor_inspection_equipment_label": label, "minor_inspection_performa_variant": None,
            "minor_inspection_equipment_display_order": order,
        })


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    for sid, code in ((SHIFT, "SHIFT"), (M4, "M4-HR")):
        ensure_section(db_session, id=sid, code=code, name=code)
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("41771", loco_type="WAG9H")
    for v in MINOR:
        _tbta(mock_bldcms_client, v)
    return headers


def _shed_in(client, headers, variant="IB", loco="41771"):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []},
        headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _reqs(db_session, visit_id):
    return (db_session.query(models.ShedVisitChecksheetRequirement)
            .join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id)
            .order_by(models.ShedVisitChecksheetRequirement.id).all())


def _m4_rows(rows):
    return [r for r in rows if r.section_name_snapshot == "M4-HR"]


@pytest.mark.parametrize("variant", MINOR)
def test_a_new_visit_gets_exactly_seventeen_m4hr_requirements_in_printed_order(
        client, db_session, env, mock_bldcms_client, variant):
    _m4hr(mock_bldcms_client, variant)

    rows = _m4_rows(_reqs(db_session, _shed_in(client, env, variant)))

    assert len(rows) == 17
    assert [(r.minor_inspection_equipment_label_snapshot, r.template_id) for r in rows] == \
        [(label, tpl) for _eq, _code, label, tpl in M4HR]
    assert all(r.is_required and r.is_active for r in rows)
    assert all(r.workflow_stage_type == "SCHEDULE_INSPECTION" for r in rows)
    assert all(r.equipment_id_snapshot is None for r in rows)


def test_bogie_clearance_is_not_an_eighteenth_requirement(client, db_session, env, mock_bldcms_client):
    _m4hr(mock_bldcms_client, "IC")

    rows = _m4_rows(_reqs(db_session, _shed_in(client, env, "IC")))

    labels = [r.minor_inspection_equipment_label_snapshot for r in rows]
    assert "BOGIE CLEARANCE" not in labels
    assert labels[-1] == "WHEEL PROFILE MEASUREMENT"
    assert len(rows) == 17


def test_the_two_damper_blocks_are_two_requirements(client, db_session, env, mock_bldcms_client):
    _m4hr(mock_bldcms_client, "IA")

    rows = _m4_rows(_reqs(db_session, _shed_in(client, env, "IA")))

    dampers = [r for r in rows if "DAMPER" in r.minor_inspection_equipment_label_snapshot]
    assert [r.minor_inspection_equipment_label_snapshot for r in dampers] == [
        "DAMPERS AND SPHERI BLOCK", "DAMPER AND SPHERIBLOCKS"]
    assert dampers[0].minor_inspection_equipment_id != dampers[1].minor_inspection_equipment_id
    assert dampers[0].template_id != dampers[1].template_id


def test_a_visit_open_before_the_seed_keeps_its_frozen_package(client, db_session, env, mock_bldcms_client):
    """An open visit's package is frozen: neither reads nor refresh add the new section to it."""
    visit_id = _shed_in(client, env, "IB")
    before = [(r.id, r.template_id, r.is_required, r.is_active) for r in _reqs(db_session, visit_id)]
    assert len(before) == 2 and _m4_rows(_reqs(db_session, visit_id)) == []

    _m4hr(mock_bldcms_client, "IB")          # the seed lands while the visit is open
    for path in (f"/api/shed-visits/{visit_id}/checksheet-work-package",
                 f"/api/shed-visits/{visit_id}/workflow"):
        assert client.get(path, headers=env).status_code == 200, path
    db_session.expire_all()
    assert [(r.id, r.template_id, r.is_required, r.is_active) for r in _reqs(db_session, visit_id)] == before

    body = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package/refresh", headers=env).json()

    assert body["retained"] == 2 and body["added"] == []
    db_session.expire_all()
    assert [(r.id, r.template_id, r.is_required, r.is_active) for r in _reqs(db_session, visit_id)] == before
    assert _m4_rows(_reqs(db_session, visit_id)) == []
