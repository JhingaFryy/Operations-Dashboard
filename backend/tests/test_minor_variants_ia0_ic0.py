"""IA0 / IC0: two new MINOR schedule variants.

They are DISTINCT variants - never normalized to IA/IC and never given IA/IC checksheet
applicability. No applicability exists for them yet, so they must be honestly unconfigured: Shed
In works, no requirements are fabricated, and Shed Out can never pass vacuously.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from app.domain.schedule import SCHEDULE_MATRIX, is_valid_schedule_pair
from app.services.checksheet_work_package_service import MINOR_STAGE_SEQUENCE
from app.services.shed_visit_phase import PHASE_SCHEDULE_IN_PROGRESS, PHASE_SPARE, phase_display_label
from tests.conftest import make_movement_supervisor_headers

ARRIVAL = datetime(2026, 9, 1, 8, 32, tzinfo=timezone.utc)
STARTED = datetime(2026, 9, 1, 9, 10, tzinfo=timezone.utc)
COMPLETED = datetime(2026, 9, 1, 13, 48, tzinfo=timezone.utc)
DEPARTED = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
TECH = "WAG9HC"


def _shed_in(client, headers, variant, family="MINOR", loco="39018"):
    return client.post("/api/shed-visits/in", json={
        "loco_number": loco, "arrival_at": ARRIVAL.isoformat(),
        "schedule_family": family, "schedule_variant": variant,
        "arrival_condition": "WORKING", "log_book_bookings": [],
    }, headers=headers)


def _configure_all_stages(mock_bldcms_client, variant):
    for stage in MINOR_STAGE_SEQUENCE:
        mock_bldcms_client.add_applicability(TECH, schedule_variant=variant, workflow_stage_type=stage)


# ------------------------------------------------------------------ domain --

def test_matrix_contains_ia0_and_ic0_as_distinct_minor_variants():
    assert SCHEDULE_MATRIX["MINOR"] == frozenset({"IA", "IA0", "IB", "IC", "IC0"})
    assert SCHEDULE_MATRIX["MAJOR"] == frozenset({"IOH", "TOH"})
    assert is_valid_schedule_pair("MINOR", "IA0") and is_valid_schedule_pair("MINOR", "IC0")
    assert not is_valid_schedule_pair("MAJOR", "IA0")
    assert not is_valid_schedule_pair("MAJOR", "IC0")


@pytest.mark.parametrize("variant", ["IA0", "IC0"])
def test_phase_labels_use_the_exact_variant(variant):
    assert phase_display_label(PHASE_SPARE, variant) == f"Spare {variant}"
    assert phase_display_label(PHASE_SCHEDULE_IN_PROGRESS, variant) == f"{variant} In Progress"


# ---------------------------------------------------------------- Shed In --

@pytest.mark.parametrize("variant", ["IA0", "IC0"])
def test_shed_in_stores_the_exact_variant(client, db_session, mock_loco_client, mock_bldcms_client, variant):
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type=TECH)

    resp = _shed_in(client, headers, variant)
    assert resp.status_code == 200, resp.text

    visit = db_session.query(models.ShedVisit).one()
    assert (visit.schedule_family, visit.schedule_variant) == ("MINOR", variant)

    row = next(r for r in client.get("/api/shed-visits/current", headers=headers).json() if r["id"] == visit.id)
    assert row["schedule_variant"] == variant
    assert row["operational_phase"] == PHASE_SPARE
    assert row["display_label"] == f"Spare {variant}"


@pytest.mark.parametrize("family,variant", [
    ("MINOR", "IA1"), ("MINOR", "ia0"), ("MINOR", "IA 0"), ("MINOR", "IOH"),
    ("MAJOR", "IA0"), ("MAJOR", "IC0"),
])
def test_invalid_pairs_are_rejected(client, db_session, mock_loco_client, family, variant):
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type=TECH)
    assert _shed_in(client, headers, variant, family=family).status_code == 422
    assert db_session.query(models.ShedVisit).count() == 0


@pytest.mark.parametrize("variant", ["IOH", "TOH"])
def test_major_shed_in_is_unchanged(client, db_session, mock_loco_client, variant):
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type=TECH)
    resp = _shed_in(client, headers, variant, family="MAJOR")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stages_created"] == 0


# ---------------------------------------------------- start / complete --

@pytest.mark.parametrize("variant", ["IA0", "IC0"])
def test_start_and_complete_schedule(client, db_session, mock_loco_client, variant):
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type=TECH)
    visit_id = _shed_in(client, headers, variant).json()["id"]
    # Migration 012: Start Schedule needs Test Before satisfied first.
    test_before = db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit_id, stage_type="TEST_BEFORE").one()
    test_before.status, test_before.completed_at = "COMPLETED", STARTED
    db_session.commit()

    resp = client.post(f"/api/shed-visits/{visit_id}/start-schedule",
                       json={"started_at": STARTED.isoformat()}, headers=headers)
    assert resp.status_code == 200, resp.text
    row = next(r for r in client.get("/api/shed-visits/current", headers=headers).json() if r["id"] == visit_id)
    assert row["display_label"] == f"{variant} In Progress"

    resp = client.post(f"/api/shed-visits/{visit_id}/complete-schedule",
                       json={"completed_at": COMPLETED.isoformat()}, headers=headers)
    assert resp.status_code == 200, resp.text
    row = next(r for r in client.get("/api/shed-visits/current", headers=headers).json() if r["id"] == visit_id)
    # MINOR: Complete Schedule ends the inspection; Ready comes after Test After.
    assert row["display_label"] == f"{variant} Inspection Complete"
    assert row["available_actions"] == ["MARK_READY"]


# ------------------------------------------------ requirements: no fallback --

@pytest.mark.parametrize("variant,sibling", [("IA0", "IA"), ("IC0", "IC")])
def test_no_requirements_are_borrowed_from_the_sibling_variant(
    client, db_session, mock_loco_client, mock_bldcms_client, variant, sibling
):
    """IA (or IC) is fully configured; IA0 (or IC0) is not. Shed In must not snapshot the
    sibling's requirements, and explicit generation reports NO_APPLICABILITY_CONFIGURED."""
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type=TECH)
    _configure_all_stages(mock_bldcms_client, sibling)

    visit_id = _shed_in(client, headers, variant).json()["id"]
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0
    assert db_session.query(models.ShedVisitChecksheetRequirement).count() == 0

    resp = client.post(f"/api/shed-visits/{visit_id}/checksheet-work-package", headers=headers)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "NO_APPLICABILITY_CONFIGURED"
    assert detail["schedule_variant"] == variant
    assert db_session.query(models.ShedVisitChecksheetRequirement).count() == 0


