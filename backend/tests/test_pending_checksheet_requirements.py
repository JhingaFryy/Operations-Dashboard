"""Operational Control phase: the Pending Checksheets panel.

Covers the full brief: active-visit-only scoping, loco/schedule grouping, requirements visible
before any checksheet exists, the per-status visibility rules, per-visit Optional/Deactivate
semantics (and their strict isolation from BL-DCMS global applicability), Minor and Major
requirement generation, and Admin authorization.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models import ShedVisitChecksheetPackage, ShedVisitChecksheetRequirement
from tests.conftest import (
    auth_header,
    grant_access,
    hash_password,
    make_movement_supervisor_headers,
    ensure_section,
    make_section,
    make_shed_visit,
    make_user,
)

PANEL = "/api/admin/pending-checksheet-requirements"


@pytest.fixture()
def admin(db_session):
    # Requirement configuration is Admin-only (migration 012 phase): a genuine Admin, which also sees
    # every section of the panel.
    return make_user(db_session, id=1, employee_id="ADM1", name="Admin", role="Admin",
                     password_hash=hash_password("x"))


@pytest.fixture()
def supervisor(db_session):
    user = make_user(db_session, id=2, employee_id="SUP1", name="Sup", role="Supervisor",
                     password_hash=hash_password("x"))
    grant_access(db_session, user_id=2)
    return user


def _admin_h():
    return auth_header("ADM1", "Admin", 1)


def _sup_h():
    return auth_header("SUP1", "Supervisor", 2)


def _package(db_session, visit_id, package_id=1):
    pkg = ShedVisitChecksheetPackage(
        id=package_id, shed_visit_id=visit_id, generated_by=None,
        generated_at=datetime.now(timezone.utc),
    )
    db_session.add(pkg)
    db_session.commit()
    return pkg


def _requirement(db_session, package_id, req_id, **fields):
    now = datetime.now(timezone.utc)
    data = dict(
        id=req_id,
        package_id=package_id,
        # A normal Inspection row: Test Before / Test After rows are not configurable (migration 012,
        # covered in test_minor_workflow_refinement.py).
        workflow_stage_type="SCHEDULE_INSPECTION",
        applicability_id=req_id,
        template_id=req_id,
        requirement_source="APPLICABILITY",
        is_required=True,
        is_active=True,
        template_name_snapshot=f"Template {req_id}",
        technology_snapshot="3_PHASE",
        section_id_snapshot=1,
        section_name_snapshot="SHIFT",
        equipment_id_snapshot=None,
        equipment_name_snapshot=None,
        maintenance_type_snapshot=None,
        created_at=now,
        updated_at=now,
    )
    data.update(fields)
    row = ShedVisitChecksheetRequirement(**data)
    db_session.add(row)
    db_session.commit()
    return row


def _visit_with_requirement(db_session, visit_id=10, status="IN_SHED", **req):
    ensure_section(db_session, id=1, code="SHIFT", name="SHIFT")
    make_shed_visit(db_session, id=visit_id, loco_number="39018", status=status)
    _package(db_session, visit_id)
    return _requirement(db_session, 1, 1, **req)


# ---------------------------------------------------------------- 1. active visits only --

def test_only_active_shed_visits_are_returned(db_session, client, admin):
    ensure_section(db_session, id=1, code="SHIFT", name="SHIFT")
    make_shed_visit(db_session, id=10, loco_number="39018", status="IN_SHED")
    make_shed_visit(db_session, id=11, loco_number="30634", status="READY")
    make_shed_visit(db_session, id=12, loco_number="11111", status="DEPARTED")
    for pkg_id, visit_id in ((1, 10), (2, 11), (3, 12)):
        _package(db_session, visit_id, package_id=pkg_id)
        _requirement(db_session, pkg_id, pkg_id)

    body = client.get(PANEL, headers=_admin_h()).json()
    locos = {g["loco_number"] for g in body["groups"]}
    assert locos == {"39018", "30634"}   # READY counts as active; DEPARTED does not


# --------------------------------------------------------- 13. closed visit excluded --

def test_closed_visit_requirements_never_appear(db_session, client, admin):
    _visit_with_requirement(db_session, visit_id=10, status="DEPARTED")
    body = client.get(PANEL, headers=_admin_h()).json()
    assert body["groups"] == []


# ------------------------------------------------------- 2. grouped by loco / schedule --

def test_grouped_by_locomotive_and_schedule(db_session, client, admin):
    ensure_section(db_session, id=1, code="SHIFT", name="SHIFT")
    make_shed_visit(db_session, id=10, loco_number="39018", schedule_family="MINOR", schedule_variant="IA")
    make_shed_visit(db_session, id=11, loco_number="39160", schedule_family="MAJOR", schedule_variant="IOH")
    _package(db_session, 10, package_id=1)
    _package(db_session, 11, package_id=2)
    _requirement(db_session, 1, 1)
    _requirement(db_session, 2, 2, workflow_stage_type=None, applicability_id=None,
                 requirement_source="MAJOR_DIRECT", section_name_snapshot="M6-HR")

    groups = {g["loco_number"]: g for g in client.get(PANEL, headers=_admin_h()).json()["groups"]}
    assert groups["39018"]["schedule_variant"] == "IA"
    assert groups["39018"]["schedule_family"] == "MINOR"
    assert groups["39160"]["schedule_variant"] == "IOH"
    assert len(groups["39018"]["requirements"]) == 1
    assert len(groups["39160"]["requirements"]) == 1


# ------------------------------------------ 3. requirement visible before checksheet exists --

def test_requirement_is_visible_before_any_checksheet_exists(db_session, client, admin, mock_bldcms_client):
    _visit_with_requirement(db_session)
    assert mock_bldcms_client.visits == {}   # no checksheet_header row anywhere

    row = client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"][0]
    assert row["display_status"] == "Pending"
    assert row["checksheet_id"] is None


# ------------------------------------------------------------- 4-8. lifecycle visibility --

@pytest.mark.parametrize(
    "status,visible,expected_display",
    [
        ("DRAFT", True, "Draft"),
        ("REJECTED", True, "Needs Correction"),
        ("SUBMITTED", False, None),
        ("UNDER_REVIEW", False, None),
        ("APPROVED", False, None),
    ],
)
def test_lifecycle_visibility(db_session, client, admin, mock_bldcms_client, status, visible, expected_display):
    _visit_with_requirement(db_session)
    mock_bldcms_client.add_checksheet(
        10, checksheet_id=500, template_id=1, section_id=1, equipment_id=None,
        workflow_stage_type="SCHEDULE_INSPECTION", maintenance_type=None, status=status,
    )

    rows = client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"]
    if visible:
        assert len(rows) == 1
        assert rows[0]["display_status"] == expected_display
        assert rows[0]["checksheet_id"] == 500
    else:
        assert rows == []


def test_rejected_checksheet_reappears_automatically(db_session, client, admin, mock_bldcms_client):
    """A submitted requirement leaves the panel; rejecting it must bring it back with no Admin
    action and no mapping to recreate."""
    _visit_with_requirement(db_session)
    item = mock_bldcms_client.add_checksheet(
        10, checksheet_id=500, template_id=1, section_id=1, workflow_stage_type="SCHEDULE_INSPECTION",
        status="SUBMITTED",
    )
    assert client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"] == []

    item["status"] = "REJECTED"
    rows = client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"]
    assert len(rows) == 1
    assert rows[0]["display_status"] == "Needs Correction"


def test_resubmission_beats_an_older_rejected_row_for_the_same_identity(db_session, client, admin, mock_bldcms_client):
    """Several checksheets can share an identity over a visit; the furthest-along one decides."""
    _visit_with_requirement(db_session)
    mock_bldcms_client.add_checksheet(10, checksheet_id=1, template_id=1, section_id=1,
                                      workflow_stage_type="SCHEDULE_INSPECTION", status="REJECTED")
    mock_bldcms_client.add_checksheet(10, checksheet_id=2, template_id=1, section_id=1,
                                      workflow_stage_type="SCHEDULE_INSPECTION", status="SUBMITTED")
    assert client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"] == []


# ---------------------------------------------------------------- 9. optional semantics --

def test_optional_requirement_stays_visible_with_a_badge(db_session, client, admin):
    _visit_with_requirement(db_session, is_required=False)
    group = client.get(PANEL, headers=_admin_h()).json()["groups"][0]
    assert len(group["requirements"]) == 1
    assert group["requirements"][0]["is_required"] is False
    assert group["requirements"][0]["display_status"] == "Pending"
    assert group["counts"]["optional"] == 1


def test_optional_requirement_still_disappears_once_submitted(db_session, client, admin, mock_bldcms_client):
    _visit_with_requirement(db_session, is_required=False)
    mock_bldcms_client.add_checksheet(10, checksheet_id=500, template_id=1, section_id=1,
                                      workflow_stage_type="SCHEDULE_INSPECTION", status="SUBMITTED")
    assert client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"] == []


def test_mark_optional_is_per_visit_and_persists(db_session, client, admin):
    row = _visit_with_requirement(db_session)
    resp = client.patch(f"{PANEL}/{row.id}/required?is_required=false", headers=_admin_h())
    assert resp.status_code == 200
    assert resp.json()["is_required"] is False

    db_session.expire_all()
    assert db_session.get(ShedVisitChecksheetRequirement, row.id).is_required is False
    assert db_session.get(ShedVisitChecksheetRequirement, row.id).changed_by == 1


# ------------------------------------------------------------- 10-12. deactivate semantics --

def test_deactivate_hides_from_default_list_but_keeps_the_row(db_session, client, admin):
    row = _visit_with_requirement(db_session)
    client.patch(f"{PANEL}/{row.id}/active?is_active=false", headers=_admin_h())

    group = client.get(PANEL, headers=_admin_h()).json()["groups"][0]
    assert group["requirements"] == []
    assert group["counts"]["deactivated"] == 1
    # Never physically deleted.
    db_session.expire_all()
    assert db_session.get(ShedVisitChecksheetRequirement, row.id) is not None


def test_deactivated_requirements_are_reachable_through_the_deactivated_view(db_session, client, admin):
    row = _visit_with_requirement(db_session)
    client.patch(f"{PANEL}/{row.id}/active?is_active=false", headers=_admin_h())

    rows = client.get(f"{PANEL}?include_deactivated=true", headers=_admin_h()).json()["groups"][0]["requirements"]
    assert len(rows) == 1
    assert rows[0]["is_active"] is False


def test_re_enable_works(db_session, client, admin):
    row = _visit_with_requirement(db_session)
    client.patch(f"{PANEL}/{row.id}/active?is_active=false", headers=_admin_h())
    resp = client.patch(f"{PANEL}/{row.id}/active?is_active=true", headers=_admin_h())
    assert resp.status_code == 200
    assert client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"][0]["is_active"] is True


def test_deactivate_is_scoped_to_one_visit_only(db_session, client, admin):
    """Deactivating 39018/IA/Test Before must not touch the identical requirement on another
    locomotive's visit."""
    ensure_section(db_session, id=1, code="SHIFT", name="SHIFT")
    make_shed_visit(db_session, id=10, loco_number="39018")
    make_shed_visit(db_session, id=11, loco_number="30634")
    _package(db_session, 10, package_id=1)
    _package(db_session, 11, package_id=2)
    a = _requirement(db_session, 1, 1, template_id=7, applicability_id=7)
    b = _requirement(db_session, 2, 2, template_id=7, applicability_id=7)

    client.patch(f"{PANEL}/{a.id}/active?is_active=false", headers=_admin_h())

    db_session.expire_all()
    assert db_session.get(ShedVisitChecksheetRequirement, a.id).is_active is False
    assert db_session.get(ShedVisitChecksheetRequirement, b.id).is_active is True

    groups = {g["loco_number"]: g for g in client.get(PANEL, headers=_admin_h()).json()["groups"]}
    assert groups["39018"]["requirements"] == []
    assert len(groups["30634"]["requirements"]) == 1


