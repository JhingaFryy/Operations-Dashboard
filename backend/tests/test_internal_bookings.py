"""POST /api/internal/bookings - service-to-service booking creation.

Covers what is specific to this new entry point: that it reuses the EXISTING internal-key trust
boundary (and that an Admin JWT is not a substitute for it), that it reuses the EXISTING
equipment -> section routing rather than reimplementing it, and the idempotency contract.

Deliberately NOT re-tested here: the exact/ancestor-fallback/first-level-only mapping algorithm
itself. That lives in equipment_service.resolve_sections() and is already covered by
tests/test_equipment.py and tests/test_booking_routing.py against the Dashboard-native creation
flows. What IS asserted here is that this route produces the SAME routing outcomes for the same
mapping fixtures - i.e. that it goes through that one implementation - not a second copy of the
algorithm's own test suite.
"""

import app.core.dependencies as dependencies_module
from app.db.models import Booking, BookingEvent, BookingSectionAssignment
from tests.conftest import (
    grant_access,
    make_defect_type,
    make_section,
    make_shed_visit,
    make_stage,
    make_user,
)

INTERNAL_KEY = "test-internal-key-not-for-production"
CLIENT_ID = "8f14e45f-ceea-467a-9a3e-1b2c3d4e5f60"


class _StubSettings:
    def __init__(self, operations_internal_api_key):
        self.operations_internal_api_key = operations_internal_api_key


def _set_internal_key(monkeypatch, key):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings(key))


def _payload(**overrides):
    body = {
        "client_booking_id": CLIENT_ID,
        "shed_visit_id": 1,
        "equipment_node_id": 20,
        "booking_source": "TEST_BEFORE",
        "defect_type_id": 1,
        "description": "Blower bearing noisy",
        "checksheet_id": 777,
        "technician_employee_id": "T001",
    }
    body.update(overrides)
    return body


def _post(client, body=None, key=INTERNAL_KEY, extra_headers=None):
    headers = dict(extra_headers or {})
    if key is not None:
        headers["X-Internal-API-Key"] = key
    return client.post("/api/internal/bookings", json=body or _payload(), headers=headers)


def _seed(db_session, mock_loco_client, *, visit_status="IN_SHED", mapped_codes=("M4-HR",)):
    """One shed visit, one TEST_BEFORE stage, one section, one defect type, one technician, and a
    two-level equipment tree whose LEAF (20) is mapped unless a test overrides it."""
    make_section(db_session, 1, "M4-HR")
    make_section(db_session, 2, "M5-BOGIE")
    make_user(db_session, 1, "T001", "Technician One", "TECHNICIAN", None)
    make_defect_type(db_session, 1, "MECHANICAL")
    make_shed_visit(db_session, 1, "39126", status=visit_status)
    make_stage(db_session, 1, 1, "TEST_BEFORE", 1, status="IN_PROGRESS")
    mock_loco_client.add_node(10, 1, None, "Auxiliaries")
    mock_loco_client.add_node(20, 1, 10, "TM Blower")
    if mapped_codes:
        mock_loco_client.set_mapping(20, list(mapped_codes))


# ------------------------------------------------------------------------------------- auth --


