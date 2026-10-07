"""Under AMC: the third per-visit requirement state.

"Under AMC" means the equipment is maintained by outside Firm Staff under an Annual Maintenance
Contract, so no workshop technician owes its checksheet on THIS visit. It is deliberately NOT the
same as Optional (may be done, never blocks) or Deactivated (not expected at all), and most of this
suite is about keeping those three distinct - in the stored row, in the completion gate, and in what
the API reports.

Authorization is the other half: AMC is the one override a Supervisor may set, and only for their own
section, while Mark Optional and Deactivate stay Admin-only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import models
from app.services.checksheet_requirement_completion_service import evaluate_requirement_completion
from app.services.pending_requirement_service import (
    apply_requirement_override,
    set_requirement_under_amc,
)
from tests.conftest import auth_header, hash_password

M1, M6 = 1, 2


@pytest.fixture()
def world(db_session):
    """One open MINOR visit with a frozen package: four M1-HR requirements and one M6-HR, plus
    Test Before and Test After, which no override may touch."""
    db_session.add_all([
        models.Section(id=M1, name="M1-HR", code="M1-HR"),
        models.Section(id=M6, name="M6-HR", code="M6-HR"),
    ])
    db_session.add_all([
        models.User(id=1, employee_id="ADM1", name="Admin", mobile="9000000001", role="Admin",
                    is_active=True, password_hash=hash_password("pw")),
        models.User(id=2, employee_id="SUP1", name="M1 Supervisor", mobile="9000000002",
                    role="Supervisor", is_active=True, section_id=M1, password_hash=hash_password("pw")),
        models.User(id=3, employee_id="SUP6", name="M6 Supervisor", mobile="9000000003",
                    role="Supervisor", is_active=True, section_id=M6, password_hash=hash_password("pw")),
        models.User(id=4, employee_id="TEC1", name="Technician", mobile="9000000011",
                    role="Technician", is_active=True, section_id=M1, password_hash=hash_password("pw")),
        models.User(id=5, employee_id="SUPX", name="Sectionless", mobile="9000000004",
                    role="Supervisor", is_active=True, section_id=None, password_hash=hash_password("pw")),
    ])
    now = datetime.now(timezone.utc)
    db_session.add(models.ShedVisit(
        id=700, loco_number="32032", arrival_at=now, schedule_family="MINOR",
        schedule_variant="IA", status="IN_SHED", created_at=now, updated_at=now))
    db_session.commit()

    db_session.add(models.ShedVisitChecksheetPackage(
        id=70, shed_visit_id=700, generated_at=now, minor_inspection_configuration_complete=True))
    db_session.commit()

    ids = {}
    rows = [
        ("insp_a", "SCHEDULE_INSPECTION", M1, 101),
        ("insp_b", "SCHEDULE_INSPECTION", M1, 102),
        ("insp_c", "SCHEDULE_INSPECTION", M1, 103),
        ("insp_m6", "SCHEDULE_INSPECTION", M6, 104),
        ("tb", "TEST_BEFORE", M1, 105),
        ("ta", "TEST_AFTER", M1, 106),
    ]
    for key, stage, section_id, template_id in rows:
        row = models.ShedVisitChecksheetRequirement(
            package_id=70, template_id=template_id, workflow_stage_type=stage,
            # chk_checksheet_requirement_model_coherent: an APPLICABILITY requirement must carry both
            # an applicability_id and a workflow_stage_type. The id is a BL-DCMS snapshot value with
            # no foreign key here, so any integer satisfies the shape.
            applicability_id=template_id,
            is_required=True, is_active=True, technology_snapshot="3_PHASE",
            section_id_snapshot=section_id, section_name_snapshot="M1-HR" if section_id == M1 else "M6-HR",
            template_name_snapshot=key, requirement_source="APPLICABILITY",
            created_at=now, updated_at=now,
        )
        db_session.add(row)
        db_session.commit()
        ids[key] = row.id
    return {"db": db_session, "ids": ids}


def _row(db, requirement_id) -> models.ShedVisitChecksheetRequirement:
    return db.get(models.ShedVisitChecksheetRequirement, requirement_id)


def _detail(excinfo) -> dict:
    d = excinfo.value.detail
    return d if isinstance(d, dict) else {"message": d}


# ======================================================================== three states ==========


def test_the_three_states_are_distinct_and_resolve_in_precedence_order(world):
    db, rid = world["db"], world["ids"]["insp_a"]
    row = _row(db, rid)
    assert row.effective_state == "REQUIRED" and row.blocks_completion

    set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    assert _row(db, rid).effective_state == "UNDER_AMC"
    assert _row(db, rid).blocks_completion is False

    set_requirement_under_amc(db, rid, under_amc=False, actor_id=1)
    apply_requirement_override(db, rid, is_required=False, actor_id=1)
    assert _row(db, rid).effective_state == "OPTIONAL"

    apply_requirement_override(db, rid, is_active=False, actor_id=1)
    # Deactivated outranks Optional: the row is not expected at all.
    assert _row(db, rid).effective_state == "DEACTIVATED"


def test_amc_is_never_reported_as_optional_or_deactivated(world):
    db, rid = world["db"], world["ids"]["insp_a"]
    out = set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    assert out.under_amc is True
    assert out.effective_state == "UNDER_AMC"
    # The underlying flags are untouched - this is what lets AMC be lifted cleanly.
    assert out.is_required is True
    assert out.is_active is True


# ================================================================== transition matrix ==========


def test_a_deactivated_requirement_cannot_be_marked_amc(world):
    db, rid = world["db"], world["ids"]["insp_a"]
    apply_requirement_override(db, rid, is_active=False, actor_id=1)
    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    assert _detail(excinfo)["code"] == "AMC_REQUIREMENT_DEACTIVATED"
    assert _row(db, rid).under_amc is False


def test_an_optional_requirement_cannot_be_marked_amc_until_required_again(world):
    db, rid = world["db"], world["ids"]["insp_a"]
    apply_requirement_override(db, rid, is_required=False, actor_id=1)
    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    assert _detail(excinfo)["code"] == "AMC_REQUIREMENT_OPTIONAL"

    # Made required again, AMC is accepted.
    apply_requirement_override(db, rid, is_required=True, actor_id=1)
    assert set_requirement_under_amc(db, rid, under_amc=True, actor_id=1).under_amc is True


def test_an_amc_requirement_cannot_be_marked_optional(world):
    db, rid = world["db"], world["ids"]["insp_a"]
    set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    with pytest.raises(Exception) as excinfo:
        apply_requirement_override(db, rid, is_required=False, actor_id=1)
    assert _detail(excinfo)["code"] == "AMC_REQUIREMENT_ACTIVE"
    # Nothing moved.
    assert _row(db, rid).is_required is True
    assert _row(db, rid).under_amc is True


def test_deactivating_an_amc_requirement_clears_amc_in_the_same_transaction(world):
    """Not an error: "not expected at all" is a wider statement than "contracted out", so it is
    honoured. The database CHECK would reject the row otherwise, which is what guarantees this cannot
    be forgotten."""
    db, rid = world["db"], world["ids"]["insp_a"]
    set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)

    out = apply_requirement_override(db, rid, is_active=False, actor_id=1)
    assert out.is_active is False
    assert out.under_amc is False
    assert out.effective_state == "DEACTIVATED"
    assert _row(db, rid).amc_cleared_at is not None


def test_clearing_amc_restores_the_underlying_state_exactly(world):
    db, rid = world["db"], world["ids"]["insp_a"]
    before = _row(db, rid)
    was_required, was_active = before.is_required, before.is_active

    set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    out = set_requirement_under_amc(db, rid, under_amc=False, actor_id=1)

    assert out.is_required == was_required
    assert out.is_active == was_active
    assert out.effective_state == "REQUIRED"
    # The record of who excused it survives the clearing.
    row = _row(db, rid)
    assert row.amc_marked_by == 1 and row.amc_marked_at is not None
    assert row.amc_cleared_by == 1 and row.amc_cleared_at is not None


def test_marking_twice_and_clearing_twice_are_both_idempotent(world):
    db, rid = world["db"], world["ids"]["insp_a"]
    set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    first_marked_at = _row(db, rid).amc_marked_at
    set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    assert _row(db, rid).amc_marked_at == first_marked_at

    set_requirement_under_amc(db, rid, under_amc=False, actor_id=1)
    cleared = _row(db, rid).amc_cleared_at
    set_requirement_under_amc(db, rid, under_amc=False, actor_id=1)
    assert _row(db, rid).amc_cleared_at == cleared


# ======================================================================= authorization ==========


def test_an_admin_may_toggle_any_section(world):
    db, ids = world["db"], world["ids"]
    for key in ("insp_a", "insp_m6"):
        assert set_requirement_under_amc(db, ids[key], under_amc=True, actor_id=1).under_amc is True


def test_a_supervisor_may_toggle_their_own_section(world):
    db, ids = world["db"], world["ids"]
    out = set_requirement_under_amc(db, ids["insp_a"], under_amc=True, actor_id=2)
    assert out.under_amc is True
    assert set_requirement_under_amc(db, ids["insp_a"], under_amc=False, actor_id=2).under_amc is False


def test_a_supervisor_may_not_toggle_another_section(world):
    db, ids = world["db"], world["ids"]
    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, ids["insp_m6"], under_amc=True, actor_id=2)
    assert _detail(excinfo)["code"] == "AMC_OUTSIDE_SECTION"
    assert _row(db, ids["insp_m6"]).under_amc is False


def test_a_sectionless_supervisor_is_refused(world):
    db, ids = world["db"], world["ids"]
    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, ids["insp_a"], under_amc=True, actor_id=5)
    assert _detail(excinfo)["code"] == "AMC_OUTSIDE_SECTION"


def test_a_requirement_with_no_section_is_admin_only(world):
    """Production has zero such rows today (audit section 8: 2,735 of 2,735 carry a section), so this
    is the fail-closed path: a row nobody can attribute to a section is a row no Supervisor owns."""
    db, rid = world["db"], world["ids"]["insp_a"]
    _row(db, rid).section_id_snapshot = None
    db.commit()

    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, rid, under_amc=True, actor_id=2)
    assert _detail(excinfo)["code"] == "AMC_SECTION_UNKNOWN"
    # Admin still can.
    assert set_requirement_under_amc(db, rid, under_amc=True, actor_id=1).under_amc is True


def test_a_technician_is_refused(world):
    db, ids = world["db"], world["ids"]
    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, ids["insp_a"], under_amc=True, actor_id=4)
    assert _detail(excinfo)["code"] == "ACTOR_NOT_PERMITTED"


def test_an_inactive_or_unknown_actor_is_refused(world):
    db, ids = world["db"], world["ids"]
    db.get(models.User, 2).is_active = False
    db.commit()
    for actor in (2, 9999, None):
        with pytest.raises(Exception) as excinfo:
            set_requirement_under_amc(db, ids["insp_a"], under_amc=True, actor_id=actor)
        assert _detail(excinfo)["code"] == "ACTOR_NOT_PERMITTED"


def test_optional_and_deactivate_remain_admin_only_at_the_route(world):
    """The decision was explicit: AMC widens to Supervisor, the other two do not. Asserted on the
    route's dependency so a later edit that swapped it would fail here."""
    import inspect

    from app.api import pending_requirements as od_routes
    from app.core.dependencies import require_admin

    for name in ("set_requirement_required", "set_requirement_active"):
        sig = inspect.signature(getattr(od_routes, name))
        dep = sig.parameters["current_user"].default
        assert dep.dependency is require_admin, f"{name} must stay Admin-only"


