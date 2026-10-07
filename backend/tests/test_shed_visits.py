from app.db import models
from tests.conftest import (
    ensure_section,
    make_movement_supervisor_headers,
    auth_header,
    grant_access,
    make_defect_type,
    make_section,
    make_user,
)


def _admin_headers(db_session):
    """Historically an Admin; now the entitled SHIFT (movement) Supervisor, which is the account
    that may drive the full shed workflow under the Supervisor-only policy."""
    return make_movement_supervisor_headers(db_session)


def _base_setup(db_session, mock_loco_client):
    """One locomotive, two sections, one mapped equipment node, two defect types."""
    mock_loco_client.add_locomotive("39126", "WAG9HC")
    make_section(db_session, 1, "M1-HR")
    make_section(db_session, 2, "M2-HR")
    make_defect_type(db_session, 1, "DEFECTIVE")
    make_defect_type(db_session, 2, "BROKEN")
    mock_loco_client.add_node(1843, 1, None, "Aux Converter")
    mock_loco_client.set_mapping(1843, ["M1-HR", "M2-HR"])


def _shed_in_payload(**overrides):
    payload = {
        "loco_number": "39126",
        "schedule_family": "MINOR",
        "schedule_variant": "IA",
        "arrival_condition": "WORKING",
        "arrival_at": "2026-08-31T09:35:00+05:30",
        "log_book_bookings": [],
    }
    payload.update(overrides)
    return payload