def test_missing_key_401(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    assert _post(client, key=None).status_code == 401


def test_wrong_key_401(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    assert _post(client, key="totally-wrong-key").status_code == 401


def test_admin_jwt_is_not_a_substitute_for_the_internal_key(
    client, db_session, mock_loco_client, monkeypatch
):
    """The two trust domains stay separate: a real, valid Admin Bearer token - which opens every
    human-facing route in the app - must NOT open this one."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    make_user(db_session, 2, "A001", "Admin", "ADMIN", None)
    grant_access(db_session, 2)

    from app.core.security import create_access_token

    token = create_access_token({"sub": "A001"})

    resp = _post(client, key=None, extra_headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert db_session.query(Booking).count() == 0


def test_no_booking_is_created_by_a_rejected_request(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    _post(client, key="wrong")

    assert db_session.query(Booking).count() == 0


# --------------------------------------------------------------------------------- creation --


def test_valid_creation(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["client_booking_id"] == CLIENT_ID
    assert body["status"] == "OPEN"
    assert body["booking_source"] == "TEST_BEFORE"
    assert body["equipment_node_id"] == 20
    assert body["section_codes"] == ["M4-HR"]
    assert body["section_assignments_created"] == 1

    booking = db_session.query(Booking).one()
    assert booking.client_booking_id == CLIENT_ID
    assert booking.description == "Blower bearing noisy"
    assert booking.status == "OPEN"
    # Attributed to the technician the caller named, resolved via the shared users table.
    assert booking.created_by == 1
    # Linked to the visit's own TEST_BEFORE stage, exactly like a Dashboard-native TB finding.
    assert booking.stage_id == 1

    assignments = db_session.query(BookingSectionAssignment).all()
    assert [(a.section_id, a.status, a.assignment_source) for a in assignments] == [
        (1, "OPEN", "AUTO_MAPPING")
    ]

    events = db_session.query(BookingEvent).order_by(BookingEvent.id).all()
    assert [e.event_type for e in events] == ["CREATED", "AUTO_ROUTED"]
    # Audit breadcrumb back to the originating BL-DCMS performa.
    assert events[0].event_data["checksheet_id"] == 777
    assert events[0].event_data["origin"] == "BLDCMS_CHECKSHEET"


def test_technician_may_be_omitted(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client, _payload(technician_employee_id=None))

    assert resp.status_code == 200, resp.text
    assert db_session.query(Booking).one().created_by is None


def test_unknown_technician_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client, _payload(technician_employee_id="NOBODY"))

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "UNKNOWN_TECHNICIAN"
    assert db_session.query(Booking).count() == 0


def test_unknown_shed_visit_404(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    assert _post(client, _payload(shed_visit_id=999)).status_code == 404


def test_closed_visit_rejected(client, db_session, mock_loco_client, monkeypatch):
    """Shed Out gating is unchanged: a closed visit accepts no new booking, so a late-arriving
    dispatch can never retroactively re-block a departed locomotive."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client, visit_status="CLOSED")

    resp = _post(client)

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "VISIT_CLOSED"
    assert db_session.query(Booking).count() == 0


def test_unknown_equipment_node_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client, _payload(equipment_node_id=99999))

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "EQUIPMENT_NOT_FOUND"
    assert db_session.query(Booking).count() == 0


def test_unknown_defect_type_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client, _payload(defect_type_id=4242))

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "UNKNOWN_DEFECT_TYPE"


def test_inactive_defect_type_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    make_defect_type(db_session, 9, "RETIRED", is_active=False)

    resp = _post(client, _payload(defect_type_id=9))

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "UNKNOWN_DEFECT_TYPE"


def test_loco_master_unavailable_is_a_controlled_502(
    client, db_session, mock_loco_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    mock_loco_client.unavailable = True

    resp = _post(client)

    assert resp.status_code == 502
    assert db_session.query(Booking).count() == 0


# ---------------------------------------------------------------------------- booking source --


def test_unknown_booking_source_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client, _payload(booking_source="NOT_A_SOURCE"))

    assert resp.status_code == 422
    assert db_session.query(Booking).count() == 0


