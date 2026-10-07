"""Booking ownership (origin) rules - see app/services/booking_origin.py.

    TEST_BEFORE / TEST_AFTER  -> created only from the Android checksheet, via BL-DCMS (internal route)
    LOG_BOOK                  -> created only on the Dashboard (Shed In)
    SCHEDULE_INSPECTION       -> never created
The Dashboard still displays, assigns and progresses every booking, and Shed Out still gates on all.
"""

from datetime import datetime, timezone

import pytest

import app.core.dependencies as dependencies_module
from app.db import models
from tests.conftest import (
    make_defect_type,
    make_minor_stages,
    make_movement_supervisor_headers,
    make_section,
    make_section_supervisor_headers,
    make_shed_visit,
    make_true_admin_headers,
    make_user,
)

INTERNAL_KEY = "test-internal-key-not-for-production"


class _StubSettings:
    operations_internal_api_key = INTERNAL_KEY


@pytest.fixture()
def internal(monkeypatch):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings())
    return {"X-Internal-API-Key": INTERNAL_KEY}


@pytest.fixture()
def world(db_session, mock_loco_client):
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_user(db_session, 50, "T050", "Technician", "Technician", "hash", section_id=1)
    mock_loco_client.add_locomotive("39126", "WAG9HC")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    mock_loco_client.set_mapping(1843, ["M1-HR", "M2-HR"])
    visit = make_shed_visit(db_session, 900, "39126", schedule_variant="IA")
    make_minor_stages(db_session, visit.id, base_id=9000)
    return visit


def _payload(**overrides):
    body = {
        "client_booking_id": "2a0f7a64-2a8e-4f5e-9e0e-6f2f1f7a0001",
        "shed_visit_id": 900,
        "equipment_node_id": 1843,
        "booking_source": "TEST_BEFORE",
        "defect_type_id": 1,
        "description": "Blower bearing noisy",
        "checksheet_id": 4401,
        "technician_employee_id": "T050",
    }
    body.update(overrides)
    return body


def _internal_post(client, headers, **overrides):
    return client.post("/api/internal/bookings", json=_payload(**overrides), headers=headers)


# ==================================================================== Dashboard creation ==

@pytest.mark.parametrize("kind,source", [("test-before", "TEST_BEFORE"), ("test-after", "TEST_AFTER"),
                                         ("schedule-inspection", "SCHEDULE_INSPECTION")])