# ============================================================================== scope ==========


@pytest.mark.parametrize("key, code", [("tb", "AMC_NOT_APPLICABLE"), ("ta", "AMC_NOT_APPLICABLE")])
def test_test_before_and_test_after_can_never_be_amc(world, key, code):
    db, ids = world["db"], world["ids"]
    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, ids[key], under_amc=True, actor_id=1)
    assert _detail(excinfo)["code"] == code


def test_a_major_requirement_supports_amc(world):
    """MAJOR requirements are stageless (workflow_stage_type NULL) and evaluated at visit level."""
    db = world["db"]
    now = datetime.now(timezone.utc)
    db.add(models.ShedVisit(id=800, loco_number="39078", arrival_at=now, schedule_family="MAJOR",
                            schedule_variant="TOH", status="IN_SHED", created_at=now, updated_at=now))
    db.commit()
    db.add(models.ShedVisitChecksheetPackage(id=80, shed_visit_id=800, generated_at=now))
    db.commit()
    row = models.ShedVisitChecksheetRequirement(
        package_id=80, template_id=201, workflow_stage_type=None, is_required=True, is_active=True,
        technology_snapshot="3_PHASE", section_id_snapshot=M1, section_name_snapshot="M1-HR",
        template_name_snapshot="major", requirement_source="MAJOR_DIRECT",
        created_at=now, updated_at=now)
    db.add(row)
    db.commit()

    assert set_requirement_under_amc(db, row.id, under_amc=True, actor_id=1).under_amc is True


