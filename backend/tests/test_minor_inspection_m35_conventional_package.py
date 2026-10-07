"""M35-Aux / M35-CP / M35-TM CONVENTIONAL (BL-DCMS migrations 046 / 047 / 048) in the frozen work package.

BL-DCMS applies 'IA / IC' literally: the Aux and TM slots have no applicability at all on IB, so a WAP-4
IB visit never receives them, while the Compressor (no printed schedule column -> all five variants) does.

    before: 30 on every variant (M1-HR 14 + M2-HR 16)
    after : IA 37, IA0 37, IB 31, IC 37, IC0 37 - still no Test Before / Test After.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers

ARRIVAL = datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc)
MINOR = ("IA", "IA0", "IB", "IC", "IC0")
M1, M2, AUX, CP, TM, SHIFT = 9, 8, 5, 15, 6, 18
NEW = {  # section id, section name, [(equipment id, code, label)], variants the slot applies to
    "M35-Aux": (AUX, [(300, "AUXILIARY_MOTOR", "Auxiliary Motor"), (301, "MVMT_AUXILIARY", "MVMT Auxiliary"),
                      (302, "ARNO", "ARNO"), (303, "MCPA", "MCPA")], ("IA", "IA0", "IC", "IC0")),
    "M35-CP": (CP, [(310, "COMPRESSOR", "Compressor")], MINOR),
    "M35-TM": (TM, [(320, "TRACTION_MOTOR", "Traction Motor"), (321, "TM_DDS", "TM DDS")], ("IA", "IA0", "IC", "IC0")),
}


def _row(variant, template_id, eq_id, code, label, section_id, section, order, performa=None):
    return {
        "applicability_id": template_id * 10 + MINOR.index(variant), "template_id": template_id,
        "template_name": f"{section} Minor Inspection - {label}", "technology": "CONVENTIONAL",
        "section_id": section_id, "section_name": section, "equipment_id": None, "equipment_name": None,
        "maintenance_type": None, "is_required": True, "minor_inspection_equipment_id": eq_id,
        "minor_inspection_equipment_code": code, "minor_inspection_equipment_name": label,
        "minor_inspection_equipment_label": label, "minor_inspection_performa_variant": performa,
        "minor_inspection_equipment_display_order": order,
    }


def _configure(mock, variant, with_m35=True, after_049=False):
    """What BL-DCMS's resolver returns for a WAP-4 on this variant."""
    target = mock.applicability.setdefault(("WAP4", "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for n in range(12):                                                         # M1-HR 042
        target.append(_row(variant, 3000 + n, 114 + n, f"M1_{n}", f"M1 slot {n}", M1, "M1-HR", n + 1))
    for slot, eq_id in enumerate((126, 127)):                                   # M1-HR 044 Pantograph
        for p in range(7):
            target.append(_row(variant, 4000 + slot * 10 + p, eq_id, f"HRPT_PT_{slot + 1}",
                               f"Pantograph PT-{slot + 1}", M1, "M1-HR", 13 + slot, performa=f"type-{p}"))
    for n in range(16):                                                         # M2-HR 045
        code = chr(ord("A") + n)
        if after_049 and code == "I" and variant in ("IA", "IB"):
            continue                                                            # 049: Air Flow Relays is IC-only
        target.append(_row(variant, 5000 + n, 200 + n, code, f"{code} - slot", M2, "M2-HR", n + 1))
    if with_m35:
        for section, (sid, slots, variants) in NEW.items():
            if variant not in variants:
                continue                                                        # no applicability row at all
            for order, (eq_id, code, label) in enumerate(slots, start=1):
                target.append(_row(variant, 6000 + eq_id, eq_id, code, label, sid, section, order))


@pytest.fixture()
def env(db_session, mock_loco_client):
    for sid, code in ((SHIFT, "SHIFT"), (M1, "M1-HR"), (M2, "M2-HR"), (AUX, "M35-Aux"), (CP, "M35-CP"), (TM, "M35-TM")):
        ensure_section(db_session, id=sid, code=code, name=code)
    mock_loco_client.add_locomotive("22324", loco_type="WAP4")
    return make_movement_supervisor_headers(db_session)


def _reqs(client, db_session, headers, variant):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": "22324", "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR",
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
            .filter(models.ShedVisitChecksheetPackage.shed_visit_id == resp.json()["id"]).all())


@pytest.mark.parametrize("variant", MINOR)
def test_before_the_batch_every_variant_has_30(client, db_session, env, mock_bldcms_client, variant):
    _configure(mock_bldcms_client, variant, with_m35=False)
    assert len(_reqs(client, db_session, env, variant)) == 30


@pytest.mark.parametrize("variant,total", [("IA", 37), ("IA0", 37), ("IB", 31), ("IC", 37), ("IC0", 37)])
def test_combined_totals_after_046_047_048(client, db_session, env, mock_bldcms_client, variant, total):
    _configure(mock_bldcms_client, variant)

    rows = _reqs(client, db_session, env, variant)

    assert len(rows) == total
    assert {r.workflow_stage_type for r in rows} == {"SCHEDULE_INSPECTION"}          # still no TB / TA
    by_section = {}
    for r in rows:
        by_section.setdefault(r.section_name_snapshot, []).append(r)
    assert len(by_section["M35-CP"]) == 1
    if variant == "IB":
        assert "M35-Aux" not in by_section and "M35-TM" not in by_section
    else:
        assert len(by_section["M35-Aux"]) == 4 and len(by_section["M35-TM"]) == 2


@pytest.mark.parametrize("variant,total", [("IA", 36), ("IA0", 37), ("IB", 30), ("IC", 37), ("IC0", 37)])
def test_totals_after_049_drop_the_empty_air_flow_relays_requirement(client, db_session, env, mock_bldcms_client,
                                                                     variant, total):
    """BL-DCMS migration 049: M2-HR CONVENTIONAL 'I - Air Flow Relays' (one IC-only checkpoint) no longer
    applies to IA or IB, so those packages lose one requirement with nothing to fill."""
    _configure(mock_bldcms_client, variant, after_049=True)

    rows = _reqs(client, db_session, env, variant)

    assert len(rows) == total
    m2 = {r.minor_inspection_equipment_label_snapshot for r in rows if r.section_name_snapshot == "M2-HR"}
    assert ("I - slot" in m2) is (variant not in ("IA", "IB"))
    assert len(m2) == (15 if variant in ("IA", "IB") else 16)
