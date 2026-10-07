"""Regression net for the `chk_booking_started` production failure.

POST /api/section-assignments/{id}/start failed in production with:

    new row for relation "bookings" violates check constraint "chk_booking_started"

because the live `bookings` table still carried three CHECK constraints from a
pre-assignment architecture (status <> 'IN_PROGRESS' OR started_at IS NOT NULL,
and the ATTENDED/REOPENED equivalents), while Booking.status became a DERIVED
aggregate over booking_section_assignments that deliberately writes only
`status`. migrations/003_decouple_booking_status_from_legacy_timestamps.sql
drops those three; app/db/models.py mirrors the post-003 constraint set.

*** IMPORTANT, READ BEFORE TRUSTING THESE TESTS ***
This suite runs against a disposable in-memory SQLite database built from
app/db/models' metadata (see tests/conftest.py), NOT against PostgreSQL. SQLite
does enforce CHECK constraints, so the constraints mirrored in models.py are
genuinely exercised here — but they are only ever as accurate as the mirror.
These tests therefore prove that the CODE never produces a row violating the
constraint set we believe the database has; they do NOT and cannot prove what
the live rdcms database actually has. Only applying migration 003 and
re-checking pg_constraint proves that. test_bookings_model_has_no_status_
timestamp_coupling below is the explicit statement of the contract the
migration must establish.
"""

from datetime import datetime, timezone

from app.db import models
from tests.conftest import (
    grant_access,
    make_true_admin_headers,
    ensure_section,
    make_movement_supervisor_headers,
    auth_header,
    make_assignment,
    make_booking,
    make_defect_type,
    make_section,
    make_shed_visit,
    make_user,
)


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)



def _section_owner_headers(db_session, section_id, user_id=50, employee_id="OWNSUP"):
    """Assignment mutations are section-scoped: only the owning section's Supervisor may act.
    Movement Supervisors get locomotive movement, never other sections' booking work."""
    ensure_section(db_session, section_id, f"M{section_id}-HR")
    existing = db_session.get(models.User, user_id)
    if existing is None:
        make_user(db_session, user_id, employee_id, "Owner Sup", "Supervisor", "hash",
                  section_id=section_id)
        grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)

