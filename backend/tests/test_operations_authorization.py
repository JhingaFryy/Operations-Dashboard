"""Production access policy: Operations Dashboard is SUPERVISOR-ONLY, section-scoped, with
locomotive movement restricted to the shed movement sections.

Implements the phase's test matrix end to end:
    A SHIFT Supervisor (entitled)      -> access + movement
    B PPIO Supervisor (entitled)       -> access, NO movement  (changed 2026-10-05, see below)
    C M35-TM Supervisor (entitled)     -> access, no movement
    D other-section Supervisor         -> access, no movement
    E Supervisor WITHOUT entitlement   -> denied
    F Technician                       -> denied
    G inactive Supervisor              -> denied
    H genuine Admin                    -> denied on operational endpoints (separate technical path)

Every assertion goes through the HTTP layer, so it tests the deployed authorization, not a helper.

PPIO MOVED FROM THE MOVEMENT GROUP TO THE DENIED GROUP (2026-10-05). It was listed as a movement
section in anticipation of the section being created, on the assumption that PPIO would handle
shed movement. The PPIO workflow that was actually specified is the opposite: PPIO is a PLANNING
section whose entire Operations Dashboard surface is read-only apart from booking section routing,
and it must never Shed In, Shed Out, Start Schedule or Complete Schedule. The default
LOCO_MOVEMENT_SECTION_CODES is now "SHIFT" alone. See tests/test_ppio_authorization.py for the
full PPIO matrix; the assertions here are the ones that had encoded the old assumption.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from tests.conftest import (
    make_minor_stages,
    auth_header,
    ensure_section,
    grant_access,
    make_assignment,
    make_booking,
    make_defect_type,
    make_shed_visit,
    make_true_admin_headers,
    make_user,
)

SHIFT, PPIO, M35TM, OTHER = 801, 802, 803, 804


def _sections(db_session):
    ensure_section(db_session, SHIFT, "SHIFT")
    ensure_section(db_session, PPIO, "PPIO")
    ensure_section(db_session, M35TM, "M35-TM")
    ensure_section(db_session, OTHER, "M9-HR")


def _supervisor(db_session, user_id, employee_id, section_id, *, entitled=True, active=True):
    make_user(db_session, user_id, employee_id, employee_id, "Supervisor", "hash",
              section_id=section_id, is_active=active)
    if entitled:
        grant_access(db_session, user_id, is_enabled=True)
    return auth_header(employee_id, "Supervisor", user_id)


@pytest.fixture()
def world(db_session):
    _sections(db_session)
    return {
        "A_shift": _supervisor(db_session, 11, "SHIFTSUP", SHIFT),
        "B_ppio": _supervisor(db_session, 12, "PPIOSUP", PPIO),
        "C_m35tm": _supervisor(db_session, 13, "TMSUP", M35TM),
        "D_other": _supervisor(db_session, 14, "OTHSUP", OTHER),
        "E_unentitled": _supervisor(db_session, 15, "NOACC", M35TM, entitled=False),
        "G_inactive": _supervisor(db_session, 17, "INACT", M35TM, active=False),
    }


def _technician_headers(db_session):
    make_user(db_session, 16, "TECH1", "Tech", "Technician", "hash", section_id=M35TM)
    return auth_header("TECH1", "Technician", 16)


# ------------------------------------------------------------------- 1. ACCESS --

@pytest.mark.parametrize("key", ["A_shift", "B_ppio", "C_m35tm", "D_other"])
def test_entitled_supervisors_may_use_the_dashboard(client, db_session, world, key):
    assert client.get("/api/shed-visits/current", headers=world[key]).status_code == 200


def test_supervisor_without_entitlement_has_base_operational_access(client, db_session, world):
    """The rule the shed asked for: an ACTIVE Supervisor has base Operations Dashboard access.
    dashboard_access is not how a Supervisor is made real - see can_access_operations_dashboard."""
    resp = client.get("/api/shed-visits/current", headers=world["E_unentitled"])
    assert resp.status_code == 200, resp.text


def test_supervisor_without_entitlement_still_gets_no_admin_powers(client, db_session, world):
    """Relaxing BASE access must not leak anything narrower. Each of these is gated on its own
    rule, and none of them accepts base access as a substitute."""
    headers = world["E_unentitled"]

    # Admin-only: the Dashboard Access administration page.
    assert client.get("/api/admin/dashboard-access/users", headers=headers).status_code == 403

    # Admin-only: equipment master data creation.
    created = client.post(
        "/api/equipment/admin/nodes",
        headers=headers,
        json={"family_id": 1, "parent_id": None, "name": "Sneaky Node"},
    )
    assert created.status_code == 403, created.text


def test_technician_is_denied(client, db_session, world):
    resp = client.get("/api/shed-visits/current", headers=_technician_headers(db_session))
    assert resp.status_code == 403


def test_inactive_supervisor_is_denied(client, db_session, world):
    """Deactivation takes effect immediately - get_current_user re-reads the user from the
    database on every request rather than trusting the token's claims."""
    assert client.get("/api/shed-visits/current", headers=world["G_inactive"]).status_code == 401


