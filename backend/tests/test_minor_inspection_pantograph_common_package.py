"""The common Pantograph source (BL-DCMS migration 044) in the frozen work package.

BL-DCMS now returns SEVEN alternative performas for each physical Pantograph slot, in both
technologies. The package must still hold exactly ONE requirement per slot - PT-1 and PT-2 - never
one per performa, and never one for both slots together:

  * 3_PHASE: 73 SCHEDULE_INSPECTION applicability rows per variant (59 single-performa slots +
    2 slots x 7 performas) still make 61 inspection requirements + Test Before + Test After = 63,
    exactly as before 044 (when the two slots offered 2 performas each).
  * CONVENTIONAL: 12 slots from migration 042 + PT-1 + PT-2 = 14 requirements; still no TB/TA.

A slot is satisfied by whichever of its seven performas the technician filed, and filing PT-1 never
settles PT-2 - even when both carry the same performa.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_true_admin_headers

ARRIVAL = datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc)
PANEL = "/api/admin/pending-checksheet-requirements"
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
M1, SHIFT = 9, 18
PERFORMAS = ["Schunk - WBL 85 HR Make HRPT", "FTRTIL LX3600 make HRPT", "FTRTIL LX3800 make HRPT",
             "MERSEN MPS HR20 make HRPT", "MINEL HRP01 - V5 make HRPT",
             "IR03, AM92 & of Similar Design make Pantograph", "IR01, AM12 & of Similar Design make Pantograph"]
# technology -> (loco number, loco type, [(equipment id, label)] for the non-Pantograph slots,
#                PT-1 id, PT-2 id, first template id of the pantograph block, TB/TA?)
TECH = {
    "3_PHASE": ("30476", "WAP7", [(1000 + n, f"3PH slot {n}") for n in range(1, 60)], 87, 88, 7000, True),
    "CONVENTIONAL": ("22560", "WAP4", [(114 + n, f"CONV slot {n}") for n in range(12)], 126, 127, 8000, False),
}


def _pt_templates(tech, slot_index):
    """The seven template ids of one slot (slot_index 0 = PT-1, 1 = PT-2)."""
    base = TECH[tech][5] + slot_index * 10
    return [base + i for i in range(7)]


def _configure(mock, tech, variant):
    """Exactly what BL-DCMS's resolver returns for this technology after migration 044."""
    loco, loco_type, singles, pt1, pt2, _base, tbta = TECH[tech]
    target = mock.applicability.setdefault((loco_type, "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    order = 0
    for eq_id, label in singles:
        order += 1
        target.append(_row(tech, variant, 100000 + eq_id, eq_id, label, None, order))
    for slot_index, (eq_id, label) in enumerate(((pt1, "Pantograph PT-1"), (pt2, "Pantograph PT-2"))):
        order += 1
        for tpl, performa in zip(_pt_templates(tech, slot_index), PERFORMAS):
            target.append(_row(tech, variant, tpl, eq_id, label, performa, order))
    if tbta:
        for stage, appl in (("TEST_BEFORE", 1), ("TEST_AFTER", 2)):
            mock.applicability.setdefault((loco_type, "MINOR", variant, stage), []).append({
                "applicability_id": appl * 100 + MINOR.index(variant), "template_id": 179, "template_name": "TB/TA",
                "technology": tech, "section_id": SHIFT, "section_name": "SHIFT", "equipment_id": None,
                "equipment_name": None, "maintenance_type": None, "is_required": True})


def _row(tech, variant, template_id, eq_id, label, performa, order):
    code = {"Pantograph PT-1": "HRPT_PT_1", "Pantograph PT-2": "HRPT_PT_2"}.get(label, f"CODE_{eq_id}")
    return {
        "applicability_id": template_id * 10 + MINOR.index(variant), "template_id": template_id,
        "template_name": f"M1-HR Minor Inspection - {label}" + (f" ({performa})" if performa else ""),
        "technology": tech, "section_id": M1, "section_name": "M1-HR", "equipment_id": None,
        "equipment_name": None, "maintenance_type": None, "is_required": True,
        "minor_inspection_equipment_id": eq_id, "minor_inspection_equipment_code": code,
        "minor_inspection_equipment_name": label, "minor_inspection_equipment_label": label,
        "minor_inspection_performa_variant": performa, "minor_inspection_equipment_display_order": order,
    }


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    for sid, code in ((SHIFT, "SHIFT"), (M1, "M1-HR")):
        ensure_section(db_session, id=sid, code=code, name=code)
    for loco, loco_type, *_ in TECH.values():
        mock_loco_client.add_locomotive(loco, loco_type=loco_type)
    return make_movement_supervisor_headers(db_session)


def _shed_in(client, headers, tech, variant):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": TECH[tech][0], "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _reqs(db_session, visit_id):
    return (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id).all())


def _pantographs(rows, tech):
    ids = (TECH[tech][3], TECH[tech][4])
    return [r for r in rows if r.minor_inspection_equipment_id in ids]


def _submit(mock, visit_id, variant, template_id, minor_id):
    mock.add_checksheet(visit_id, checksheet_id=len(mock.visits.get(visit_id, [])) + 1, template_id=template_id,
                        section_id=M1, schedule_variant=variant, workflow_stage_type="SCHEDULE_INSPECTION",
                        status="SUBMITTED", minor_inspection_equipment_id=minor_id)


def _outstanding(client, db_session, visit_id):
    group = next(g for g in client.get(PANEL, headers=make_true_admin_headers(db_session)).json()["groups"]
                 if g["shed_visit_id"] == visit_id)
    return {b["minor_inspection_equipment_label"] for b in group["requirements"]
            if b["minor_inspection_equipment_label"] in ("Pantograph PT-1", "Pantograph PT-2")}


# ================================================================ 5 & 6. package counts ==

@pytest.mark.parametrize("variant", MINOR)
def test_5_the_3_phase_package_stays_at_63_requirements(client, db_session, env, mock_bldcms_client, variant):
    _configure(mock_bldcms_client, "3_PHASE", variant)
    returned = mock_bldcms_client.applicability[("WAP7", "MINOR", variant, "SCHEDULE_INSPECTION")]

    rows = _reqs(db_session, _shed_in(client, env, "3_PHASE", variant))

    assert len(returned) == 73                                           # 59 + 2 slots x 7 performas
    assert len([r for r in rows if r.workflow_stage_type == "SCHEDULE_INSPECTION"]) == 61
    assert len(rows) == 63                                               # + Test Before + Test After
    assert {r.workflow_stage_type for r in rows} == {"SCHEDULE_INSPECTION", "TEST_BEFORE", "TEST_AFTER"}


@pytest.mark.parametrize("variant", MINOR)
def test_6_conventional_has_exactly_14_requirements_and_no_tb_ta(client, db_session, env, mock_bldcms_client, variant):
    _configure(mock_bldcms_client, "CONVENTIONAL", variant)
    returned = mock_bldcms_client.applicability[("WAP4", "MINOR", variant, "SCHEDULE_INSPECTION")]

    rows = _reqs(db_session, _shed_in(client, env, "CONVENTIONAL", variant))

    assert len(returned) == 26                                           # 12 + 2 slots x 7 performas
    assert len(rows) == 14
    assert {r.workflow_stage_type for r in rows} == {"SCHEDULE_INSPECTION"}


# ============================================== 3 & 4. two slots, alternatives collapsed ==

@pytest.mark.parametrize("tech", list(TECH))
@pytest.mark.parametrize("variant", MINOR)
def test_3_and_4_seven_alternatives_collapse_into_one_requirement_per_slot(
        client, db_session, env, mock_bldcms_client, tech, variant):
    _configure(mock_bldcms_client, tech, variant)

    rows = _pantographs(_reqs(db_session, _shed_in(client, env, tech, variant)), tech)

    assert len(rows) == 2
    assert {r.minor_inspection_equipment_id for r in rows} == {TECH[tech][3], TECH[tech][4]}
    assert {r.minor_inspection_equipment_label_snapshot for r in rows} == {"Pantograph PT-1", "Pantograph PT-2"}
    for r in rows:
        slot_index = 0 if r.minor_inspection_equipment_id == TECH[tech][3] else 1
        assert r.template_id in _pt_templates(tech, slot_index)          # a reference, never another slot's
        assert r.is_required and r.is_active


# ================================================ 7 & 8. independent satisfaction per slot ==

@pytest.mark.parametrize("tech", list(TECH))
@pytest.mark.parametrize("performa_index", range(7))
def test_any_of_the_seven_performas_satisfies_its_own_slot_only(
        client, db_session, env, mock_bldcms_client, tech, performa_index):
    _configure(mock_bldcms_client, tech, "IB")
    visit_id = _shed_in(client, env, tech, "IB")

    _submit(mock_bldcms_client, visit_id, "IB", _pt_templates(tech, 0)[performa_index], TECH[tech][3])

    assert _outstanding(client, db_session, visit_id) == {"Pantograph PT-2"}


@pytest.mark.parametrize("tech", list(TECH))
def test_7_pt1_and_pt2_filed_with_different_types_satisfy_both(client, db_session, env, mock_bldcms_client, tech):
    _configure(mock_bldcms_client, tech, "IC")
    visit_id = _shed_in(client, env, tech, "IC")

    _submit(mock_bldcms_client, visit_id, "IC", _pt_templates(tech, 0)[1], TECH[tech][3])   # FTRTIL LX3600
    after_pt1 = _outstanding(client, db_session, visit_id)
    _submit(mock_bldcms_client, visit_id, "IC", _pt_templates(tech, 1)[0], TECH[tech][4])   # Schunk
    after_both = _outstanding(client, db_session, visit_id)

    assert after_pt1 == {"Pantograph PT-2"}
    assert after_both == set()


@pytest.mark.parametrize("tech", list(TECH))
def test_8_the_same_type_on_pt1_never_settles_pt2(client, db_session, env, mock_bldcms_client, tech):
    _configure(mock_bldcms_client, tech, "IA")
    visit_id = _shed_in(client, env, tech, "IA")

    _submit(mock_bldcms_client, visit_id, "IA", _pt_templates(tech, 0)[5], TECH[tech][3])   # IR03 on PT-1

    assert _outstanding(client, db_session, visit_id) == {"Pantograph PT-2"}


@pytest.mark.parametrize("tech", list(TECH))
def test_readiness_moves_by_exactly_one_per_slot(client, db_session, env, mock_bldcms_client, tech):
    _configure(mock_bldcms_client, tech, "IB")
    visit_id = _shed_in(client, env, tech, "IB")

    def satisfied():
        group = next(g for g in client.get(PANEL, headers=make_true_admin_headers(db_session)).json()["groups"]
                     if g["shed_visit_id"] == visit_id)
        return group["counts"]["satisfied"]

    before = satisfied()
    _submit(mock_bldcms_client, visit_id, "IB", _pt_templates(tech, 0)[3], TECH[tech][3])
    one = satisfied()
    _submit(mock_bldcms_client, visit_id, "IB", _pt_templates(tech, 1)[6], TECH[tech][4])
    two = satisfied()

    assert (one - before, two - before) == (1, 2)
