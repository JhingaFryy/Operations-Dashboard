"""The READY gate: checksheets are mandatory for MINOR **and** MAJOR.

A locomotive cannot be marked Ready while any checksheet the visit actually requires is missing,
DRAFT or REJECTED. The rule is enforced at EVERY transition that writes status READY / ready_at -
MINOR Mark Ready and MAJOR Complete Schedule - from one definition
(checksheet_requirement_completion_service.assert_checksheets_complete), which is also what Shed
Out gates on.

What does NOT block: optional rows, deactivated rows, and a Test Before an Admin explicitly
skipped. What is never relaxed: no role, no flag and no alternate route gets past it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from tests.conftest import (
    make_minor_stages,
    make_movement_supervisor_headers,
    make_section,
    make_shed_visit,
    make_true_admin_headers,
    make_user,
    grant_access,
    auth_header,
)

ARRIVAL = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
STARTED = ARRIVAL + timedelta(hours=1)
COMPLETED = ARRIVAL + timedelta(hours=5)
READY_AT = ARRIVAL + timedelta(hours=7)

SATISFYING = ("SUBMITTED", "UNDER_REVIEW", "APPROVED")
UNSATISFYING = ("DRAFT", "REJECTED")


# --------------------------------------------------------------------------------- fixtures --

def _headers(db_session):
    return make_movement_supervisor_headers(db_session)


def _package(db_session, visit_id, package_id=1):
    pkg = models.ShedVisitChecksheetPackage(
        id=package_id, shed_visit_id=visit_id, generated_by=None,
        generated_at=datetime.now(timezone.utc),
    )
    db_session.add(pkg)
    db_session.commit()
    return pkg


def _req(db_session, package_id, req_id, *, major=False, stage="SCHEDULE_INSPECTION",
         is_required=True, is_active=True, **over):
    now = datetime.now(timezone.utc)
    data = dict(
        id=req_id, package_id=package_id,
        workflow_stage_type=None if major else stage,
        applicability_id=None if major else req_id,
        requirement_source="MAJOR_EQUIPMENT" if major else "APPLICABILITY",
        template_id=req_id, is_required=is_required, is_active=is_active,
        template_name_snapshot=f"T{req_id}", technology_snapshot="3_PHASE",
        section_id_snapshot=1, section_name_snapshot="M1-HR",
        equipment_id_snapshot=None, equipment_name_snapshot=None,
        maintenance_type_snapshot=None, created_at=now, updated_at=now,
    )
    data.update(over)
    row = models.ShedVisitChecksheetRequirement(**data)
    db_session.add(row)
    db_session.commit()
    return row


def _satisfy(mock_bldcms_client, visit_id, req, status="SUBMITTED", **over):
    """A BL-DCMS checksheet whose identity matches `req` - the same correlation Shed Out uses."""
    fields = dict(
        checksheet_id=1000 + req.id,
        template_id=req.template_id,
        section_id=req.section_id_snapshot,
        equipment_id=req.equipment_id_snapshot,
        workflow_stage_type=req.workflow_stage_type,
        maintenance_type=req.maintenance_type_snapshot,
        minor_inspection_equipment_id=req.minor_inspection_equipment_id,
        status=status,
        # Real BL-DCMS rows always carry their family; the gate only counts the visit's own.
        schedule_family="MINOR" if req.workflow_stage_type else "MAJOR",
    )
    fields.update(over)
    return mock_bldcms_client.add_checksheet(visit_id, **fields)


def _minor_visit(db_session, visit_id=900, *, test_before="COMPLETED", test_after="COMPLETED"):
    """A MINOR visit sitting at INSPECTION_COMPLETED - the one phase Mark Ready is offered in."""
    make_section(db_session, id=1, code="M1-HR", name="M1-HR")
    visit = make_shed_visit(db_session, visit_id, "39126", schedule_variant="IA",
                            arrival_at=ARRIVAL, created_by=None)
    stages = make_minor_stages(db_session, visit.id, base_id=visit_id * 10)
    for stage in stages:
        if stage.stage_type == "TEST_BEFORE":
            stage.status = test_before
            stage.completed_at = ARRIVAL if test_before == "COMPLETED" else None
            stage.skipped_at = ARRIVAL if test_before == "SKIPPED" else None
        elif stage.stage_type == "TEST_AFTER":
            stage.status = test_after
            stage.completed_at = COMPLETED if test_after == "COMPLETED" else None
        else:
            stage.status = "COMPLETED"
            stage.completed_at = COMPLETED
    visit.schedule_started_at = STARTED
    visit.inspection_completed_at = COMPLETED
    db_session.commit()
    return visit


def _major_visit(db_session, visit_id=901, variant="IOH"):
    """A MAJOR visit with its schedule started - Complete Schedule is its Ready transition."""
    make_section(db_session, id=1, code="M1-HR", name="M1-HR")
    visit = make_shed_visit(db_session, visit_id, "39160", schedule_family="MAJOR",
                            schedule_variant=variant, arrival_at=ARRIVAL, created_by=None)
    visit.schedule_started_at = STARTED
    db_session.commit()
    return visit


def _mark_ready(client, headers, visit_id, ready_at=READY_AT):
    return client.post(f"/api/shed-visits/{visit_id}/mark-ready",
                       json={"ready_at": ready_at.isoformat()}, headers=headers)


def _complete_schedule(client, headers, visit_id, completed_at=COMPLETED):
    return client.post(f"/api/shed-visits/{visit_id}/complete-schedule",
                       json={"completed_at": completed_at.isoformat()}, headers=headers)


def _assert_blocked(resp, db_session, visit_id, outstanding=None):
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "CHECKSHEETS_INCOMPLETE"
    if outstanding is not None:
        assert detail["outstanding"] == outstanding
    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit_id)
    # The refusal is total: no ready_at, no READY status, nothing half-written.
    assert visit.ready_at is None
    assert visit.status == "IN_SHED"
    return detail


def _assert_ready(resp, db_session, visit_id):
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit_id)
    assert visit.status == "READY" and visit.ready_at is not None
    return visit


# ============================================================================ MINOR matrix ==

def test_1_all_required_checksheets_submitted_allows_ready(client, db_session, mock_loco_client,
                                                           mock_bldcms_client):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    for req_id, stage in ((1, "TEST_BEFORE"), (2, "SCHEDULE_INSPECTION"), (3, "TEST_AFTER")):
        _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, req_id, stage=stage))

    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)


def test_2_a_draft_required_checksheet_blocks_ready(client, db_session, mock_loco_client,
                                                    mock_bldcms_client):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1), status="DRAFT")

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id, outstanding=1)
    assert detail["outstanding_entries"][0]["reason"] == "DRAFT"
    assert detail["outstanding_entries"][0]["status"] == "DRAFT"


def test_3_a_rejected_required_checksheet_blocks_ready(client, db_session, mock_loco_client,
                                                       mock_bldcms_client):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1), status="REJECTED")

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id, outstanding=1)
    assert detail["outstanding_entries"][0]["reason"] == "REJECTED"


def test_4_a_missing_required_checksheet_blocks_ready(client, db_session, mock_loco_client,
                                                      mock_bldcms_client):
    """No checksheet instance exists at all - and none is created to satisfy the gate."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, section_name_snapshot="M1-HR", equipment_name_snapshot="Pantograph PT-1")

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id, outstanding=1)
    entry = detail["outstanding_entries"][0]
    assert entry["reason"] == "MISSING" and entry["status"] is None
    assert entry["section"] == "M1-HR" and entry["label"] == "Pantograph PT-1"
    assert mock_bldcms_client.visits.get(visit.id, []) == []   # nothing was created anywhere