def test_dashboard_cannot_create_checksheet_stage_bookings(client, db_session, world, kind, source):
    for headers in (make_movement_supervisor_headers(db_session), make_true_admin_headers(db_session)):
        resp = client.post(
            f"/api/shed-visits/{world.id}/{kind}/bookings",
            json={"bookings": [{"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "x"}]},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "BOOKING_ORIGIN_NOT_ALLOWED"
        assert resp.json()["detail"]["booking_source"] == source
    assert db_session.query(models.Booking).count() == 0
    assert db_session.query(models.BookingEvent).count() == 0


def test_dashboard_log_book_booking_at_shed_in_is_log_book_with_no_checksheet_link(
    client, db_session, mock_loco_client
):
    make_section(db_session, 1, "M1-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    mock_loco_client.add_locomotive("39126", "WAG9HC")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    mock_loco_client.set_mapping(1843, ["M1-HR"])
    headers = make_movement_supervisor_headers(db_session)

    resp = client.post("/api/shed-visits/in", json={
        "loco_number": "39126", "schedule_family": "MINOR", "schedule_variant": "IA",
        "arrival_condition": "WORKING", "arrival_at": "2026-08-31T09:35:00+05:30",
        "log_book_bookings": [{"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "Isolated after fault"}],
    }, headers=headers)
    assert resp.status_code == 200, resp.text

    booking = db_session.query(models.Booking).one()
    assert booking.booking_source == "LOG_BOOK"
    assert booking.stage_id is None and booking.client_booking_id is None
    assert booking.created_by == 1
    created = db_session.query(models.BookingEvent).filter_by(booking_id=booking.id, event_type="CREATED").one()
    assert "checksheet_id" not in (created.event_data or {})


# ============================================================ checksheet (internal) channel ==

@pytest.mark.parametrize("source,stage", [("TEST_BEFORE", "TEST_BEFORE"), ("TEST_AFTER", "TEST_AFTER")])
def test_checksheet_booking_is_created_with_full_linkage(client, db_session, world, internal, source, stage):
    resp = _internal_post(client, internal, booking_source=source)
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is True and resp.json()["section_codes"] == ["M1-HR", "M2-HR"]

    booking = db_session.query(models.Booking).one()
    assert booking.booking_source == source
    assert booking.shed_visit_id == 900
    assert booking.client_booking_id == "2a0f7a64-2a8e-4f5e-9e0e-6f2f1f7a0001"
    assert booking.created_by == 50
    assert db_session.get(models.ShedVisitStage, booking.stage_id).stage_type == stage
    created = db_session.query(models.BookingEvent).filter_by(booking_id=booking.id, event_type="CREATED").one()
    assert created.event_data["checksheet_id"] == 4401
    assert created.event_data["workflow_stage_type"] == stage
    assert created.event_data["origin"] == "BLDCMS_CHECKSHEET"


@pytest.mark.parametrize("source,message", [
    ("SCHEDULE_INSPECTION", "Minor Inspection"),
    ("LOG_BOOK", "Log Book"),          # a Log Book booking cannot masquerade as a checksheet booking
    ("MANUAL", "checksheet channel"),
])
def test_checksheet_channel_refuses_non_checksheet_sources(client, db_session, world, internal, source, message):
    resp = _internal_post(client, internal, booking_source=source)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "BOOKING_ORIGIN_NOT_ALLOWED"
    assert message in resp.json()["detail"]["message"]
    assert db_session.query(models.Booking).count() == 0


def test_checksheet_booking_must_name_its_checksheet(client, db_session, world, internal):
    resp = _internal_post(client, internal, checksheet_id=None)
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "CHECKSHEET_LINK_REQUIRED"
    assert db_session.query(models.Booking).count() == 0


def test_checksheet_booking_stage_must_exist_on_the_visit(client, db_session, world, internal, mock_loco_client):
    major = make_shed_visit(db_session, 901, "39160", schedule_family="MAJOR", schedule_variant="IOH")
    resp = _internal_post(client, internal, shed_visit_id=major.id)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "STAGE_NOT_ON_VISIT"

    no_stages = make_shed_visit(db_session, 902, "39161", schedule_variant="IA")
    resp = _internal_post(client, internal, shed_visit_id=no_stages.id, booking_source="TEST_AFTER")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "STAGE_NOT_ON_VISIT"
    assert db_session.query(models.Booking).count() == 0


def test_checksheet_retry_is_idempotent(client, db_session, world, internal):
    first = _internal_post(client, internal)
    again = _internal_post(client, internal)
    assert first.status_code == again.status_code == 200
    assert again.json()["created"] is False and again.json()["booking_id"] == first.json()["booking_id"]
    assert db_session.query(models.Booking).count() == 1
    assert db_session.query(models.BookingSectionAssignment).count() == 2


# ================================================ received bookings are managed as before ==

def test_received_test_before_booking_is_visible_assignable_and_attendable(client, db_session, world, internal):
    assert _internal_post(client, internal).status_code == 200
    booking = db_session.query(models.Booking).one()

    admin = make_true_admin_headers(db_session)
    pool = client.get("/api/bookings", params={"shed_visit_id": 900}, headers=admin).json()
    assert [(b["id"], b["booking_source"]) for b in pool] == [(booking.id, "TEST_BEFORE")]
    listed = client.get("/api/shed-visits/900/test-before/bookings", headers=make_movement_supervisor_headers(db_session)).json()
    assert [b["id"] for b in listed] == [booking.id]

    m1 = make_section_supervisor_headers(db_session, user_id=61, section_id=1, employee_id="M1SUP")
    m2 = make_section_supervisor_headers(db_session, user_id=62, section_id=2, employee_id="M2SUP")
    assignments = {a.section_id: a for a in db_session.query(models.BookingSectionAssignment).filter_by(booking_id=booking.id)}
    for section_id, headers in ((1, m1), (2, m2)):
        assert client.post(f"/api/section-assignments/{assignments[section_id].id}/start", headers=headers).status_code == 200
    db_session.expire_all()
    assert db_session.get(models.Booking, booking.id).status == "IN_PROGRESS"
    for section_id, headers in ((1, m1), (2, m2)):
        resp = client.post(f"/api/section-assignments/{assignments[section_id].id}/attend", json={"remarks": "done"}, headers=headers)
        assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(models.Booking, booking.id).status == "ATTENDED"

    # ATTENDED -> REOPENED stays Admin-only.
    assert client.post(f"/api/section-assignments/{assignments[1].id}/reopen", json={"reason": "again"}, headers=m1).status_code == 403


def test_received_booking_still_holds_shed_out_until_attended(client, db_session, world, internal):
    for stage in db_session.query(models.ShedVisitStage).filter_by(shed_visit_id=900):
        stage.status, stage.completed_at = "COMPLETED", datetime.now(timezone.utc)
    db_session.commit()
    assert _internal_post(client, internal, booking_source="TEST_AFTER").status_code == 200
    booking = db_session.query(models.Booking).one()

    elig = client.get("/api/shed-visits/900/shed-out-eligibility", headers=make_movement_supervisor_headers(db_session)).json()
    assert [b["booking_id"] for b in elig["booking_blockers"]] == [booking.id]


# ================================================================ equipment for Android ==

def test_internal_equipment_hierarchy_is_the_loco_master_tree(client, db_session, world, internal, mock_loco_client):
    assert client.get("/api/internal/equipment/families").status_code == 401
    families = client.get("/api/internal/equipment/families", headers=internal)
    assert families.status_code == 200, families.text
    search = client.get("/api/internal/equipment/nodes/search", params={"q": "Aux"}, headers=internal)
    assert search.status_code == 200, search.text
    assert [n["id"] for n in search.json()] == [1843]
