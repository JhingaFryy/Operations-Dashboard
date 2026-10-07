"""MAJOR Shed Out, and what the operator is told when Shed Out refuses.

Checksheets are mandatory for MAJOR exactly as for MINOR: a MAJOR visit leaves only when it is
READY, every required Major checksheet is satisfied and every booking is attended. It never
depends on Test Before / Test After / Inspection stages, which MAJOR does not have.

When Shed Out refuses, the refusal names the gate: the checksheet gate returns the same structured
CHECKSHEETS_INCOMPLETE payload Ready refuses with (under `checksheets`), bookings keep their own
blocker list, and the message is never just "not eligible".
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from tests.conftest import (
    make_assignment,
    make_booking,
    make_defect_type,
    make_minor_stages,
    make_movement_supervisor_headers,
    make_section,
    make_shed_visit,
)

ARRIVAL = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
STARTED = ARRIVAL + timedelta(hours=1)
READY_AT = ARRIVAL + timedelta(days=5)
DEPARTED = READY_AT + timedelta(hours=2)


def _package(db, visit_id, package_id=1):
    db.add(models.ShedVisitChecksheetPackage(
        id=package_id, shed_visit_id=visit_id, generated_by=None,
        generated_at=datetime.now(timezone.utc)))
    db.commit()


def _major_req(db, req_id, *, is_required=True, is_active=True, package_id=1, **over):
    now = datetime.now(timezone.utc)
    data = dict(
        id=req_id, package_id=package_id, workflow_stage_type=None, applicability_id=None,
        requirement_source="MAJOR_EQUIPMENT", template_id=req_id,
        is_required=is_required, is_active=is_active,
        template_name_snapshot=f"Major T{req_id}", technology_snapshot="3_PHASE",
        section_id_snapshot=1, section_name_snapshot="M35-Aux",
        equipment_id_snapshot=req_id * 10, equipment_name_snapshot=f"Equipment {req_id}",
        maintenance_type_snapshot=None, created_at=now, updated_at=now,
    )
    data.update(over)
    row = models.ShedVisitChecksheetRequirement(**data)
    db.add(row)
    db.commit()
    return row


def _satisfy(bldcms, visit_id, req, status="SUBMITTED"):
    bldcms.add_checksheet(
        visit_id, checksheet_id=5000 + req.id, template_id=req.template_id,
        section_id=req.section_id_snapshot, equipment_id=req.equipment_id_snapshot,
        workflow_stage_type=None, maintenance_type=req.maintenance_type_snapshot,
        minor_inspection_equipment_id=None, status=status, schedule_family="MAJOR",
        schedule_variant="TOH")


def _major_visit(db, visit_id=901, *, ready=True, variant="TOH"):
    make_section(db, id=1, code="M35-Aux", name="M35-Aux")
    visit = make_shed_visit(db, visit_id, "39066", schedule_family="MAJOR",
                            schedule_variant=variant, arrival_at=ARRIVAL, created_by=None)
    visit.schedule_started_at = STARTED
    if ready:
        visit.ready_at = READY_AT
        visit.ready_source = "DASHBOARD"
        visit.status = "READY"
    db.commit()
    return visit


def _shed_out(client, headers, visit_id):
    return client.post(f"/api/shed-visits/{visit_id}/out",
                       json={"departed_at": DEPARTED.isoformat()}, headers=headers)


def _assert_still_in_shed(db, visit_id, status="READY"):
    db.expire_all()
    visit = db.get(models.ShedVisit, visit_id)
    assert visit.status == status and visit.departed_at is None


# ------------------------------------------------ 1. MAJOR READY + incomplete -> blocked --

def test_1_a_ready_major_visit_with_outstanding_checksheets_cannot_shed_out(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Visit 14's exact situation: READY from before the Ready gate, checksheets not done."""
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    for req_id in range(1, 5):
        _major_req(db_session, req_id)

    resp = _shed_out(client, headers, visit.id)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SHED_OUT_BLOCKED"
    _assert_still_in_shed(db_session, visit.id)


# ------------------------- 2. the structured CHECKSHEETS_INCOMPLETE payload reaches the client --

def test_2_the_refusal_carries_the_ready_gate_checksheet_payload(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _major_req(db_session, 1))
    _major_req(db_session, 2)                                          # missing
    _satisfy(mock_bldcms_client, visit.id, _major_req(db_session, 3), status="DRAFT")
    _satisfy(mock_bldcms_client, visit.id, _major_req(db_session, 4), status="REJECTED")
    _major_req(db_session, 5, is_required=False)                       # optional: not counted
    _major_req(db_session, 6, is_active=False)                         # deactivated: not counted

    detail = _shed_out(client, headers, visit.id).json()["detail"]

    assert detail["message"] == "Shed Out blocked: 3 required checksheets are still outstanding."
    assert detail["blocked_by"] == ["CHECKSHEETS"]
    checksheets = detail["checksheets"]
    assert checksheets["code"] == "CHECKSHEETS_INCOMPLETE"
    assert (checksheets["total_required"], checksheets["satisfied"], checksheets["outstanding"]) == (4, 1, 3)
    assert (checksheets["optional"], checksheets["deactivated"]) == (1, 1)
    reasons = {e["label"]: e["reason"] for e in checksheets["outstanding_entries"]}
    assert reasons == {"Equipment 2": "MISSING", "Equipment 3": "DRAFT", "Equipment 4": "REJECTED"}
    assert all(e["section"] == "M35-Aux" for e in checksheets["outstanding_entries"])
    # The pre-existing keys are still there, unchanged, for any older consumer.
    assert detail["stage_blockers"] == [] and detail["booking_blockers"] == []
    assert len(detail["checksheet_blockers"]) == 3


