"""PT-1 and PT-2 stay two requirements in the frozen package and in readiness.

BL-DCMS returns one applicability row per performa, and a pantograph slot has two make performas
(FTRTIL / Schunk). Those alternatives collapse to ONE requirement for that slot - the locomotive has
one pantograph in that position, of one make. What must never collapse is the two SLOTS: PT-1 and
PT-2 are separate minor_inspection_equipment records and therefore separate requirements, counted
and satisfied independently.

The two slots share one source performa definition (which is what keeps their rows and UI controls
synchronised, see BL-DCMS migration 041). These tests prove that sharing a definition does not merge
the requirements.
"""

import pytest

from app.db import models
from tests.conftest import make_true_admin_headers
from tests.test_minor_inspection_m1hr_package import (  # noqa: F401 - fixtures reused deliberately
    ARRIVAL,
    M1,
    M1HR,
    MINOR,
    PANEL,
    _group,
    _m1,
    _reqs,
    _shed_in,
    _submit,
    env,
)

PT1_ID, PT2_ID = 115, 116
PT1_TEMPLATES, PT2_TEMPLATES = [615, 616], [617, 618]


def _pantographs(rows):
    return [r for r in rows if r.minor_inspection_equipment_id in (PT1_ID, PT2_ID)]


def _admin_group(client, db_session, visit_id):
    """The pending panel is section-scoped; an Admin sees every section's rows."""
    return _group(client, make_true_admin_headers(db_session), visit_id)


def _outstanding_labels(group):
    return {b["minor_inspection_equipment_label"] for b in group["requirements"]
            if b["minor_inspection_equipment_id"] in (PT1_ID, PT2_ID)}


# ------------------------------------------------- 2. a fresh package holds one of each slot --

@pytest.mark.parametrize("variant", MINOR)
def test_a_fresh_package_has_one_pt1_requirement_and_one_pt2_requirement(
        client, db_session, env, mock_bldcms_client, variant):
    _m1(mock_bldcms_client, variant)

    rows = _pantographs(_reqs(db_session, _shed_in(client, env, variant)))

    assert len(rows) == 2
    assert {r.minor_inspection_equipment_id for r in rows} == {PT1_ID, PT2_ID}
    assert {r.minor_inspection_equipment_label_snapshot for r in rows} == {
        "HR Pantograph PT-1", "HR Pantograph PT-2"}
    # Four applicability rows (2 slots x 2 makes) collapsed to two requirements - one per slot.
    assert len({r.id for r in rows}) == 2
    assert all(r.is_required and r.is_active for r in rows)


def test_each_slot_keeps_its_own_reference_template(client, db_session, env, mock_bldcms_client):
    _m1(mock_bldcms_client, "IB")

    rows = {r.minor_inspection_equipment_id: r for r in _pantographs(_reqs(db_session, _shed_in(client, env, "IB")))}

    assert rows[PT1_ID].template_id in PT1_TEMPLATES
    assert rows[PT2_ID].template_id in PT2_TEMPLATES
    assert rows[PT1_ID].template_id != rows[PT2_ID].template_id


# ------------------------------------------------------- 3 & 4. independent satisfaction -----

def test_submitting_pt1_does_not_satisfy_pt2(client, db_session, env, mock_bldcms_client):
    _m1(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")

    _submit(mock_bldcms_client, visit_id, "IB", "SCHEDULE_INSPECTION", 615, minor_id=PT1_ID, section=M1)

    group = _admin_group(client, db_session, visit_id)
    assert _outstanding_labels(group) == {"HR Pantograph PT-2"}


def test_submitting_pt2_does_not_satisfy_pt1(client, db_session, env, mock_bldcms_client):
    _m1(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")

    _submit(mock_bldcms_client, visit_id, "IB", "SCHEDULE_INSPECTION", 617, minor_id=PT2_ID, section=M1)

    group = _admin_group(client, db_session, visit_id)
    assert _outstanding_labels(group) == {"HR Pantograph PT-1"}


def test_the_other_make_on_the_other_slot_satisfies_only_its_own_slot(
        client, db_session, env, mock_bldcms_client):
    """Same make, different slot: filing FTRTIL on PT-1 leaves PT-2 outstanding even though PT-2's
    FTRTIL performa is generated from the very same definition."""
    _m1(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")

    _submit(mock_bldcms_client, visit_id, "IB", "SCHEDULE_INSPECTION", 615, minor_id=PT1_ID, section=M1)
    after_pt1 = _outstanding_labels(_admin_group(client, db_session, visit_id))
    _submit(mock_bldcms_client, visit_id, "IB", "SCHEDULE_INSPECTION", 617, minor_id=PT2_ID, section=M1)
    after_both = _outstanding_labels(_admin_group(client, db_session, visit_id))

    assert after_pt1 == {"HR Pantograph PT-2"}
    assert after_both == set()


def test_both_slots_are_counted_separately_in_readiness(client, db_session, env, mock_bldcms_client):
    _m1(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")
    before = _admin_group(client, db_session, visit_id)["counts"]

    _submit(mock_bldcms_client, visit_id, "IB", "SCHEDULE_INSPECTION", 615, minor_id=PT1_ID, section=M1)
    one_done = _admin_group(client, db_session, visit_id)["counts"]
    _submit(mock_bldcms_client, visit_id, "IB", "SCHEDULE_INSPECTION", 618, minor_id=PT2_ID, section=M1)
    both_done = _admin_group(client, db_session, visit_id)["counts"]

    # Each slot moves the satisfied count by exactly one - never both at once, never neither.
    assert one_done["satisfied"] == before["satisfied"] + 1
    assert both_done["satisfied"] == before["satisfied"] + 2


# -------------------------------------------- 6. alternatives collapse, slots never merge ----

@pytest.mark.parametrize("variant", MINOR)
def test_alternative_makes_collapse_within_a_slot_but_the_slots_do_not(
        client, db_session, env, mock_bldcms_client, variant):
    _m1(mock_bldcms_client, variant)

    rows = _reqs(db_session, _shed_in(client, env, variant), section="M1-HR")

    # 16 equipment, 18 applicability rows (PT-1 and PT-2 each contribute two makes) -> 16 rows.
    assert len(rows) == len(M1HR) == 16
    assert len(_pantographs(rows)) == 2
    by_equipment = {}
    for r in rows:
        by_equipment.setdefault(r.minor_inspection_equipment_id, []).append(r)
    assert all(len(v) == 1 for v in by_equipment.values())
    assert PT1_ID in by_equipment and PT2_ID in by_equipment


def test_a_checksheet_filed_against_the_wrong_slot_satisfies_neither_the_other(
        client, db_session, env, mock_bldcms_client):
    """Identity is the equipment, not the template: PT-2's template reported under PT-1's equipment
    id settles PT-1 only - it can never silently close both."""
    _m1(mock_bldcms_client, "IB")
    visit_id = _shed_in(client, env, "IB")

    _submit(mock_bldcms_client, visit_id, "IB", "SCHEDULE_INSPECTION", 617, minor_id=PT1_ID, section=M1)

    assert _outstanding_labels(_admin_group(client, db_session, visit_id)) == {"HR Pantograph PT-2"}