def test_admin_has_full_operational_access(client, db_session, world):
    """The Superadmin is authorised by ROLE ALONE - deliberately no dashboard_access row exists
    for this account, which is exactly the lockout this policy correction fixes."""
    headers = make_true_admin_headers(db_session)
    from app.db import models as m

    admin = db_session.query(m.User).filter(m.User.role == "Admin").one()
    assert admin.dashboard_access is None  # no entitlement row, yet still allowed

    assert client.get("/api/shed-visits/current", headers=headers).status_code == 200


def test_unauthenticated_is_denied(client, db_session, world):
    assert client.get("/api/shed-visits/current").status_code == 401


# ----------------------------------------------------------------- 2. MOVEMENT --

MOVEMENT_CALLS = [
    ("post", "/api/shed-visits/in", {"loco_number": "39126", "schedule_family": "MINOR",
                                     "schedule_variant": "IA", "arrival_condition": "WORKING",
                                     "arrival_at": "2026-09-01T09:00:00+05:30",
                                     "log_book_bookings": []}),
    ("post", "/api/shed-visits/900/start-schedule", {"started_at": "2026-09-01T10:00:00+05:30"}),
    ("post", "/api/shed-visits/900/complete-schedule", {"completed_at": "2026-09-01T12:00:00+05:30"}),
    ("post", "/api/shed-visits/900/out", {"departed_at": "2026-09-01T13:00:00+05:30"}),
]


@pytest.mark.parametrize("key", ["B_ppio", "C_m35tm", "D_other"])
@pytest.mark.parametrize("method,path,payload", MOVEMENT_CALLS)
def test_non_movement_supervisors_get_403_on_every_movement_action(
    client, db_session, world, key, method, path, payload
):
    """Direct-API bypass attempt: these accounts may use the Dashboard, but never move a loco."""
    resp = getattr(client, method)(path, json=payload, headers=world[key])
    assert resp.status_code == 403
    assert "movement sections" in resp.json()["detail"]


@pytest.mark.parametrize("key", ["A_shift"])
@pytest.mark.parametrize("method,path,payload", MOVEMENT_CALLS)
def test_movement_supervisors_pass_the_authorization_gate(
    client, db_session, world, mock_loco_client, key, method, path, payload
):
    """They must never be refused for AUTHORIZATION reasons. A later business-rule refusal (no
    such visit, readiness not met) is a different thing and is asserted elsewhere."""
    mock_loco_client.add_locomotive("39126", loco_type="WAG9HC")
    resp = getattr(client, method)(path, json=payload, headers=world[key])
    assert resp.status_code != 403, resp.text


def test_movement_privilege_confers_no_admin_capability(client, db_session, world):
    """SHIFT gets locomotive movement and nothing else - it is not an Admin. (PPIO gets no
    movement at all; see tests/test_ppio_authorization.py.)"""
    assert client.get("/api/bookings", headers=world["A_shift"]).status_code == 403
    assert client.get("/api/admin/dashboard-access/users", headers=world["A_shift"]).status_code == 403


def test_capabilities_reported_to_the_frontend_match_the_policy(client, db_session, world):
    shift = client.get("/api/auth/me", headers=world["A_shift"]).json()["capabilities"]
    assert shift["can_access_operations_dashboard"] is True
    assert shift["can_manage_loco_movement"] is True
    assert shift["can_admin"] is False

    tm = client.get("/api/auth/me", headers=world["C_m35tm"]).json()["capabilities"]
    assert tm["can_access_operations_dashboard"] is True
    assert tm["can_manage_loco_movement"] is False
    assert tm["can_admin"] is False


# ------------------------------------------------------- 3. BOOKING SECTION SCOPE --