def test_2b_a_missing_work_package_is_named_not_counted_as_zero(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session)

    detail = _shed_out(client, headers, visit.id).json()["detail"]

    assert "no checksheet work package" in detail["message"]
    assert "Shed Out is not possible" in detail["message"]
    assert detail["checksheets"]["work_package_generated"] is False


# ------------------------ 4. MAJOR READY + complete + bookings attended -> Shed Out allowed --

@pytest.mark.parametrize("variant", ("IOH", "TOH"))
def test_4_a_complete_ready_major_visit_sheds_out(
    client, db_session, mock_loco_client, mock_bldcms_client, variant
):
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session, variant=variant)
    _package(db_session, visit.id)
    for req_id in (1, 2):
        _satisfy(mock_bldcms_client, visit.id, _major_req(db_session, req_id), status="APPROVED")
    _major_req(db_session, 3, is_required=False)            # optional and never filled: fine
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_booking(db_session, 70, visit.id, defect_type_id=1, status="ATTENDED")
    make_assignment(db_session, 71, 70, 1, status="ATTENDED")

    resp = _shed_out(client, headers, visit.id)

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    closed = db_session.get(models.ShedVisit, visit.id)
    assert closed.status == "CLOSED" and closed.departed_at is not None
    assert closed.departure_source == "DASHBOARD"


# ------------------------------------------------------------- 5. no TB/TA for MAJOR --

def test_5_major_shed_out_needs_no_test_before_or_after(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _major_req(db_session, 1))
    assert db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).count() == 0

    eligibility = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers).json()
    assert eligibility["eligible"] is True
    assert eligibility["stage_blockers"] == []           # no TB / Inspection / TA invented

    assert _shed_out(client, headers, visit.id).status_code == 200


def test_5b_a_major_visit_that_never_became_ready_cannot_shed_out_via_the_api(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """The register only offers Shed Out in READY; the API now agrees. Every gate is satisfied
    here except the one that matters: the schedule was never completed."""
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session, ready=False)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _major_req(db_session, 1))

    resp = _shed_out(client, headers, visit.id)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_NOT_READY"
    assert "Complete the schedule" in resp.json()["detail"]["message"]
    _assert_still_in_shed(db_session, visit.id, status="IN_SHED")


def test_5c_the_detailed_gate_is_reported_before_the_not_ready_one(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session, ready=False)
    _package(db_session, visit.id)
    _major_req(db_session, 1)

    assert _shed_out(client, headers, visit.id).json()["detail"]["code"] == "SHED_OUT_BLOCKED"


# ---------------------------------------------------- 7. booking blocker stays readable --

def test_7_a_booking_blocker_is_named_and_listed(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _major_req(db_session, 1))
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_booking(db_session, 80, visit.id, defect_type_id=1, status="OPEN")
    make_assignment(db_session, 81, 80, 1, status="OPEN")
    make_booking(db_session, 82, visit.id, defect_type_id=1, status="OPEN")   # no assignment at all

    detail = _shed_out(client, headers, visit.id).json()["detail"]

    assert detail["blocked_by"] == ["BOOKINGS"]
    assert detail["checksheets"] is None                      # checksheets are not the problem
    assert "1 booking still has work outstanding" in detail["message"]
    assert "1 booking has no responsible section assigned" in detail["message"]
    assert {(b["booking_id"], b["status"]) for b in detail["booking_blockers"]} == {
        (80, "OPEN"), (82, "NO_ASSIGNMENTS"),
    }
    _assert_still_in_shed(db_session, visit.id)


def test_7b_every_refusing_gate_is_reported_together(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _major_req(db_session, 1)
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_booking(db_session, 80, visit.id, defect_type_id=1, status="OPEN")
    make_assignment(db_session, 81, 80, 1, status="OPEN")

    detail = _shed_out(client, headers, visit.id).json()["detail"]

    assert detail["blocked_by"] == ["BOOKINGS", "CHECKSHEETS"]
    assert "booking" in detail["message"] and "1 required checksheet is still outstanding" in detail["message"]


# ------------------------------------------------------- 6. MINOR gates unchanged --

def test_6_minor_stage_blockers_are_named_in_the_message(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    headers = make_movement_supervisor_headers(db_session)
    make_section(db_session, id=1, code="M35-Aux", name="M35-Aux")
    visit = make_shed_visit(db_session, 950, "39126", schedule_variant="IA",
                            arrival_at=ARRIVAL, created_by=None)
    for stage in make_minor_stages(db_session, visit.id, base_id=9500):
        stage.status = "PENDING" if stage.stage_type == "TEST_AFTER" else "COMPLETED"
    db_session.commit()
    _package(db_session, visit.id)
    _major_req(db_session, 1, is_required=False, workflow_stage_type="SCHEDULE_INSPECTION",
               applicability_id=1, requirement_source="APPLICABILITY")   # neutral, non-blocking

    detail = _shed_out(client, headers, visit.id).json()["detail"]

    assert detail["code"] == "SHED_OUT_BLOCKED"
    assert detail["blocked_by"] == ["STAGES"]
    assert detail["message"] == "Shed Out blocked: workflow stages not complete - Test After (PENDING)."
    assert detail["stage_blockers"] == [{"stage_type": "TEST_AFTER", "status": "PENDING"}]