def test_special_checking_is_not_an_application_level_source(
    client, db_session, mock_loco_client, monkeypatch
):
    """Present in the database CHECK constraint for historical reasons, deliberately absent from
    BOOKING_SOURCES - nothing creates it and nothing should start."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    assert _post(client, _payload(booking_source="SPECIAL_CHECKING")).status_code == 422


def test_trip_inspection_source_accepted(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client, _payload(booking_source="TRIP_INSPECTION"))

    assert resp.status_code == 200, resp.text
    booking = db_session.query(Booking).one()
    assert booking.booking_source == "TRIP_INSPECTION"
    # Names no shed_visit_stages.stage_type, so it links to no stage and gates none.
    assert booking.stage_id is None


def test_general_checking_source_accepted(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    resp = _post(client, _payload(booking_source="GENERAL_CHECKING", client_booking_id="gc-1"))

    assert resp.status_code == 200, resp.text
    booking = db_session.query(Booking).one()
    assert booking.booking_source == "GENERAL_CHECKING"
    assert booking.stage_id is None


# --------------------------------------------------------------------------------- routing --


def test_exact_node_mapping_wins(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client, mapped_codes=("M4-HR",))
    mock_loco_client.set_mapping(10, ["M5-BOGIE"])  # ancestor also mapped, must be ignored

    resp = _post(client)

    assert resp.json()["section_codes"] == ["M4-HR"]


def test_ancestor_fallback_when_leaf_unmapped(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client, mapped_codes=())
    mock_loco_client.set_mapping(10, ["M5-BOGIE"])

    resp = _post(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["section_codes"] == ["M5-BOGIE"]


def test_all_sections_at_the_first_matching_level_are_used(
    client, db_session, mock_loco_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client, mapped_codes=("M4-HR", "M5-BOGIE"))

    resp = _post(client)

    assert resp.json()["section_codes"] == ["M4-HR", "M5-BOGIE"]
    assert resp.json()["section_assignments_created"] == 2
    assert db_session.query(BookingSectionAssignment).count() == 2


def test_no_section_mapping_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client, mapped_codes=())

    resp = _post(client)

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "NO_SECTION_MAPPING"
    # Never created unrouted.
    assert db_session.query(Booking).count() == 0
    assert db_session.query(BookingSectionAssignment).count() == 0


def test_mapped_code_unknown_to_dashboard_is_no_section_mapping(
    client, db_session, mock_loco_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client, mapped_codes=("SECTION-THAT-DOES-NOT-EXIST",))

    resp = _post(client)

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "NO_SECTION_MAPPING"
    assert db_session.query(Booking).count() == 0


# ------------------------------------------------------------------------------ idempotency --


def test_same_id_same_payload_returns_the_existing_booking(
    client, db_session, mock_loco_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    first = _post(client)
    second = _post(client)

    assert first.status_code == 200 and second.status_code == 200, second.text
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["booking_id"] == first.json()["booking_id"]
    assert second.json()["section_codes"] == first.json()["section_codes"]


def test_retry_creates_no_second_booking_assignment_or_event(
    client, db_session, mock_loco_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    _post(client)
    _post(client)
    _post(client)

    assert db_session.query(Booking).count() == 1
    assert db_session.query(BookingSectionAssignment).count() == 1
    assert db_session.query(BookingEvent).count() == 2  # CREATED + AUTO_ROUTED, exactly once


def test_two_different_client_ids_create_two_bookings(
    client, db_session, mock_loco_client, monkeypatch
):
    """Server-side idempotency deduplicates a RETRANSMISSION, never a genuinely distinct defect.
    Identical equipment and identical remarks under a different key must still create a second
    booking - the same defect wording legitimately recurs."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    _post(client, _payload(client_booking_id="row-a"))
    _post(client, _payload(client_booking_id="row-b"))

    assert db_session.query(Booking).count() == 2


