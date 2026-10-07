"""PPIO: a PLANNING section, and what it must never be able to do.

PPIO exists so planning staff can be scoped as a section - belong to it, be authorised by it, and
route bookings to maintenance sections. It is NOT a maintenance section: it performs no
checksheets, owns no equipment, and moves no locomotives.

PHASE 0 IS WHY THIS FILE SHIPS BEFORE THE SECTION DOES. `LOCO_MOVEMENT_SECTION_CODES` defaulted to
"SHIFT,PPIO", and can_manage_loco_movement() matches a user's section by code/name against that
list. So a PPIO Supervisor would have acquired Shed In / Shed Out / Start Schedule / Complete
Schedule the instant the sections row was inserted - no code change, no deploy, no review. The
default is now "SHIFT", and the first test below asserts that from the settings object rather than
trusting the comment that says so.
"""

import pathlib
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.core import authz
from app.core.config import Settings, get_settings
from app.db import models
from tests.conftest import auth_header, hash_password, make_section, make_user

PPIO_CODE = "PPIO"
MAINTENANCE_CODES = ("M1-HR", "M2-HR", "M6-HR")


@pytest.fixture()
def ppio_world(db_session):
    """One PPIO planner, one maintenance Supervisor, one Admin, one movement Supervisor.

    The comparison accounts matter as much as PPIO itself: every denial below has to be a denial
    of PPIO specifically, not a denial that would equally hit a normal Supervisor or an Admin.
    """
    ppio = make_section(db_session, 90, PPIO_CODE, "PPIO")
    m2 = make_section(db_session, 91, "M2-HR", "M2-HR")
    m6 = make_section(db_session, 92, "M6-HR", "M6-HR")
    shift = make_section(db_session, 93, "SHIFT", "SHIFT")
    m35tm = make_section(db_session, 94, "M35-TM", "M35-TM")

    pw = hash_password("x")
    planner = make_user(db_session, 90, "PPIO01", "PPIO Planner", "Supervisor", pw,
                        section_id=ppio.id)
    maintenance = make_user(db_session, 91, "M2SUP", "M2 Supervisor", "Supervisor", pw,
                            section_id=m2.id)
    movement = make_user(db_session, 93, "SHIFT1", "Shift Supervisor", "Supervisor", pw,
                         section_id=shift.id)
    admin = make_user(db_session, 95, "ADM1", "Admin", "Admin", pw, section_id=None)
    db_session.commit()

    return {
        "db": db_session,
        "sections": {"PPIO": ppio, "M2-HR": m2, "M6-HR": m6, "SHIFT": shift, "M35-TM": m35tm},
        "planner": planner, "maintenance": maintenance, "movement": movement, "admin": admin,
        "planner_headers": auth_header("PPIO01", "Supervisor", 90),
        "maintenance_headers": auth_header("M2SUP", "Supervisor", 91),
        "movement_headers": auth_header("SHIFT1", "Supervisor", 93),
        "admin_headers": auth_header("ADM1", "Admin", 95),
    }


# =============================================================================================
# PHASE 0. The latent privilege grant.
# =============================================================================================


def test_the_movement_default_does_not_include_PPIO():
    """Asserted on the settings object, not on a comment. This is the whole reason Phase 0 ships
    before the section is created."""
    settings = Settings(_env_file=None)
    codes = {c.strip().upper() for c in settings.loco_movement_section_codes.split(",") if c.strip()}
    assert "PPIO" not in codes, (
        "PPIO is back in the locomotive-movement default - a PPIO Supervisor would acquire "
        "Shed In/Out the moment the section row exists"
    )
    # SHIFT is still a movement section; this narrowed one entry, it did not empty the list.
    assert "SHIFT" in codes


def test_a_PPIO_supervisor_cannot_manage_loco_movement(ppio_world):
    assert authz.can_manage_loco_movement(ppio_world["planner"]) is False


def test_a_movement_supervisor_still_can(ppio_world):
    """The other half of the rule: narrowing the list must not have broken SHIFT."""
    get_settings.cache_clear()
    assert authz.can_manage_loco_movement(ppio_world["movement"]) is True


def test_an_admin_still_can(ppio_world):
    assert authz.can_manage_loco_movement(ppio_world["admin"]) is True


def test_a_normal_maintenance_supervisor_still_cannot(ppio_world):
    """Unchanged behaviour - a maintenance Supervisor never had movement."""
    assert authz.can_manage_loco_movement(ppio_world["maintenance"]) is False


def test_PPIO_is_denied_movement_even_if_only_its_NAME_matched(ppio_world):
    """can_manage_loco_movement matches on code OR name, so a section whose code differs from its
    name cannot sneak through either half."""
    section = ppio_world["sections"]["PPIO"]
    section.code = "PLANNING"
    section.name = "PPIO"
    ppio_world["db"].commit()
    assert authz.can_manage_loco_movement(ppio_world["planner"]) is False


def test_PPIO_still_has_base_operations_dashboard_access(ppio_world):
    """PPIO is a real Supervisor: it reads Overview, Loco Workflow and Shed Visits. Narrowing
    movement must not have removed base access."""
    assert authz.can_access_operations_dashboard(ppio_world["planner"]) is True


def test_PPIO_gets_no_admin_or_cross_section_capability(ppio_world):
    planner = ppio_world["planner"]
    assert authz.can_use_admin_functions(planner) is False
    assert authz.can_access_all_sections(planner) is False


def test_the_capability_payload_reports_movement_false_for_PPIO(ppio_world):
    """The frontend reflects the backend, so the issued capability set is itself part of the
    contract - a true here would re-expose the buttons."""
    caps = authz.capabilities_for(ppio_world["planner"])
    assert caps["can_manage_loco_movement"] is False
    assert caps["can_access_all_sections"] is False
    assert caps["can_admin"] is False


# =============================================================================================
# PHASE 0, through the real routes. The helpers above prove the predicate; these prove the
# ROUTES are actually wired to it - fail-closed at the HTTP boundary, not merely in a function.
# =============================================================================================

MOVEMENT_ROUTES = [
    ("Shed In", "post", "/api/shed-visits/in"),
    ("Start Schedule", "post", "/api/shed-visits/1/start-schedule"),
    ("Complete Schedule", "post", "/api/shed-visits/1/complete-schedule"),
    ("Mark Ready", "post", "/api/shed-visits/1/mark-ready"),
    ("Shed Out", "post", "/api/shed-visits/1/out"),
    ("start Test Before", "post", "/api/shed-visits/1/stages/test-before/start"),
    ("complete Test Before", "post", "/api/shed-visits/1/stages/test-before/complete"),
    ("start Schedule Inspection", "post", "/api/shed-visits/1/stages/schedule-inspection/start"),
    ("complete Schedule Inspection", "post", "/api/shed-visits/1/stages/schedule-inspection/complete"),
    ("start Test After", "post", "/api/shed-visits/1/stages/test-after/start"),
]


@pytest.mark.parametrize("label,method,path", MOVEMENT_ROUTES, ids=[r[0] for r in MOVEMENT_ROUTES])
def test_PPIO_is_refused_by_every_movement_route(ppio_world, client, label, method, path):
    """403, not 422 and not 500: the movement dependency must decide before any payload is even
    considered, so an empty body is the right probe. A route that validated first could leak
    which arguments it wanted to a caller who may not call it at all."""
    get_settings.cache_clear()
    response = getattr(client, method)(path, json={}, headers=ppio_world["planner_headers"])
    assert response.status_code == 403, (
        f"{label} answered {response.status_code} for a PPIO planner: {response.text[:200]}"
    )