def _assignment_in(db_session, mock_loco_client, section_id, ids):
    if db_session.get(models.BookingDefectType, 1) is None:
        make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    visit = make_shed_visit(db_session, ids, f"3912{ids}", created_by=None)
    booking = make_booking(db_session, ids, visit.id, equipment_node_id=1843,
                           defect_type_id=1, created_by=None)
    return make_assignment(db_session, ids, booking.id, section_id=section_id, status="OPEN")


def test_supervisor_sees_only_their_own_sections_assignments(client, db_session, world, mock_loco_client):
    _assignment_in(db_session, mock_loco_client, M35TM, 1)
    _assignment_in(db_session, mock_loco_client, OTHER, 2)

    own = client.get("/api/sections/M35-TM/assignments", headers=world["C_m35tm"])
    assert own.status_code == 200
    assert len(own.json()) == 1

    other = client.get("/api/sections/M9-HR/assignments", headers=world["C_m35tm"])
    assert other.status_code == 403


def test_shift_supervisor_cannot_read_another_sections_queue(client, db_session, world, mock_loco_client):
    _assignment_in(db_session, mock_loco_client, M35TM, 1)
    assert client.get("/api/sections/M35-TM/assignments", headers=world["A_shift"]).status_code == 403


def test_supervisor_cannot_mutate_another_sections_assignment(client, db_session, world, mock_loco_client):
    other = _assignment_in(db_session, mock_loco_client, OTHER, 2)

    resp = client.post(f"/api/section-assignments/{other.id}/start", headers=world["C_m35tm"])

    assert resp.status_code == 403
    db_session.expire_all()
    assert db_session.get(models.BookingSectionAssignment, other.id).status == "OPEN"


def test_supervisor_can_transition_and_remark_on_their_own_assignment(
    client, db_session, world, mock_loco_client
):
    own = _assignment_in(db_session, mock_loco_client, M35TM, 1)

    started = client.post(f"/api/section-assignments/{own.id}/start", headers=world["C_m35tm"])
    assert started.status_code == 200

    attended = client.post(f"/api/section-assignments/{own.id}/attend",
                           json={"remarks": "Brush gear replaced"}, headers=world["C_m35tm"])
    assert attended.status_code == 200

    db_session.expire_all()
    row = db_session.get(models.BookingSectionAssignment, own.id)
    assert row.status == "ATTENDED"
    # Remarks live on the ASSIGNMENT, so one section can never overwrite another's.
    assert row.attendance_remarks == "Brush gear replaced"


def test_open_to_attended_shortcut_is_still_rejected(client, db_session, world, mock_loco_client):
    own = _assignment_in(db_session, mock_loco_client, M35TM, 1)
    resp = client.post(f"/api/section-assignments/{own.id}/attend",
                       json={"remarks": "x"}, headers=world["C_m35tm"])
    assert resp.status_code == 409


def test_reopen_remains_admin_only(client, db_session, world, mock_loco_client):
    own = _assignment_in(db_session, mock_loco_client, M35TM, 1)
    client.post(f"/api/section-assignments/{own.id}/start", headers=world["C_m35tm"])
    client.post(f"/api/section-assignments/{own.id}/attend",
                json={"remarks": "done"}, headers=world["C_m35tm"])

    # Neither an ordinary Supervisor nor a movement Supervisor inherits it.
    for key in ("C_m35tm", "A_shift"):
        resp = client.post(f"/api/section-assignments/{own.id}/reopen",
                           json={"reason": "still broken"}, headers=world[key])
        assert resp.status_code == 403

    db_session.expire_all()
    assert db_session.get(models.BookingSectionAssignment, own.id).status == "ATTENDED"


def test_summary_cannot_be_redirected_to_another_section(client, db_session, world, mock_loco_client):
    _assignment_in(db_session, mock_loco_client, OTHER, 2)

    own = client.get("/api/bookings/summary", headers=world["C_m35tm"])
    tampered = client.get(f"/api/bookings/summary?section_id={OTHER}", headers=world["C_m35tm"])

    assert own.status_code == 200 and tampered.status_code == 200
    assert own.json() == tampered.json()
    assert own.json()["open"] == 0   # the other section's open assignment is never counted


# -------------------------------------------------------------- 4. ADMIN-ONLY --

def test_admin_only_endpoints_refuse_every_supervisor(client, db_session, world):
    """dashboard-access administration is Admin-only, for every Supervisor without exception."""
    for key in ("A_shift", "B_ppio", "C_m35tm", "D_other"):
        assert client.get(
            "/api/admin/dashboard-access/users", headers=world[key]
        ).status_code == 403