def test_a_closed_visit_refuses_the_toggle(world):
    db, ids = world["db"], world["ids"]
    visit = db.get(models.ShedVisit, 700)
    visit.status = "CLOSED"
    visit.departed_at = datetime.now(timezone.utc)
    visit.departure_source = "DASHBOARD"
    db.commit()
    with pytest.raises(Exception) as excinfo:
        set_requirement_under_amc(db, ids["insp_a"], under_amc=True, actor_id=1)
    assert _detail(excinfo)["code"] == "VISIT_NOT_ACTIVE"


# =================================================================== the completion gate ========


def test_amc_requirements_are_counted_separately_and_never_block(world):
    db, ids = world["db"], world["ids"]
    set_requirement_under_amc(db, ids["insp_a"], under_amc=True, actor_id=1)
    apply_requirement_override(db, ids["insp_b"], is_required=False, actor_id=1)
    apply_requirement_override(db, ids["insp_c"], is_active=False, actor_id=1)

    visit = db.get(models.ShedVisit, 700)
    # bldcms_client=None: with no client the gate reports BLDCMS_UNAVAILABLE rather than consulting
    # checksheets, which is exactly what this test wants - it is asserting the EXEMPTION counts, which
    # are computed before any checksheet lookup.
    result = evaluate_requirement_completion(db, None, visit)

    # Three different exemptions, three different counts. None collapsed.
    assert result.under_amc == 1
    assert result.optional == 1
    assert result.deactivated == 1