@pytest.mark.parametrize("label,method,path", MOVEMENT_ROUTES, ids=[r[0] for r in MOVEMENT_ROUTES])
def test_a_maintenance_supervisor_is_refused_the_same_way(ppio_world, client, label, method, path):
    """Unchanged behaviour, pinned so PPIO's denial cannot be mistaken for a new rule: a normal
    maintenance Supervisor never had movement either."""
    get_settings.cache_clear()
    response = getattr(client, method)(path, json={}, headers=ppio_world["maintenance_headers"])
    assert response.status_code == 403


def test_a_movement_supervisor_gets_PAST_authorization(ppio_world, client):
    """The positive control. SHIFT must still reach the endpoint's own logic - any status other
    than 403 proves authorization let it through, which is all this asserts."""
    get_settings.cache_clear()
    response = client.post(
        "/api/shed-visits/in", json={}, headers=ppio_world["movement_headers"]
    )
    assert response.status_code != 403, (
        "narrowing the movement list broke SHIFT, which is a regression, not the intent"
    )


def test_PPIO_can_still_READ_the_operations_surfaces(ppio_world, client):
    """Overview / Loco Workflow / Shed Visits are read-only for PPIO but they are NOT denied -
    a 403 here would make the account useless for planning."""
    get_settings.cache_clear()
    for path in ("/api/shed-visits/current", "/api/shed-visits/history"):
        response = client.get(path, headers=ppio_world["planner_headers"])
        assert response.status_code != 403, f"{path} denied a PPIO planner: {response.text[:160]}"


# =============================================================================================
# BOOKING SECTION ROUTING - ADDITIVE ONLY.
#
# PPIO adds responsible sections. It cannot remove one, cannot replace the set, and cannot touch
# anything else about a booking. Every source is routable, because the boundary is what may be
# DONE rather than which source a finding came from.
# =============================================================================================

ALL_SOURCES = (
    "LOG_BOOK", "TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER",
    "SPECIAL_CHECKING", "MANUAL", "TRIP_INSPECTION", "GENERAL_CHECKING",
)


@pytest.fixture()
def routing_world(ppio_world, mock_loco_client):
    """A PPIO planner holding can_route_bookings, and one booking per source."""
    from tests.conftest import grant_access, make_booking, make_shed_visit

    db = ppio_world["db"]
    # DELIBERATELY NO dashboard_access ROW FOR THE PLANNER. PPIO identity is the capability: a
    # normal PPIO Supervisor must work immediately after account creation, with no admin grant.
    # This fixture previously granted can_route_bookings=True, which hid the production defect -
    # every routing test passed while a real PPIOTEST account had routing denied.
    grant_access(db, ppio_world["maintenance"].id, is_enabled=True)
    make_shed_visit(db, 1, "30542")

    bookings = {}
    for index, source in enumerate(ALL_SOURCES, start=1):
        bookings[source] = make_booking(
            db, index, shed_visit_id=1, booking_source=source, description=f"{source} finding"
        )
    ppio_world["bookings"] = bookings
    return ppio_world


def _add(client, world, booking, section_ids, headers_key="planner_headers", reason=None):
    body = {"section_ids": section_ids}
    if reason is not None:
        body["reason"] = reason
    return client.post(
        f"/api/bookings/{booking.id}/section-assignments", json=body, headers=world[headers_key]
    )


# --- every source is visible and routable ------------------------------------------------------


def test_PPIO_sees_the_whole_pool(routing_world, client):
    """Planning responsibility covers every source, so the pool is not filtered for them."""
    response = client.get("/api/bookings", headers=routing_world["planner_headers"])
    assert response.status_code == 200, response.text[:200]
    assert {b["booking_source"] for b in response.json()} == set(ALL_SOURCES)


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_PPIO_may_add_a_section_to_any_source(routing_world, client, source):
    """The source allow-list is gone. An addition cannot remove or edit anything, so which source
    a finding came from is not what makes the operation safe."""
    booking = routing_world["bookings"][source]
    m2 = routing_world["sections"]["M2-HR"]
    response = _add(client, routing_world, booking, [m2.id])
    assert response.status_code == 201, f"{source}: {response.status_code} {response.text[:200]}"
    assert response.json()["added_section_ids"] == [m2.id]


def test_an_unknown_future_source_is_also_routable(routing_world, client):
    """A new legitimate source must not need a code change here before a planner can route it.

    bookings.booking_source carries a CHECK constraint naming the eight known values, so an
    unrecognised value cannot be stored today - which is why this is asserted on the service
    rather than through a stored row. The point is that NOTHING in the routing path reads, writes
    or validates the source at all.
    """
    import inspect

    from app.services import booking_routing_service as svc

    source = inspect.getsource(svc)
    assert "ROUTABLE_BOOKING_SOURCES" not in source
    assert not hasattr(svc, "is_routable_source")
    assert not hasattr(svc, "assert_routable")


# --- additive semantics ------------------------------------------------------------------------


def test_PPIO_can_add_one_section(routing_world, client):
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2 = routing_world["sections"]["M2-HR"]
    response = _add(client, routing_world, booking, [m2.id])
    assert response.status_code == 201
    assert [s["section_code"] for s in response.json()["sections"]] == ["M2-HR"]


def test_PPIO_can_add_MULTIPLE_sections(routing_world, client):
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2, m6 = routing_world["sections"]["M2-HR"], routing_world["sections"]["M6-HR"]
    response = _add(client, routing_world, booking, [m2.id, m6.id])
    assert response.status_code == 201
    assert sorted(response.json()["added_section_ids"]) == sorted([m2.id, m6.id])


def test_the_worked_example_from_the_brief(routing_world, client):
    """Traction Motor booking already on M35-TM; planner adds M2-HR and M6-HR. All three remain,
    and M35-TM is untouched."""
    from tests.conftest import make_assignment
    from app.db.models import BookingSectionAssignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m35 = routing_world["sections"]["M35-TM"]
    m2, m6 = routing_world["sections"]["M2-HR"], routing_world["sections"]["M6-HR"]
    auto = make_assignment(db, 100, booking.id, m35.id, status="OPEN")
    assert auto.assignment_source == "AUTO_MAPPING"

    response = _add(client, routing_world, booking, [m2.id, m6.id])
    assert response.status_code == 201, response.text[:300]

    codes = sorted(s["section_code"] for s in response.json()["sections"])
    assert codes == ["M2-HR", "M35-TM", "M6-HR"]

    rows = {
        a.section_id: a
        for a in db.query(BookingSectionAssignment).filter(
            BookingSectionAssignment.booking_id == booking.id
        ).all()
    }
    assert rows[m35.id].assignment_source == "AUTO_MAPPING"
    assert rows[m2.id].assignment_source == "MANUAL"
    assert rows[m6.id].assignment_source == "MANUAL"


@pytest.mark.parametrize("source_value", ["AUTO_MAPPING", "MANUAL"])
def test_an_existing_assignment_is_NOT_rewritten(routing_world, client, source_value):
    """Not re-saved at all: source, assigned_at, status and lifecycle timestamps all survive.
    Re-stating an AUTO_MAPPING row as MANUAL would erase how the booking reached that section."""
    from tests.conftest import make_assignment
    from app.db.models import BookingSectionAssignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2, m6 = routing_world["sections"]["M2-HR"], routing_world["sections"]["M6-HR"]
    existing = make_assignment(db, 110, booking.id, m2.id, status="OPEN")
    existing.assignment_source = source_value
    db.commit()
    before = (existing.assignment_source, existing.assigned_at, existing.status,
              existing.started_at, existing.attended_at)

    assert _add(client, routing_world, booking, [m6.id]).status_code == 201

    row = db.query(BookingSectionAssignment).filter(
        BookingSectionAssignment.booking_id == booking.id,
        BookingSectionAssignment.section_id == m2.id,
    ).one()
    assert (row.assignment_source, row.assigned_at, row.status,
            row.started_at, row.attended_at) == before


