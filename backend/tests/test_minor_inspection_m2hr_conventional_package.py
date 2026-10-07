"""M2-HR CONVENTIONAL (BL-DCMS migration 045) in the frozen work package.

A WAP-4 Minor visit gains sixteen M2-HR requirements - one per printed lettered block A-P - on top of
the fourteen M1-HR ones (migration 042 + the two Pantograph slots of 044): 14 -> 30 per variant.
CONVENTIONAL still has no Test Before / Test After performa. The 3_PHASE package is untouched: 63.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_true_admin_headers

ARRIVAL = datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc)
PANEL = "/api/admin/pending-checksheet-requirements"
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
M1, M2, SHIFT = 9, 8, 18
M2HR = ["TK-2 Resistance", "PC-8 Relay", "DC-DC Converter", "Flasher Light", "CVVRS", "Speedometer", "TMDDS",
        "Pressure Relays, Pressure Switches", "Air Flow Relays", "Gauges", "Marker Lights", "UA ,U Meter & Ammeter",
        "MPCS", "Signalling Lamp / Panel", "Target Relays", "SIV"]
PANTO_TYPES = 7


def _row(variant, template_id, eq_id, code, label, section_id, section, order, performa=None, technology="CONVENTIONAL"):
    return {
        "applicability_id": template_id * 10 + MINOR.index(variant), "template_id": template_id,
        "template_name": f"{section} Minor Inspection - {label}", "technology": technology, "section_id": section_id,
        "section_name": section, "equipment_id": None, "equipment_name": None, "maintenance_type": None,
        "is_required": True, "minor_inspection_equipment_id": eq_id, "minor_inspection_equipment_code": code,
        "minor_inspection_equipment_name": label, "minor_inspection_equipment_label": label,
        "minor_inspection_performa_variant": performa, "minor_inspection_equipment_display_order": order,
    }


def _conventional(mock, variant, with_m2hr=True):
    """What BL-DCMS returns for a WAP-4 after 042 + 044 (+ 045 when with_m2hr)."""
    target = mock.applicability.setdefault(("WAP4", "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for n in range(12):                                                  # M1-HR 042
        target.append(_row(variant, 3000 + n, 114 + n, f"M1_{n}", f"M1 slot {n}", M1, "M1-HR", n + 1))
    for slot, (eq_id, label) in enumerate(((126, "Pantograph PT-1"), (127, "Pantograph PT-2"))):   # 044
        for p in range(PANTO_TYPES):
            target.append(_row(variant, 4000 + slot * 10 + p, eq_id, f"HRPT_PT_{slot + 1}", label, M1, "M1-HR",
                               13 + slot, performa=f"type-{p}"))
    if with_m2hr:
        for n, label in enumerate(M2HR):                                 # M2-HR 045
            code = chr(ord("A") + n)
            target.append(_row(variant, 5000 + n, 200 + n, code, f"{code} - {label}", M2, "M2-HR", n + 1))


@pytest.fixture()
def env(db_session, mock_loco_client):
    for sid, code in ((SHIFT, "SHIFT"), (M1, "M1-HR"), (M2, "M2-HR")):
        ensure_section(db_session, id=sid, code=code, name=code)
    mock_loco_client.add_locomotive("22560", loco_type="WAP4")
    return make_movement_supervisor_headers(db_session)


def _shed_in(client, headers, variant):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": "22560", "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _reqs(db_session, visit_id, section=None):
    rows = (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id).all())
    return [r for r in rows if section is None or r.section_name_snapshot == section]


@pytest.mark.parametrize("variant", MINOR)
def test_before_045_a_wap4_visit_has_14_requirements(client, db_session, env, mock_bldcms_client, variant):
    _conventional(mock_bldcms_client, variant, with_m2hr=False)

    assert len(_reqs(db_session, _shed_in(client, env, variant))) == 14


@pytest.mark.parametrize("variant", MINOR)
def test_24_after_045_a_wap4_visit_has_30_requirements(client, db_session, env, mock_bldcms_client, variant):
    _conventional(mock_bldcms_client, variant)

    rows = _reqs(db_session, _shed_in(client, env, variant))

    assert len(rows) == 30
    assert len([r for r in rows if r.section_name_snapshot == "M1-HR"]) == 14
    m2 =[r for r in rows if r.section_name_snapshot == "M2-HR"]
    assert [r.minor_inspection_equipment_label_snapshot for r in m2] == \
        [f"{chr(ord('A') + n)} - {label}" for n, label in enumerate(M2HR)]
    assert all(r.is_required and r.is_active and r.workflow_stage_type == "SCHEDULE_INSPECTION" for r in rows)


@pytest.mark.parametrize("variant", MINOR)
def test_25_there_is_still_no_test_before_or_test_after(client, db_session, env, mock_bldcms_client, variant):
    _conventional(mock_bldcms_client, variant)

    rows = _reqs(db_session, _shed_in(client, env, variant))

    assert {r.workflow_stage_type for r in rows} == {"SCHEDULE_INSPECTION"}


def test_each_m2hr_block_is_satisfied_independently(client, db_session, env, mock_bldcms_client):
    _conventional(mock_bldcms_client, "IC")
    visit_id = _shed_in(client, env, "IC")

    def outstanding():
        group = next(g for g in client.get(PANEL, headers=make_true_admin_headers(db_session)).json()["groups"]
                     if g["shed_visit_id"] == visit_id)
        return {b["minor_inspection_equipment_label"] for b in group["requirements"]
                if (b["minor_inspection_equipment_label"] or "")[:4] in {f"{chr(ord('A') + n)} - " for n in range(16)}}

    before = outstanding()
    mock_bldcms_client.add_checksheet(visit_id, checksheet_id=1, template_id=5005, section_id=M2, schedule_variant="IC",
                                      workflow_stage_type="SCHEDULE_INSPECTION", status="SUBMITTED",
                                      minor_inspection_equipment_id=205)          # F - Speedometer
    after = outstanding()

    assert len(before) == 16
    assert before - after == {"F - Speedometer"}
