"""The Shed Out checksheet gate is schedule-family specific and visit specific.

A MINOR visit is satisfied only by that MINOR visit's own checksheets; a MAJOR visit only by that
MAJOR visit's own. A checksheet of the other family, of a previous visit, or of any future family
(TI, GC) never counts - even when every identity field happens to match.
"""

from datetime import datetime, timezone

from app.db import models
from tests.conftest import (
    make_minor_stages,
    make_movement_supervisor_headers,
    ensure_section,
    make_shed_visit,
)

VISIT_MINOR = 900
VISIT_MAJOR = 901
OLD_VISIT = 850


def _package(db, visit_id, package_id):
    db.add(models.ShedVisitChecksheetPackage(id=package_id, shed_visit_id=visit_id,
                                             generated_by=None, generated_at=datetime.now(timezone.utc)))
    db.commit()


def _req(db, package_id, req_id, *, stage, **over):
    now = datetime.now(timezone.utc)
    major = stage is None
    data = dict(
        id=req_id, package_id=package_id, workflow_stage_type=stage,
        applicability_id=None if major else req_id,
        requirement_source="MAJOR_EQUIPMENT" if major else "APPLICABILITY",
        template_id=req_id, is_required=True, is_active=True,
        template_name_snapshot=f"T{req_id}", technology_snapshot="3_PHASE",
        section_id_snapshot=1, section_name_snapshot="M1-HR",
        equipment_id_snapshot=None if not major else req_id * 10,
        equipment_name_snapshot=None, maintenance_type_snapshot=None,
        created_at=now, updated_at=now,
    )
    data.update(over)
    row = models.ShedVisitChecksheetRequirement(**data)
    db.add(row)
    db.commit()
    return row


def _sheet(bldcms, visit_id, req, *, family, status="SUBMITTED", variant=None, checksheet_id=None):
    """A BL-DCMS row whose IDENTITY matches `req` exactly - only family/visit may differ."""
    return bldcms.add_checksheet(
        visit_id, checksheet_id=checksheet_id or 7000 + req.id, template_id=req.template_id,
        section_id=req.section_id_snapshot, equipment_id=req.equipment_id_snapshot,
        workflow_stage_type=req.workflow_stage_type, maintenance_type=req.maintenance_type_snapshot,
        minor_inspection_equipment_id=None, status=status, schedule_family=family,
        schedule_variant=variant or ("IA" if family == "MINOR" else "TOH"),
    )


def _closed_previous(db, visit_id, loco, family, variant):
    """A previous visit of the same locomotive - necessarily CLOSED, since a loco has at most one
    open visit."""
    ensure_section(db, 1, "M1-HR")
    arrival = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    return make_shed_visit(db, visit_id, loco, schedule_family=family, schedule_variant=variant,
                           status="CLOSED", arrival_at=arrival,
                           departed_at=datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc),
                           departure_source="DASHBOARD", created_by=None)


def _minor_visit(db, visit_id=VISIT_MINOR):
    ensure_section(db, 1, "M1-HR")
    visit = make_shed_visit(db, visit_id, "39015", schedule_variant="IA", created_by=None)
    for stage in make_minor_stages(db, visit.id, base_id=visit_id * 10):
        stage.status = "COMPLETED"
    db.commit()
    return visit


def _major_visit(db, visit_id=VISIT_MAJOR):
    ensure_section(db, 1, "M1-HR")
    return make_shed_visit(db, visit_id, "39066", schedule_family="MAJOR",
                           schedule_variant="TOH", created_by=None)


def _eligibility(client, db, visit_id):
    resp = client.get(f"/api/shed-visits/{visit_id}/shed-out-eligibility",
                      headers=make_movement_supervisor_headers(db))
    assert resp.status_code == 200, resp.text
    return resp.json()


# =============================================================================== MINOR ==