def test_shed_in_without_bookings(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/in", json=_shed_in_payload(), headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "IN_SHED"
    assert body["loco_number"] == "39126"
    assert body["bookings_created"] == 0
    assert body["section_assignments_created"] == 0
    assert body["stages_created"] == 3


def test_shed_in_with_one_booking_creates_section_assignments(client, db_session, mock_loco_client):
    """Business Rule Alignment: booking creation resolves equipment->section mapping
    (most-specific-mapping-wins) and creates one booking_section_assignments row per resolved
    section - the booking is routed to its mapped section(s), not globally visible."""
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[
                {"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "Isolated after fault"}
            ]
        ),
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bookings_created"] == 1
    # 1843 is mapped to both M1-HR and M2-HR - one assignment per resolved section.
    assert body["section_assignments_created"] == 2

    booking = db_session.query(models.Booking).one()
    assert booking.booking_source == "LOG_BOOK"
    assert booking.equipment_node_id == 1843
    assert booking.defect_type_id == 1
    assert booking.status == "OPEN"

    assignments = db_session.query(models.BookingSectionAssignment).all()
    assert len(assignments) == 2
    assert {a.assignment_source for a in assignments} == {"AUTO_MAPPING"}


def test_shed_in_with_multiple_bookings(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    mock_loco_client.add_node(2101, 1, None, "Pantograph")
    mock_loco_client.set_mapping(2101, ["M1-HR"])
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[
                {"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "Aux converter isolated"},
                {"equipment_node_id": 2101, "defect_type_id": 2, "remarks": "Pantograph raising issue"},
            ]
        ),
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bookings_created"] == 2
    # 1843 -> M1-HR + M2-HR (2), 2101 -> M1-HR (1)
    assert body["section_assignments_created"] == 3
    assert db_session.query(models.Booking).count() == 2


def test_ia_creates_three_stages(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in", json=_shed_in_payload(schedule_variant="IA"), headers=headers
    )

    assert resp.status_code == 200, resp.text
    stages = db_session.query(models.ShedVisitStage).order_by(models.ShedVisitStage.stage_order).all()
    assert [s.stage_type for s in stages] == [
        "TEST_BEFORE",
        "SCHEDULE_INSPECTION",
        "TEST_AFTER",
    ]
    assert all(s.status == "PENDING" for s in stages)


def test_ia_does_not_create_special_checking_stage(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in", json=_shed_in_payload(schedule_variant="IA"), headers=headers
    )

    assert resp.status_code == 200, resp.text
    stage_types = {
        s.stage_type for s in db_session.query(models.ShedVisitStage).all()
    }
    assert "SPECIAL_CHECKING" not in stage_types


def test_ib_creates_three_stages(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in", json=_shed_in_payload(schedule_variant="IB"), headers=headers
    )

    assert resp.status_code == 200, resp.text
    assert db_session.query(models.ShedVisitStage).count() == 3


def test_ic_creates_three_stages(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in", json=_shed_in_payload(schedule_variant="IC"), headers=headers
    )

    assert resp.status_code == 200, resp.text
    assert db_session.query(models.ShedVisitStage).count() == 3


def test_duplicate_open_loco_rejected_409(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    first = client.post("/api/shed-visits/in", json=_shed_in_payload(), headers=headers)
    assert first.status_code == 200, first.text

    second = client.post("/api/shed-visits/in", json=_shed_in_payload(), headers=headers)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "LOCO_ALREADY_IN_SHED"

    # No partial second visit was created.
    assert db_session.query(models.ShedVisit).count() == 1


def test_invalid_locomotive_rejected(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in", json=_shed_in_payload(loco_number="NOPE"), headers=headers
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "UNKNOWN_LOCOMOTIVE"
    assert db_session.query(models.ShedVisit).count() == 0


def test_invalid_arrival_condition(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in", json=_shed_in_payload(arrival_condition="BROKEN"), headers=headers
    )

    assert resp.status_code == 422
    assert db_session.query(models.ShedVisit).count() == 0


def test_invalid_schedule_variant(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in", json=_shed_in_payload(schedule_variant="ID"), headers=headers
    )

    assert resp.status_code == 422
    assert db_session.query(models.ShedVisit).count() == 0


def test_invalid_defect_type_rejected(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[{"equipment_node_id": 1843, "defect_type_id": 999, "remarks": "x"}]
        ),
        headers=headers,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "UNKNOWN_DEFECT_TYPE"
    assert db_session.query(models.ShedVisit).count() == 0


def test_blank_remarks_rejected(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[{"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "   "}]
        ),
        headers=headers,
    )

    assert resp.status_code == 422
    assert db_session.query(models.ShedVisit).count() == 0


def test_unmapped_equipment_rejects_whole_request(client, db_session, mock_loco_client):
    """Business Rule Alignment: equipment with no resolvable section mapping (no exact mapping,
    no ancestor mapping) rejects the whole Shed In request with NO_SECTION_MAPPING - a booking
    is never created unrouted."""
    _base_setup(db_session, mock_loco_client)
    mock_loco_client.add_node(9999, 1, None, "Unmapped Widget")
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[
                {"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "fine one"},
                {"equipment_node_id": 9999, "defect_type_id": 1, "remarks": "unmapped one"},
            ]
        ),
        headers=headers,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "NO_SECTION_MAPPING"
    assert db_session.query(models.ShedVisit).count() == 0
    assert db_session.query(models.Booking).count() == 0
    assert db_session.query(models.BookingSectionAssignment).count() == 0


def test_nonexistent_equipment_rejects_whole_request(client, db_session, mock_loco_client):
    """Equipment must still exist in Loco Master (a real validation), even though its
    section-mapping status no longer matters."""
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[
                {"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "fine one"},
                {"equipment_node_id": 424242, "defect_type_id": 1, "remarks": "nonexistent one"},
            ]
        ),
        headers=headers,
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "EQUIPMENT_NOT_FOUND"
    assert detail["booking_index"] == 1
    assert detail["equipment_node_id"] == 424242
    # Whole Shed In rejected — nothing created, not even the first (valid) booking.
    assert db_session.query(models.ShedVisit).count() == 0
    assert db_session.query(models.Booking).count() == 0


def test_rollback_if_second_booking_fails(client_allow_500, db_session, mock_loco_client, monkeypatch):
    client = client_allow_500
    _base_setup(db_session, mock_loco_client)
    mock_loco_client.add_node(2101, 1, None, "Pantograph")
    mock_loco_client.set_mapping(2101, ["M1-HR"])
    headers = _admin_headers(db_session)

    from app.services import booking_creation_service

    real_booking = booking_creation_service.Booking
    calls = {"n": 0}

    def flaky_booking(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure creating second booking")
        return real_booking(*args, **kwargs)

    monkeypatch.setattr(booking_creation_service, "Booking", flaky_booking)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[
                {"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "first"},
                {"equipment_node_id": 2101, "defect_type_id": 1, "remarks": "second"},
            ]
        ),
        headers=headers,
    )

    assert resp.status_code == 500
    assert db_session.query(models.ShedVisit).count() == 0
    assert db_session.query(models.Booking).count() == 0
    assert db_session.query(models.ShedVisitStage).count() == 0


def test_correct_audit_user_recorded(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/in", json=_shed_in_payload(), headers=headers)
    assert resp.status_code == 200, resp.text

    visit = db_session.query(models.ShedVisit).one()
    assert visit.created_by == 1
    assert visit.updated_by == 1


def test_shed_in_event_created(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post("/api/shed-visits/in", json=_shed_in_payload(), headers=headers)
    assert resp.status_code == 200, resp.text

    events = db_session.query(models.ShedVisitEvent).all()
    assert len(events) == 1
    assert events[0].event_type == "SHED_IN"
    assert events[0].source == "DASHBOARD"


def test_booking_source_is_log_book(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[{"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "x"}]
        ),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    booking = db_session.query(models.Booking).one()
    assert booking.booking_source == "LOG_BOOK"

    events = db_session.query(models.BookingEvent).all()
    created_events = [e for e in events if e.event_type == "CREATED"]
    assert len(created_events) == 1
    # 1843 is mapped to both M1-HR and M2-HR - one AUTO_ROUTED event per resolved section.
    auto_routed_events = [e for e in events if e.event_type == "AUTO_ROUTED"]
    assert len(auto_routed_events) == 2


def test_current_shed_visits(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)
    client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(
            log_book_bookings=[{"equipment_node_id": 1843, "defect_type_id": 1, "remarks": "x"}]
        ),
        headers=headers,
    )

    resp = client.get("/api/shed-visits/current", headers=headers)

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["loco_number"] == "39126"
    assert items[0]["status"] == "IN_SHED"
    assert items[0]["booking_total"] == 1
    # Common Booking Pool reform: pending is derived from booking.status directly - the freshly
    # created booking is OPEN, so it counts as 1 pending, regardless of section mapping.
    assert items[0]["pending_booking_count"] == 1


def test_shed_visits_require_authentication(client, db_session, mock_loco_client):
    resp = client.post("/api/shed-visits/in", json=_shed_in_payload())
    assert resp.status_code == 401

    resp2 = client.get("/api/shed-visits/current")
    assert resp2.status_code == 401


def test_shed_visits_are_readable_by_any_active_supervisor(client, db_session, mock_loco_client):
    """No dashboard_access row required for base operational access. Locomotive MOVEMENT is a
    separate, narrower gate and is covered by the movement-section tests below."""
    make_user(db_session, 2, "SUP1", "Sup One", "Supervisor", "hash")
    # No grant_access call — no dashboard_access row at all.
    headers = auth_header("SUP1", "Supervisor", 2)

    resp = client.get("/api/shed-visits/current", headers=headers)
    assert resp.status_code == 200, resp.text


def test_entitled_supervisor_outside_a_movement_section_cannot_shed_in(
    client, db_session, mock_loco_client
):
    """Dashboard access alone is not enough for locomotive movement.

    An entitled Supervisor may use the Operations Dashboard, but Shed In / Start Schedule /
    Complete Schedule / Shed Out belong to the shed movement sections (SHIFT/PPIO) only.
    """
    _base_setup(db_session, mock_loco_client)
    ensure_section(db_session, 77, "M9-HR")
    make_user(db_session, 2, "SUP1", "Sup One", "Supervisor", "hash", section_id=77)
    grant_access(db_session, 2, is_enabled=True)
    headers = auth_header("SUP1", "Supervisor", 2)

    resp = client.post("/api/shed-visits/in", json=_shed_in_payload(), headers=headers)

    assert resp.status_code == 403
    assert "movement sections" in resp.json()["detail"]


# --- MAJOR (IOH/TOH) schedule support --------------------------------------


def test_major_ioh_shed_in_succeeds(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(schedule_family="MAJOR", schedule_variant="IOH"),
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schedule_family"] == "MAJOR"
    assert body["schedule_variant"] == "IOH"
    assert body["status"] == "IN_SHED"
    assert body["stages_created"] == 0

    visit = db_session.query(models.ShedVisit).one()
    assert visit.schedule_family == "MAJOR"
    assert visit.schedule_variant == "IOH"
    assert visit.arrival_condition == "WORKING"
    assert visit.status == "IN_SHED"


def test_major_toh_shed_in_succeeds(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(schedule_family="MAJOR", schedule_variant="TOH"),
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schedule_family"] == "MAJOR"
    assert body["schedule_variant"] == "TOH"
    assert body["stages_created"] == 0


def test_major_shed_in_creates_no_stages_and_no_work_package_row(client, db_session, mock_loco_client):
    """MAJOR must not invoke the Minor-only stage/work-package orchestration
    (MINOR_STAGE_SEQUENCE / generate_work_package): no ShedVisitStage rows
    and no ShedVisitChecksheetPackage row get created as a side effect of
    Shed In for a MAJOR visit."""
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(schedule_family="MAJOR", schedule_variant="IOH"),
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    assert db_session.query(models.ShedVisitStage).count() == 0
    assert db_session.query(models.ShedVisitChecksheetPackage).count() == 0


def test_major_ia_cross_family_rejected(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(schedule_family="MAJOR", schedule_variant="IA"),
        headers=headers,
    )

    assert resp.status_code == 422
    assert db_session.query(models.ShedVisit).count() == 0


def test_minor_ioh_cross_family_rejected(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(schedule_family="MINOR", schedule_variant="IOH"),
        headers=headers,
    )

    assert resp.status_code == 422
    assert db_session.query(models.ShedVisit).count() == 0


def test_unknown_schedule_family_rejected(client, db_session, mock_loco_client):
    _base_setup(db_session, mock_loco_client)
    headers = _admin_headers(db_session)

    resp = client.post(
        "/api/shed-visits/in",
        json=_shed_in_payload(schedule_family="MEGA", schedule_variant="IOH"),
        headers=headers,
    )

    assert resp.status_code == 422
    assert db_session.query(models.ShedVisit).count() == 0

