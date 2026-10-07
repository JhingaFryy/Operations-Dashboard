"""Operational Control phase: per-visit requirement flags now gate Shed Out.

  ACTIVE + REQUIRED    -> blocks until operationally satisfied
  ACTIVE + OPTIONAL    -> never blocks
  INACTIVE/DEACTIVATED -> never blocks
  no work package      -> blocks (the required set is UNKNOWN, not empty)

Covers MINOR and MAJOR, the full lifecycle matrix, reopen-after-submit, and an explicit
regression guard that the booking gate is unchanged.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import (
    make_movement_supervisor_headers,
    auth_header,
    make_assignment,
    make_booking,
    make_minor_stages,
    make_section,
    make_shed_visit,
    make_user,
)

SATISFYING = ("SUBMITTED", "UNDER_REVIEW", "APPROVED")
UNSATISFYING = ("DRAFT", "REJECTED")


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _package(db_session, visit_id, package_id=1):
    pkg = models.ShedVisitChecksheetPackage(
        id=package_id, shed_visit_id=visit_id, generated_by=None,
        generated_at=datetime.now(timezone.utc),
    )
    db_session.add(pkg)
    db_session.commit()
    return pkg


def _req(db_session, package_id, req_id, *, major=False, is_required=True, is_active=True, **over):
    now = datetime.now(timezone.utc)
    data = dict(
        id=req_id, package_id=package_id,
        workflow_stage_type=None if major else "TEST_BEFORE",
        applicability_id=None if major else req_id,
        requirement_source="MAJOR_EQUIPMENT" if major else "APPLICABILITY",
        template_id=req_id, is_required=is_required, is_active=is_active,
        template_name_snapshot=f"T{req_id}", technology_snapshot="3_PHASE",
        section_id_snapshot=1, section_name_snapshot="M35-TM",
        equipment_id_snapshot=None, equipment_name_snapshot=None,
        maintenance_type_snapshot=None, created_at=now, updated_at=now,
    )
    data.update(over)
    row = models.ShedVisitChecksheetRequirement(**data)
    db_session.add(row)
    db_session.commit()
    return row


def _minor_visit(db_session, visit_id=900):
    make_section(db_session, id=1, code="M35-TM", name="M35-TM")
    visit = make_shed_visit(db_session, visit_id, "39126", schedule_variant="IA", created_by=None)
    for s in make_minor_stages(db_session, visit.id, base_id=visit_id * 10):
        s.status = "COMPLETED"
    db_session.commit()
    return visit


def _major_visit(db_session, visit_id=901, variant="IOH"):
    make_section(db_session, id=1, code="M35-TM", name="M35-TM")
    visit = make_shed_visit(db_session, visit_id, "39160", schedule_family="MAJOR",
                            schedule_variant=variant, created_by=None)
    return visit


def _eligibility(client, headers, visit_id):
    resp = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _kinds(body):
    return {b["kind"] for b in body["checksheet_blockers"]}


# ------------------------------------------------------------ missing work package --

def test_missing_work_package_blocks_shed_out(client, db_session, mock_loco_client):
    """An ungenerated package means the required set is UNKNOWN - never "nothing to do"."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert _kinds(body) == {"WORK_PACKAGE_NOT_GENERATED"}
    assert body["checksheet_summary"]["work_package_generated"] is False
    # The stage and booking gates are individually satisfied - only checksheets hold it.
    assert body["stage_blockers"] == [] and body["booking_blockers"] == []


def test_shed_out_is_refused_when_checksheets_block(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)

    resp = client.post(f"/api/shed-visits/{visit.id}/out",
                       json={"departed_at": datetime.now(timezone.utc).isoformat()},
                       headers=headers)
    assert resp.status_code == 409
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).status == "IN_SHED"


# ---------------------------------------------------------------- flag semantics --

def test_active_required_row_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, is_required=True, is_active=True)

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert _kinds(body) == {"REQUIREMENT_PENDING"}
    assert body["checksheet_summary"]["blocking"] == 1


def test_active_optional_row_does_not_block(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    # Normal Inspection equipment: Optional/Deactivate still apply (Test Before / Test After rows no
    # longer honour them - see test_minor_workflow_refinement.py).
    _req(db_session, 1, 1, is_required=False, is_active=True, workflow_stage_type="SCHEDULE_INSPECTION")

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is True
    assert body["checksheet_blockers"] == []
    assert body["checksheet_summary"]["optional"] == 1
    assert body["checksheet_summary"]["blocking"] == 0


def test_deactivated_row_does_not_block(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, is_required=True, is_active=False, workflow_stage_type="SCHEDULE_INSPECTION")

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is True
    assert body["checksheet_summary"]["deactivated"] == 1
    assert body["checksheet_summary"]["blocking"] == 0


def test_a_mix_blocks_only_on_the_active_required_row(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    si = {"workflow_stage_type": "SCHEDULE_INSPECTION"}
    _req(db_session, 1, 1, is_required=False, is_active=True, **si)    # optional
    _req(db_session, 1, 2, is_required=True, is_active=False, **si)    # deactivated
    _req(db_session, 1, 3, is_required=True, is_active=True, **si)     # blocking

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert [b["template_id"] for b in body["checksheet_blockers"]] == [3]
    summary = body["checksheet_summary"]
    assert (summary["total"], summary["blocking"], summary["optional"], summary["deactivated"]) == (3, 1, 1, 1)


# ------------------------------------------------------------- lifecycle matrix --

@pytest.mark.parametrize("status", SATISFYING)
def test_satisfying_statuses_clear_the_requirement(client, db_session, mock_loco_client,
                                                   mock_bldcms_client, status):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1)
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=5, template_id=1, section_id=1,
                                      equipment_id=None, workflow_stage_type="TEST_BEFORE",
                                      maintenance_type=None, status=status)

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is True
    assert body["checksheet_summary"]["satisfied"] == 1


