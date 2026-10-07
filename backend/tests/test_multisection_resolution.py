"""Multi-section booking resolution: parent status, Shed Out blocker display, pending counts.

These tests exist because the audit proved a specific defect: booking_routing_service.add_sections
inserted a new OPEN assignment row without re-deriving the parent Booking.status, so a booking
whose only section was ATTENDED kept reporting ATTENDED while carrying unresolved work.

HOW STATUS IS PRODUCED HERE
---------------------------
Lifecycle statuses are created through the real transition functions
(section_dashboard_service.start_assignment / attend_assignment) or through recompute_booking_status
- never by assigning Booking.status by hand. A hand-set parent status would make these tests pass
against a broken aggregate, which is exactly the failure mode under test. The one deliberate
exception is test_stale_parent_status_is_detectable_by_the_audit_query, which fabricates a stale
row precisely because detecting stale rows is its subject.
"""

from app.db import models
from app.domain.booking_resolution import (
    aggregate_booking_status,
    is_booking_resolved,
    unresolved_display_status,
)
from app.services import booking_routing_service, section_dashboard_service, shed_out_service
from app.services.section_dashboard_service import recompute_booking_status
from tests.conftest import (
    auth_header,
    grant_access,
    hash_password,
    make_assignment,
    make_booking,
    make_section,
    make_shed_visit,
    make_user,
)

M4_CODE = "M4-HR"
MACHINE_CODE = "MACHINE SHOP"


# ------------------------------------------------------------------- the audited world ---------


def _world(db):
    """The audit's own example: a CBC Operating Handle booking routed to M4-HR, plus a planner and
    a MACHINE SHOP supervisor so both sides of the addition can be exercised."""
    ppio = make_section(db, 190, "PPIO", "Production Planning")
    m4 = make_section(db, 191, M4_CODE, "M4 Heavy Repair")
    machine = make_section(db, 192, MACHINE_CODE, "Machine Shop")

    pw = hash_password("x")
    planner = make_user(db, 190, "PPIOMS", "Planner", "Supervisor", pw, section_id=ppio.id)
    m4_sup = make_user(db, 191, "M4SUP", "M4 Supervisor", "Supervisor", pw, section_id=m4.id)
    mc_sup = make_user(db, 192, "MCSUP", "Machine Supervisor", "Supervisor", pw, section_id=machine.id)
    grant_access(db, 191, is_enabled=True)
    grant_access(db, 192, is_enabled=True)
    db.commit()

    visit = make_shed_visit(db, 190, "39126", created_by=None)
    booking = make_booking(db, 1901, visit.id, description="CBC Operating Handle")
    assignment = make_assignment(db, 19001, booking.id, m4.id)
    return {
        "db": db,
        "visit": visit,
        "booking": booking,
        "m4": m4,
        "machine": machine,
        "m4_assignment": assignment,
        "planner": planner,
        "m4_sup": m4_sup,
        "mc_sup": mc_sup,
        "m4_headers": auth_header("M4SUP", "Supervisor", 191),
        "mc_headers": auth_header("MCSUP", "Supervisor", 192),
    }


def _attend(db, client_stub, assignment_id, user):
    """Drive a row to ATTENDED through the REAL transitions, OPEN -> IN_PROGRESS -> ATTENDED."""
    section_dashboard_service.start_assignment(db, client_stub, assignment_id, user)
    return section_dashboard_service.attend_assignment(db, client_stub, assignment_id, "done", user)


def _statuses(db, booking_id):
    return sorted(
        (a.section_id, a.status)
        for a in db.query(models.BookingSectionAssignment).filter(
            models.BookingSectionAssignment.booking_id == booking_id
        )
    )


# =============================================================================================
# 1-2. The audited defect: adding a section re-derives the parent status.
# =============================================================================================


