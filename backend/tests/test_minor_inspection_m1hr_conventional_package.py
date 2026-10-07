"""M1-HR CONVENTIONAL (BL-DCMS migration 042) in the frozen work package.

Twelve new SCHEDULE_INSPECTION requirements for a WAP-4, one per printed block of
"ELS/BL/M1HR - Check sheet for WAP4 for Inspection schedule", in printed order, for every MINOR
variant. Two things this proves and the BL-DCMS tests cannot:

  * HT-1 / HT-2 and Driving Desk-1 / Desk-2 print nearly identical rows, but each is its OWN
    requirement here: filing one never settles the other (user decisions D1 and D2).
  * CONVENTIONAL still has NO Test Before and NO Test After performa (D6), so a WAP-4 Minor visit
    would receive inspection work it cannot complete. The seed is configuration staging only, and
    these tests pin that gap rather than papering over it.

The 3_PHASE package is untouched: its per-variant baseline of 63 requirements (61 Minor Inspection
equipment collapsed from 63 applicability rows, plus Test Before and Test After) is asserted here
so that adding a second technology can never quietly change it.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_true_admin_headers

ARRIVAL = datetime(2026, 9, 19, 7, 30, tzinfo=timezone.utc)
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
PANEL = "/api/admin/pending-checksheet-requirements"
M1, SHIFT = 9, 18
WAP4, WAP7 = "22560", "30476"

# (minor equipment id, source code, printed name, template id) in printed order - migration 042.
CONV = [
    (501, "HT_1", "HT-1 : Reverser & EPC", 701),
    (502, "HT_2", "HT-2 : Reverser & EPC", 702),
    (503, "DRIVING_DESK_1", "Driving Desk-1", 703),
    (504, "DRIVING_DESK_2", "Driving Desk-2", 704),
    (505, "SWITCH_BOARD", "Switch board", 705),
    (506, "HVSI_1_AND_2", "HVSI-1 & 2", 706),
    (507, "ZCPA", "ZCPA", 707),
    (508, "ZPT", "ZPT", 708),
    (509, "TMDDS", "TMDDS", 709),
    (510, "OTHER_PROGRAMME_SWITCHES", "Other programme Switches", 710),
    (511, "TK_PANEL", "TK PANEL", 711),
    (512, "C118", "C118", 712),
]


def _conventional(mock, variant):
    """Exactly what BL-DCMS's /integration/applicability/resolve returns after migration 042."""
    target = mock.applicability.setdefault(("WAP-4", "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for order, (eq_id, code, label, tpl) in enumerate(CONV, start=1):
        target.append({
            "applicability_id": tpl * 10 + MINOR.index(variant), "template_id": tpl,
            "template_name": f"M1-HR Minor Inspection - {label}", "technology": "CONVENTIONAL",
            "section_id": M1, "section_name": "M1-HR", "equipment_id": None, "equipment_name": None,
            "maintenance_type": None, "is_required": True, "minor_inspection_equipment_id": eq_id,
            "minor_inspection_equipment_code": code, "minor_inspection_equipment_name": label,
            "minor_inspection_equipment_label": label, "minor_inspection_performa_variant": None,
            "minor_inspection_equipment_display_order": order,
        })


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    for sid, code in ((SHIFT, "SHIFT"), (M1, "M1-HR")):
        ensure_section(db_session, id=sid, code=code, name=code)
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive(WAP4, loco_type="WAP-4")
    return headers


def _shed_in(client, headers, variant="IB", loco=WAP4):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []},
        headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _reqs(db_session, visit_id, section=None):
    q = (db_session.query(models.ShedVisitChecksheetRequirement)
         .join(models.ShedVisitChecksheetPackage)
         .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id)
         .order_by(models.ShedVisitChecksheetRequirement.id))
    rows = q.all()
    return [r for r in rows if section is None or r.section_name_snapshot == section]


def _submit(mock, visit_id, variant, template_id, minor_id):
    mock.add_checksheet(shed_visit_id=visit_id, template_id=template_id, section_id=M1,
                        schedule_family="MINOR", schedule_variant=variant,
                        workflow_stage_type="SCHEDULE_INSPECTION", status="SUBMITTED",
                        minor_inspection_equipment_id=minor_id)


def _outstanding(client, db_session, visit_id):
    """The pending panel is section-scoped; an Admin sees every section's rows."""
    group = next(g for g in client.get(PANEL, headers=make_true_admin_headers(db_session)).json()["groups"]
                 if g["shed_visit_id"] == visit_id)
    return group