def test_deactivate_never_touches_global_applicability(db_session, client, admin, mock_bldcms_client):
    """The panel's write path must be incapable of reaching BL-DCMS master configuration.

    Operations Dashboard has no write access to checksheet_template_applicability at all - it
    only ever READS it through resolve_applicability - so the strongest available assertion is
    that a deactivate performs no BL-DCMS call whatsoever and leaves the resolver's answer
    identical.
    """
    mock_bldcms_client.applicability[("WAG9HC", "MINOR", "IA", "TEST_BEFORE")] = [
        {"applicability_id": 1, "template_id": 1, "is_required": True, "template_name": "T1",
         "technology": "3_PHASE", "section_id": 1, "section_name": "SHIFT"}
    ]
    before = mock_bldcms_client.resolve_applicability("WAG9HC", "MINOR", "IA", "TEST_BEFORE")

    row = _visit_with_requirement(db_session)
    client.patch(f"{PANEL}/{row.id}/active?is_active=false", headers=_admin_h())
    client.patch(f"{PANEL}/{row.id}/required?is_required=false", headers=_admin_h())

    after = mock_bldcms_client.resolve_applicability("WAG9HC", "MINOR", "IA", "TEST_BEFORE")
    assert before == after
    assert after[0]["is_required"] is True and after[0]["applicability_id"] == 1