def test_adding_a_section_the_booking_already_has_is_a_NO_OP(routing_world, client):
    """Reported as already-assigned, not an error, and not a second row."""
    from tests.conftest import make_assignment
    from app.db.models import BookingEvent, BookingSectionAssignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2 = routing_world["sections"]["M2-HR"]
    make_assignment(db, 120, booking.id, m2.id, status="OPEN")
    events_before = db.query(BookingEvent).filter(BookingEvent.booking_id == booking.id).count()

    response = _add(client, routing_world, booking, [m2.id])
    assert response.status_code == 201
    body = response.json()
    assert body["added_section_ids"] == []
    assert body["already_assigned_section_ids"] == [m2.id]

    assert db.query(BookingSectionAssignment).filter(
        BookingSectionAssignment.booking_id == booking.id).count() == 1
    # No misleading duplicate event for a no-op.
    assert db.query(BookingEvent).filter(
        BookingEvent.booking_id == booking.id).count() == events_before


def test_a_mixed_request_adds_only_what_is_missing(routing_world, client):
    from tests.conftest import make_assignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2, m6 = routing_world["sections"]["M2-HR"], routing_world["sections"]["M6-HR"]
    make_assignment(db, 130, booking.id, m2.id, status="OPEN")

    body = _add(client, routing_world, booking, [m2.id, m6.id]).json()
    assert body["added_section_ids"] == [m6.id]
    assert body["already_assigned_section_ids"] == [m2.id]


# --- removal is impossible BY CONTRACT ---------------------------------------------------------


def test_the_set_REPLACEMENT_endpoint_no_longer_exists(routing_world, client):
    """The earlier PUT .../sections took a desired set and could un-route by omission. It is gone,
    not merely restricted - a contract that cannot express removal is the safeguard."""
    booking = routing_world["bookings"]["LOG_BOOK"]
    response = client.put(
        f"/api/bookings/{booking.id}/sections",
        json={"section_ids": [routing_world["sections"]["M2-HR"].id]},
        headers=routing_world["planner_headers"],
    )
    assert response.status_code in (404, 405), (
        f"a desired-set route still answers {response.status_code}"
    )


