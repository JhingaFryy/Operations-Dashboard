"""3-Phase IA0 / IC0 with TB/TA configured and Inspection NOT configured (staged Minor rollout).

BL-DCMS applicability (mocked here exactly as the integration returns it) maps the shared 3-Phase
TB/TA performa (template 179) to IA0/IC0 TEST_BEFORE and TEST_AFTER only. The package must freeze
exactly those two requirements, never borrow IA/IC rows, never fabricate an Inspection row, and
everything downstream - the pending panel, stage reconciliation, Shed Out - must treat Inspection
as unconfigured, not complete.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers

ARRIVAL = datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)
STARTED = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
LOCO_3PH = "39015"
LOCO_CONV = "22560"
PANEL = "/api/admin/pending-checksheet-requirements"
TEMPLATE_179 = 179


def _map(mock_bldcms_client, variant, stage, applicability_id, loco_type="WAP7"):
    """One BL-DCMS applicability row for the shared 3-Phase TB/TA performa."""
    mock_bldcms_client.applicability.setdefault((loco_type, "MINOR", variant, stage), []).append({
        "applicability_id": applicability_id, "template_id": TEMPLATE_179,
        "template_name": "3 Phase Loco Test Before / Test After", "technology": "3_PHASE",
        "section_id": 1, "section_name": "SHIFT", "equipment_id": None, "equipment_name": None,
        "maintenance_type": None, "schedule_family": "MINOR", "schedule_variant": variant,
        "workflow_stage_type": stage, "is_required": True,
    })


def _production_like_applicability(mock_bldcms_client):
    """Mirrors live rdcms after migration 027: TB/TA for IA, IB, IC, IA0, IC0; no Inspection."""
    ids = {"IA": (1, 2), "IB": (3, 4), "IC": (5, 6), "IA0": (20, 21), "IC0": (22, 23)}
    for variant, (tb, ta) in ids.items():
        _map(mock_bldcms_client, variant, "TEST_BEFORE", tb)
        _map(mock_bldcms_client, variant, "TEST_AFTER", ta)
    return ids


def _shed_in(client, headers, variant, loco=LOCO_3PH):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(),
        "schedule_family": "MINOR", "schedule_variant": variant,
        "arrival_condition": "WORKING", "log_book_bookings": [],
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _requirements(db_session, visit_id):
    return (
        db_session.query(models.ShedVisitChecksheetRequirement)
        .join(models.ShedVisitChecksheetPackage)
        .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id)
        .all()
    )


@pytest.fixture()
def setup(db_session, mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=1, code="SHIFT", name="SHIFT")
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive(LOCO_3PH, loco_type="WAP7")
    mock_loco_client.add_locomotive(LOCO_CONV, loco_type="WAP4")
    ids = _production_like_applicability(mock_bldcms_client)
    return headers, ids


# ------------------------------------------------------------ package generation --

@pytest.mark.parametrize("variant", ["IA0", "IC0"])
def test_package_freezes_exactly_tb_and_ta_from_template_179(client, db_session, setup, variant):
    headers, ids = setup
    visit_id = _shed_in(client, headers, variant)

    reqs = _requirements(db_session, visit_id)
    assert sorted((r.workflow_stage_type, r.template_id, r.applicability_id) for r in reqs) == sorted([
        ("TEST_BEFORE", TEMPLATE_179, ids[variant][0]),
        ("TEST_AFTER", TEMPLATE_179, ids[variant][1]),
    ])
    assert all(r.is_required and r.is_active and r.section_name_snapshot == "SHIFT"
               and r.equipment_id_snapshot is None for r in reqs)
    # No fabricated Inspection requirement.
    assert not [r for r in reqs if r.workflow_stage_type == "SCHEDULE_INSPECTION"]


@pytest.mark.parametrize("variant,sibling", [("IA0", "IA"), ("IA", "IA0"), ("IC0", "IC"), ("IC", "IC0"),
                                             ("IB", "IA0"), ("IB", "IC0")])
def test_exact_variant_match_never_borrows_sibling_rows(client, db_session, setup, variant, sibling):
    headers, ids = setup
    visit_id = _shed_in(client, headers, variant)
    got = {r.applicability_id for r in _requirements(db_session, visit_id)}
    assert got == set(ids[variant])
    assert not got & set(ids[sibling])


def test_conventional_ia0_gets_no_package(client, db_session, setup):
    """3-Phase IA0 is configured; Conventional IA0 is not. BL-DCMS resolves by the loco's own
    technology, so a Conventional loco must stay honestly unconfigured - never template 179."""
    headers, _ = setup
    visit_id = _shed_in(client, headers, "IA0", loco=LOCO_CONV)
    assert _requirements(db_session, visit_id) == []

    resp = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "NO_APPLICABILITY_CONFIGURED"


# ------------------------------------------------------------------ pending panel --

@pytest.mark.parametrize("variant", ["IA0", "IC0"])
def test_panel_reports_inspection_unconfigured_even_once_tb_ta_are_submitted(
    client, db_session, setup, mock_bldcms_client, variant
):
    headers, _ = setup
    visit_id = _shed_in(client, headers, variant)

    group = next(g for g in client.get(PANEL, headers=headers).json()["groups"] if g["shed_visit_id"] == visit_id)
    assert group["work_package_generated"] is True
    assert group["generation_note"] is None
    assert group["unconfigured_stages"] == ["SCHEDULE_INSPECTION"]
    assert sorted(r["workflow_stage_type"] for r in group["requirements"]) == ["TEST_AFTER", "TEST_BEFORE"]

    for stage in ("TEST_BEFORE", "TEST_AFTER"):
        mock_bldcms_client.add_checksheet(visit_id, template_id=TEMPLATE_179, section_id=1,
                                          schedule_variant=variant, workflow_stage_type=stage,
                                          status="SUBMITTED")
    group = next(g for g in client.get(PANEL, headers=headers).json()["groups"] if g["shed_visit_id"] == visit_id)
    assert group["requirements"] == []            # both satisfied, so they leave the queue ...
    assert group["unconfigured_stages"] == ["SCHEDULE_INSPECTION"]   # ... but Inspection is NOT done


def test_fully_configured_minor_group_reports_no_unconfigured_stages(
    client, db_session, setup, mock_bldcms_client, mock_loco_client
):
    headers, _ = setup
    _map(mock_bldcms_client, "IB", "SCHEDULE_INSPECTION", 99)
    ib_visit = _shed_in(client, headers, "IB")
    groups = {g["shed_visit_id"]: g for g in client.get(PANEL, headers=headers).json()["groups"]}
    assert groups[ib_visit]["unconfigured_stages"] == []


def test_visit_without_package_has_no_unconfigured_stage_claims(client, db_session, setup):
    headers, _ = setup
    visit_id = _shed_in(client, headers, "IA0", loco=LOCO_CONV)
    group = next(g for g in client.get(PANEL, headers=headers).json()["groups"] if g["shed_visit_id"] == visit_id)
    assert group["work_package_generated"] is False
    assert group["unconfigured_stages"] == []     # unknown, not "only Inspection missing"


# -------------------------------------------- reconciliation, TA gate, Shed Out --

@pytest.mark.parametrize("variant", ["IA0", "IC0"])
def test_tb_satisfied_and_inspection_unconfigured_keeps_ta_and_shed_out_blocked(
    client, db_session, setup, mock_bldcms_client, variant
):
    headers, _ = setup
    visit_id = _shed_in(client, headers, variant)
    client.post(f"/api/shed-visits/{visit_id}/start-schedule",
                json={"started_at": STARTED.isoformat()}, headers=headers)
    # Even TA itself submitted: it must still not complete while Inspection is unconfigured.
    for stage in ("TEST_BEFORE", "TEST_AFTER"):
        mock_bldcms_client.add_checksheet(visit_id, template_id=TEMPLATE_179, section_id=1,
                                          schedule_variant=variant, workflow_stage_type=stage,
                                          status="SUBMITTED")

    for _ in range(3):   # reconciliation advances at most one stage per call
        resp = client.post(f"/api/shed-visits/{visit_id}/reconcile-checksheet-stages", headers=headers)
        assert resp.status_code == 200, resp.text
    reasons = {s["workflow_stage_type"]: s for s in resp.json()["stages"]}
    assert reasons["TEST_BEFORE"]["current_status"] == "COMPLETED"
    assert reasons["SCHEDULE_INSPECTION"]["reason"] == "STAGE_NOT_CONFIGURED"
    assert reasons["SCHEDULE_INSPECTION"]["current_status"] != "COMPLETED"
    assert reasons["TEST_AFTER"]["reason"] == "PREVIOUS_STAGE_NOT_COMPLETED"
    assert reasons["TEST_AFTER"]["current_status"] != "COMPLETED"

    resp = client.post(f"/api/shed-visits/{visit_id}/stages/schedule-inspection/complete", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "STAGE_NOT_CONFIGURED"

    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=headers).json()
    assert elig["eligible"] is False
    assert {b["stage_type"] for b in elig["stage_blockers"]} >= {"SCHEDULE_INSPECTION", "TEST_AFTER"}