# ================================================== the Shed Out arithmetic, proven ============


class _BlClient:
    """Returns APPROVED checksheets for exactly the template ids given, in the shape
    _index_checksheets expects. Anything not listed has no checksheet at all."""

    def __init__(self, approved: set[int], family: str = "MINOR", stage="SCHEDULE_INSPECTION"):
        self._approved, self._family, self._stage = approved, family, stage

    def get_visit_checksheets(self, visit_id: int):
        # The real contract: {"shed_visit_id": ..., "items": [...]}, with the visit id re-checked by
        # _index_checksheets so a response for another visit is treated as unreadable.
        return {
            "shed_visit_id": visit_id,
            "items": [
                {
                    "checksheet_id": 10_000 + t, "template_id": t, "status": "APPROVED",
                    "workflow_stage_type": self._stage, "schedule_family": self._family,
                    "section_id": M1, "equipment_id": None, "maintenance_type": None,
                    "minor_inspection_equipment_id": None,
                }
                for t in sorted(self._approved)
            ],
        }


def _minor_visit_with(db, *, required: int, amc: int, approved: int):
    """One MINOR visit with `required` Schedule Inspection requirements, the first `amc` of them put
    Under AMC, and `approved` of the REMAINING ones satisfied."""
    now = datetime.now(timezone.utc)
    visit_id, package_id = 900, 90
    db.add(models.ShedVisit(id=visit_id, loco_number="44373", arrival_at=now,
                            schedule_family="MINOR", schedule_variant="IB", status="IN_SHED",
                            created_at=now, updated_at=now))
    db.commit()
    db.add(models.ShedVisitChecksheetPackage(
        id=package_id, shed_visit_id=visit_id, generated_at=now,
        minor_inspection_configuration_complete=True))
    db.commit()

    template_ids = []
    for index in range(required):
        template_id = 500 + index
        row = models.ShedVisitChecksheetRequirement(
            package_id=package_id, template_id=template_id,
            workflow_stage_type="SCHEDULE_INSPECTION", applicability_id=template_id,
            requirement_source="APPLICABILITY", is_required=True, is_active=True,
            technology_snapshot="3_PHASE", section_id_snapshot=M1, section_name_snapshot="M1-HR",
            template_name_snapshot=f"eq{index}", created_at=now, updated_at=now)
        db.add(row)
        db.commit()
        template_ids.append((row.id, template_id))

    for requirement_id, _ in template_ids[:amc]:
        set_requirement_under_amc(db, requirement_id, under_amc=True, actor_id=1)

    workshop = [template_id for _, template_id in template_ids[amc:]]
    return db.get(models.ShedVisit, visit_id), set(workshop[:approved])