@pytest.mark.parametrize("status", ("UNDER_REVIEW", "APPROVED"))
def test_5_and_6_under_review_and_approved_satisfy(client, db_session, mock_loco_client,
                                                   mock_bldcms_client, status):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1), status=status)

    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)


def test_7_an_optional_inspection_checksheet_missing_does_not_block(client, db_session,
                                                                    mock_loco_client, mock_bldcms_client):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1))              # required, done
    _req(db_session, 1, 2, is_required=False)                                   # optional, missing

    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)


def test_8_a_deactivated_requirement_does_not_block(client, db_session, mock_loco_client,
                                                    mock_bldcms_client):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1))
    _req(db_session, 1, 2, is_active=False)                                     # deactivated, missing

    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)


def test_9_an_admin_skipped_test_before_satisfies_the_tb_part(client, db_session, mock_loco_client,
                                                              mock_bldcms_client):
    """The audited Admin skip - the ONLY thing other than a real Test Before checksheet that
    satisfies TB. A Conventional visit with no TB template reaches Ready exactly this way."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session, test_before="SKIPPED")
    _package(db_session, visit.id)
    _req(db_session, 1, 1, stage="TEST_BEFORE")                                 # never filled in
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 2, stage="TEST_AFTER"))

    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)


def test_10_is_required_false_is_not_a_test_before_skip(client, db_session,
                                                        mock_loco_client, mock_bldcms_client):
    """Only an explicit audited Admin skip clears Test Before. Marking its requirement row
    Optional does NOT: since migration 012 TB/TA rows are always required, so a Test Before whose
    checksheet was rejected still blocks Ready even with is_required=False."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session)               # TB stage COMPLETED, started after it - current workflow
    _package(db_session, visit.id)
    tb = _req(db_session, 1, 1, stage="TEST_BEFORE", is_required=False)
    _satisfy(mock_bldcms_client, visit.id, tb, status="REJECTED")
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 2, stage="TEST_AFTER"))

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id, outstanding=1)
    assert detail["outstanding_entries"][0]["stage"] == "TEST_BEFORE"
    assert detail["optional"] == 0                 # the Optional flag was not honoured for TB


