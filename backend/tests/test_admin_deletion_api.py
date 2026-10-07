"""The deletion HTTP surface: authorization, re-authentication, and the preview/delete/history routes.

The emphasis is on what the endpoints REFUSE. Frontend hiding is not part of the defence, so every
non-Admin role is tested against every destructive route directly, the way a tampered client would
reach them.

These run on SQLite, so they prove the ROUTE behaviour - who may call, what is validated, what is
returned. What the database does under deletion (RESTRICT, CASCADE, triggers) is proven separately
against a production-faithful PostgreSQL schema in test_admin_deletion_scratch.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.rate_limit import REAUTH_MAX_ATTEMPTS, limiter
from app.db import models, shared_tables as bl
from tests.conftest import auth_header, hash_password

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clean_limiter():
    limiter.reset_for_tests()
    yield
    limiter.reset_for_tests()


@pytest.fixture()
def world(db_session):
    """One visit with a booking, plus Admin / Supervisor / Technician accounts.

    BL's tables are created here from the Core mirror. The OD test schema does not include them -
    each service models only its own - but a visit deletion spans both systems, so the mirror has to
    exist for these routes to run at all.
    """
    bl.bl_metadata.create_all(bind=db_session.get_bind())

    section = models.Section(id=1, name="M1-HR", code="M1-HR")
    db_session.add(section)
    admin = models.User(id=1, employee_id="ADM1", name="Admin One", mobile="9000000001",
                        role="Admin", is_active=True, password_hash=hash_password(PASSWORD))
    other_admin = models.User(id=2, employee_id="ADM2", name="Admin Two", mobile="9000000002",
                              role="Admin", is_active=True,
                              password_hash=hash_password("a-different-password"))
    supervisor = models.User(id=3, employee_id="SUP1", name="Supervisor", mobile="9000000003",
                             role="Supervisor", is_active=True, section_id=1,
                             password_hash=hash_password(PASSWORD))
    technician = models.User(id=4, employee_id="TEC1", name="Technician", mobile="9000000011",
                             role="Technician", is_active=True, section_id=1,
                             password_hash=hash_password(PASSWORD))
    db_session.add_all([admin, other_admin, supervisor, technician])

    now = datetime.now(timezone.utc)
    visit = models.ShedVisit(id=700, loco_number="32032", arrival_at=now, schedule_family="MINOR",
                             schedule_variant="IA", status="IN_SHED", created_at=now, updated_at=now)
    db_session.add(visit)
    db_session.commit()

    booking = models.Booking(id=900, shed_visit_id=700, booking_source="LOG_BOOK",
                             description="Pantograph horn fault", status="OPEN",
                             created_at=now, updated_at=now)
    db_session.add(booking)
    db_session.commit()
    db_session.add(models.BookingSectionAssignment(
        booking_id=900, section_id=1, status="OPEN", assignment_source="AUTO_MAPPING",
        assigned_at=now, updated_at=now))
    db_session.commit()

    return {"db": db_session, "visit": visit, "booking": booking}


def admin_headers():
    return auth_header("ADM1", "Admin", 1)


def supervisor_headers():
    return auth_header("SUP1", "Supervisor", 3)


def technician_headers():
    return auth_header("TEC1", "Technician", 4)


# ======================================================================= authorization ==========


@pytest.mark.parametrize("headers_fn", [supervisor_headers, technician_headers])
@pytest.mark.parametrize("method, path", [
    ("get", "/api/admin/shed-visits/700/deletion-preview"),
    ("get", "/api/admin/bookings/900/deletion-preview"),
    ("delete", "/api/admin/shed-visits/700"),
    ("delete", "/api/admin/bookings/900"),
    ("get", "/api/admin/deletion-history"),
])
def test_non_admins_are_refused_on_every_route(client, world, headers_fn, method, path):
    """Server-side, before any handler code runs. Hiding the button is not the defence."""
    # client.request, not client.delete: TestClient's verb shortcuts take no `json` body.
    kwargs = {"headers": headers_fn()}
    if method == "delete":
        kwargs["json"] = {"password": PASSWORD, "reason": "attempting a deletion as a non-admin",
                          "confirmation": "DELETE 32032 IA"}
    assert client.request(method.upper(), path, **kwargs).status_code == 403


@pytest.mark.parametrize("method, path", [
    ("get", "/api/admin/shed-visits/700/deletion-preview"),
    ("delete", "/api/admin/shed-visits/700"),
    ("get", "/api/admin/deletion-history"),
])
def test_an_unauthenticated_caller_is_refused(client, world, method, path):
    kwargs = {}
    if method == "delete":
        kwargs["json"] = {"password": "x", "reason": "x" * 10, "confirmation": "y"}
    response = client.request(method.upper(), path, **kwargs)
    assert response.status_code in (401, 403)


# ============================================================================= preview ==========


def test_an_admin_can_preview_a_visit_deletion(client, world):
    response = client.get("/api/admin/shed-visits/700/deletion-preview", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["visit"]["loco_number"] == "32032"
    assert body["required_confirmation"] == "DELETE 32032 IA"
    assert body["counts"]["bookings"] == 1
    assert body["counts"]["booking_section_assignments"] == 1
    assert body["total_rows"] >= 3


def test_the_preview_of_a_missing_visit_is_a_404(client, world):
    response = client.get("/api/admin/shed-visits/9999/deletion-preview", headers=admin_headers())
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "VISIT_NOT_FOUND"


def test_a_booking_preview_states_what_survives(client, world):
    response = client.get("/api/admin/bookings/900/deletion-preview", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["booking"]["description"] == "Pantograph horn fault"
    assert body["unaffected"] == {
        "shed_visit": True, "other_bookings_on_this_visit": 0, "checksheets": True
    }


def test_the_preview_counts_match_what_the_deletion_reports(client, world):
    """The preview is the same computation as the deletion, minus the DELETEs. If these could
    disagree, the confirmation dialog would be describing something other than what happens."""
    preview = client.get("/api/admin/bookings/900/deletion-preview", headers=admin_headers()).json()
    deleted = client.request(
        "DELETE", "/api/admin/bookings/900", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "duplicate raised during training"},
    ).json()
    assert deleted["record_counts"] == preview["counts"]
    assert deleted["total_rows"] == preview["total_rows"]


# ====================================================================== re-authentication =======


def test_a_wrong_password_refuses_the_deletion(client, world):
    response = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": "wrong", "reason": "this must not be deleted",
              "confirmation": "DELETE 32032 IA"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "REAUTH_FAILED"
    # Nothing happened.
    assert world["db"].get(models.ShedVisit, 700) is not None
    assert world["db"].query(models.AdminDeletionEvent).count() == 0


def test_another_admins_password_is_refused(client, world):
    """The check is against the CURRENT account. Accepting a colleague's password would attribute the
    deletion to the wrong person in the ledger."""
    response = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": "a-different-password", "reason": "using the other admin's password",
              "confirmation": "DELETE 32032 IA"},
    )
    assert response.status_code == 401
    assert world["db"].get(models.ShedVisit, 700) is not None


def test_an_admin_deactivated_since_their_token_was_issued_is_refused(client, world):
    db = world["db"]
    db.get(models.User, 1).is_active = False
    db.commit()
    response = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "deactivated admin attempting deletion",
              "confirmation": "DELETE 32032 IA"},
    )
    # get_current_user already refuses an inactive account, so this is 401 at the gate.
    assert response.status_code == 401
    assert db.get(models.ShedVisit, 700) is not None


def test_repeated_wrong_passwords_are_rate_limited(client, world):
    body = {"password": "wrong", "reason": "guessing the password repeatedly",
            "confirmation": "DELETE 32032 IA"}
    for _ in range(REAUTH_MAX_ATTEMPTS):
        assert client.request("DELETE", "/api/admin/shed-visits/700",
                              headers=admin_headers(), json=body).status_code == 401

    blocked = client.request("DELETE", "/api/admin/shed-visits/700",
                             headers=admin_headers(), json=body)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "TOO_MANY_ATTEMPTS"
    assert "Retry-After" in blocked.headers

    # And the CORRECT password is blocked too, which is the point of a limiter.
    assert client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={**body, "password": PASSWORD}
    ).status_code == 429
    assert world["db"].get(models.ShedVisit, 700) is not None


# =========================================================== validation of the request ==========


def test_a_blank_or_short_reason_is_refused(client, world):
    for reason in ("", "   ", "too short"):
        response = client.request(
            "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
            json={"password": PASSWORD, "reason": reason, "confirmation": "DELETE 32032 IA"},
        )
        assert response.status_code in (400, 422), reason
    assert world["db"].get(models.ShedVisit, 700) is not None


def test_a_wrong_confirmation_is_refused(client, world):
    response = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "valid reason of sufficient length",
              "confirmation": "DELETE 99999 IB"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CONFIRMATION_MISMATCH"
    assert world["db"].get(models.ShedVisit, 700) is not None


def test_the_confirmation_is_compared_case_insensitively(client, world):
    response = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "typed in lower case by the admin",
              "confirmation": "delete 32032 ia"},
    )
    assert response.status_code == 200


def test_an_attempt_to_nominate_a_different_actor_is_rejected(client, world):
    """The body forbids unknown keys, so a client hoping to influence the attribution gets a 422
    rather than having the field silently ignored and the request appear to have worked."""
    response = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "valid reason of sufficient length",
              "confirmation": "DELETE 32032 IA", "employee_id": "ADM2", "actor_user_id": 2},
    )
    assert response.status_code == 422
    assert world["db"].get(models.ShedVisit, 700) is not None


def test_the_password_is_never_echoed_back(client, world):
    """Not in a success body, and not in a validation error either - a 422 that quoted the rejected
    input would put the password in the response."""
    for payload in (
        {"password": PASSWORD, "reason": "short", "confirmation": "DELETE 32032 IA"},
        {"password": PASSWORD, "reason": "valid reason of sufficient length",
         "confirmation": "WRONG"},
        {"password": PASSWORD, "reason": "valid reason of sufficient length",
         "confirmation": "DELETE 32032 IA"},
    ):
        response = client.request("DELETE", "/api/admin/shed-visits/700",
                                  headers=admin_headers(), json=payload)
        assert PASSWORD not in response.text, response.status_code


# ============================================================================= deletion =========


def test_an_admin_can_delete_a_booking(client, world):
    db = world["db"]
    response = client.request(
        "DELETE", "/api/admin/bookings/900", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "duplicate booking raised during training"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deletion_type"] == "BOOKING"
    assert body["status"] == "COMPLETED"
    assert body["actor_employee_id"] == "ADM1"
    assert body["manifest_hash"]
    assert db.get(models.Booking, 900) is None
    # The visit survives.
    assert db.get(models.ShedVisit, 700) is not None


def test_an_admin_can_delete_a_visit(client, world):
    db = world["db"]
    response = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "test visit created while learning the system",
              "confirmation": "DELETE 32032 IA"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deletion_type"] == "SHED_VISIT"
    assert body["loco_number"] == "32032"
    assert body["record_counts"]["shed_visits"] == 1
    assert db.get(models.ShedVisit, 700) is None
    assert db.get(models.Booking, 900) is None


def test_deleting_the_same_visit_twice_reports_it_is_gone(client, world):
    first = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "first and only deletion of this visit",
              "confirmation": "DELETE 32032 IA"})
    assert first.status_code == 200
    second = client.request(
        "DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "trying the same deletion again",
              "confirmation": "DELETE 32032 IA"})
    assert second.status_code == 404
    assert second.json()["detail"]["code"] == "VISIT_NOT_FOUND"


# ============================================================================== history =========


def test_the_deletion_history_is_readable_after_the_visit_is_gone(client, world):
    client.request("DELETE", "/api/admin/shed-visits/700", headers=admin_headers(),
                   json={"password": PASSWORD, "reason": "mistaken entry during staff training",
                         "confirmation": "DELETE 32032 IA"})

    history = client.get("/api/admin/deletion-history", headers=admin_headers())
    assert history.status_code == 200
    rows = history.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["loco_number"] == "32032"
    assert row["schedule_variant"] == "IA"
    assert row["actor_employee_id"] == "ADM1"
    assert row["reason"] == "mistaken entry during staff training"
    assert row["status"] == "COMPLETED"
    assert row["total_rows"] == sum(row["record_counts"].values())


def test_the_history_detail_carries_the_manifest_and_snapshots(client, world):
    deleted = client.request(
        "DELETE", "/api/admin/bookings/900", headers=admin_headers(),
        json={"password": PASSWORD, "reason": "duplicate booking raised during training"},
    ).json()

    detail = client.get(f"/api/admin/deletion-history/{deleted['id']}", headers=admin_headers())
    assert detail.status_code == 200
    body = detail.json()
    assert body["manifest"]["version"] == 1
    assert body["item_count"] == body["total_rows"]

    items = client.get(f"/api/admin/deletion-history/{deleted['id']}/items",
                       headers=admin_headers()).json()
    assert items["total"] == body["item_count"]
    kinds = {i["entity_type"] for i in items["items"]}
    assert "bookings" in kinds
    snapshot = next(i for i in items["items"] if i["entity_type"] == "bookings")["snapshot"]
    assert snapshot["description"] == "Pantograph horn fault"


def test_history_filters(client, world):
    client.request("DELETE", "/api/admin/bookings/900", headers=admin_headers(),
                   json={"password": PASSWORD, "reason": "duplicate booking raised in training"})

    assert len(client.get("/api/admin/deletion-history?deletion_type=BOOKING",
                          headers=admin_headers()).json()) == 1
    assert len(client.get("/api/admin/deletion-history?deletion_type=SHED_VISIT",
                          headers=admin_headers()).json()) == 0
    assert len(client.get("/api/admin/deletion-history?loco_number=32032",
                          headers=admin_headers()).json()) == 1
    assert len(client.get("/api/admin/deletion-history?loco_number=99999",
                          headers=admin_headers()).json()) == 0


def test_a_missing_history_record_is_a_404(client, world):
    assert client.get("/api/admin/deletion-history/4242",
                      headers=admin_headers()).status_code == 404
    assert client.get("/api/admin/deletion-history/4242/items",
                      headers=admin_headers()).status_code == 404