def test_override_on_a_closed_visit_is_refused(db_session, client, admin):
    row = _visit_with_requirement(db_session, status="DEPARTED")
    resp = client.patch(f"{PANEL}/{row.id}/active?is_active=false", headers=_admin_h())
    assert resp.status_code == 409


# --------------------------------------------------- 14. no historical cross-visit match --

def test_a_checksheet_from_another_visit_never_satisfies_this_visits_requirement(
    db_session, client, admin, mock_bldcms_client
):
    _visit_with_requirement(db_session, visit_id=10)
    # An APPROVED checksheet with the SAME identity, but recorded against a different visit.
    mock_bldcms_client.add_checksheet(99, checksheet_id=800, template_id=1, section_id=1,
                                      workflow_stage_type="SCHEDULE_INSPECTION", status="APPROVED")

    rows = client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"]
    assert len(rows) == 1
    assert rows[0]["display_status"] == "Pending"
    assert rows[0]["checksheet_id"] is None


def test_maintenance_type_is_part_of_identity(db_session, client, admin, mock_bldcms_client):
    """MAJOR Pattern C: a GC checksheet must not satisfy an OVERHAUL requirement on the same
    equipment."""
    ensure_section(db_session, id=1, code="M35-TM", name="M35-TM")
    make_shed_visit(db_session, id=10, loco_number="39160", schedule_family="MAJOR", schedule_variant="IOH")
    _package(db_session, 10)
    _requirement(db_session, 1, 1, workflow_stage_type=None, applicability_id=None,
                 requirement_source="MAJOR_EQUIPMENT_CHOICE", template_id=50,
                 equipment_id_snapshot=9, equipment_name_snapshot="TM",
                 maintenance_type_snapshot="OVERHAUL", section_name_snapshot="M35-TM")

    mock_bldcms_client.add_checksheet(10, checksheet_id=700, template_id=50, section_id=1,
                                      equipment_id=9, workflow_stage_type=None,
                                      maintenance_type="GC", status="SUBMITTED")

    rows = client.get(PANEL, headers=_admin_h()).json()["groups"][0]["requirements"]
    assert len(rows) == 1
    assert rows[0]["maintenance_type"] == "OVERHAUL"
    assert rows[0]["checksheet_id"] is None