def test_adding_a_section_to_an_attended_booking_reopens_the_parent_status(
    db_session, mock_loco_client
):
    """THE REGRESSION TEST. M4-HR attended, parent ATTENDED, planner adds MACHINE SHOP -> the
    booking is no longer resolved and must stop reporting ATTENDED."""
    w = _world(db_session)
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    assert db_session.get(models.Booking, w["booking"].id).status == "ATTENDED"

    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    db_session.expire_all()

    assert _statuses(db_session, w["booking"].id) == [
        (w["m4"].id, "ATTENDED"),
        (w["machine"].id, "OPEN"),
    ]
    assert db_session.get(models.Booking, w["booking"].id).status == "OPEN", (
        "parent status was not re-derived after the addition - the audited defect has returned"
    )


def test_adding_a_section_to_a_reopened_booking_keeps_reopened_precedence(
    db_session, mock_loco_client
):
    """REOPENED outranks a fresh OPEN row: the aggregate rule is `any REOPENED -> REOPENED`, so
    adding a section must not downgrade a booking that an Admin has reopened."""
    w = _world(db_session)
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    admin = make_user(db_session, 195, "ADMMS", "Admin", "Admin", hash_password("x"), section_id=None)
    db_session.commit()
    section_dashboard_service.reopen_assignment(
        db_session, mock_loco_client, w["m4_assignment"].id, "rework", admin
    )
    assert db_session.get(models.Booking, w["booking"].id).status == "REOPENED"

    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    db_session.expire_all()
    assert db_session.get(models.Booking, w["booking"].id).status == "REOPENED"


# =============================================================================================
# 3-4. Additive-only guarantees survive the status fix.
# =============================================================================================


def test_a_noop_addition_creates_no_duplicate_row_and_does_not_change_status(
    db_session, mock_loco_client
):
    w = _world(db_session)
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    before = db_session.get(models.Booking, w["booking"].id).status

    outcome = booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["m4"].id], w["planner"]
    )
    db_session.expire_all()

    assert outcome.added_section_ids == []
    assert outcome.already_assigned_section_ids == [w["m4"].id]
    assert _statuses(db_session, w["booking"].id) == [(w["m4"].id, "ATTENDED")]
    assert db_session.get(models.Booking, w["booking"].id).status == before == "ATTENDED"


def test_the_original_assignment_is_untouched_by_the_addition(db_session, mock_loco_client):
    w = _world(db_session)
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    original = db_session.get(models.BookingSectionAssignment, w["m4_assignment"].id)
    snapshot = (
        original.assignment_source,
        original.status,
        original.assigned_at,
        original.started_at,
        original.attended_at,
        original.attended_by,
    )

    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    db_session.expire_all()
    after = db_session.get(models.BookingSectionAssignment, w["m4_assignment"].id)
    assert (
        after.assignment_source,
        after.status,
        after.assigned_at,
        after.started_at,
        after.attended_at,
        after.attended_by,
    ) == snapshot
    assert after.assignment_source == "AUTO_MAPPING", "an AUTO_MAPPING row was rewritten as MANUAL"


# =============================================================================================
# 5-8. Section Dashboard visibility is independent per section.
# =============================================================================================


def test_both_sections_see_the_booking_after_the_addition(db_session, mock_loco_client):
    w = _world(db_session)
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )

    m4_rows = section_dashboard_service.list_section_assignments(
        db_session, mock_loco_client, M4_CODE, w["m4_sup"]
    )
    mc_rows = section_dashboard_service.list_section_assignments(
        db_session, mock_loco_client, MACHINE_CODE, w["mc_sup"]
    )
    assert [r.booking_id for r in m4_rows] == [w["booking"].id]
    assert [r.booking_id for r in mc_rows] == [w["booking"].id]
    assert mc_rows[0].status == "OPEN"
    assert mc_rows[0].section_code == MACHINE_CODE


def test_m4_attending_does_not_hide_the_machine_shop_row(db_session, mock_loco_client):
    w = _world(db_session)
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])

    mc_rows = section_dashboard_service.list_section_assignments(
        db_session, mock_loco_client, MACHINE_CODE, w["mc_sup"]
    )
    assert [(r.booking_id, r.status) for r in mc_rows] == [(w["booking"].id, "OPEN")]