def test_minor_missing_required_checksheet_blocks(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _minor_visit(db_session)
    _package(db_session, visit.id, 1)
    _req(db_session, 1, 1, stage="SCHEDULE_INSPECTION")

    body = _eligibility(client, db_session, visit.id)

    assert body["eligible"] is False
    assert body["checksheet_summary"]["satisfied"] == 0


def test_minor_complete_set_passes_the_checksheet_gate(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _minor_visit(db_session)
    _package(db_session, visit.id, 1)
    for req_id, stage in ((1, "TEST_BEFORE"), (2, "SCHEDULE_INSPECTION"), (3, "TEST_AFTER")):
        _sheet(mock_bldcms_client, visit.id, _req(db_session, 1, req_id, stage=stage), family="MINOR")

    body = _eligibility(client, db_session, visit.id)

    assert body["eligible"] is True
    assert body["checksheet_summary"]["satisfied"] == 3


def test_a_major_checksheet_never_satisfies_a_minor_requirement(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _minor_visit(db_session)
    _package(db_session, visit.id, 1)
    req = _req(db_session, 1, 1, stage="SCHEDULE_INSPECTION")
    # Every identity field matches - only the family is wrong.
    _sheet(mock_bldcms_client, visit.id, req, family="MAJOR")

    body = _eligibility(client, db_session, visit.id)

    assert body["eligible"] is False
    assert body["checksheet_summary"]["satisfied"] == 0
    assert body["checksheet_blockers"][0]["checksheet_status"] is None   # treated as missing


def test_a_previous_visits_minor_checksheet_never_satisfies_this_visit(client, db_session, mock_loco_client, mock_bldcms_client):
    """Same loco, same schedule, same requirement - but an older visit."""
    _closed_previous(db_session, OLD_VISIT, "39015", "MINOR", "IA")
    for stage in make_minor_stages(db_session, OLD_VISIT, base_id=OLD_VISIT * 10):
        stage.status = "COMPLETED"
    db_session.commit()
    visit = _minor_visit(db_session)
    _package(db_session, OLD_VISIT, 1)
    _package(db_session, visit.id, 2)
    old_req = _req(db_session, 1, 1, stage="SCHEDULE_INSPECTION")
    _req(db_session, 2, 2, stage="SCHEDULE_INSPECTION", template_id=old_req.template_id,
         applicability_id=old_req.applicability_id + 100)
    _sheet(mock_bldcms_client, OLD_VISIT, old_req, family="MINOR", status="APPROVED")

    assert _eligibility(client, db_session, OLD_VISIT)["eligible"] is True     # it counts there...
    body = _eligibility(client, db_session, visit.id)
    assert body["eligible"] is False                                           # ...never here
    assert body["checksheet_summary"]["satisfied"] == 0


def test_a_response_for_another_visit_is_not_trusted(client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch):
    """Fail closed if BL-DCMS ever answered for the wrong visit."""
    visit = _minor_visit(db_session)
    _package(db_session, visit.id, 1)
    req = _req(db_session, 1, 1, stage="SCHEDULE_INSPECTION")
    _sheet(mock_bldcms_client, visit.id, req, family="MINOR")
    real = mock_bldcms_client.get_visit_checksheets
    monkeypatch.setattr(mock_bldcms_client, "get_visit_checksheets",
                        lambda visit_id, workflow_stage_type=None: {**real(visit_id), "shed_visit_id": visit_id + 1})

    body = _eligibility(client, db_session, visit.id)

    assert body["eligible"] is False
    assert {b["kind"] for b in body["checksheet_blockers"]} == {"BLDCMS_UNAVAILABLE"}


# =============================================================================== MAJOR ==

def test_major_missing_required_checksheet_blocks(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _major_visit(db_session)
    _package(db_session, visit.id, 1)
    _req(db_session, 1, 1, stage=None)

    assert _eligibility(client, db_session, visit.id)["eligible"] is False


def test_major_complete_set_passes_the_checksheet_gate(client, db_session, mock_loco_client, mock_bldcms_client):
    visit = _major_visit(db_session)
    _package(db_session, visit.id, 1)
    for req_id in (1, 2):
        _sheet(mock_bldcms_client, visit.id, _req(db_session, 1, req_id, stage=None), family="MAJOR")
    _sheet(mock_bldcms_client, visit.id,
           _req(db_session, 1, 3, stage=None, maintenance_type_snapshot="OVERHAUL"), family="MAJOR")

    body = _eligibility(client, db_session, visit.id)

    assert body["eligible"] is True
    assert body["stage_blockers"] == []                      # no TB/TA for MAJOR
    assert body["checksheet_summary"]["satisfied"] == 3


def test_minor_tb_and_ta_never_satisfy_a_major_visit(client, db_session, mock_loco_client, mock_bldcms_client):
    """Even a Minor TB/TA row carrying this visit's id and a matching template is not Major work."""
    visit = _major_visit(db_session)
    _package(db_session, visit.id, 1)
    req = _req(db_session, 1, 1, stage=None)
    for stage in ("TEST_BEFORE", "TEST_AFTER"):
        mock_bldcms_client.add_checksheet(
            visit.id, checksheet_id=100 if stage == "TEST_BEFORE" else 101, template_id=req.template_id,
            section_id=1, equipment_id=req.equipment_id_snapshot, workflow_stage_type=stage,
            maintenance_type=None, status="APPROVED", schedule_family="MINOR", schedule_variant="IA")
    # And the same identity with no stage, but still the Minor family:
    _sheet(mock_bldcms_client, visit.id, req, family="MINOR", checksheet_id=102)

    body = _eligibility(client, db_session, visit.id)

    assert body["eligible"] is False
    assert body["checksheet_summary"]["satisfied"] == 0


def test_a_previous_visits_major_checksheet_never_satisfies_this_visit(client, db_session, mock_loco_client, mock_bldcms_client):
    _closed_previous(db_session, OLD_VISIT, "39066", "MAJOR", "TOH")
    visit = _major_visit(db_session)
    _package(db_session, OLD_VISIT, 1)
    _package(db_session, visit.id, 2)
    old_req = _req(db_session, 1, 1, stage=None)
    _req(db_session, 2, 2, stage=None, template_id=old_req.template_id,
         equipment_id_snapshot=old_req.equipment_id_snapshot)
    _sheet(mock_bldcms_client, OLD_VISIT, old_req, family="MAJOR", status="APPROVED")

    assert _eligibility(client, db_session, OLD_VISIT)["eligible"] is True
    assert _eligibility(client, db_session, visit.id)["eligible"] is False


# ================================================================================ TI/GC ==

def test_future_ti_and_gc_checksheets_never_participate(client, db_session, mock_loco_client, mock_bldcms_client):
    """A TRIP_INSPECTION or GENERAL_CHECKING checksheet (families BL-DCMS does not have yet) must
    never settle a Minor or Major requirement, even with every identity field matching."""
    minor = _minor_visit(db_session)
    major = make_shed_visit(db_session, VISIT_MAJOR, "39066", schedule_family="MAJOR",
                            schedule_variant="TOH", created_by=None)
    _package(db_session, minor.id, 1)
    _package(db_session, major.id, 2)
    minor_req = _req(db_session, 1, 1, stage="SCHEDULE_INSPECTION")
    major_req = _req(db_session, 2, 2, stage=None)
    for family in ("TRIP_INSPECTION", "GENERAL_CHECKING", None):
        _sheet(mock_bldcms_client, minor.id, minor_req, family=family, variant="TI",
               checksheet_id=hash((family, 1)) % 10000)
        _sheet(mock_bldcms_client, major.id, major_req, family=family, variant="GC",
               checksheet_id=hash((family, 2)) % 10000)

    assert _eligibility(client, db_session, minor.id)["checksheet_summary"]["satisfied"] == 0
    assert _eligibility(client, db_session, major.id)["checksheet_summary"]["satisfied"] == 0
