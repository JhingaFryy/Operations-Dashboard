"""Major equipment pairs (BL-DCMS migration 036) are independent requirements.

Traction Converter-1/-2, VCU-1/-2, Auxiliary Converter-1/-2&3 and Pantograph-1/-2 each have their
own equipment AND template, so each is its own requirement identity in the frozen package. The legacy
"HB & SB Panel" (template 139) stays one requirement; HB/SB slots come later from their own performa.
Nothing here is special-cased: the existing Ready (Complete Schedule) and Shed Out gates must refuse while any single member is outstanding, and a
checksheet of one member must never satisfy the other - even with identical content.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_shed_visit

ARRIVAL = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
STARTED = ARRIVAL + timedelta(hours=1)
COMPLETED = ARRIVAL + timedelta(days=4)
READY_AT = ARRIVAL + timedelta(days=5)
DEPARTED = READY_AT + timedelta(hours=2)
M1, M2 = 9, 8
# (requirement id, section id, section name, equipment id, equipment name, template id). Slot-1 ids are
# the live ones; slot-2 ids are illustrative - production assigns them from its sequences.
PAIRS = {
    "Traction Converter": [(1, M2, "M2-HR", 163, "Traction Converter-1", 163), (2, M2, "M2-HR", 193, "Traction Converter-2", 338)],
    "VCU": [(3, M2, "M2-HR", 161, "VCU-1", 161), (4, M2, "M2-HR", 194, "VCU-2", 339)],
    "Auxiliary Converter": [(5, M2, "M2-HR", 162, "Auxiliary Converter-1", 162),
                            (6, M2, "M2-HR", 195, "Auxiliary Converter-2&3", 340)],
    "Pantograph": [(7, M1, "M1-HR", 130, "Pantograph-1", 131), (8, M1, "M1-HR", 196, "Pantograph-2", 341)],
}
LEGACY = [(9, M1, "M1-HR", 139, "HB & SB Panel", 139)]
ALL = [row for rows in PAIRS.values() for row in rows] + LEGACY


def _visit(db, visit_id, *, ready):
    ensure_section(db, M1, "M1-HR")
    ensure_section(db, M2, "M2-HR")
    visit = make_shed_visit(db, visit_id, "39066", schedule_family="MAJOR", schedule_variant="TOH",
                            arrival_at=ARRIVAL, created_by=None)
    visit.schedule_started_at = STARTED
    if ready:
        visit.ready_at, visit.ready_source, visit.status = READY_AT, "DASHBOARD", "READY"
    db.commit()
    now = datetime.now(timezone.utc)
    db.add(models.ShedVisitChecksheetPackage(id=visit_id, shed_visit_id=visit_id, generated_by=None, generated_at=now))
    db.commit()
    for req_id, section_id, section_name, eq_id, eq_name, tpl in ALL:
        db.add(models.ShedVisitChecksheetRequirement(
            id=visit_id * 100 + req_id, package_id=visit_id, workflow_stage_type=None, applicability_id=None,
            requirement_source="MAJOR_EQUIPMENT", template_id=tpl, is_required=True, is_active=True,
            template_name_snapshot=f"Checksheet {tpl}", technology_snapshot="3_PHASE",
            section_id_snapshot=section_id, section_name_snapshot=section_name,
            equipment_id_snapshot=eq_id, equipment_name_snapshot=eq_name, maintenance_type_snapshot=None,
            created_at=now, updated_at=now))
    db.commit()
    return visit


def _submit(bldcms, visit_id, row, *, equipment_id=None, checksheet_id=None):
    req_id, section_id, _name, eq_id, _eq, tpl = row
    bldcms.add_checksheet(
        visit_id, checksheet_id=checksheet_id or 7000 + req_id, template_id=tpl, section_id=section_id,
        equipment_id=eq_id if equipment_id is None else equipment_id, workflow_stage_type=None,
        maintenance_type=None, minor_inspection_equipment_id=None, status="SUBMITTED",
        schedule_family="MAJOR", schedule_variant="TOH")


def _shed_out(client, headers, visit_id):
    return client.post(f"/api/shed-visits/{visit_id}/out", json={"departed_at": DEPARTED.isoformat()}, headers=headers)


def _complete(client, headers, visit_id):
    return client.post(f"/api/shed-visits/{visit_id}/complete-schedule",
                       json={"completed_at": COMPLETED.isoformat()}, headers=headers)


def _outstanding(detail):
    payload = detail.get("checksheets") or detail
    return sorted(e["label"] for e in payload["outstanding_entries"])


@pytest.mark.parametrize("family", sorted(PAIRS))
def test_one_member_submitted_does_not_satisfy_the_other(client, db_session, mock_loco_client, mock_bldcms_client, family):
    headers = make_movement_supervisor_headers(db_session)
    visit = _visit(db_session, 901, ready=True)
    done, missing = PAIRS[family]
    for row in ALL:
        if row != missing:
            _submit(mock_bldcms_client, visit.id, row)

    resp = _shed_out(client, headers, visit.id)

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "SHED_OUT_BLOCKED" and detail["blocked_by"] == ["CHECKSHEETS"]
    assert _outstanding(detail) == [missing[4]]
    assert detail["checksheets"]["satisfied"] == len(ALL) - 1


def test_all_second_members_missing_are_each_listed(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = make_movement_supervisor_headers(db_session)
    visit = _visit(db_session, 902, ready=True)
    for row in [rows[0] for rows in PAIRS.values()] + LEGACY:
        _submit(mock_bldcms_client, visit.id, row)

    detail = _shed_out(client, headers, visit.id).json()["detail"]

    assert _outstanding(detail) == sorted(rows[1][4] for rows in PAIRS.values())
    assert (detail["checksheets"]["total_required"], detail["checksheets"]["satisfied"]) == (9, 5)


def test_a_checksheet_carrying_the_other_members_equipment_satisfies_nothing(client, db_session, mock_loco_client,
                                                                            mock_bldcms_client):
    """Same content, wrong identity: TC-1's template filed against TC-2's equipment counts for neither."""
    headers = make_movement_supervisor_headers(db_session)
    visit = _visit(db_session, 903, ready=True)
    tc1, tc2 = PAIRS["Traction Converter"]
    for row in ALL:
        if row not in (tc1, tc2):
            _submit(mock_bldcms_client, visit.id, row)
    _submit(mock_bldcms_client, visit.id, tc1, equipment_id=tc2[3], checksheet_id=8001)

    detail = _shed_out(client, headers, visit.id).json()["detail"]

    assert _outstanding(detail) == ["Traction Converter-1", "Traction Converter-2"]


def test_complete_schedule_ready_gate_blocks_on_a_single_missing_member(client, db_session, mock_loco_client,
                                                                       mock_bldcms_client):
    headers = make_movement_supervisor_headers(db_session)
    visit = _visit(db_session, 904, ready=False)
    for row in ALL:
        if row[4] != "Auxiliary Converter-2&3":
            _submit(mock_bldcms_client, visit.id, row)

    resp = _complete(client, headers, visit.id)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "CHECKSHEETS_INCOMPLETE"
    assert _outstanding(resp.json()["detail"]) == ["Auxiliary Converter-2&3"]
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).ready_at is None


def test_every_member_submitted_passes_both_gates(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = make_movement_supervisor_headers(db_session)
    visit = _visit(db_session, 905, ready=False)
    for row in ALL:
        _submit(mock_bldcms_client, visit.id, row)

    assert _complete(client, headers, visit.id).status_code == 200
    resp = _shed_out(client, headers, visit.id)
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).status == "CLOSED"