# =========================================================== the twelve requirements ==

@pytest.mark.parametrize("variant", MINOR)
def test_23_a_new_wap4_visit_gets_exactly_twelve_requirements_in_printed_order(
        client, db_session, env, mock_bldcms_client, variant):
    _conventional(mock_bldcms_client, variant)

    rows = _reqs(db_session, _shed_in(client, env, variant), section="M1-HR")

    assert len(rows) == 12
    assert [(r.minor_inspection_equipment_label_snapshot, r.template_id) for r in rows] == \
        [(label, tpl) for _eq, _code, label, tpl in CONV]
    assert all(r.is_required and r.is_active for r in rows)
    assert all(r.workflow_stage_type == "SCHEDULE_INSPECTION" for r in rows)
    assert all(r.equipment_id_snapshot is None for r in rows)


@pytest.mark.parametrize("variant", MINOR)
def test_24_a_wap4_minor_visit_still_has_no_test_before_and_no_test_after(
        client, db_session, env, mock_bldcms_client, variant):
    """D6: CONVENTIONAL TB/TA performa does not exist yet, so the visit gains inspection work only."""
    _conventional(mock_bldcms_client, variant)

    rows = _reqs(db_session, _shed_in(client, env, variant))

    assert {r.workflow_stage_type for r in rows} == {"SCHEDULE_INSPECTION"}
    assert [r for r in rows if r.workflow_stage_type == "TEST_BEFORE"] == []
    assert [r for r in rows if r.workflow_stage_type == "TEST_AFTER"] == []
    assert len(rows) == 12


def test_before_this_seed_a_wap4_visit_had_no_requirements_at_all(client, db_session, env):
    """The package baseline this migration moves: CONVENTIONAL was 0, and only the 12 are added."""
    rows = _reqs(db_session, _shed_in(client, env, "IB"))

    assert rows == []


# ============================================ the near-identical slots stay separate ==

def test_3_ht1_and_ht2_are_two_requirements(client, db_session, env, mock_bldcms_client):
    _conventional(mock_bldcms_client, "IB")

    rows = [r for r in _reqs(db_session, _shed_in(client, env, "IB"), section="M1-HR")
            if r.minor_inspection_equipment_id in (501, 502)]

    assert len(rows) == 2
    assert {r.minor_inspection_equipment_label_snapshot for r in rows} == {
        "HT-1 : Reverser & EPC", "HT-2 : Reverser & EPC"}
    assert rows[0].template_id != rows[1].template_id


def test_7_the_two_driving_desks_are_two_requirements(client, db_session, env, mock_bldcms_client):
    _conventional(mock_bldcms_client, "IB")

    rows = [r for r in _reqs(db_session, _shed_in(client, env, "IB"), section="M1-HR")
            if r.minor_inspection_equipment_id in (503, 504)]

    assert len(rows) == 2
    assert {r.minor_inspection_equipment_label_snapshot for r in rows} == {"Driving Desk-1", "Driving Desk-2"}
    assert rows[0].template_id != rows[1].template_id