def test_machine_shop_attending_does_not_hide_the_m4_row(db_session, mock_loco_client):
    w = _world(db_session)
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    mc_assignment = (
        db_session.query(models.BookingSectionAssignment)
        .filter(
            models.BookingSectionAssignment.booking_id == w["booking"].id,
            models.BookingSectionAssignment.section_id == w["machine"].id,
        )
        .one()
    )
    _attend(db_session, mock_loco_client, mc_assignment.id, w["mc_sup"])

    m4_rows = section_dashboard_service.list_section_assignments(
        db_session, mock_loco_client, M4_CODE, w["m4_sup"]
    )
    assert [(r.booking_id, r.status) for r in m4_rows] == [(w["booking"].id, "OPEN")]


# =============================================================================================
# 9. Resolved only when every section is resolved.
# =============================================================================================


def test_the_booking_becomes_attended_only_when_every_assignment_is_attended(
    db_session, mock_loco_client
):
    w = _world(db_session)
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    mc_assignment = (
        db_session.query(models.BookingSectionAssignment)
        .filter(
            models.BookingSectionAssignment.booking_id == w["booking"].id,
            models.BookingSectionAssignment.section_id == w["machine"].id,
        )
        .one()
    )

    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    assert db_session.get(models.Booking, w["booking"].id).status == "OPEN", (
        "one section attending must not resolve a two-section booking"
    )

    _attend(db_session, mock_loco_client, mc_assignment.id, w["mc_sup"])
    assert db_session.get(models.Booking, w["booking"].id).status == "ATTENDED"


# =============================================================================================
# 10-11. Shed Out: the gate blocks, and the message it shows is not self-contradictory.
# =============================================================================================


def test_shed_out_blocks_and_reports_open_not_a_stale_attended(db_session, mock_loco_client):
    """The audited contradiction: the gate blocked correctly while the blocker list printed
    ATTENDED next to the booking that was blocking it."""
    w = _world(db_session)
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    db_session.expire_all()

    eligibility = shed_out_service.evaluate_shed_out_eligibility(db_session, w["visit"])
    blockers = {b.booking_id: b.status for b in eligibility.booking_blockers}
    assert w["booking"].id in blockers, "Shed Out stopped blocking on an unresolved added section"
    assert blockers[w["booking"].id] == "OPEN"
    assert blockers[w["booking"].id] != "ATTENDED"


def test_a_fully_attended_multi_section_booking_is_not_a_shed_out_blocker(
    db_session, mock_loco_client
):
    w = _world(db_session)
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    mc_assignment = (
        db_session.query(models.BookingSectionAssignment)
        .filter(
            models.BookingSectionAssignment.booking_id == w["booking"].id,
            models.BookingSectionAssignment.section_id == w["machine"].id,
        )
        .one()
    )
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    _attend(db_session, mock_loco_client, mc_assignment.id, w["mc_sup"])

    eligibility = shed_out_service.evaluate_shed_out_eligibility(db_session, w["visit"])
    assert [b.booking_id for b in eligibility.booking_blockers] == []


def test_a_booking_with_zero_assignments_still_reports_no_assignments(db_session, mock_loco_client):
    """The routing-gap case must keep its own distinct blocker status rather than collapsing into
    OPEN - it needs a different fix from "a section has not finished yet"."""
    w = _world(db_session)
    db_session.delete(db_session.get(models.BookingSectionAssignment, w["m4_assignment"].id))
    db_session.commit()

    eligibility = shed_out_service.evaluate_shed_out_eligibility(db_session, w["visit"])
    assert [(b.booking_id, b.status) for b in eligibility.booking_blockers] == [
        (w["booking"].id, "NO_ASSIGNMENTS")
    ]


# =============================================================================================
# 12-13. The shed-visit pending count.
# =============================================================================================


def _pending(db, visit_id):
    from app.services import shed_visit_service

    rows = shed_visit_service.list_current_visits(db)
    return next(r.pending_booking_count for r in rows if r.id == visit_id)


def test_pending_count_includes_a_booking_whose_added_section_is_open(
    db_session, mock_loco_client
):
    w = _world(db_session)
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    assert _pending(db_session, w["visit"].id) == 0

    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    db_session.expire_all()
    assert _pending(db_session, w["visit"].id) == 1, (
        "the pending count missed a booking with an unresolved added section"
    )