def test_10b_a_grandfathered_legacy_start_still_waives_test_before(client, db_session,
                                                                    mock_loco_client, mock_bldcms_client):
    """Unchanged from the earlier workflow decision, and deliberately re-asserted here: a MINOR
    visit whose schedule was started before the Test Before gate existed (its TB was never
    completed or skipped at the start) has TB waived downstream - no retroactive Test Before is
    demanded and no timestamp is fabricated. Every OTHER required checksheet still blocks."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session, test_before="PENDING")   # only reachable as a legacy start
    _package(db_session, visit.id)
    _req(db_session, 1, 1, stage="TEST_BEFORE")               # never filled in - waived
    inspection = _req(db_session, 1, 2, stage="SCHEDULE_INSPECTION")
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 3, stage="TEST_AFTER"))

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id, outstanding=1)
    assert detail["outstanding_entries"][0]["stage"] == "SCHEDULE_INSPECTION"

    _satisfy(mock_bldcms_client, visit.id, inspection)
    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)


def test_11_a_missing_test_after_checksheet_blocks(client, db_session, mock_loco_client,
                                                   mock_bldcms_client):
    """The TEST_AFTER stage row is COMPLETED but its checksheet is not satisfied: the stage gate
    alone was never enough, and the checksheet gate catches it."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1))
    _req(db_session, 1, 2, stage="TEST_AFTER")

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id, outstanding=1)
    assert detail["outstanding_entries"][0]["stage"] == "TEST_AFTER"