def test_the_service_has_no_removal_path_at_all(routing_world):
    """Asserted on the module, because this is a structural guarantee rather than a rule that
    could be relaxed: there is no delete, and no function that would perform one."""
    import inspect

    from app.services import booking_routing_service as svc

    assert not hasattr(svc, "replace_sections")
    assert not hasattr(svc, "_assert_removable")

    # No delete is issued anywhere in the module. Checked against the executable lines only -
    # the docstring legitimately discusses why removal was taken away.
    code_lines = [
        line for line in inspect.getsource(svc).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    body = "\n".join(code_lines)
    assert "db.delete(" not in body, "the routing service can delete an assignment"

    # And the outcome it reports has no removal field, so no caller can believe removal is
    # merely unused rather than impossible.
    assert "removed_section_ids" not in {f.name for f in svc.dataclasses.fields(svc.RoutingOutcome)} \
        if hasattr(svc, "dataclasses") else True
    import dataclasses

    assert "removed_section_ids" not in {f.name for f in dataclasses.fields(svc.RoutingOutcome)}


def test_an_existing_assignment_SURVIVES_an_addition_that_omits_it(routing_world, client):
    """The exact accident the old contract permitted: the planner names only M6-HR, and M35-TM
    stays. Under a desired-set contract this request would have un-routed it."""
    from tests.conftest import make_assignment
    from app.db.models import BookingSectionAssignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m35, m6 = routing_world["sections"]["M35-TM"], routing_world["sections"]["M6-HR"]
    make_assignment(db, 140, booking.id, m35.id, status="OPEN")

    assert _add(client, routing_world, booking, [m6.id]).status_code == 201
    sections = {a.section_id for a in db.query(BookingSectionAssignment).filter(
        BookingSectionAssignment.booking_id == booking.id).all()}
    assert sections == {m35.id, m6.id}


@pytest.mark.parametrize("status_value", ["IN_PROGRESS", "ATTENDED", "REOPENED"])
def test_an_assignment_with_work_behind_it_is_untouched_by_an_addition(
    routing_world, client, status_value
):
    """Previously this needed a removal-safety rule. Now it needs none: an addition cannot reach
    an existing row, whatever state it is in."""
    from tests.conftest import make_assignment
    from app.db.models import BookingSectionAssignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2, m6 = routing_world["sections"]["M2-HR"], routing_world["sections"]["M6-HR"]
    worked = make_assignment(db, 150, booking.id, m2.id, status=status_value)
    before = (worked.status, worked.started_at, worked.attended_at)

    assert _add(client, routing_world, booking, [m6.id]).status_code == 201
    row = db.query(BookingSectionAssignment).filter(
        BookingSectionAssignment.booking_id == booking.id,
        BookingSectionAssignment.section_id == m2.id,
    ).one()
    assert (row.status, row.started_at, row.attended_at) == before


# --- destinations ------------------------------------------------------------------------------


def test_PPIO_cannot_add_PPIO_as_a_destination(routing_world, client):
    """Refused by the planning check, which runs first and gives the more specific reason - PPIO
    routes, it is never routed to. The assignable-section check would refuse it too."""
    from app.db.models import BookingSectionAssignment

    booking = routing_world["bookings"]["LOG_BOOK"]
    response = _add(client, routing_world, booking, [routing_world["sections"]["PPIO"].id])
    assert response.status_code == 400
    assert "planning section" in response.text.lower()
    assert routing_world["db"].query(BookingSectionAssignment).filter(
        BookingSectionAssignment.booking_id == booking.id).count() == 0


# --- destinations are DERIVED from the sections table ------------------------------------------
#
# The hardcoded ASSIGNABLE_WORK_SECTION_CODES is gone. It was ten codes taken from the Minor
# Inspection section set, and it refused exactly the cases this feature exists for: welding at
# MACHINE SHOP, rain leakage to PRE-MONSOON, and anything planning decides SHIFT, CMS Lab or
# MILL-WRIGHT must also act on.


@pytest.fixture()
def wider_sections(routing_world):
    """The non-Minor sections that actually exist in this shed, plus one invented future one."""
    from tests.conftest import make_section

    db = routing_world["db"]
    for index, code in enumerate(
        ("MACHINE SHOP", "PRE-MONSOON", "MILL-WRIGHT", "CMS Lab", "M4-HR"), start=200
    ):
        routing_world["sections"][code] = make_section(db, index, code, code)
    return routing_world


@pytest.mark.parametrize(
    "code", ["M4-HR", "MACHINE SHOP", "PRE-MONSOON", "MILL-WRIGHT", "CMS Lab", "SHIFT"]
)
def test_any_existing_non_planning_section_is_assignable(wider_sections, client, code):
    """Every one of these was refused by the old allow-list."""
    booking = wider_sections["bookings"]["LOG_BOOK"]
    section = wider_sections["sections"][code]
    response = _add(client, wider_sections, booking, [section.id])
    assert response.status_code == 201, f"{code}: {response.status_code} {response.text[:200]}"
    assert [s["section_code"] for s in response.json()["sections"]] == [code]


def test_the_welding_example_from_the_brief(wider_sections, client):
    """CBC operating handle maps to M4-HR; it is broken and needs welding, so MACHINE SHOP must
    also act. M4-HR stays responsible and keeps its AUTO_MAPPING row."""
    from tests.conftest import make_assignment
    from app.db.models import BookingSectionAssignment

    db = wider_sections["db"]
    booking = wider_sections["bookings"]["LOG_BOOK"]
    m4 = wider_sections["sections"]["M4-HR"]
    shop = wider_sections["sections"]["MACHINE SHOP"]
    auto = make_assignment(db, 300, booking.id, m4.id, status="OPEN")
    assert auto.assignment_source == "AUTO_MAPPING"

    response = _add(client, wider_sections, booking, [shop.id], reason="Handle needs welding")
    assert response.status_code == 201, response.text[:300]

    rows = {
        a.section_id: a
        for a in db.query(BookingSectionAssignment).filter(
            BookingSectionAssignment.booking_id == booking.id
        ).all()
    }
    assert set(rows) == {m4.id, shop.id}
    assert rows[m4.id].assignment_source == "AUTO_MAPPING"
    assert rows[shop.id].assignment_source == "MANUAL"


def test_the_rain_leakage_example_from_the_brief(wider_sections, client):
    """Routed normally, then the planning meeting adds PRE-MONSOON. Original responsibility
    remains."""
    from tests.conftest import make_assignment

    db = wider_sections["db"]
    booking = wider_sections["bookings"]["LOG_BOOK"]
    m6 = wider_sections["sections"]["M6-HR"]
    monsoon = wider_sections["sections"]["PRE-MONSOON"]
    make_assignment(db, 310, booking.id, m6.id, status="OPEN")

    body = _add(client, wider_sections, booking, [monsoon.id]).json()
    assert sorted(s["section_code"] for s in body["sections"]) == ["M6-HR", "PRE-MONSOON"]


def test_a_section_created_AFTER_this_build_is_automatically_assignable(routing_world, client):
    """The property that matters most: a non-planning section inserted through the normal
    section-management workflow becomes assignable with NO code change and no deploy. A
    hardcoded list could not satisfy this test."""
    from tests.conftest import make_section

    future = make_section(routing_world["db"], 400, "FUTURE-SECTION", "Future Section")
    response = _add(client, routing_world, routing_world["bookings"]["LOG_BOOK"], [future.id])
    assert response.status_code == 201, response.text[:300]
    assert [s["section_code"] for s in response.json()["sections"]] == ["FUTURE-SECTION"]


def test_a_FUTURE_planning_section_is_excluded_automatically(routing_world, client, monkeypatch):
    """Exclusion goes through the canonical helper, so adding a code to PLANNING_SECTION_CODES
    excludes it everywhere at once - no caller compares a section code itself."""
    from tests.conftest import make_section

    planning2 = make_section(routing_world["db"], 410, "PLANNING-2", "Planning Two")
    monkeypatch.setattr(
        authz, "PLANNING_SECTION_CODES", frozenset({"PPIO", "PLANNING-2"})
    )
    response = _add(client, routing_world, routing_world["bookings"]["LOG_BOOK"], [planning2.id])
    assert response.status_code == 400
    assert "planning section" in response.text.lower()


def test_a_mixed_request_containing_an_invalid_destination_adds_NOTHING(routing_world, client):
    from app.db.models import BookingSectionAssignment

    booking = routing_world["bookings"]["LOG_BOOK"]
    m2, ppio = routing_world["sections"]["M2-HR"], routing_world["sections"]["PPIO"]
    response = _add(client, routing_world, booking, [m2.id, ppio.id])
    assert response.status_code == 400
    assert routing_world["db"].query(BookingSectionAssignment).filter(
        BookingSectionAssignment.booking_id == booking.id).count() == 0


def test_a_nonexistent_section_is_refused(routing_world, client):
    response = _add(client, routing_world, routing_world["bookings"]["LOG_BOOK"], [999999])
    assert response.status_code == 400
    assert "unknown section" in response.text.lower()


def test_an_empty_request_is_refused(routing_world, client):
    response = _add(client, routing_world, routing_world["bookings"]["LOG_BOOK"], [])
    assert response.status_code == 422


def test_a_nonexistent_booking_is_a_404(routing_world, client):
    response = client.post(
        "/api/bookings/999999/section-assignments",
        json={"section_ids": [routing_world["sections"]["M2-HR"].id]},
        headers=routing_world["planner_headers"],
    )
    assert response.status_code == 404


def test_there_is_NO_hardcoded_assignable_section_list_any_more():
    """STATIC SAFETY CHECK. The hardcoded ASSIGNABLE_WORK_SECTION_CODES is gone, and the
    destination decision goes through the canonical planning helper instead."""
    import inspect

    from app.core import authz

    assert not hasattr(authz, "ASSIGNABLE_WORK_SECTION_CODES")

    body = inspect.getsource(authz.is_assignable_work_section)
    assert "is_planning_section" in body, "the destination rule bypasses the canonical helper"
    # No section code is compared in the decision itself.
    for code in ("M2-HR", "SHIFT", "MACHINE SHOP", "PPIO"):
        assert code not in body, f"{code} is hardcoded in the destination decision"


def test_the_destination_rule_is_exists_AND_not_planning(routing_world):
    """Asserted directly on the predicate, so the rule is visible without a route."""
    from app.core.authz import is_assignable_work_section
    from app.db.models import Section

    # Exists and is not planning -> assignable, whatever it is called.
    for code in ("M2-HR", "SHIFT", "MACHINE SHOP", "PRE-MONSOON", "CMS Lab", "ANYTHING-NEW"):
        assert is_assignable_work_section(Section(code=code, name=code)) is True, code
    # Planning -> never.
    assert is_assignable_work_section(Section(code="PPIO", name="PPIO")) is False
    # A section id that matched no row.
    assert is_assignable_work_section(None) is False


def test_the_routing_service_has_no_section_code_literals(routing_world):
    """The service decides via the canonical helper; it never compares a section code itself.

    Checked against EXECUTABLE code only - the module docstring and comments legitimately use
    real section codes as worked examples ("a Traction Motor finding maps to M35-TM..."), which is
    documentation, not a decision. Comments and string literals are stripped by the tokeniser so
    the assertion is about code rather than about prose.
    """
    import io
    import tokenize

    from app.services import booking_routing_service as svc

    source = pathlib.Path(svc.__file__).read_text()
    executable = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        executable.append(token.string)
    body = " ".join(executable)

    for code in ("M2-HR", "M35-TM", "SHIFT", "MACHINE SHOP", "PPIO"):
        assert code not in body, f"{code} is compared in the routing service's executable code"


# --- capability, and everything PPIO still cannot do -------------------------------------------


def test_routing_is_a_capability_not_a_supervisor_right(routing_world, client):
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2 = routing_world["sections"]["M2-HR"]
    assert _add(client, routing_world, booking, [m2.id], "maintenance_headers").status_code == 403
    assert _add(client, routing_world, booking, [m2.id]).status_code == 201


def test_an_unauthenticated_caller_cannot_route(routing_world, client):
    booking = routing_world["bookings"]["LOG_BOOK"]
    response = client.post(
        f"/api/bookings/{booking.id}/section-assignments",
        json={"section_ids": [routing_world["sections"]["M2-HR"].id]},
    )
    assert response.status_code in (401, 403)


def test_the_retired_legacy_add_section_route_is_untouched(routing_world, client):
    """Still 410 for Admin - past the permission check and into the retired service. The planner
    is refused earlier, by the OLD can_add_booking_sections flag it was deliberately not granted."""
    booking = routing_world["bookings"]["LOG_BOOK"]
    body = {"section_id": routing_world["sections"]["M2-HR"].id, "reason": "x"}
    assert client.post(f"/api/bookings/{booking.id}/sections", json=body,
                       headers=routing_world["admin_headers"]).status_code == 410
    assert client.post(f"/api/bookings/{booking.id}/sections", json=body,
                       headers=routing_world["planner_headers"]).status_code == 403


def test_routing_changes_NOTHING_else_about_the_booking(routing_world, client):
    from tests.conftest import make_assignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m2, m6 = routing_world["sections"]["M2-HR"], routing_world["sections"]["M6-HR"]
    make_assignment(db, 160, booking.id, m2.id, status="OPEN")

    watched = ("description", "booking_source", "equipment_node_id", "defect_type_id", "status",
               "shed_visit_id", "stage_id", "created_by", "created_at", "client_booking_id",
               "started_at", "attended_at", "attendance_remarks")
    before = {f: getattr(booking, f, None) for f in watched}

    assert _add(client, routing_world, booking, [m6.id]).status_code == 201

    db.refresh(booking)
    assert {f: getattr(booking, f, None) for f in watched} == before


def test_PPIO_cannot_start_attend_close_reopen_or_delete(routing_world, client):
    from tests.conftest import make_assignment

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    assignment = make_assignment(db, 170, booking.id, routing_world["sections"]["M2-HR"].id)

    for path, body in (
        (f"/api/bookings/{booking.id}/start", {}),
        (f"/api/bookings/{booking.id}/attend", {"remarks": "x"}),
        (f"/api/bookings/{booking.id}/reopen", {"reason": "x"}),
        (f"/api/section-assignments/{assignment.id}/start", {}),
        (f"/api/section-assignments/{assignment.id}/attend", {"remarks": "x"}),
        (f"/api/section-assignments/{assignment.id}/reopen", {"reason": "x"}),
    ):
        response = client.post(path, json=body, headers=routing_world["planner_headers"])
        assert response.status_code in (403, 404, 410), f"{path} -> {response.status_code}"

    deleted = client.delete(
        f"/api/admin/bookings/{booking.id}", headers=routing_world["planner_headers"]
    )
    assert deleted.status_code in (403, 404, 405, 422)


# --- audit -------------------------------------------------------------------------------------


def test_the_audit_event_records_actor_timestamp_and_both_SETS(routing_world, client):
    from tests.conftest import make_assignment
    from app.db.models import BookingEvent

    db = routing_world["db"]
    booking = routing_world["bookings"]["LOG_BOOK"]
    m35, m6 = routing_world["sections"]["M35-TM"], routing_world["sections"]["M6-HR"]
    make_assignment(db, 180, booking.id, m35.id, status="OPEN")

    assert _add(client, routing_world, booking, [m6.id],
                reason="Aux compressor, M6 owns it").status_code == 201

    events = db.query(BookingEvent).filter(
        BookingEvent.booking_id == booking.id, BookingEvent.event_type == "FORWARDED"
    ).all()
    assert len(events) == 1
    event = events[0]
    assert event.created_by == routing_world["planner"].id
    assert event.created_at is not None
    assert event.remarks == "Aux compressor, M6 owns it"
    assert event.to_section_id == m6.id
    # Nothing was moved AWAY from: a planner adds responsibility, never transfers it.
    assert event.from_section_id is None

    data = event.event_data
    assert data["change"] == "SECTION_RESPONSIBILITY_ADDED"
    assert data["previous_section_ids"] == [m35.id]
    assert data["added_section_ids"] == [m6.id]
    assert sorted(data["new_section_ids"]) == sorted([m35.id, m6.id])
    assert data["previous_section_codes"] == ["M35-TM"]
    assert data["added_section_codes"] == ["M6-HR"]
    assert data["actor_employee_id"] == "PPIO01"


def test_the_event_type_is_one_the_CHECK_constraint_already_permits():
    from app.services.booking_routing_service import ROUTING_EVENT_TYPE

    assert ROUTING_EVENT_TYPE == "FORWARDED"


def test_an_admin_can_also_add_and_is_recorded_as_the_actor(routing_world, client):
    from app.db.models import BookingEvent

    db = routing_world["db"]
    booking = routing_world["bookings"]["TEST_BEFORE"]
    assert _add(client, routing_world, booking,
                [routing_world["sections"]["M2-HR"].id], "admin_headers").status_code == 201
    event = db.query(BookingEvent).filter(
        BookingEvent.booking_id == booking.id, BookingEvent.event_type == "FORWARDED"
    ).first()
    assert event.created_by == routing_world["admin"].id
    assert event.event_data["actor_employee_id"] == "ADM1"


# --- read access, unchanged from before --------------------------------------------------------


def test_an_ordinary_supervisor_still_cannot_read_the_global_pool(routing_world, client):
    assert client.get("/api/bookings",
                      headers=routing_world["maintenance_headers"]).status_code == 403


def test_an_admin_can_still_read_the_global_pool(routing_world, client):
    assert client.get("/api/bookings", headers=routing_world["admin_headers"]).status_code == 200


def test_PPIO_still_cannot_reach_any_ADMIN_surface(routing_world, client):
    for path in ("/api/admin/dashboard-access/users", "/api/admin/equipment-mapping/sections"):
        response = client.get(path, headers=routing_world["planner_headers"])
        assert response.status_code in (403, 404), f"{path} -> {response.status_code}"


def test_the_capability_payload_is_unchanged_for_PPIO(routing_world, client):
    planner = client.get("/api/auth/me", headers=routing_world["planner_headers"]).json()
    assert planner["permissions"]["can_route_bookings"] is True
    assert planner["capabilities"]["can_manage_loco_movement"] is False
    assert planner["capabilities"]["can_admin"] is False
    maintenance = client.get("/api/auth/me",
                             headers=routing_world["maintenance_headers"]).json()
    assert maintenance["permissions"]["can_route_bookings"] is False


# =============================================================================================
# PLANNER BOOKING CREATION.
#
# Reuses the normal Booking entity and the normal creation service - same CREATED event, same
# Loco Master auto-mapping, same NO_SECTION_MAPPING refusal. No parallel table, no second path.
# =============================================================================================


@pytest.fixture()
def creation_world(routing_world, mock_loco_client, db_session):
    """Equipment that auto-maps to M35-TM, plus a defect type - the brief's worked example."""
    from app.db import models

    mock_loco_client.add_locomotive("30542", loco_type="WAG9HC")
    mock_loco_client.add_node(1843, 1, None, "Traction Motor")
    mock_loco_client.set_mapping(1843, ["M35-TM"])

    defect = models.BookingDefectType(
        id=1, code="WORN", name="Worn", is_active=True, created_at=datetime.now(timezone.utc)
    )
    db_session.add(defect)
    db_session.commit()
    routing_world["defect"] = defect
    return routing_world


def _create(client, world, section_ids=None, headers_key="planner_headers", **over):
    body = {
        "shed_visit_id": 1,
        "equipment_node_id": 1843,
        "defect_type_id": world["defect"].id,
        "description": "Planner-raised finding",
        "additional_section_ids": section_ids or [],
    }
    body.update(over)
    return client.post("/api/bookings/planning", json=body, headers=world[headers_key])


def test_PPIO_can_create_a_booking(creation_world, client):
    response = _create(client, creation_world)
    assert response.status_code == 201, response.text[:400]
    body = response.json()
    assert body["booking_id"] > 0
    # MANUAL, not a new "PPIO" source value - overloading the source would corrupt a field that
    # drives stage reconciliation and Test After gating.
    assert body["booking_source"] == "MANUAL"


def test_the_auto_mapped_section_is_KEPT(creation_world, client):
    """Equipment maps to M35-TM; the planner supplies nothing extra. The auto-mapping stands."""
    body = _create(client, creation_world).json()
    assert [s["section_code"] for s in body["sections"]] == ["M35-TM"]
    assert [s["assignment_source"] for s in body["sections"]] == ["AUTO_MAPPING"]


def test_PPIO_may_add_extra_sections_DURING_creation(creation_world, client):
    """The brief's worked example: auto-maps to M35-TM, planner also chooses M2-HR and M6-HR."""
    m2, m6 = creation_world["sections"]["M2-HR"], creation_world["sections"]["M6-HR"]
    response = _create(client, creation_world, [m2.id, m6.id])
    assert response.status_code == 201, response.text[:400]
    body = response.json()

    assert sorted(s["section_code"] for s in body["sections"]) == ["M2-HR", "M35-TM", "M6-HR"]
    by_code = {s["section_code"]: s["assignment_source"] for s in body["sections"]}
    assert by_code["M35-TM"] == "AUTO_MAPPING", "the auto-mapping was overwritten"
    assert by_code["M2-HR"] == "MANUAL"
    assert by_code["M6-HR"] == "MANUAL"
    assert sorted(body["added_section_ids"]) == sorted([m2.id, m6.id])


def test_an_invalid_extra_section_rolls_the_WHOLE_creation_back(creation_world, client):
    """No half-routed booking: the destinations are validated before the booking is written."""
    from app.db.models import Booking

    before = creation_world["db"].query(Booking).count()
    response = _create(client, creation_world, [creation_world["sections"]["PPIO"].id])
    assert response.status_code == 400
    assert creation_world["db"].query(Booking).count() == before


def test_any_non_planning_section_may_be_chosen_at_creation(creation_world, client):
    """SHIFT used to be refused by the hardcoded list. It is a valid current section, so planning
    may route to it."""
    response = _create(client, creation_world, [creation_world["sections"]["SHIFT"].id])
    assert response.status_code == 201, response.text[:300]
    by_code = {s["section_code"]: s["assignment_source"] for s in response.json()["sections"]}
    assert by_code["SHIFT"] == "MANUAL"
    assert by_code["M35-TM"] == "AUTO_MAPPING"


def test_a_planning_section_is_still_refused_at_creation(creation_world, client):
    from app.db.models import Booking

    before = creation_world["db"].query(Booking).count()
    response = _create(client, creation_world, [creation_world["sections"]["PPIO"].id])
    assert response.status_code == 400
    assert creation_world["db"].query(Booking).count() == before


def test_the_created_booking_is_AUDITABLE_and_names_the_planner(creation_world, client):
    from app.db.models import BookingEvent

    booking_id = _create(client, creation_world,
                         [creation_world["sections"]["M2-HR"].id]).json()["booking_id"]
    events = creation_world["db"].query(BookingEvent).filter(
        BookingEvent.booking_id == booking_id
    ).all()
    types = {e.event_type for e in events}
    # The normal creation vocabulary, reused: CREATED plus one AUTO_ROUTED per auto-mapped
    # section, plus FORWARDED for the planner's addition.
    assert "CREATED" in types
    assert "FORWARDED" in types
    created = next(e for e in events if e.event_type == "CREATED")
    assert created.created_by == creation_world["planner"].id
    assert created.event_data.get("origin") == "PLANNING"
    assert created.event_data.get("actor_employee_id") == "PPIO01"


def test_created_by_is_the_planner_so_the_badge_can_be_derived(creation_world, client):
    """"Added by PPIO" is DERIVED from bookings.created_by, not a stored flag or a source value."""
    from app.db.models import Booking

    booking_id = _create(client, creation_world).json()["booking_id"]
    booking = creation_world["db"].query(Booking).filter(Booking.id == booking_id).one()
    assert booking.created_by == creation_world["planner"].id


def test_the_pool_reports_the_booking_as_planner_raised(creation_world, client):
    """The badge's data, end to end through the real pool endpoint."""
    booking_id = _create(client, creation_world).json()["booking_id"]
    rows = client.get("/api/bookings", headers=creation_world["planner_headers"]).json()
    row = next(b for b in rows if b["id"] == booking_id)
    assert row["created_by_planning_section"] is True
    assert row["created_by_section_code"] == "PPIO"
    assert row["created_by_name"] == "PPIO Planner"
    # And its source is still semantically correct, not overloaded.
    assert row["booking_source"] == "MANUAL"


def test_a_booking_raised_by_anyone_else_is_NOT_badged(creation_world, client):
    """The badge must not appear on every MANUAL booking - only on planner-raised ones."""
    rows = client.get("/api/bookings", headers=creation_world["admin_headers"]).json()
    manual = next(b for b in rows if b["booking_source"] == "MANUAL" and b["id"] <= 8)
    assert manual["created_by_planning_section"] is False


def test_an_admin_created_booking_is_not_badged_either(creation_world, client):
    booking_id = _create(client, creation_world, headers_key="admin_headers").json()["booking_id"]
    rows = client.get("/api/bookings", headers=creation_world["admin_headers"]).json()
    row = next(b for b in rows if b["id"] == booking_id)
    assert row["created_by_planning_section"] is False
    assert row["created_by_section_code"] is None  # Admin has no section


def test_a_maintenance_supervisor_cannot_create_a_planning_booking(creation_world, client):
    assert _create(client, creation_world, headers_key="maintenance_headers").status_code == 403


def test_the_planner_cannot_set_status_source_or_timestamps_at_creation(creation_world, client):
    """Unknown fields are ignored by the schema, so a planner cannot smuggle lifecycle state in."""
    from app.db.models import Booking

    response = _create(
        client, creation_world,
        booking_source="SCHEDULE_INSPECTION", status="ATTENDED",
        started_at="2020-01-01T00:00:00Z", created_by=99999,
    )
    assert response.status_code == 201, response.text[:300]
    booking = creation_world["db"].query(Booking).filter(
        Booking.id == response.json()["booking_id"]
    ).one()
    assert booking.booking_source == "MANUAL"
    assert booking.status == "OPEN"
    assert booking.started_at is None
    assert booking.created_by == creation_world["planner"].id


# =============================================================================================
# UNMAPPED EQUIPMENT.
#
# Ordinary creation refuses equipment Loco Master has no section mapping for
# (NO_SECTION_MAPPING), because nobody would be responsible for the finding. That rule is about
# the OUTCOME - a booking must never exist with zero assignments - not about where the sections
# came from. A planner naming the responsible sections supplies exactly what the mapping would
# have, so the outcome is satisfied.
#
# The rule itself is NOT relaxed: with no mapping and nothing selected, planning is refused too.
# =============================================================================================


@pytest.fixture()
def unmapped_equipment(creation_world, mock_loco_client):
    """A node that exists but has no section mapping at all."""
    mock_loco_client.add_node(1900, 1, None, "Unmapped Gadget")
    mock_loco_client.set_mapping(1900, [])
    return creation_world


def test_1_auto_map_with_no_extras(creation_world, client):
    """Mapped equipment, planner adds nothing: the AUTO_MAPPING assignment stands alone."""
    body = _create(client, creation_world).json()
    assert [(s["section_code"], s["assignment_source"]) for s in body["sections"]] == [
        ("M35-TM", "AUTO_MAPPING")
    ]
    assert body["added_section_ids"] == []


def test_2_auto_map_with_extras(creation_world, client):
    """Mapped equipment plus planner choices: mapping preserved, choices added as MANUAL."""
    m2, m6 = creation_world["sections"]["M2-HR"], creation_world["sections"]["M6-HR"]
    body = _create(client, creation_world, [m2.id, m6.id]).json()
    by_code = {s["section_code"]: s["assignment_source"] for s in body["sections"]}
    assert by_code == {"M35-TM": "AUTO_MAPPING", "M2-HR": "MANUAL", "M6-HR": "MANUAL"}
    assert sorted(body["added_section_ids"]) == sorted([m2.id, m6.id])


def test_3_no_auto_map_with_explicit_sections_SUCCEEDS(unmapped_equipment, client):
    """The new behaviour. The planner's sections become the booking's routing, as MANUAL - not
    AUTO_MAPPING, because nothing was auto-mapped and claiming otherwise would be a lie in the
    audit trail."""
    m2, m6 = unmapped_equipment["sections"]["M2-HR"], unmapped_equipment["sections"]["M6-HR"]
    response = _create(client, unmapped_equipment, [m2.id, m6.id], equipment_node_id=1900)
    assert response.status_code == 201, response.text[:400]

    body = response.json()
    assert sorted(s["section_code"] for s in body["sections"]) == ["M2-HR", "M6-HR"]
    assert {s["assignment_source"] for s in body["sections"]} == {"MANUAL"}
    # They came in as the fallback, so they are the booking's initial set rather than additions -
    # and crucially there is exactly one row each, not two.
    from app.db.models import BookingSectionAssignment

    rows = unmapped_equipment["db"].query(BookingSectionAssignment).filter(
        BookingSectionAssignment.booking_id == body["booking_id"]
    ).all()
    assert len(rows) == 2


def test_4_no_auto_map_with_ZERO_sections_FAILS(unmapped_equipment, client):
    """The zero-assignment guarantee, kept. A booking nobody is responsible for is not a
    booking, and planning gets no exemption from that."""
    from app.db.models import Booking

    before = unmapped_equipment["db"].query(Booking).count()
    response = _create(client, unmapped_equipment, [], equipment_node_id=1900)
    assert response.status_code == 422, response.text[:300]
    assert "NO_SECTION_MAPPING" in response.text
    assert unmapped_equipment["db"].query(Booking).count() == before, "a booking was left behind"


def test_5_ORDINARY_creation_with_no_auto_map_still_fails(
    unmapped_equipment, mock_loco_client
):
    """THE SERVICE WAS NOT GLOBALLY RELAXED.

    Called the way every existing caller calls it - without the fallback - unmapped equipment is
    still refused with NO_SECTION_MAPPING. Exercised against the real service rather than through
    the planning route, because the planning route is precisely the one caller that does pass it.
    """
    from app.services import booking_creation_service

    with pytest.raises(HTTPException) as exc:
        booking_creation_service.create_booking(
            unmapped_equipment["db"],
            mock_loco_client,
            shed_visit_id=1,
            stage_id=None,
            booking_source="LOG_BOOK",
            equipment_node_id=1900,
            defect_type_id=unmapped_equipment["defect"].id,
            description="ordinary booking on unmapped equipment",
            actor=unmapped_equipment["maintenance"],
            now=datetime.now(timezone.utc),
        )
    assert exc.value.status_code == 422
    assert "NO_SECTION_MAPPING" in str(exc.value.detail)
    unmapped_equipment["db"].rollback()


def test_5c_an_EMPTY_fallback_list_is_the_same_as_none(unmapped_equipment, mock_loco_client):
    """`[]` must not count as "a fallback was supplied" - it carries no sections, so it would
    create the zero-assignment booking the whole rule exists to prevent."""
    from app.services import booking_creation_service

    with pytest.raises(HTTPException) as exc:
        booking_creation_service.create_booking(
            unmapped_equipment["db"],
            mock_loco_client,
            shed_visit_id=1,
            stage_id=None,
            booking_source="MANUAL",
            equipment_node_id=1900,
            defect_type_id=unmapped_equipment["defect"].id,
            description="empty fallback",
            actor=unmapped_equipment["planner"],
            now=datetime.now(timezone.utc),
            unmapped_fallback_sections=[],
        )
    assert exc.value.status_code == 422
    assert "NO_SECTION_MAPPING" in str(exc.value.detail)
    unmapped_equipment["db"].rollback()


def test_5b_the_fallback_parameter_DEFAULTS_to_no_fallback():
    """Asserted on the signature: an existing caller that knows nothing about this parameter
    cannot accidentally acquire the new behaviour."""
    import inspect

    from app.services import booking_creation_service

    param = inspect.signature(booking_creation_service.create_booking).parameters[
        "unmapped_fallback_sections"
    ]
    assert param.default is None
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_a_fallback_can_NEVER_override_a_mapping_that_resolves(creation_world, client):
    """Mapped equipment ignores the fallback entirely: M35-TM stays AUTO_MAPPING and the
    planner's sections are added on top, not substituted for it. So the parameter cannot be used
    to launder a manual choice into an auto-routing or to bypass the mapping."""
    m2 = creation_world["sections"]["M2-HR"]
    body = _create(client, creation_world, [m2.id]).json()
    by_code = {s["section_code"]: s["assignment_source"] for s in body["sections"]}
    assert by_code["M35-TM"] == "AUTO_MAPPING"
    assert by_code["M2-HR"] == "MANUAL"


def test_6_provenance_survives_the_unmapped_path(unmapped_equipment, client):
    """"Added by PPIO" is derived from created_by, so it must hold on this path too."""
    from app.db.models import BookingEvent

    m2 = unmapped_equipment["sections"]["M2-HR"]
    booking_id = _create(
        client, unmapped_equipment, [m2.id], equipment_node_id=1900
    ).json()["booking_id"]

    created = unmapped_equipment["db"].query(BookingEvent).filter(
        BookingEvent.booking_id == booking_id, BookingEvent.event_type == "CREATED"
    ).one()
    assert created.created_by == unmapped_equipment["planner"].id
    assert created.event_data.get("origin") == "PLANNING"
    assert created.event_data.get("actor_employee_id") == "PPIO01"

    rows = client.get("/api/bookings", headers=unmapped_equipment["planner_headers"]).json()
    row = next(b for b in rows if b["id"] == booking_id)
    assert row["created_by_planning_section"] is True
    assert row["created_by_section_code"] == "PPIO"
    assert row["booking_source"] == "MANUAL"


def test_the_unmapped_routing_event_does_not_claim_to_be_AUTOMATIC(unmapped_equipment, client):
    """A person routed these, so the event is FORWARDED rather than AUTO_ROUTED. Both are already
    permitted by chk_booking_event_type, so no DDL and no new vocabulary."""
    from app.db.models import BookingEvent

    m2 = unmapped_equipment["sections"]["M2-HR"]
    booking_id = _create(
        client, unmapped_equipment, [m2.id], equipment_node_id=1900
    ).json()["booking_id"]

    types = {
        e.event_type
        for e in unmapped_equipment["db"].query(BookingEvent).filter(
            BookingEvent.booking_id == booking_id
        ).all()
    }
    assert "AUTO_ROUTED" not in types, "a planner-chosen section was recorded as auto-routed"
    assert "FORWARDED" in types


def test_a_mapped_booking_still_records_AUTO_ROUTED(creation_world, client):
    """The control for the test above - the normal path's event vocabulary is unchanged."""
    from app.db.models import BookingEvent

    booking_id = _create(client, creation_world).json()["booking_id"]
    types = {
        e.event_type
        for e in creation_world["db"].query(BookingEvent).filter(
            BookingEvent.booking_id == booking_id
        ).all()
    }
    assert "AUTO_ROUTED" in types


# =============================================================================================
# PPIO IDENTITY IS THE CAPABILITY.
#
# Routing first lived ONLY in dashboard_access.can_route_bookings, so a normal PPIO Supervisor -
# role Supervisor, section PPIO, created through the ordinary workflow - had routing DENIED out
# of the box. In production that showed up as two visible faults at once: the global Booking Pool
# was hidden (it is gated on routing), and the sidebar, which inferred "planner" from "has
# routing", instead offered a "PPIO Bookings" section work queue - a dashboard for a section
# nothing can ever be routed to.
# =============================================================================================


def test_a_normal_PPIO_supervisor_can_route_with_NO_dashboard_access_row(ppio_world):
    """The whole fix, at the predicate. No row, no grant, no admin action."""
    from app.db.models import DashboardAccess

    planner = ppio_world["planner"]
    assert (
        ppio_world["db"].query(DashboardAccess)
        .filter(DashboardAccess.user_id == planner.id).count() == 0
    )
    assert authz.is_ppio_planner(planner) is True
    assert authz.can_route_bookings(planner) is True


def test_an_ordinary_maintenance_supervisor_still_cannot_route(ppio_world):
    """Unchanged. Deriving the capability from PPIO identity must not hand it to everyone."""
    assert authz.is_ppio_planner(ppio_world["maintenance"]) is False
    assert authz.can_route_bookings(ppio_world["maintenance"]) is False


def test_an_admin_still_routes_by_role(ppio_world):
    assert authz.can_route_bookings(ppio_world["admin"]) is True
    assert authz.is_ppio_planner(ppio_world["admin"]) is False


def test_the_explicit_flag_still_works_for_an_exceptional_non_planning_account(ppio_world):
    """dashboard_access.can_route_bookings is kept, and still means what it meant - it is now the
    way to grant routing to a NON-planning account, rather than the only way to be a planner."""
    from tests.conftest import grant_access

    maintenance = ppio_world["maintenance"]
    from app.db.models import DashboardAccess

    assert authz.can_route_bookings(maintenance) is False
    ppio_world["db"].query(DashboardAccess).filter(
        DashboardAccess.user_id == maintenance.id
    ).delete()
    ppio_world["db"].commit()
    grant_access(ppio_world["db"], maintenance.id, is_enabled=True, can_route_bookings=True)
    ppio_world["db"].expire_all()
    maintenance = ppio_world["db"].get(type(maintenance), maintenance.id)
    assert authz.can_route_bookings(maintenance) is True
    # And it does NOT make them a planner - they keep their own section work queue.
    assert authz.is_ppio_planner(maintenance) is False
    assert authz.is_planning_user(maintenance) is False


def test_a_TECHNICIAN_in_the_planning_section_is_not_a_planner(ppio_world):
    """Role still has to be right. Belonging to PPIO does not make a Technician a planner."""
    from tests.conftest import hash_password, make_user

    tech = make_user(
        ppio_world["db"], 96, "PPIOTEC", "PPIO Tech", "Technician", hash_password("x"),
        section_id=ppio_world["sections"]["PPIO"].id,
    )
    assert authz.is_ppio_planner(tech) is False
    assert authz.can_route_bookings(tech) is False


def test_an_INACTIVE_PPIO_supervisor_is_not_a_planner(ppio_world):
    planner = ppio_world["planner"]
    planner.is_active = False
    ppio_world["db"].commit()
    assert authz.is_ppio_planner(planner) is False
    assert authz.can_route_bookings(planner) is False


def test_a_FUTURE_planning_section_confers_planning_automatically(ppio_world, monkeypatch):
    """The capability follows the canonical planning set, so adding a code there is enough."""
    from tests.conftest import hash_password, make_section, make_user

    section = make_section(ppio_world["db"], 420, "PLANNING-2", "Planning Two")
    user = make_user(
        ppio_world["db"], 97, "PLAN2", "Planner Two", "Supervisor", hash_password("x"),
        section_id=section.id,
    )
    assert authz.is_ppio_planner(user) is False
    monkeypatch.setattr(authz, "PLANNING_SECTION_CODES", frozenset({"PPIO", "PLANNING-2"}))
    assert authz.is_ppio_planner(user) is True
    assert authz.can_route_bookings(user) is True


# --- through the real routes, with no grant -----------------------------------------------------


def test_PPIO_reaches_the_global_booking_pool_with_no_grant(routing_world, client):
    """The symptom that was reported: the pool was hidden because routing was denied."""
    assert client.get("/api/bookings", headers=routing_world["planner_headers"]).status_code == 200


def test_PPIO_can_route_with_no_grant(routing_world, client):
    response = _add(
        client, routing_world, routing_world["bookings"]["LOG_BOOK"],
        [routing_world["sections"]["M2-HR"].id],
    )
    assert response.status_code == 201, response.text[:300]


def test_PPIO_can_create_a_planning_booking_with_no_grant(creation_world, client):
    assert _create(client, creation_world).status_code == 201


# --- PPIO has NO section work queue -------------------------------------------------------------


def test_PPIO_is_DENIED_its_own_section_dashboard(routing_world, client):
    """The second reported symptom: a "PPIO Bookings" work queue was being offered for a section
    nothing is ever routed to. Denied at the API, not merely hidden in the sidebar - a planner
    typing /section-dashboard reaches these same routes."""
    response = client.get(
        "/api/sections/PPIO/assignments", headers=routing_world["planner_headers"]
    )
    assert response.status_code == 403
    assert "planning section" in response.text.lower()


def test_PPIO_is_denied_ANOTHER_sections_dashboard_too(routing_world, client):
    """Refused because of the KIND of section it belongs to, not which one it named."""
    response = client.get(
        "/api/sections/M2-HR/assignments", headers=routing_world["planner_headers"]
    )
    assert response.status_code == 403


def test_a_maintenance_supervisor_still_gets_its_OWN_section_dashboard(routing_world, client):
    """The control. Narrowing this for planning must not touch anybody else."""
    response = client.get(
        "/api/sections/M2-HR/assignments", headers=routing_world["maintenance_headers"]
    )
    assert response.status_code == 200, response.text[:200]


def test_an_admin_still_reaches_any_section_dashboard(routing_world, client):
    for code in ("M2-HR", "M6-HR"):
        response = client.get(
            f"/api/sections/{code}/assignments", headers=routing_world["admin_headers"]
        )
        assert response.status_code == 200, f"{code}: {response.text[:160]}"


# --- /auth/me is the contract the frontend reflects ---------------------------------------------


def test_auth_me_reports_the_resolved_PPIO_capabilities(routing_world, client):
    body = client.get("/api/auth/me", headers=routing_world["planner_headers"]).json()
    assert body["role"] == "Supervisor"
    assert body["section"]["code"] == "PPIO"
    assert body["permissions"]["can_route_bookings"] is True
    assert body["capabilities"]["is_planning_section"] is True
    assert body["capabilities"]["can_manage_loco_movement"] is False
    assert body["capabilities"]["can_admin"] is False
    assert body["capabilities"]["can_access_all_sections"] is False


def test_auth_me_for_a_maintenance_supervisor_is_unchanged(routing_world, client):
    body = client.get("/api/auth/me", headers=routing_world["maintenance_headers"]).json()
    assert body["permissions"]["can_route_bookings"] is False
    assert body["capabilities"]["is_planning_section"] is False
    assert body["capabilities"]["can_manage_loco_movement"] is False


def test_PPIO_still_has_no_movement_and_no_lifecycle_with_the_new_capability(routing_world, client):
    """Recognising PPIO as a planner must not have widened anything else."""
    get_settings.cache_clear()
    assert authz.can_manage_loco_movement(routing_world["planner"]) is False
    for label, method, path in MOVEMENT_ROUTES:
        response = getattr(client, method)(
            path, json={}, headers=routing_world["planner_headers"]
        )
        assert response.status_code == 403, f"{label} -> {response.status_code}"