@pytest.mark.parametrize(
    "required, amc, approved, expect_ready",
    [
        # The brief's MINOR example: 10 mandatory, 2 Under AMC, 8 completed -> passes; 7 -> fails.
        (10, 2, 8, True),
        (10, 2, 7, False),
        # AMC alone never satisfies anything else.
        (10, 2, 0, False),
        # No AMC at all behaves exactly as before.
        (5, 0, 5, True),
        (5, 0, 4, False),
        # Every requirement under AMC: nothing is owed, and nothing is faked as done.
        (3, 3, 0, True),
    ],
)
def test_the_minor_completion_gate_arithmetic(world, required, amc, approved, expect_ready):
    db = world["db"]
    visit, approved_templates = _minor_visit_with(db, required=required, amc=amc, approved=approved)

    result = evaluate_requirement_completion(db, _BlClient(approved_templates), visit)

    assert result.under_amc == amc
    assert result.ready is expect_ready, (
        f"{required} required, {amc} AMC, {approved} approved -> ready={result.ready}"
    )
    # AMC is never counted as satisfied: only real workshop checksheets are.
    assert result.satisfied == min(approved, required - amc)


def test_amc_never_excuses_an_unrelated_requirement(world):
    """The guarantee that matters most. Marking one requirement AMC must not let a DIFFERENT
    incomplete checksheet through."""
    db = world["db"]
    visit, _ = _minor_visit_with(db, required=4, amc=1, approved=0)

    # Nothing approved: the three remaining workshop requirements all still block.
    result = evaluate_requirement_completion(db, _BlClient(set()), visit)
    assert result.ready is False
    assert result.under_amc == 1
    assert result.blocking == 3
    assert len(result.blockers) == 3


def test_the_major_completion_gate_arithmetic(world):
    """The brief's MAJOR example: 6 mandatory, 1 Under AMC, 5 completed -> passes; 4 -> fails.
    MAJOR requirements are stageless and evaluated at visit level."""
    db = world["db"]
    now = datetime.now(timezone.utc)
    db.add(models.ShedVisit(id=950, loco_number="39078", arrival_at=now, schedule_family="MAJOR",
                            schedule_variant="TOH", status="IN_SHED", created_at=now, updated_at=now))
    db.commit()
    db.add(models.ShedVisitChecksheetPackage(id=95, shed_visit_id=950, generated_at=now))
    db.commit()

    ids = []
    for index in range(6):
        row = models.ShedVisitChecksheetRequirement(
            package_id=95, template_id=600 + index, workflow_stage_type=None,
            requirement_source="MAJOR_DIRECT", is_required=True, is_active=True,
            technology_snapshot="3_PHASE", section_id_snapshot=M1, section_name_snapshot="M1-HR",
            template_name_snapshot=f"major{index}", created_at=now, updated_at=now)
        db.add(row)
        db.commit()
        ids.append((row.id, 600 + index))

    set_requirement_under_amc(db, ids[0][0], under_amc=True, actor_id=1)
    visit = db.get(models.ShedVisit, 950)
    workshop = [template_id for _, template_id in ids[1:]]

    five = evaluate_requirement_completion(db, _BlClient(set(workshop), family="MAJOR", stage=None), visit)
    assert five.under_amc == 1
    assert five.ready is True

    four = evaluate_requirement_completion(
        db, _BlClient(set(workshop[:4]), family="MAJOR", stage=None), visit)
    assert four.ready is False
    assert four.under_amc == 1