def test_11b_a_pending_test_after_stage_still_blocks_with_its_own_message(client, db_session,
                                                                          mock_loco_client, mock_bldcms_client):
    """The pre-existing Test After stage gate is untouched and still answers first, so the
    operator sees the specific Test After message rather than a generic checksheet count."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session, test_after="PENDING")
    _package(db_session, visit.id)

    resp = _mark_ready(client, headers, visit.id)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "TEST_AFTER_NOT_SATISFIED"


def test_12_every_inspection_done_but_test_after_outstanding_blocks(client, db_session,
                                                                     mock_loco_client, mock_bldcms_client):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    for req_id in (1, 2, 3):
        _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, req_id))
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 4, stage="TEST_AFTER"), status="DRAFT")

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id, outstanding=1)
    assert detail["satisfied"] == 3 and detail["total_required"] == 4


def test_13_one_m1hr_pantograph_alternative_satisfies_that_equipment(client, db_session,
                                                                      mock_loco_client, mock_bldcms_client):
    """PT-1 has alternative makes (performas). The requirement's identity is the Minor Inspection
    EQUIPMENT, so whichever alternative template was actually opened satisfies it - both are never
    required, and the other make's template_id is not looked for."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    pt1 = _req(
        db_session, 1, 1, stage="SCHEDULE_INSPECTION",
        equipment_name_snapshot=None,
        minor_inspection_equipment_id=77,
        minor_inspection_equipment_code_snapshot="PT-1",
        minor_inspection_equipment_name_snapshot="Pantograph PT-1",
    )
    # Filled in on a DIFFERENT template from the one snapshotted - the other approved make.
    _satisfy(mock_bldcms_client, visit.id, pt1, template_id=pt1.template_id + 500, status="SUBMITTED")

    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)


def test_14_an_optional_hlc_missing_does_not_block(client, db_session, mock_loco_client,
                                                   mock_bldcms_client):
    """HLC-1/HLC-2 are configured Optional today; Ready must not wait for them."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1))
    for req_id, code in ((2, "HLC-1"), (3, "HLC-2")):
        _req(db_session, 1, req_id, is_required=False,
             minor_inspection_equipment_id=req_id * 10,
             minor_inspection_equipment_code_snapshot=code,
             minor_inspection_equipment_name_snapshot=f"Head Light {code}")

    detail_resp = _mark_ready(client, headers, visit.id)
    _assert_ready(detail_resp, db_session, visit.id)


def test_a_visit_with_no_work_package_cannot_be_marked_ready(client, db_session, mock_loco_client,
                                                             mock_bldcms_client):
    """Requirements UNKNOWN is not requirements NONE - and "configuration incomplete" is never a
    reason to wave a visit through."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session)

    resp = _mark_ready(client, headers, visit.id)
    detail = _assert_blocked(resp, db_session, visit.id)
    assert detail["work_package_generated"] is False


def test_an_unreadable_bldcms_blocks_rather_than_passing(client, db_session, mock_loco_client,
                                                         mock_bldcms_client):
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1)
    mock_bldcms_client.unavailable = True

    detail = _assert_blocked(_mark_ready(client, headers, visit.id), db_session, visit.id)
    assert detail["checksheets_readable"] is False


# ============================================================================ MAJOR matrix ==

@pytest.mark.parametrize("variant", ("IOH", "TOH"))
def test_major_pattern_a_all_required_submitted_allows_ready(client, db_session, mock_loco_client,
                                                             mock_bldcms_client, variant):
    """Pattern A: section/equipment-based Major checksheets, for both Major variants."""
    headers = _headers(db_session)
    visit = _major_visit(db_session, variant=variant)
    _package(db_session, visit.id)
    for req_id in (1, 2):
        _satisfy(mock_bldcms_client, visit.id,
                 _req(db_session, 1, req_id, major=True, equipment_id_snapshot=req_id * 7,
                      equipment_name_snapshot=f"Equip {req_id}"))

    _assert_ready(_complete_schedule(client, headers, visit.id), db_session, visit.id)