def test_same_id_conflicting_description_rejected(
    client, db_session, mock_loco_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    _post(client)

    resp = _post(client, _payload(description="Something completely different"))

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "IDEMPOTENCY_CONFLICT"
    assert detail["conflicting_fields"] == ["description"]
    # The stored booking is never silently mutated to match the newer claim.
    booking = db_session.query(Booking).one()
    assert booking.description == "Blower bearing noisy"


def test_same_id_conflicting_equipment_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    mock_loco_client.add_node(30, 1, 10, "Other Blower")
    mock_loco_client.set_mapping(30, ["M4-HR"])
    _post(client)

    resp = _post(client, _payload(equipment_node_id=30))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert db_session.query(Booking).one().equipment_node_id == 20


def test_same_id_conflicting_source_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    make_stage(db_session, 2, 1, "TEST_AFTER", 3)   # so the retry reaches the idempotency check
    _post(client)

    resp = _post(client, _payload(booking_source="TEST_AFTER"))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert db_session.query(Booking).one().booking_source == "TEST_BEFORE"


def test_whitespace_only_difference_in_description_is_still_a_match(
    client, db_session, mock_loco_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    _post(client)

    resp = _post(client, _payload(description="  Blower bearing noisy  "))

    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is False
    assert db_session.query(Booking).count() == 1


def test_retry_succeeds_even_after_the_defect_type_is_deactivated(
    client, db_session, mock_loco_client, monkeypatch
):
    """A retry only has to report a booking that already exists; it must not be rejected because
    reference data has since been retired underneath it."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)
    _post(client)

    defect_type = db_session.query(Booking).one().defect_type_id
    from app.db.models import BookingDefectType

    db_session.query(BookingDefectType).filter(BookingDefectType.id == defect_type).update(
        {"is_active": False}
    )
    db_session.commit()

    resp = _post(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is False


def test_blank_client_booking_id_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    assert _post(client, _payload(client_booking_id="   ")).status_code == 422


def test_blank_description_rejected(client, db_session, mock_loco_client, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    assert _post(client, _payload(description="   ")).status_code == 422


# ------------------------------------------------------------ existing flows stay unaffected --


def test_dashboard_native_bookings_still_have_a_null_client_booking_id(
    client, db_session, mock_loco_client, monkeypatch
):
    """Nothing about the new column changes the Dashboard-native creation flows: many rows with a
    NULL key coexist, because the uniqueness is enforced only among non-NULL values."""
    from tests.conftest import make_booking

    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed(db_session, mock_loco_client)

    make_booking(db_session, 501, 1, defect_type_id=1, description="native one")
    make_booking(db_session, 502, 1, defect_type_id=1, description="native two")

    assert db_session.query(Booking).filter(Booking.client_booking_id.is_(None)).count() == 2


# ------------------------------------------------------- internal defect-type read (Phase 9) --


def _get_defect_types(client, key=INTERNAL_KEY):
    headers = {"X-Internal-API-Key": key} if key is not None else {}
    return client.get("/api/internal/booking-defect-types", headers=headers)


def test_internal_defect_types_requires_the_internal_key(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_defect_type(db_session, 1, "MECHANICAL")

    assert _get_defect_types(client, key=None).status_code == 401
    assert _get_defect_types(client, key="wrong").status_code == 401


def test_internal_defect_types_lists_active_only_in_sort_order(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_defect_type(db_session, 1, "ELECTRICAL", name="Electrical", sort_order=2)
    make_defect_type(db_session, 2, "MECHANICAL", name="Mechanical", sort_order=1)
    make_defect_type(db_session, 3, "RETIRED", name="Retired", sort_order=0, is_active=False)

    resp = _get_defect_types(client)

    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"id": 2, "code": "MECHANICAL", "name": "Mechanical"},
        {"id": 1, "code": "ELECTRICAL", "name": "Electrical"},
    ]


def test_internal_defect_types_exposes_no_internal_fields(client, db_session, monkeypatch):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    make_defect_type(db_session, 1, "MECHANICAL")

    item = _get_defect_types(client).json()[0]

    assert set(item) == {"id", "code", "name"}


# ------------------------------------------------------ technology / equipment family (Phase H) --
#
# The request carries no technology and no family: Dashboard derives the visit locomotive's family
# from BL-DCMS and checks the node against it. Every case below is the SAME request body, differing
# only in server-side state, which is the point - nothing the caller sends decides this.


def _seed_both_families(db_session, mock_loco_client, loco_number="39126"):
    make_section(db_session, 1, "M4-HR")
    make_user(db_session, 1, "T001", "Technician One", "TECHNICIAN", None)
    make_defect_type(db_session, 1, "MECHANICAL")
    make_shed_visit(db_session, 1, loco_number, status="IN_SHED")
    make_stage(db_session, 1, 1, "TEST_BEFORE", 1, status="IN_PROGRESS")
    mock_loco_client.add_node(10, 1, None, "Auxiliaries")           # 3-phase tree
    mock_loco_client.add_node(20, 1, 10, "TM Blower")
    mock_loco_client.set_mapping(20, ["M4-HR"])
    mock_loco_client.add_node(30, 2, None, "Tap Changer")           # conventional tree
    mock_loco_client.add_node(40, 2, 30, "GR Contactor")
    mock_loco_client.set_mapping(40, ["M4-HR"])


def _detail(response):
    return response.json()["detail"]


def test_a_3_phase_visit_accepts_3_phase_equipment(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")

    response = _post(client, _payload(equipment_node_id=20))

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True


def test_a_3_phase_visit_refuses_conventional_equipment(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")

    response = _post(client, _payload(equipment_node_id=40))

    assert response.status_code == 422
    assert _detail(response)["code"] == "EQUIPMENT_FAMILY_MISMATCH"
    assert db_session.query(Booking).count() == 0


def test_a_conventional_visit_accepts_conventional_equipment(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "CONVENTIONAL")

    response = _post(client, _payload(equipment_node_id=40))

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True


def test_a_conventional_visit_refuses_3_phase_equipment(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "CONVENTIONAL")

    response = _post(client, _payload(equipment_node_id=20))

    assert response.status_code == 422
    assert _detail(response)["code"] == "EQUIPMENT_FAMILY_MISMATCH"
    assert db_session.query(Booking).count() == 0


def test_a_technology_field_in_the_body_is_not_authorization(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    """A caller claiming the locomotive is Conventional does not make it so: the extra field is not
    part of the contract, and the visit's own locomotive still decides."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")

    response = _post(client, _payload(equipment_node_id=40, technology="CONVENTIONAL", family="CONVENTIONAL"))

    assert response.status_code == 422
    assert _detail(response)["code"] == "EQUIPMENT_FAMILY_MISMATCH"


def test_an_unknown_node_is_still_reported_as_unknown_equipment(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)

    response = _post(client, _payload(equipment_node_id=9999))

    assert response.status_code == 422
    assert _detail(response)["code"] == "EQUIPMENT_NOT_FOUND"


def test_an_inactive_defect_type_is_refused(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    make_defect_type(db_session, 2, "RETIRED", is_active=False)

    response = _post(client, _payload(equipment_node_id=20, defect_type_id=2))

    assert response.status_code == 422
    assert _detail(response)["code"] == "UNKNOWN_DEFECT_TYPE"


def test_blank_remarks_are_refused_before_any_write(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)

    for blank in ("", "   ", "\n\t "):
        response = _post(client, _payload(equipment_node_id=20, description=blank))
        assert response.status_code == 422, blank
    assert db_session.query(Booking).count() == 0


def test_a_locomotive_bldcms_does_not_know_is_refused_not_guessed(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", None)

    response = _post(client, _payload(equipment_node_id=20))

    assert response.status_code == 422
    assert _detail(response)["code"] == "LOCOMOTIVE_NOT_FOUND"
    assert db_session.query(Booking).count() == 0


def test_an_idempotent_retry_is_not_re_validated_against_the_family(
    client, db_session, mock_loco_client, mock_bldcms_client, monkeypatch
):
    """The stored booking already exists; a retry only has to report it. This is the pre-existing
    retry contract (see the deactivated-defect-type test above) and the family check must not
    become a new way for a retry to fail."""
    _set_internal_key(monkeypatch, INTERNAL_KEY)
    _seed_both_families(db_session, mock_loco_client)
    mock_bldcms_client.set_locomotive_technology("39126", "3_PHASE")
    first = _post(client, _payload(equipment_node_id=20))
    assert first.status_code == 200

    mock_bldcms_client.unavailable = True
    retry = _post(client, _payload(equipment_node_id=20))

    assert retry.status_code == 200
    assert retry.json()["created"] is False
