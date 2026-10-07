"""M8-HR / M9-HR (BL-DCMS migrations 053-058) in the frozen work package.

BL-DCMS now scopes Minor equipment by locomotive model (053): M9-HR's Hotel Load Converter applies to WAP-7
only, so BL-DCMS resolves it for loco_type 'WAP7' and never for the WAG9 family. This app passes each
locomotive's own Loco Master loco_type through unchanged, so a WAP-7 package holds the HLC requirement and a
WAG9H/WAG9HC package does not. A visit frozen before the seeds never gains them.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers
from tests.test_minor_inspection_m35_conventional_package import MINOR, _row

ARRIVAL = datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc)
M8, M9 = 13, 14
M9_CORE = ["VACUUM_CIRCUIT_BREAKER", "TRACTION_CONVERTER", "HB_CUBICLE", "SURGE_ARRESTOR", "KEY_INTER_LOCKING",
           "EQUIPMENTS_EARTHING", "EARTHING_SWITCH_BV_BOX", "HARMONIC_FILTER_CAPACITOR", "RTIS", "BATTERY_BOX", "BATTERY_SET"]
M8_3PH = ["MAIN_TRANSFORMER", "OIL_COOLING_UNIT_RADIATOR", "HARMONIC_RESISTANCE", "PRIMARY_VOLTAGE_TRANSFORMER",
          "HIGH_VOLTAGE_BUSHING", "AUXILIARY_TRANSFORMER", "CONSERVATOR_1_2", "TRACTION_CONVERTER_SR",
          "MAIN_BUSHING_HV_CABLE_WITH_PLUG", "MUB_RESISTOR", "HOTEL_LOAD_CONVERTER"]


def _configure(mock, loco_type, variant):
    """What BL-DCMS resolves after 053-058 for this loco_type (model scope applied server-side)."""
    target = mock.applicability.setdefault((loco_type, "MINOR", variant, "SCHEDULE_INSPECTION"), [])
    for order, code in enumerate(M8_3PH, start=1):
        target.append(_row(variant, 9100 + order, 900 + order, code, code, M8, "M8-HR", order))
    m9 = M9_CORE + (["HOTEL_LOAD_CONVERTER"] if loco_type == "WAP7" else [])
    for order, code in enumerate(m9, start=1):
        target.append(_row(variant, 9200 + order, 950 + order, code, code, M9, "M9-HR", order))
    for row in target:
        row["technology"] = "3_PHASE"


@pytest.fixture()
def env(db_session, mock_loco_client):
    for sid, code in ((M8, "M8-HR"), (M9, "M9-HR")):
        ensure_section(db_session, id=sid, code=code, name=code)
    for loco, loco_type in (("37350", "WAP7"), ("44432", "WAG9H"), ("41680", "WAG9HC"), ("37351", "WAP7")):
        mock_loco_client.add_locomotive(loco, loco_type=loco_type)
    return make_movement_supervisor_headers(db_session)


def _reqs(client, db_session, headers, loco, variant):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(), "schedule_family": "MINOR", "schedule_variant": variant,
        "arrival_condition": "WORKING", "log_book_bookings": []}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"], (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
                               .filter(models.ShedVisitChecksheetPackage.shed_visit_id == resp.json()["id"]).all())


@pytest.mark.parametrize("variant", MINOR)
@pytest.mark.parametrize("loco,loco_type,m9", [("37350", "WAP7", 12), ("44432", "WAG9H", 11), ("41680", "WAG9HC", 11)])
def test_m9_hlc_only_in_wap7_packages_and_m8_hlc_everywhere(client, db_session, env, mock_bldcms_client, variant, loco,
                                                           loco_type, m9):
    for lt in ("WAP7", "WAG9H", "WAG9HC"):
        _configure(mock_bldcms_client, lt, variant)
    _vid, rows = _reqs(client, db_session, env, loco, variant)
    by = {}
    for r in rows:
        by.setdefault(r.section_name_snapshot, set()).add(r.minor_inspection_equipment_code_snapshot)
    assert len(by["M9-HR"]) == m9 and ("HOTEL_LOAD_CONVERTER" in by["M9-HR"]) is (loco_type == "WAP7")
    assert len(by["M8-HR"]) == 11 and "HOTEL_LOAD_CONVERTER" in by["M8-HR"]


def test_a_visit_frozen_before_the_seeds_never_gains_m8_m9(client, db_session, env, mock_bldcms_client):
    mock_bldcms_client.applicability[("WAP7", "MINOR", "IC", "SCHEDULE_INSPECTION")] = [
        _row("IC", 5000, 200, "A", "A - slot", 8, "M2-HR", 1)]
    ensure_section(db_session, id=8, code="M2-HR", name="M2-HR")
    old, before = _reqs(client, db_session, env, "37350", "IC")
    snapshot = sorted((r.id, r.template_id, r.is_required, r.is_active) for r in before)

    _configure(mock_bldcms_client, "WAP7", "IC")                      # 054-058 applied while the visit is open
    body = client.post(f"/api/shed-visits/{old}/checksheet-work-package/refresh", headers=env).json()
    assert body["added"] == []
    db_session.expire_all()
    after = (db_session.query(models.ShedVisitChecksheetRequirement).join(models.ShedVisitChecksheetPackage)
             .filter(models.ShedVisitChecksheetPackage.shed_visit_id == old).all())
    assert sorted((r.id, r.template_id, r.is_required, r.is_active) for r in after) == snapshot

    _new, rows = _reqs(client, db_session, env, "37351", "IC")
    assert {r.section_name_snapshot for r in rows} >= {"M8-HR", "M9-HR"}