@pytest.mark.parametrize("status", ("DRAFT", "REJECTED", None))
def test_major_pattern_a_one_unsatisfied_requirement_blocks(client, db_session, mock_loco_client,
                                                            mock_bldcms_client, status):
    """Missing (status None), DRAFT and REJECTED all block a Major Complete Schedule."""
    headers = _headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id,
             _req(db_session, 1, 1, major=True, equipment_id_snapshot=7, equipment_name_snapshot="Equip 1"))
    outstanding_req = _req(db_session, 1, 2, major=True, equipment_id_snapshot=14,
                           equipment_name_snapshot="Equip 2")
    if status is not None:
        _satisfy(mock_bldcms_client, visit.id, outstanding_req, status=status)

    detail = _assert_blocked(_complete_schedule(client, headers, visit.id), db_session, visit.id,
                             outstanding=1)
    assert detail["outstanding_entries"][0]["reason"] == (status or "MISSING")
    assert detail["satisfied"] == 1 and detail["total_required"] == 2


def test_major_pattern_b_direct_equipment_null_checksheet_is_enforced(client, db_session,
                                                                      mock_loco_client, mock_bldcms_client):
    """Pattern B (e.g. M6-HR): a section-level checksheet with equipment_id NULL."""
    headers = _headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    direct = _req(db_session, 1, 1, major=True, requirement_source="MAJOR_DIRECT",
                  section_name_snapshot="M6-HR", equipment_id_snapshot=None,
                  equipment_name_snapshot=None)

    detail = _assert_blocked(_complete_schedule(client, headers, visit.id), db_session, visit.id,
                             outstanding=1)
    assert detail["outstanding_entries"][0]["section"] == "M6-HR"

    _satisfy(mock_bldcms_client, visit.id, direct)
    _assert_ready(_complete_schedule(client, headers, visit.id), db_session, visit.id)


def test_major_pattern_c_maintenance_type_is_part_of_the_identity(client, db_session,
                                                                   mock_loco_client, mock_bldcms_client):
    """Pattern C (M35-TM): one equipment, several templates separated only by maintenance_type.
    A GC checksheet must not satisfy the OVERHAUL requirement."""
    headers = _headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    overhaul = _req(db_session, 1, 1, major=True, section_name_snapshot="M35-TM",
                    equipment_id_snapshot=55, equipment_name_snapshot="Traction Motor 1",
                    maintenance_type_snapshot="OVERHAUL")
    # The GC checksheet for the same equipment: right equipment, wrong maintenance_type.
    _satisfy(mock_bldcms_client, visit.id, overhaul, maintenance_type="GC", template_id=999)

    detail = _assert_blocked(_complete_schedule(client, headers, visit.id), db_session, visit.id,
                             outstanding=1)
    assert detail["outstanding_entries"][0]["maintenance_type"] == "OVERHAUL"

    _satisfy(mock_bldcms_client, visit.id, overhaul)
    _assert_ready(_complete_schedule(client, headers, visit.id), db_session, visit.id)


def test_major_optional_and_deactivated_rows_do_not_block(client, db_session, mock_loco_client,
                                                          mock_bldcms_client):
    headers = _headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1, major=True))
    _req(db_session, 1, 2, major=True, is_required=False)
    _req(db_session, 1, 3, major=True, is_active=False)

    _assert_ready(_complete_schedule(client, headers, visit.id), db_session, visit.id)


def test_major_with_no_work_package_cannot_become_ready(client, db_session, mock_loco_client,
                                                        mock_bldcms_client):
    headers = _headers(db_session)
    visit = _major_visit(db_session)

    detail = _assert_blocked(_complete_schedule(client, headers, visit.id), db_session, visit.id)
    assert detail["work_package_generated"] is False


def test_no_test_before_or_after_is_invented_for_major(client, db_session, mock_loco_client,
                                                       mock_bldcms_client):
    """MAJOR has no TB/TA. The gate must not fabricate stage requirements for it."""
    headers = _headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _satisfy(mock_bldcms_client, visit.id, _req(db_session, 1, 1, major=True))

    _assert_ready(_complete_schedule(client, headers, visit.id), db_session, visit.id)
    assert db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).count() == 0


# ==================================================================== every READY path ==