def _setup(db_session, mock_loco_client, section_ids=(1,), statuses=("OPEN",)):
    for section_id in section_ids:
        ensure_section(db_session, section_id, f"M{section_id}-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, 1, "39126", created_by=None)
    booking = make_booking(
        db_session, 1, visit.id, equipment_node_id=1843, defect_type_id=1, created_by=None
    )
    assignments = [
        make_assignment(db_session, i + 1, booking.id, section_id=section_id, status=status)
        for i, (section_id, status) in enumerate(zip(section_ids, statuses))
    ]
    return visit, booking, assignments


def _constraint_names(model):
    from sqlalchemy import CheckConstraint

    return {c.name for c in model.__table__.constraints if isinstance(c, CheckConstraint)}


# ---------------------------------------------------------------- schema contract --


def test_bookings_model_has_no_status_timestamp_coupling():
    """The exact contract migration 003 establishes in PostgreSQL. These three
    constraint names must NOT exist on `bookings`: `status` is derived from the
    assignment set, and the parent-level timestamp columns are vestigial and
    written by nothing, so any coupling between them is unsatisfiable."""
    names = _constraint_names(models.Booking)
    assert "chk_booking_started" not in names
    assert "chk_booking_attended" not in names
    assert "chk_booking_reopened" not in names
    # the enum/shape constraints are retained and still meaningful
    assert "chk_booking_status" in names
    assert "chk_booking_source" in names


def test_assignment_model_owns_the_status_timestamp_coupling():
    """The integrity dropped from `bookings` lives here instead, where a single
    authoritative start/attend moment genuinely exists per row."""
    names = _constraint_names(models.BookingSectionAssignment)
    assert "chk_booking_section_started" in names
    assert "chk_booking_section_attended" in names
    assert "chk_booking_section_attended_requires_start" in names
    assert "chk_booking_section_reopened_requires_attend" in names
    # Rejected by design - false across a reopen cycle, see the model comment.
    assert "chk_booking_section_attend_after_start" not in names


# ------------------------------------------------------- the actual failing call --


def test_start_assignment_leaves_parent_timestamps_null(client, db_session, mock_loco_client):
    """The precise production failure. Starting an assignment moves the parent
    booking to IN_PROGRESS while bookings.started_at stays NULL - which is
    exactly the row chk_booking_started rejected."""
    headers = _section_owner_headers(db_session, 1)
    _, booking, assignments = _setup(db_session, mock_loco_client)

    resp = client.post(f"/api/section-assignments/{assignments[0].id}/start", headers=headers)

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    parent = db_session.get(models.Booking, booking.id)
    assert parent.status == "IN_PROGRESS"
    assert parent.started_at is None
    assert parent.started_by is None
    assert parent.started_by_section_id is None


def test_attend_and_reopen_leave_parent_timestamps_null(client, db_session, mock_loco_client):
    """The two latent siblings of the same defect: chk_booking_attended would
    have rejected the ATTENDED derivation and chk_booking_reopened the REOPENED
    one, on the very next call after start was fixed."""
    headers = _section_owner_headers(db_session, 1)
    _, booking, assignments = _setup(db_session, mock_loco_client, (1,), ("IN_PROGRESS",))

    attended = client.post(
        f"/api/section-assignments/{assignments[0].id}/attend",
        json={"remarks": "done"},
        headers=headers,
    )
    assert attended.status_code == 200, attended.text
    db_session.expire_all()
    parent = db_session.get(models.Booking, booking.id)
    assert parent.status == "ATTENDED"
    assert parent.attended_at is None
    assert parent.attended_by is None
    assert parent.attended_by_section_id is None

    reopened = client.post(
        f"/api/section-assignments/{assignments[0].id}/reopen",
        json={"reason": "not fixed"},
        headers=make_true_admin_headers(db_session),
    )
    assert reopened.status_code == 200, reopened.text
    db_session.expire_all()
    parent = db_session.get(models.Booking, booking.id)
    assert parent.status == "REOPENED"
    assert parent.reopened_at is None
    assert parent.reopened_by is None


def test_full_lifecycle_round_trip_multi_section(client, db_session, mock_loco_client):
    """OPEN -> IN_PROGRESS -> ATTENDED -> REOPENED -> IN_PROGRESS across two
    sections, asserting the derived parent status at every step. Also covers
    partial attendance: one ATTENDED + one IN_PROGRESS must leave the parent at
    IN_PROGRESS, never ATTENDED."""
    headers = _section_owner_headers(db_session, 1)
    _, booking, (a1, a2) = _setup(db_session, mock_loco_client, (1, 2), ("OPEN", "OPEN"))

    def parent_status():
        db_session.expire_all()
        return db_session.get(models.Booking, booking.id).status

    assert parent_status() == "OPEN"

    sec2 = _section_owner_headers(db_session, 2, user_id=51, employee_id="OWNSUP2")

    assert client.post(f"/api/section-assignments/{a1.id}/start", headers=headers).status_code == 200
    assert parent_status() == "IN_PROGRESS"

    assert client.post(f"/api/section-assignments/{a2.id}/start", headers=sec2).status_code == 200
    assert parent_status() == "IN_PROGRESS"

    r = client.post(
        f"/api/section-assignments/{a1.id}/attend", json={"remarks": "ok"}, headers=headers
    )
    assert r.status_code == 200, r.text
    # partial: one ATTENDED, one IN_PROGRESS -> parent must NOT be ATTENDED
    assert parent_status() == "IN_PROGRESS"

    r = client.post(
        f"/api/section-assignments/{a2.id}/attend", json={"remarks": "ok"}, headers=sec2
    )
    assert r.status_code == 200, r.text
    assert parent_status() == "ATTENDED"

    r = client.post(
        f"/api/section-assignments/{a1.id}/reopen", json={"reason": "recheck"}, headers=make_true_admin_headers(db_session)
    )
    assert r.status_code == 200, r.text
    assert parent_status() == "REOPENED"

    # restarting the reopened assignment writes a fresh started_at while keeping
    # the earlier attended_at as history - the case that disqualified a
    # candidate attended_at >= started_at constraint.
    r = client.post(f"/api/section-assignments/{a1.id}/start", headers=headers)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    restarted = db_session.get(models.BookingSectionAssignment, a1.id)
    assert restarted.status == "IN_PROGRESS"
    assert restarted.attended_at is not None
    assert restarted.attended_at < restarted.started_at
    assert parent_status() == "IN_PROGRESS"


def test_forbidden_transitions_still_rejected(client, db_session, mock_loco_client):
    headers = _section_owner_headers(db_session, 1)
    _, _, (a_open, a_reopened) = _setup(
        db_session, mock_loco_client, (1, 2), ("OPEN", "REOPENED")
    )

    direct_open = client.post(
        f"/api/section-assignments/{a_open.id}/attend", json={"remarks": "x"}, headers=headers
    )
    assert direct_open.status_code == 409

    direct_reopened = client.post(
        f"/api/section-assignments/{a_reopened.id}/attend",
        json={"remarks": "x"},
        headers=_section_owner_headers(db_session, 2, user_id=51, employee_id="OWNSUP2"),
    )
    assert direct_reopened.status_code == 409


# ------------------------------------------------------------ sequence-backed ids --


def test_autoincrement_ids_assigned_without_explicit_pk(db_session, mock_loco_client):
    """Documents the sequence assumption: every Operations-Dashboard-owned table
    relies on its serial/identity default for `id`, so the app never supplies
    one. Against PostgreSQL that is nextval() on <table>_id_seq and requires the
    application role to hold USAGE (or UPDATE) on it - the class of failure
    behind the previously seen 'permission denied for sequence
    shed_visits_id_seq'.

    This runs on SQLite (rowid autoincrement), so it proves only that no code
    path depends on assigning ids itself. It CANNOT verify the PostgreSQL
    sequence grants; that was verified separately with a read-only
    has_sequence_privilege() query against live rdcms (elsbladmin holds
    USAGE+SELECT on all ten Dashboard sequences)."""
    ensure_section(db_session, 1, "M1-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    now = datetime.now(timezone.utc)

    visit = models.ShedVisit(
        loco_number="39999",
        arrival_at=now,
        arrival_source="DASHBOARD",
        visit_type="SCHEDULED",
        status="IN_SHED",
        created_at=now,
        updated_at=now,
    )
    db_session.add(visit)
    db_session.flush()
    assert visit.id is not None

    booking = models.Booking(
        shed_visit_id=visit.id,
        booking_source="LOG_BOOK",
        description="seq check",
        equipment_node_id=1843,
        defect_type_id=1,
        status="OPEN",
        created_at=now,
        updated_at=now,
    )
    db_session.add(booking)
    db_session.flush()
    assert booking.id is not None

    assignment = models.BookingSectionAssignment(
        booking_id=booking.id,
        section_id=1,
        assignment_source="AUTO_MAPPING",
        status="OPEN",
        assigned_at=now,
        updated_at=now,
    )
    event = models.BookingEvent(
        booking_id=booking.id, event_type="CREATED", created_at=now
    )
    db_session.add_all([assignment, event])
    db_session.flush()
    assert assignment.id is not None
    assert event.id is not None
    db_session.rollback()