# --------------------------------------------------------------------------- filters --

def test_filters(db_session, client, admin):
    ensure_section(db_session, id=1, code="SHIFT", name="SHIFT")
    make_shed_visit(db_session, id=10, loco_number="39018", schedule_variant="IA")
    make_shed_visit(db_session, id=11, loco_number="30634", schedule_variant="IB")
    _package(db_session, 10, package_id=1)
    _package(db_session, 11, package_id=2)
    _requirement(db_session, 1, 1, section_id_snapshot=1, equipment_id_snapshot=5)
    _requirement(db_session, 2, 2, section_id_snapshot=2, equipment_id_snapshot=6)

    def locos(**params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        body = client.get(f"{PANEL}?{qs}", headers=_admin_h()).json()
        return {g["loco_number"] for g in body["groups"] if g["requirements"]}

    assert locos(loco_number="39018") == {"39018"}
    assert locos(schedule_variant="IB") == {"30634"}
    assert locos(section_id=1) == {"39018"}
    assert locos(equipment_id=6) == {"30634"}
    assert locos(display_status="Pending") == {"39018", "30634"}
    assert locos(technology="3_PHASE") == {"39018", "30634"}
    assert locos(technology="CONVENTIONAL") == set()


# ---------------------------------------------------- 15/16/17. requirement generation --

def test_minor_generation_snapshots_tb_ta_requirements(db_session, client, admin,
                                                       mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=1, code="SHIFT", name="SHIFT")
    make_shed_visit(db_session, id=10, loco_number="39018", schedule_family="MINOR", schedule_variant="IA")
    mock_loco_client.add_locomotive("39018", loco_type="WAG9HC")
    for stage in ("TEST_BEFORE", "TEST_AFTER"):
        mock_bldcms_client.applicability[("WAG9HC", "MINOR", "IA", stage)] = [
            {"applicability_id": 1 if stage == "TEST_BEFORE" else 2, "template_id": 179,
             "is_required": True, "template_name": "3 Phase TB/TA", "technology": "3_PHASE",
             "section_id": 1, "section_name": "SHIFT"}
        ]

    resp = client.post("/api/shed-visits/10/checksheet-work-package", headers=_admin_h())
    assert resp.status_code == 200

    rows = db_session.query(ShedVisitChecksheetRequirement).all()
    assert {r.workflow_stage_type for r in rows} == {"TEST_BEFORE", "TEST_AFTER"}
    assert all(r.requirement_source == "APPLICABILITY" for r in rows)
    assert all(r.is_active is True for r in rows)


def test_major_generation_covers_all_three_patterns(db_session, mock_loco_client, mock_bldcms_client):
    from app.services.checksheet_work_package_service import generate_major_work_package

    ensure_section(db_session, id=1, code="M35-TM", name="M35-TM")
    visit = make_shed_visit(db_session, id=11, loco_number="39160",
                            schedule_family="MAJOR", schedule_variant="IOH")
    mock_loco_client.add_locomotive("39160", loco_type="WAG9HC")
    mock_bldcms_client.add_major_requirement(template_id=1, equipment_id=5, equipment_name="TMB",
                                             source="MAJOR_EQUIPMENT", is_required=True)
    mock_bldcms_client.add_major_requirement(template_id=2, equipment_id=9, equipment_name="TM",
                                             maintenance_type="GC",
                                             source="MAJOR_EQUIPMENT_CHOICE", is_required=False)
    mock_bldcms_client.add_major_requirement(template_id=3, source="MAJOR_DIRECT", is_required=True)

    created = generate_major_work_package(db_session, mock_loco_client, mock_bldcms_client, visit.id, None)
    assert created == 3

    rows = db_session.query(ShedVisitChecksheetRequirement).all()
    assert {r.requirement_source for r in rows} == {
        "MAJOR_EQUIPMENT", "MAJOR_EQUIPMENT_CHOICE", "MAJOR_DIRECT"
    }
    # Major is equipment-wise and stageless, and has no applicability decision to point at.
    assert all(r.workflow_stage_type is None and r.applicability_id is None for r in rows)
    # The ambiguous maintenance_type choice is snapshotted non-required rather than guessed at.
    choice = next(r for r in rows if r.requirement_source == "MAJOR_EQUIPMENT_CHOICE")
    assert choice.is_required is False
    assert choice.maintenance_type_snapshot == "GC"


def test_unconfigured_content_never_fabricates_requirements(db_session, mock_loco_client, mock_bldcms_client):
    from app.services.checksheet_work_package_service import generate_major_work_package
    from fastapi import HTTPException

    visit = make_shed_visit(db_session, id=11, loco_number="39160",
                            schedule_family="MAJOR", schedule_variant="IOH")
    mock_loco_client.add_locomotive("39160", loco_type="WAG9HC")
    mock_bldcms_client.major_unconfigured_sections = ["M7-HR", "SHIFT"]
    # No requirements configured at all.

    with pytest.raises(HTTPException) as exc:
        generate_major_work_package(db_session, mock_loco_client, mock_bldcms_client, visit.id, None)
    assert exc.value.status_code == 409
    assert db_session.query(ShedVisitChecksheetRequirement).count() == 0


def test_visit_without_a_generated_package_is_reported_explicitly(db_session, client, admin):
    """Must never read as "nothing is pending"."""
    make_shed_visit(db_session, id=10, loco_number="39018")
    group = client.get(PANEL, headers=_admin_h()).json()["groups"][0]
    assert group["work_package_generated"] is False
    assert group["requirements"] == []
    assert group["generation_note"]


def test_bldcms_unavailable_is_flagged_not_silently_shown_as_pending(db_session, client, admin, mock_bldcms_client):
    _visit_with_requirement(db_session)
    mock_bldcms_client.unavailable = True
    body = client.get(PANEL, headers=_admin_h()).json()
    assert body["bldcms_available"] is False


# ------------------------------------------------------------ 18. admin authorization --

def test_mutations_require_admin(db_session, client, admin, supervisor):
    row = _visit_with_requirement(db_session)
    for path in (f"{PANEL}/{row.id}/required?is_required=false",
                 f"{PANEL}/{row.id}/active?is_active=false"):
        assert client.patch(path, headers=_sup_h()).status_code == 403
    db_session.expire_all()
    assert db_session.get(ShedVisitChecksheetRequirement, row.id).is_required is True
    assert db_session.get(ShedVisitChecksheetRequirement, row.id).is_active is True


def test_panel_requires_authentication(db_session, client):
    assert client.get(PANEL).status_code in (401, 403)