def test_the_blocker_detail_reports_the_amc_count(world):
    """A blocked Shed Out must say "8 satisfied, 2 under AMC" rather than implying ten were done."""
    from app.services.checksheet_requirement_completion_service import build_ready_blocker_detail

    db = world["db"]
    visit, approved = _minor_visit_with(db, required=5, amc=2, approved=1)
    result = evaluate_requirement_completion(db, _BlClient(approved), visit)

    detail = build_ready_blocker_detail(result, action="Shed Out")
    assert detail["under_amc"] == 2
    assert detail["satisfied"] == 1
    # AMC rows are NOT listed as outstanding - they are not owed by anyone in the workshop.
    assert detail["outstanding"] == 2


# ========================================== the list endpoint must report it too =================


def test_the_list_builder_reports_the_amc_fields(world):
    """THE REGRESSION THAT REACHED PRODUCTION.

    PendingRequirementOut is constructed in TWO places - the list builder in get_pending_requirements
    and requirement_to_out for the PATCH responses - and the first implementation extended only the
    second. Every list row therefore carried amc_eligible=False by schema default, the Pending
    Checksheets page never rendered a toggle, and nothing failed loudly: the field was simply missing
    and its default was a plausible value.

    The list is the ONLY response the page reads, so this is the assertion that matters.
    """
    from app.services.pending_requirement_service import get_pending_requirements

    db, ids = world["db"], world["ids"]
    set_requirement_under_amc(db, ids["insp_a"], under_amc=True, actor_id=1)

    response = get_pending_requirements(db, bldcms_client=None)
    rows = {r.requirement_id: r for g in response.groups for r in g.requirements}
    assert rows, "the panel should list this visit's requirements"

    marked = rows[ids["insp_a"]]
    assert marked.under_amc is True
    assert marked.effective_state == "UNDER_AMC"
    assert marked.amc_eligible is True
    assert marked.amc_marked_at is not None

    # An ordinary Schedule Inspection row is eligible but not marked - this is the main use case, and
    # it is the row the toggle must appear on.
    plain = rows[ids["insp_b"]]
    assert plain.amc_eligible is True
    assert plain.under_amc is False
    assert plain.effective_state == "REQUIRED"

    # Test Before / Test After are never eligible.
    assert rows[ids["tb"]].amc_eligible is False
    assert rows[ids["ta"]].amc_eligible is False


def test_a_list_row_with_no_checksheet_is_still_eligible(world):
    """The main use case. These requirements have no checksheet_header at all - 914 of production's
    active requirements do not (audit section 4) - and eligibility must not depend on one."""
    from app.services.pending_requirement_service import get_pending_requirements

    db = world["db"]
    response = get_pending_requirements(db, bldcms_client=None)
    rows = [r for g in response.groups for r in g.requirements
            if r.workflow_stage_type == "SCHEDULE_INSPECTION"]
    assert rows
    for row in rows:
        assert row.checksheet_id is None, "fixture rows deliberately have no checksheet"
        assert row.amc_eligible is True


def test_both_constructions_report_the_same_amc_fields(world):
    """Belt and braces against the two paths drifting again. The list row and the PATCH response for
    the SAME requirement must agree on every AMC field."""
    from app.services.pending_requirement_service import get_pending_requirements

    db, rid = world["db"], world["ids"]["insp_a"]
    patched = set_requirement_under_amc(db, rid, under_amc=True, actor_id=1)
    listed = next(
        r for g in get_pending_requirements(db, bldcms_client=None).groups
        for r in g.requirements if r.requirement_id == rid
    )
    for field in ("under_amc", "amc_eligible", "effective_state", "amc_marked_by"):
        assert getattr(listed, field) == getattr(patched, field), field
