"""M4-HR CONVENTIONAL (BL-DCMS migration 051) in the frozen work package.

BL-DCMS seeds 16 WAP-4 M4-HR slots with variant-aware applicability: AXLE BOX (UST) prints only IC rows, so it
has no applicability at all on IA / IB and a WAP-4 IA or IB visit never receives it. Every other slot applies to
all five variants. One requirement per Minor equipment; no Test Before / Test After.

    before (after 049): IA 36, IA0 37, IB 30, IC 37, IC0 37
    after 051         : IA 51, IA0 53, IB 45, IC 53, IC0 53
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers
from tests.test_minor_inspection_m35_conventional_package import (
    AUX, CP, M1, M2, MINOR, SHIFT, TM, _configure, _row)

ARRIVAL = datetime(2026, 9, 21, 7, 30, tzinfo=timezone.utc)
M4 = 10
IC_ONLY = ("IA0", "IC", "IC0")
CODES = ["BOGIE_FRAME", "BRAKE_RIGGING", "TM_BOGIE", "HORN_STAY", "HELICAL_SPRING", "BUFFERS",
         "RAIL_GUARD_AND_CATTLE_GUARD", "HAND_BREAK", "CBC", "SCREW_COUPLING", "DRAFT_GEAR_AND_YOKE", "AXLE_BOX_UST",
         "MSU", "SANDER", "GEAR_CASE_CCF", "WHEEL_PROFILE_MEASUREMENT"]
BEFORE = {"IA": 36, "IA0": 37, "IB": 30, "IC": 37, "IC0": 37}
AFTER = {"IA": 51, "IA0": 53, "IB": 45, "IC": 53, "IC0": 53}


def _configure_m4(mock, variant):
    """What BL-DCMS's resolver returns for a WAP-4 on this variant after 051."""
    target = mock.applicability.setdefault(("WAP4", "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for order, code in enumerate(CODES, start=1):
        if code == "AXLE_BOX_UST" and variant not in IC_ONLY:
            continue                                                            # no applicability row at all
        target.append(_row(variant, 7000 + order, 150 + order, code, code.replace("_", " "), M4, "M4-HR", order))


@pytest.fixture()
def env(db_session, mock_loco_client):
    for sid, code in ((SHIFT, "SHIFT"), (M1, "M1-HR"), (M2, "M2-HR"), (AUX, "M35-Aux"), (CP, "M35-CP"),
                      (TM, "M35-TM"), (M4, "M4-HR")):
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
def test_baseline_before_051(client, db_session, env, mock_bldcms_client, variant):
    _configure(mock_bldcms_client, variant, after_049=True)
    assert len(_reqs(client, db_session, env, variant)) == BEFORE[variant]


@pytest.mark.parametrize("variant", MINOR)
def test_combined_totals_after_051(client, db_session, env, mock_bldcms_client, variant):
    _configure(mock_bldcms_client, variant, after_049=True)
    _configure_m4(mock_bldcms_client, variant)

    rows = _reqs(client, db_session, env, variant)

    assert len(rows) == AFTER[variant]
    assert {r.workflow_stage_type for r in rows} == {"SCHEDULE_INSPECTION"}          # still no TB / TA
    m4 = [r for r in rows if r.section_name_snapshot == "M4-HR"]
    assert len(m4) == AFTER[variant] - BEFORE[variant] == (16 if variant in IC_ONLY else 15)
    assert all(r.is_required for r in m4)
    assert len({r.minor_inspection_equipment_id for r in m4}) == len(m4)    # one per slot


@pytest.mark.parametrize("variant", MINOR)
def test_axle_box_ust_only_on_ia0_ic_ic0(client, db_session, env, mock_bldcms_client, variant):
    _configure(mock_bldcms_client, variant, after_049=True)
    _configure_m4(mock_bldcms_client, variant)

    codes = {r.minor_inspection_equipment_code_snapshot for r in _reqs(client, db_session, env, variant)
             if r.section_name_snapshot == "M4-HR"}

    assert ("AXLE_BOX_UST" in codes) is (variant in IC_ONLY)
    assert codes - {"AXLE_BOX_UST"} == set(CODES) - {"AXLE_BOX_UST"}