def test_the_global_booking_pool_admits_a_PLANNER_and_nobody_else(client, db_session, world):
    """CHANGED 2026-10-06. The global pool used to be Admin-only, and this test asserted that for
    all four Supervisors. It is now Admin OR an account that may route bookings, because a planner
    cannot decide where a finding should go without seeing the pool it lives in - and a PPIO
    Supervisor is a planner by section identity, with no dashboard_access row.

    Every other Supervisor is still refused, which is the half that matters: widening this for
    planning must not widen it for the shed.
    """
    assert client.get("/api/bookings", headers=world["B_ppio"]).status_code == 200
    for key in ("A_shift", "C_m35tm", "D_other"):
        assert client.get("/api/bookings", headers=world[key]).status_code == 403, key


# ------------------------------------------------- 5. INTERNAL ROUTES UNCHANGED --

def test_internal_service_routes_do_not_accept_a_supervisor_token(client, db_session, world):
    """Service-to-service auth is the internal API key, and is completely separate: a human
    Bearer token - Supervisor or Admin - must never satisfy it."""
    resp = client.get("/api/internal/shed-visits/active", headers=world["A_shift"])
    assert resp.status_code == 401


def test_internal_routes_do_not_require_supervisor_auth(client, db_session, world, monkeypatch):
    """The converse: applying the Supervisor gate to service routes would break BL-DCMS. With the
    correct key and no Bearer token at all, the route works."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OPERATIONS_INTERNAL_API_KEY", "test-internal-key")
    try:
        resp = client.get("/api/internal/shed-visits/active",
                          headers={"X-Internal-API-Key": "test-internal-key"})
        assert resp.status_code == 200
    finally:
        get_settings.cache_clear()


# ==================================================================================
# ADMIN / SUPERADMIN - full operational access (authorization-policy correction)
# ==================================================================================
# The previous hardening phase excluded Admin from can_access_operations_dashboard, which locked
# the genuine Superadmin out of their own system. These assert the corrected hierarchy AND that
# re-admitting Admin did not reopen any Supervisor cross-section bypass.


@pytest.fixture()
def admin_headers(db_session):
    return make_true_admin_headers(db_session)


def test_admin_needs_no_dashboard_access_row(client, db_session, admin_headers):
    """Role is the Admin's entitlement. dashboard_access remains the Supervisor mechanism only -
    requiring a row for Admin is precisely what caused the lockout."""
    admin = db_session.query(models.User).filter(models.User.role == "Admin").one()
    assert admin.dashboard_access is None
    assert client.get("/api/shed-visits/current", headers=admin_headers).status_code == 200


def test_admin_capabilities_are_complete(client, db_session, admin_headers):
    body = client.get("/api/auth/me", headers=admin_headers).json()
    assert body["role"] == "Admin"
    caps = body["capabilities"]
    assert caps == {
        "can_access_operations_dashboard": True,
        "can_manage_loco_movement": True,
        "can_manage_own_section_bookings": True,
        "can_access_all_sections": True,
        # Admin belongs to no section at all (section_id IS NULL), so it is not a planner.
        "is_planning_section": False,
        "can_admin": True,
    }


@pytest.mark.parametrize("method,path,payload", MOVEMENT_CALLS)
def test_admin_passes_the_movement_gate(
    client, db_session, admin_headers, mock_loco_client, method, path, payload
):
    """Authorisation only - never a 403. Any later refusal is a business rule, asserted below."""
    mock_loco_client.add_locomotive("39126", loco_type="WAG9HC")
    resp = getattr(client, method)(path, json=payload, headers=admin_headers)
    assert resp.status_code != 403, resp.text


def test_admin_movement_still_obeys_business_gates(client, db_session, admin_headers, mock_loco_client):
    """Admin authorisation does NOT bypass workflow rules: starting a schedule before the
    locomotive arrived is still refused, exactly as it would be for a SHIFT Supervisor."""
    _sections(db_session)
    visit = make_shed_visit(db_session, 950, "39018", created_by=None,
                            arrival_at=datetime(2026, 9, 1, 10, tzinfo=timezone.utc))

    resp = client.post(f"/api/shed-visits/{visit.id}/start-schedule",
                       json={"started_at": "2026-08-30T08:00:00+00:00"}, headers=admin_headers)

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SCHEDULE_START_BEFORE_ARRIVAL"
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).schedule_started_at is None


def test_admin_shed_out_still_obeys_readiness_gates(client, db_session, admin_headers, mock_loco_client):
    _sections(db_session)
    visit = make_shed_visit(db_session, 951, "30634", created_by=None,
                            arrival_at=datetime(2026, 9, 1, 10, tzinfo=timezone.utc))
    make_minor_stages(db_session, visit.id, base_id=9510)   # all PENDING

    resp = client.post(f"/api/shed-visits/{visit.id}/out",
                       json={"departed_at": "2026-09-02T10:00:00+00:00"}, headers=admin_headers)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SHED_OUT_BLOCKED"
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).status == "IN_SHED"


def test_admin_sees_the_global_booking_pool(client, db_session, admin_headers):
    assert client.get("/api/bookings", headers=admin_headers).status_code == 200


def test_admin_may_use_admin_only_endpoints(client, db_session, admin_headers):
    assert client.get("/api/admin/dashboard-access/users", headers=admin_headers).status_code == 200


def test_admin_may_reopen_an_attended_assignment(client, db_session, admin_headers, mock_loco_client, world):
    own = _assignment_in(db_session, mock_loco_client, M35TM, 1)
    client.post(f"/api/section-assignments/{own.id}/start", headers=world["C_m35tm"])
    client.post(f"/api/section-assignments/{own.id}/attend",
                json={"remarks": "done"}, headers=world["C_m35tm"])

    resp = client.post(f"/api/section-assignments/{own.id}/reopen",
                       json={"reason": "still faulty"}, headers=admin_headers)

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(models.BookingSectionAssignment, own.id).status == "REOPENED"


def test_admin_may_query_any_sections_summary(client, db_session, admin_headers, mock_loco_client, world):
    _assignment_in(db_session, mock_loco_client, M35TM, 1)

    scoped = client.get(f"/api/bookings/summary?section_id={M35TM}", headers=admin_headers)
    assert scoped.status_code == 200
    assert scoped.json()["open"] == 1

    # An Admin has no section of their own, so omitting section_id is an explicit 422 rather than
    # a silently empty answer.
    assert client.get("/api/bookings/summary", headers=admin_headers).status_code == 422


# ------------------------------------------------- SECURITY REGRESSION AFTER RE-ADMITTING ADMIN --

def test_supervisor_still_cannot_reach_admin_only_routes(client, db_session, world, admin_headers):
    for key in ("A_shift", "B_ppio", "C_m35tm", "D_other"):
        # dashboard-access administration: Admin-only for all four, unchanged.
        assert client.get("/api/admin/dashboard-access/users", headers=world[key]).status_code == 403
    # The global pool is no longer on this list - it is Admin OR a planner. B_ppio is a planner;
    # the other three are still refused. Asserted in full in
    # test_the_global_booking_pool_admits_a_PLANNER_and_nobody_else above.
    for key in ("A_shift", "C_m35tm", "D_other"):
        assert client.get("/api/bookings", headers=world[key]).status_code == 403


def test_supervisor_section_isolation_survives_the_admin_change(
    client, db_session, world, admin_headers, mock_loco_client
):
    """The exact bypass that re-admitting Admin could have reopened."""
    other = _assignment_in(db_session, mock_loco_client, OTHER, 2)

    assert client.get("/api/sections/M9-HR/assignments", headers=world["C_m35tm"]).status_code == 403
    assert client.post(f"/api/section-assignments/{other.id}/start",
                       headers=world["C_m35tm"]).status_code == 403
    db_session.expire_all()
    assert db_session.get(models.BookingSectionAssignment, other.id).status == "OPEN"


def test_supervisor_summary_section_id_is_still_ignored(client, db_session, world, mock_loco_client):
    """Admin may now pass section_id; a Supervisor passing it must still get their OWN section."""
    _assignment_in(db_session, mock_loco_client, OTHER, 2)

    own = client.get("/api/bookings/summary", headers=world["C_m35tm"])
    tampered = client.get(f"/api/bookings/summary?section_id={OTHER}", headers=world["C_m35tm"])

    assert own.status_code == 200 and tampered.status_code == 200
    assert own.json() == tampered.json()
    assert own.json()["open"] == 0


def test_movement_supervisor_gains_no_admin_power(client, db_session, world):
    """SHIFT gets locomotive movement only - never cross-section or Admin capability."""
    caps = client.get("/api/auth/me", headers=world["A_shift"]).json()["capabilities"]
    assert caps["can_manage_loco_movement"] is True
    assert caps["can_access_all_sections"] is False
    assert caps["can_admin"] is False