def test_pending_count_excludes_a_fully_attended_multi_section_booking(
    db_session, mock_loco_client
):
    w = _world(db_session)
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id], w["planner"]
    )
    mc_assignment = (
        db_session.query(models.BookingSectionAssignment)
        .filter(
            models.BookingSectionAssignment.booking_id == w["booking"].id,
            models.BookingSectionAssignment.section_id == w["machine"].id,
        )
        .one()
    )
    _attend(db_session, mock_loco_client, w["m4_assignment"].id, w["m4_sup"])
    _attend(db_session, mock_loco_client, mc_assignment.id, w["mc_sup"])
    db_session.expire_all()

    assert _pending(db_session, w["visit"].id) == 0


def test_pending_count_does_not_double_count_a_multi_section_booking(
    db_session, mock_loco_client
):
    """One booking with three unresolved rows is ONE pending booking. The count joins assignments,
    so without COUNT(DISTINCT) this would report 3."""
    w = _world(db_session)
    third = make_section(db_session, 193, "M6-HR", "M6 Heavy Repair")
    booking_routing_service.add_sections(
        db_session, w["booking"].id, [w["machine"].id, third.id], w["planner"]
    )
    db_session.expire_all()
    assert len(_statuses(db_session, w["booking"].id)) == 3
    assert _pending(db_session, w["visit"].id) == 1


def test_pending_count_includes_a_booking_with_zero_assignments(db_session, mock_loco_client):
    w = _world(db_session)
    db_session.delete(db_session.get(models.BookingSectionAssignment, w["m4_assignment"].id))
    db_session.commit()
    assert _pending(db_session, w["visit"].id) == 1


# =============================================================================================
# The shared formula itself. Pure, so tested without a database.
# =============================================================================================


def test_aggregate_formula_matches_the_documented_rule():
    assert aggregate_booking_status([]) == "OPEN"
    assert aggregate_booking_status(["OPEN"]) == "OPEN"
    assert aggregate_booking_status(["ATTENDED"]) == "ATTENDED"
    assert aggregate_booking_status(["ATTENDED", "ATTENDED"]) == "ATTENDED"
    assert aggregate_booking_status(["ATTENDED", "OPEN"]) == "OPEN"
    assert aggregate_booking_status(["ATTENDED", "IN_PROGRESS"]) == "IN_PROGRESS"
    assert aggregate_booking_status(["ATTENDED", "REOPENED"]) == "REOPENED"
    assert aggregate_booking_status(["IN_PROGRESS", "REOPENED"]) == "REOPENED"


def test_zero_assignments_is_never_resolved():
    assert is_booking_resolved([]) is False
    assert unresolved_display_status([]) == "OPEN"


def test_unresolved_display_status_is_none_exactly_when_resolved():
    assert unresolved_display_status(["ATTENDED"]) is None
    assert unresolved_display_status(["ATTENDED", "ATTENDED"]) is None
    assert unresolved_display_status(["ATTENDED", "OPEN"]) == "OPEN"
    assert unresolved_display_status(["ATTENDED", "IN_PROGRESS"]) == "IN_PROGRESS"
    assert unresolved_display_status(["OPEN", "IN_PROGRESS"]) == "IN_PROGRESS"
    assert unresolved_display_status(["OPEN", "IN_PROGRESS", "REOPENED"]) == "REOPENED"


def test_the_display_status_can_never_contradict_the_stored_aggregate():
    """Both come from the same precedence, so an unresolved booking's displayed status is always
    its aggregate - which is what makes the Shed Out blocker trustworthy."""
    for statuses in (
        ["OPEN"],
        ["IN_PROGRESS"],
        ["REOPENED"],
        ["ATTENDED", "OPEN"],
        ["ATTENDED", "IN_PROGRESS"],
        ["ATTENDED", "REOPENED"],
        ["OPEN", "IN_PROGRESS", "REOPENED"],
    ):
        assert unresolved_display_status(statuses) == aggregate_booking_status(statuses)