def test_ia_is_unchanged_and_still_generates_from_its_own_applicability(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type=TECH)
    _configure_all_stages(mock_bldcms_client, "IA")
    _configure_all_stages(mock_bldcms_client, "IA0")   # must never leak into an IA visit

    assert _shed_in(client, headers, "IA").status_code == 200
    reqs = db_session.query(models.ShedVisitChecksheetRequirement).all()
    assert len(reqs) == len(MINOR_STAGE_SEQUENCE)
    ia_ids = {i["applicability_id"] for k, items in mock_bldcms_client.applicability.items()
              if k[2] == "IA" for i in items}
    assert {r.applicability_id for r in reqs} == ia_ids


# --------------------------------------------- readiness: never vacuous --

@pytest.mark.parametrize("variant", ["IA0", "IC0"])
def test_shed_out_is_blocked_on_zero_requirements(
    client, db_session, mock_loco_client, mock_bldcms_client, variant
):
    """Even with every stage COMPLETED and no bookings, an unconfigured variant has no work
    package, and a missing package is a blocker - never "nothing to do"."""
    headers = make_movement_supervisor_headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type=TECH)
    visit_id = _shed_in(client, headers, variant).json()["id"]
    for stage in db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit_id):
        stage.status = "COMPLETED"
    db_session.commit()
    client.post(f"/api/shed-visits/{visit_id}/start-schedule",
                json={"started_at": STARTED.isoformat()}, headers=headers)
    client.post(f"/api/shed-visits/{visit_id}/complete-schedule",
                json={"completed_at": COMPLETED.isoformat()}, headers=headers)

    elig = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=headers)
    assert elig.status_code == 200, elig.text
    body = elig.json()
    assert body["eligible"] is False
    assert "WORK_PACKAGE_NOT_GENERATED" in {b["kind"] for b in body["checksheet_blockers"]}

    out = client.post(f"/api/shed-visits/{visit_id}/out",
                      json={"departed_at": DEPARTED.isoformat()}, headers=headers)
    assert out.status_code == 409
    assert out.json()["detail"]["code"] == "SHED_OUT_BLOCKED"
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit_id).status != "CLOSED"