@pytest.mark.parametrize("status", UNSATISFYING)
def test_unsatisfying_statuses_still_block(client, db_session, mock_loco_client,
                                           mock_bldcms_client, status):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1)
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=5, template_id=1, section_id=1,
                                      equipment_id=None, workflow_stage_type="TEST_BEFORE",
                                      maintenance_type=None, status=status)

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert body["checksheet_blockers"][0]["checksheet_status"] == status


def test_rejection_after_submission_re_blocks_shed_out(client, db_session, mock_loco_client,
                                                       mock_bldcms_client):
    """No manual regeneration: the requirement becomes unsatisfied again on its own."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1)
    item = mock_bldcms_client.add_checksheet(visit.id, checksheet_id=5, template_id=1,
                                             section_id=1, equipment_id=None,
                                             workflow_stage_type="TEST_BEFORE", status="SUBMITTED")
    assert _eligibility(client, headers, visit.id)["eligible"] is True

    item["status"] = "REJECTED"
    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert _kinds(body) == {"REQUIREMENT_PENDING"}


def test_bldcms_unavailable_blocks_rather_than_passing(client, db_session, mock_loco_client,
                                                       mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1)
    mock_bldcms_client.unavailable = True

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert _kinds(body) == {"BLDCMS_UNAVAILABLE"}


# ------------------------------------------------------------------------- MAJOR --

@pytest.mark.parametrize("variant", ["IOH", "TOH"])
def test_major_visit_completes_without_any_stages(client, db_session, mock_loco_client,
                                                  mock_bldcms_client, variant):
    """Major has no TB/Inspection/TA stages and none are invented - readiness is entirely
    visit-level requirement satisfaction plus the booking gate."""
    headers = _admin_headers(db_session)
    visit = _major_visit(db_session, variant=variant)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, major=True, equipment_id_snapshot=9, maintenance_type_snapshot="OVERHAUL")
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=7, template_id=1, section_id=1,
                                      equipment_id=9, workflow_stage_type=None,
                                      maintenance_type="OVERHAUL", status="SUBMITTED",
                                      schedule_family="MAJOR", schedule_variant=variant)

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is True
    assert body["stage_blockers"] == []
    assert db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=visit.id).count() == 0


def test_major_unsatisfied_requirement_blocks(client, db_session, mock_loco_client):
    headers = _admin_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, major=True)

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert _kinds(body) == {"REQUIREMENT_PENDING"}


def test_major_pattern_c_optional_choices_do_not_block_but_other_required_work_does(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """Pattern C snapshots both maintenance_type choices as Optional. That must not make the
    whole visit complete while genuinely required Major work elsewhere is outstanding."""
    headers = _admin_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    # Traction Motor GC / Overhaul - both optional until an Admin promotes one.
    _req(db_session, 1, 1, major=True, is_required=False, equipment_id_snapshot=9,
         maintenance_type_snapshot="GC")
    _req(db_session, 1, 2, major=True, is_required=False, equipment_id_snapshot=9,
         maintenance_type_snapshot="OVERHAUL")
    # A genuinely required Pattern A requirement in another section.
    _req(db_session, 1, 3, major=True, is_required=True, equipment_id_snapshot=5,
         section_name_snapshot="M35-Aux")

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert [b["template_id"] for b in body["checksheet_blockers"]] == [3]
    assert body["checksheet_summary"]["optional"] == 2


def test_major_maintenance_type_is_part_of_identity(client, db_session, mock_loco_client,
                                                    mock_bldcms_client):
    """A GC checksheet must not satisfy an OVERHAUL requirement on the same equipment."""
    headers = _admin_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, major=True, equipment_id_snapshot=9, maintenance_type_snapshot="OVERHAUL")
    mock_bldcms_client.add_checksheet(visit.id, checksheet_id=7, template_id=1, section_id=1,
                                      equipment_id=9, workflow_stage_type=None,
                                      maintenance_type="GC", status="SUBMITTED")

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False


def test_a_checksheet_from_another_visit_never_satisfies(client, db_session, mock_loco_client,
                                                         mock_bldcms_client):
    headers = _admin_headers(db_session)
    visit = _major_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, major=True)
    mock_bldcms_client.add_checksheet(999, checksheet_id=7, template_id=1, section_id=1,
                                      workflow_stage_type=None, status="APPROVED")

    assert _eligibility(client, headers, visit.id)["eligible"] is False


# ------------------------------------------------- booking gate regression guard --

def test_booking_gate_is_unchanged_by_the_checksheet_gate(client, db_session, mock_loco_client):
    """An unattended booking must still block even when every checksheet requirement is clear."""
    headers = _admin_headers(db_session)
    visit = _minor_visit(db_session)
    _package(db_session, visit.id)
    _req(db_session, 1, 1, is_required=False, workflow_stage_type="SCHEDULE_INSPECTION")   # no checksheet blockers at all

    make_section(db_session, id=2, code="SEC2", name="SEC2")
    booking = make_booking(db_session, 5000, visit.id, booking_source="LOG_BOOK")
    make_assignment(db_session, 6000, booking.id, 2, status="OPEN")

    body = _eligibility(client, headers, visit.id)
    assert body["eligible"] is False
    assert body["checksheet_blockers"] == []
    assert [b["booking_id"] for b in body["booking_blockers"]] == [5000]