def test_submitting_ht1_leaves_ht2_outstanding(client, db_session, env, mock_bldcms_client):
    """Identical printed text, two physical HT panels: one can never settle the other."""
    _conventional(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")

    _submit(mock_bldcms_client, visit_id, "IB", 701, 501)

    outstanding = {b["minor_inspection_equipment_label"] for b in _outstanding(client, db_session, visit_id)["requirements"]}
    assert "HT-1 : Reverser & EPC" not in outstanding
    assert "HT-2 : Reverser & EPC" in outstanding


def test_submitting_desk_1_leaves_desk_2_outstanding(client, db_session, env, mock_bldcms_client):
    _conventional(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")

    _submit(mock_bldcms_client, visit_id, "IB", 703, 503)

    outstanding = {b["minor_inspection_equipment_label"] for b in _outstanding(client, db_session, visit_id)["requirements"]}
    assert "Driving Desk-1" not in outstanding
    assert "Driving Desk-2" in outstanding


def test_each_of_the_twelve_is_counted_and_satisfied_independently(
        client, db_session, env, mock_bldcms_client):
    _conventional(mock_bldcms_client, "IC")
    visit_id = _shed_in(client, env, "IC")
    before = _outstanding(client, db_session, visit_id)["counts"]["satisfied"]

    for step, (eq_id, _code, _label, tpl) in enumerate(CONV, start=1):
        _submit(mock_bldcms_client, visit_id, "IC", tpl, eq_id)
        assert _outstanding(client, db_session, visit_id)["counts"]["satisfied"] == before + step

    assert _outstanding(client, db_session, visit_id)["requirements"] == []


def test_11_hvsi_1_and_2_is_one_requirement_not_two(client, db_session, env, mock_bldcms_client):
    _conventional(mock_bldcms_client, "IA")

    rows = _reqs(db_session, _shed_in(client, env, "IA"), section="M1-HR")
    hvsi = [r for r in rows if "HVSI" in r.minor_inspection_equipment_label_snapshot]

    assert len(hvsi) == 1
    assert hvsi[0].minor_inspection_equipment_label_snapshot == "HVSI-1 & 2"


# ======================================================== the 3_PHASE package is untouched ==

def test_27_a_3_phase_visit_is_unaffected_by_the_conventional_configuration(
        client, db_session, env, mock_loco_client, mock_bldcms_client):
    """A WAP-7 resolves against 3_PHASE only: the CONVENTIONAL rows are keyed to WAP-4 and can never
    leak into its package."""
    mock_loco_client.add_locomotive(WAP7, loco_type="WAP-7")
    for variant in MINOR:
        _conventional(mock_bldcms_client, variant)

    rows = _reqs(db_session, _shed_in(client, env, "IB", loco=WAP7))

    assert rows == []                                   # nothing was mocked for WAP-7 in this module
    assert all(r.minor_inspection_equipment_id not in [e for e, *_ in CONV] for r in rows)


def test_27_the_3_phase_baseline_of_63_requirements_is_composed_the_documented_way(
        client, db_session, env, mock_loco_client, mock_bldcms_client):
    """Production's 3_PHASE Minor package: 63 SCHEDULE_INSPECTION applicability rows collapse to 61
    requirements (PT-1 and PT-2 each return two make performas for one slot), plus Test Before and
    Test After = 63 requirements. Migration 042 adds no 3_PHASE row, so this total cannot move."""
    mock_loco_client.add_locomotive(WAP7, loco_type="WAP-7")
    target = mock_bldcms_client.applicability.setdefault(
        ("WAP-7", "MINOR", "IB", "SCHEDULE_INSPECTION"), [])
    for n in range(61):                                  # 61 Minor Inspection equipment slots
        performas = 2 if n < 2 else 1                    # the two pantograph slots offer two makes
        for p in range(performas):
            target.append({
                "applicability_id": 9000 + n * 10 + p, "template_id": 800 + n * 10 + p,
                "template_name": f"3_PHASE slot {n}", "technology": "3_PHASE", "section_id": M1,
                "section_name": "M1-HR", "equipment_id": None, "equipment_name": None,
                "maintenance_type": None, "is_required": True,
                "minor_inspection_equipment_id": 900 + n, "minor_inspection_equipment_code": f"SLOT_{n}",
                "minor_inspection_equipment_name": f"slot {n}", "minor_inspection_equipment_label": f"slot {n}",
                "minor_inspection_performa_variant": f"MAKE_{p}" if performas > 1 else None,
                "minor_inspection_equipment_display_order": n + 1})
    assert len(target) == 63                             # applicability rows, before collapse
    for stage in ("TEST_BEFORE", "TEST_AFTER"):
        mock_bldcms_client.applicability.setdefault(("WAP-7", "MINOR", "IB", stage), []).append({
            "applicability_id": 9900 + len(stage), "template_id": 179, "template_name": "TB/TA",
            "technology": "3_PHASE", "section_id": SHIFT, "section_name": "SHIFT", "equipment_id": None,
            "equipment_name": None, "maintenance_type": None, "is_required": True})
    for variant in MINOR:
        _conventional(mock_bldcms_client, variant)       # CONVENTIONAL exists alongside it

    rows = _reqs(db_session, _shed_in(client, env, "IB", loco=WAP7))

    inspection = [r for r in rows if r.workflow_stage_type == "SCHEDULE_INSPECTION"]
    assert len(inspection) == 61                         # 63 rows collapsed to 61 slots
    assert len(rows) == 63                               # + Test Before + Test After
    assert {r.workflow_stage_type for r in rows} == {"SCHEDULE_INSPECTION", "TEST_BEFORE", "TEST_AFTER"}
    assert all(r.minor_inspection_equipment_id not in [e for e, *_ in CONV] for r in rows)
