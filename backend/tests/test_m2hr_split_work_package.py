"""Frozen work packages after M2-HR's equipment split.

BL migration 079 gives each physical position its own equipment row, its own template and its own
applicability. Operations Dashboard resolves a visit's Minor requirements from that applicability at
Shed In and freezes them, so the questions here are:

  * does a NEW visit's package contain BOTH slots of each pair, as two separate requirements?
  * does an EXISTING frozen package stay exactly as it was, with the old names and the old count?

The second matters more. A frozen package is a visit's authoritative scope and is never re-resolved;
if the split leaked into one, a locomotive already part-way through its schedule would suddenly owe
three checksheets nobody had been asked for.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import models
from app.services import checksheet_work_package_service as wp
from tests.conftest import make_shed_visit

#: The three pairs, as migration 079 leaves them: (code, name, equipment_id, template_id).
PAIR_SLOTS = [
    ("G", "Central Electronics Rack-1", 13, 14),
    ("G2", "Central Electronics Rack-2", 33, 34),
    ("H", "Auxiliary Converter-1", 14, 16),
    ("H2", "Auxiliary Converter-2&3", 34, 36),
    ("I", "Traction Converter-1 (SR)", 15, 18),
    ("I2", "Traction Converter-2 (SR)", 35, 38),
]


def _minor_item(code: str, name: str, equipment_id: int, template_id: int) -> dict:
    """One item as BL-DCMS reports it for a Minor Inspection directory equipment."""
    return {
        "applicability_id": template_id * 10,
        "template_id": template_id,
        "template_name": f"M2-HR Minor Inspection - {code} - {name}",
        "technology": "3_PHASE",
        "section_id": 2,
        "section_name": "M2-HR",
        "equipment_id": None,
        "equipment_name": None,
        "maintenance_type": None,
        "is_required": True,
        "minor_inspection_equipment_id": equipment_id,
        "minor_inspection_equipment_code": code,
        "minor_inspection_equipment_name": name,
        # Slot-1 carries the performa's printed letter; slot-2's code is synthetic and unprinted.
        "minor_inspection_equipment_label": f"{code} - {name}" if not code.endswith("2") else name,
    }


@pytest.fixture()
def visit(db_session):
    return make_shed_visit(
        db_session, 500, "30542", status="IN_SHED",
        schedule_family="MINOR", schedule_variant="IA",
    )


def _configure(mock_bldcms_client, items):
    """Make the mock BL-DCMS client report `items` for the Inspection stage, and nothing elsewhere."""
    mock_bldcms_client.minor_inspection_configuration_complete = True
    mock_bldcms_client.applicability[("3_PHASE", "MINOR", "IA", "SCHEDULE_INSPECTION")] = items
    for stage in ("TEST_BEFORE", "TEST_AFTER"):
        mock_bldcms_client.applicability[("3_PHASE", "MINOR", "IA", stage)] = []


def _admin(db) -> models.User:
    """The actor generation is attributed to. Re-read rather than cached, since several tests create
    it themselves."""
    user = db.query(models.User).filter(models.User.employee_id == "ADM1").one_or_none()
    if user is None:
        user = models.User(employee_id="ADM1", name="Admin", mobile="9000000001",
                           role="Admin", is_active=True)
        db.add(user)
        db.commit()
    return user


def _generate(db, visit, bldcms_client, loco_client):
    # Generation resolves the locomotive's technology from Loco Master first and refuses the whole
    # package if it cannot - so the mock has to know this locomotive. loco_type is the key the
    # applicability fixture store is also keyed by.
    loco_client.add_locomotive(visit.loco_number, loco_type="3_PHASE")
    """generate_work_package is idempotent: an already-generated visit is returned untouched without
    BL-DCMS being consulted again. That is precisely what keeps a frozen package frozen."""
    return wp.generate_work_package(
        db, loco_client, bldcms_client, visit.id, _admin(db),
    )


def _requirements(db, visit_id: int):
    return (
        db.query(models.ShedVisitChecksheetRequirement)
        .join(models.ShedVisitChecksheetPackage,
              models.ShedVisitChecksheetPackage.id
              == models.ShedVisitChecksheetRequirement.package_id)
        .filter(models.ShedVisitChecksheetPackage.shed_visit_id == visit_id)
        .order_by(models.ShedVisitChecksheetRequirement.id)
        .all()
    )


# =========================================== a NEW visit gets all six slots (items 1, 11) =======


def test_a_new_minor_visit_freezes_both_slots_of_every_pair(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    _configure(mock_bldcms_client, [_minor_item(*s) for s in PAIR_SLOTS])

    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)

    rows = [r for r in _requirements(db_session, visit.id)
            if r.workflow_stage_type == "SCHEDULE_INSPECTION"]
    assert len(rows) == 6
    # Six DISTINCT equipment identities - the property that makes them independent requirements.
    assert len({r.minor_inspection_equipment_id for r in rows}) == 6
    assert len({r.template_id for r in rows}) == 6
    assert sorted(r.minor_inspection_equipment_code_snapshot for r in rows) == [
        "G", "G2", "H", "H2", "I", "I2"
    ]


def test_the_new_package_snapshots_the_new_names(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    """Item 11. The frozen snapshot carries the renamed slot-1 and the new slot-2, so the Dashboard
    shows the current names for a visit frozen from now on."""
    _configure(mock_bldcms_client, [_minor_item(*s) for s in PAIR_SLOTS])

    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)

    names = {r.minor_inspection_equipment_code_snapshot:
             r.minor_inspection_equipment_name_snapshot
             for r in _requirements(db_session, visit.id)
             if r.minor_inspection_equipment_id is not None}
    assert names["G"] == "Central Electronics Rack-1"
    assert names["G2"] == "Central Electronics Rack-2"
    assert names["H"] == "Auxiliary Converter-1"
    assert names["H2"] == "Auxiliary Converter-2&3"
    assert names["I"] == "Traction Converter-1 (SR)"
    assert names["I2"] == "Traction Converter-2 (SR)"
    # Slot-1 keeps its printed letter in the label; slot-2 does not.
    labels = {r.minor_inspection_equipment_code_snapshot:
              r.minor_inspection_equipment_label_snapshot
              for r in _requirements(db_session, visit.id)
              if r.minor_inspection_equipment_id is not None}
    assert labels["G"] == "G - Central Electronics Rack-1"
    assert labels["G2"] == "Central Electronics Rack-2"


def test_the_two_slots_of_a_pair_are_not_deduplicated(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    """_requirement_identity collapses a Minor equipment's ALTERNATIVE PERFORMAS into one
    requirement - deliberately, since one visit fills one performa per slot. Two different equipment
    ids must NOT collapse that way, or the split would silently produce one requirement."""
    pair = [_minor_item(*PAIR_SLOTS[0]), _minor_item(*PAIR_SLOTS[1])]
    _configure(mock_bldcms_client, pair)

    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)

    rows = [r for r in _requirements(db_session, visit.id)
            if r.workflow_stage_type == "SCHEDULE_INSPECTION"]
    assert len(rows) == 2
    assert {r.minor_inspection_equipment_id for r in rows} == {13, 33}


def test_each_slot_is_independently_overridable(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    """Items 4 and 5 at the data layer: two requirement rows means Optional, Deactivated and Under AMC
    are per-slot. No state is shared across a pair."""
    from app.services.pending_requirement_service import set_requirement_under_amc

    _configure(mock_bldcms_client, [_minor_item(*s) for s in PAIR_SLOTS[:2]])
    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)
    rows = {r.minor_inspection_equipment_code_snapshot: r
            for r in _requirements(db_session, visit.id)
            if r.minor_inspection_equipment_id is not None}

    set_requirement_under_amc(db_session, rows["G2"].id, under_amc=True,
                              actor_id=_admin(db_session).id)

    db_session.refresh(rows["G"])
    db_session.refresh(rows["G2"])
    assert rows["G2"].under_amc is True
    assert rows["G2"].effective_state == "UNDER_AMC"
    # Its sibling is untouched.
    assert rows["G"].under_amc is False
    assert rows["G"].effective_state == "REQUIRED"
    assert rows["G"].blocks_completion is True


def test_only_the_amc_slot_stops_blocking(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    from app.services.checksheet_requirement_completion_service import (
        evaluate_requirement_completion,
    )
    from app.services.pending_requirement_service import set_requirement_under_amc

    _configure(mock_bldcms_client, [_minor_item(*s) for s in PAIR_SLOTS])
    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)
    rows = {r.minor_inspection_equipment_code_snapshot: r
            for r in _requirements(db_session, visit.id)
            if r.minor_inspection_equipment_id is not None}
    set_requirement_under_amc(db_session, rows["I2"].id, under_amc=True,
                              actor_id=_admin(db_session).id)

    result = evaluate_requirement_completion(db_session, None, visit)
    assert result.under_amc == 1
    # Five of the six still block; AMC excused exactly one.
    assert result.blocking == 5


# ================================= an EXISTING frozen package is untouched (items 2, 10) ========


def test_an_existing_frozen_package_does_not_gain_the_new_slots(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    """THE guarantee. A visit frozen before the split keeps its three requirements and its old names,
    even though BL-DCMS now reports six - because generation is idempotent and a frozen package is
    never re-resolved."""
    pre_split = [
        _minor_item("G", "CENTRAL ELECTRONICS RACK", 13, 14),
        _minor_item("H", "AUXILIARY CONVERTER 1, 2 & 3", 14, 16),
        _minor_item("I", "TRACTION CONVERTER (SR)", 15, 18),
    ]
    _configure(mock_bldcms_client, pre_split)
    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)
    before = _requirements(db_session, visit.id)
    before_ids = [r.id for r in before]
    before_names = [r.minor_inspection_equipment_name_snapshot for r in before]
    assert len([r for r in before if r.minor_inspection_equipment_id is not None]) == 3

    # The split is now live: BL-DCMS reports all six. Regeneration must change nothing.
    _configure(mock_bldcms_client, [_minor_item(*s) for s in PAIR_SLOTS])
    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)

    after = _requirements(db_session, visit.id)
    assert [r.id for r in after] == before_ids
    # The OLD names survive - item 10. A frozen snapshot is history, not a live view.
    assert [r.minor_inspection_equipment_name_snapshot for r in after] == before_names
    assert "CENTRAL ELECTRONICS RACK" in before_names
    assert "Central Electronics Rack-2" not in [
        r.minor_inspection_equipment_name_snapshot for r in after
    ]


def test_a_new_visit_after_the_split_gets_six_while_the_old_one_keeps_three(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    """Both halves of the rule at once, which is the state production will actually be in."""
    _configure(mock_bldcms_client, [
        _minor_item("G", "CENTRAL ELECTRONICS RACK", 13, 14),
        _minor_item("H", "AUXILIARY CONVERTER 1, 2 & 3", 14, 16),
        _minor_item("I", "TRACTION CONVERTER (SR)", 15, 18),
    ])
    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)

    newer = make_shed_visit(db_session, 501, "33586", status="IN_SHED",
                            schedule_family="MINOR", schedule_variant="IA")
    _configure(mock_bldcms_client, [_minor_item(*s) for s in PAIR_SLOTS])
    _generate(db_session, newer, mock_bldcms_client, mock_loco_client)

    old_rows = [r for r in _requirements(db_session, visit.id)
                if r.minor_inspection_equipment_id is not None]
    new_rows = [r for r in _requirements(db_session, newer.id)
                if r.minor_inspection_equipment_id is not None]
    assert len(old_rows) == 3
    assert len(new_rows) == 6


# ================================================= the Pending Checksheets panel (item 7) =======


def test_the_pending_panel_lists_both_slots_independently(
    db_session, visit, mock_bldcms_client, mock_loco_client
):
    from app.services.pending_requirement_service import get_pending_requirements

    _configure(mock_bldcms_client, [_minor_item(*s) for s in PAIR_SLOTS])
    _generate(db_session, visit, mock_bldcms_client, mock_loco_client)

    response = get_pending_requirements(db_session, bldcms_client=None)
    rows = [r for g in response.groups for r in g.requirements
            if r.minor_inspection_equipment_id is not None]
    assert len(rows) == 6
    codes = sorted(r.minor_inspection_equipment_code for r in rows)
    assert codes == ["G", "G2", "H", "H2", "I", "I2"]
    # Each is separately eligible for an AMC toggle, and separately identified.
    assert all(r.amc_eligible for r in rows)
    assert len({r.requirement_id for r in rows}) == 6