def test_minor_complete_schedule_is_not_a_ready_path_and_stays_ungated(client, db_session,
                                                                        mock_loco_client, mock_bldcms_client):
    """MINOR Complete Schedule records the end of the physical inspection; it writes no ready_at,
    so it is deliberately not gated - and it still cannot make the visit READY."""
    make_section(db_session, id=1, code="M1-HR", name="M1-HR")
    headers = _headers(db_session)
    visit = make_shed_visit(db_session, 930, "39127", schedule_variant="IA", arrival_at=ARRIVAL,
                            created_by=None)
    for stage in make_minor_stages(db_session, visit.id, base_id=9300):
        if stage.stage_type == "TEST_BEFORE":
            stage.status, stage.completed_at = "COMPLETED", ARRIVAL
    visit.schedule_started_at = STARTED
    db_session.commit()

    resp = _complete_schedule(client, headers, visit.id)

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit.id)
    assert visit.inspection_completed_at is not None
    assert visit.ready_at is None and visit.status == "IN_SHED"


def test_mark_ready_is_refused_for_major_so_there_is_no_ungated_side_door(client, db_session,
                                                                          mock_loco_client, mock_bldcms_client):
    """A Major visit cannot be pushed through the Minor Mark Ready endpoint to dodge its own gate."""
    headers = _headers(db_session)
    visit = _major_visit(db_session)

    resp = _mark_ready(client, headers, visit.id)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MARK_READY_NOT_APPLICABLE"


def test_the_only_ready_writes_in_the_codebase_are_the_two_gated_ones():
    """A structural guard: if a new route learns to write ready_at / status READY, this fails
    until that write is routed through the same gate."""
    import pathlib
    import re

    service_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    writers = set()
    for path in service_dir.glob("*.py"):
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if re.match(r"visit\.ready_at\s*=", stripped) or re.match(
                r"visit\.status\s*=\s*[\"']READY[\"']", stripped
            ):
                writers.add(path.name)
    assert writers == {"schedule_lifecycle_service.py"}, writers

    lifecycle = (service_dir / "schedule_lifecycle_service.py").read_text()
    # Both Ready-capable functions call the gate.
    assert lifecycle.count("assert_checksheets_complete(db, bldcms_client, visit)") == 2


@pytest.mark.parametrize("role", ("admin", "movement_supervisor"))
def test_no_role_can_force_ready_through_outstanding_checksheets(client, db_session,
                                                                  mock_loco_client, mock_bldcms_client, role):
    """No override privilege exists, for anyone. Whoever is allowed to drive the workflow is
    refused by the same gate; nobody gets a bypass parameter, and the visit never becomes READY."""
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1)

    headers = make_true_admin_headers(db_session) if role == "admin" else _headers(db_session)
    resp = _mark_ready(client, headers, visit.id)

    assert resp.status_code in (403, 409), resp.text
    if resp.status_code == 409:
        assert resp.json()["detail"]["code"] == "CHECKSHEETS_INCOMPLETE"
    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit.id)
    assert visit.status == "IN_SHED" and visit.ready_at is None


# ============================================================================== Shed Out ==

def test_a_checksheet_rejected_after_ready_blocks_shed_out(client, db_session, mock_loco_client,
                                                           mock_bldcms_client):
    """READY is not a certificate: Shed Out re-evaluates, so a checksheet rejected in review after
    the visit went Ready stops the locomotive leaving, with the status still saying READY."""
    headers = _headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    req = _req(db_session, 1, 1)
    sheet = _satisfy(mock_bldcms_client, visit.id, req)
    _assert_ready(_mark_ready(client, headers, visit.id), db_session, visit.id)

    sheet["status"] = "REJECTED"          # the supported review path, after Ready

    eligibility = client.get(f"/api/shed-visits/{visit.id}/shed-out-eligibility", headers=headers)
    assert eligibility.json()["eligible"] is False
    out = client.post(f"/api/shed-visits/{visit.id}/out",
                      json={"departed_at": (READY_AT + timedelta(hours=1)).isoformat()},
                      headers=headers)
    assert out.status_code == 409
    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit.id)
    assert visit.status == "READY" and visit.departed_at is None
