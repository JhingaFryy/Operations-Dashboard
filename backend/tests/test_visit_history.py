"""Shed Visit History - the permanent archive of every shed visit, and its signed documents.

What is pinned here:
  * every visit is listed - active, Ready, closed and administratively reset, Minor and Major -
    newest first, paged and filtered in the database;
  * BL-DCMS data (loco model, checksheets, signed PDFs) is fetched in batches, and its absence is
    reported as unknown, never as zero;
  * a Supervisor sees only their own section's bookings, assignments, events and checksheets;
    raw event payloads are Admin-only;
  * a signed checksheet PDF is relayed only after this user is authorized for that checksheet of
    that visit, with no path, no public URL and no token in the query string.
"""

from datetime import datetime, timezone

import httpx
import pytest

import app.core.dependencies as dependencies_module
from app.clients.bldcms import BLDCMSClient, BLDCMSNotFoundError, BLDCMSUnavailableError
from app.db import models
from app.main import app
from app.services.bldcms_client import get_bldcms_client
from tests.conftest import (
    auth_header,
    ensure_section,
    make_booking,
    make_minor_stages,
    make_section_supervisor_headers,
    make_shed_visit,
    make_true_admin_headers,
    make_user,
)

LIST = "/api/shed-visit-history"
PDF = b"%PDF-1.7\n% signed\n%%EOF\n"
V_MINOR_OPEN, V_MAJOR_READY, V_SHED_OUT, V_RESET = 101, 102, 103, 104
M1, M2 = 1, 2
ADMIN_ID, SUP_M1_ID, SUP_M2_ID = 99, 11, 12
INTERNAL_KEY = "test-internal-key-not-for-production"


def _at(month, day, hour=8):
    return datetime(2026, month, day, hour, 0, tzinfo=timezone.utc)


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return [row["shed_visit_id"] for row in resp.json()["items"]]


def _signature():
    return {"signed": True, "signing_timestamp": "2026-09-10T10:00:00", "verification_status": "VALID"}


@pytest.fixture()
def world(db_session, client, mock_bldcms_client, mock_loco_client):
    db = db_session
    ensure_section(db, M1, "M1-HR")
    ensure_section(db, M2, "M2-HR")
    admin = make_true_admin_headers(db, user_id=ADMIN_ID)
    sup_m1 = make_section_supervisor_headers(db, SUP_M1_ID, M1, employee_id="SUPM1")
    sup_m2 = make_section_supervisor_headers(db, SUP_M2_ID, M2, employee_id="SUPM2")
    db.get(models.User, SUP_M1_ID).name = "Anil M1"
    db.commit()

    # Minor, open, Test Before done, schedule running.
    minor = make_shed_visit(db, V_MINOR_OPEN, "39015", schedule_variant="IA", arrival_at=_at(9, 10))
    stages = {s.stage_type: s for s in make_minor_stages(db, minor.id, base_id=1010)}
    stages["TEST_BEFORE"].status = "COMPLETED"
    stages["TEST_BEFORE"].started_at = _at(9, 10, 9)
    stages["TEST_BEFORE"].started_by = SUP_M1_ID
    stages["TEST_BEFORE"].completed_at = _at(9, 10, 10)
    stages["TEST_BEFORE"].completed_by = SUP_M1_ID
    minor.schedule_started_at = _at(9, 10, 12)
    db.commit()

    # Major, READY.
    major = make_shed_visit(db, V_MAJOR_READY, "30476", schedule_family="MAJOR", schedule_variant="TOH",
                            arrival_at=_at(9, 5))
    major.schedule_started_at = _at(9, 6)
    major.ready_at = _at(9, 12)
    major.status = "READY"
    db.commit()

    # Minor, closed by a normal Shed Out.
    make_shed_visit(db, V_SHED_OUT, "22560", status="CLOSED", schedule_variant="IB",
                    arrival_at=_at(8, 1), departed_at=_at(8, 3), departure_source="DASHBOARD")

    # Minor, closed by the administrative reset script - an earlier visit of the same locomotive.
    make_shed_visit(db, V_RESET, "39015", status="CLOSED", arrival_at=_at(9, 1),
                    departed_at=_at(9, 2), departure_source="SYSTEM")
    db.add(models.ShedVisitEvent(
        shed_visit_id=V_RESET, event_type="MANUAL_CORRECTION", event_time=_at(9, 2), source="SYSTEM",
        remarks="Production go-live reset", created_by=None, created_at=_at(9, 2),
        event_data={"action": "ADMIN_RESET_ACTIVE_VISIT", "reason": "Production go-live reset"},
    ))
    db.add(models.ShedVisitEvent(
        shed_visit_id=V_MINOR_OPEN, event_type="SHED_IN", event_time=_at(9, 10), source="DASHBOARD",
        created_by=SUP_M1_ID, created_at=_at(9, 10), event_data={"loco_number": "39015"},
    ))
    db.commit()

    # Bookings on the open Minor visit:
    #   701 routed to M1 (automatically; attended then reopened) AND to M2 (manually, by Admin)
    #   702 routed to M2 only
    #   703 not routed anywhere
    mock_loco_client.add_node(1843, 1, None, "Pantograph")
    make_booking(db, 701, V_MINOR_OPEN, created_by=SUP_M1_ID, status="REOPENED", description="Pan arcing")
    make_booking(db, 702, V_MINOR_OPEN, equipment_node_id=9999, status="OPEN", description="Other section")
    make_booking(db, 703, V_MINOR_OPEN, status="OPEN", description="Unrouted")
    now = _at(9, 10, 13)
    db.add_all([
        models.BookingSectionAssignment(
            id=801, booking_id=701, section_id=M1, assignment_source="AUTO_MAPPING", status="REOPENED",
            assigned_at=now, started_at=now, started_by=SUP_M1_ID, attended_at=now, attended_by=SUP_M1_ID,
            attendance_remarks="Carbon replaced", updated_at=now),
        models.BookingSectionAssignment(
            id=802, booking_id=701, section_id=M2, assignment_source="MANUAL", status="OPEN",
            assigned_at=now, assigned_by=ADMIN_ID, updated_at=now),
        models.BookingSectionAssignment(
            id=803, booking_id=702, section_id=M2, assignment_source="AUTO_MAPPING", status="OPEN",
            assigned_at=now, updated_at=now),
    ])
    db.add_all([
        models.BookingEvent(booking_id=701, event_type="CREATED", created_by=SUP_M1_ID, created_at=now,
                            event_data={"origin": "BLDCMS_CHECKSHEET", "checksheet_id": 501}),
        models.BookingEvent(booking_id=701, event_type="AUTO_ROUTED", to_section_id=M1, created_at=now),
        models.BookingEvent(booking_id=701, event_type="STARTED", to_section_id=M2, created_by=SUP_M2_ID,
                            created_at=now),
        models.BookingEvent(booking_id=701, event_type="REOPENED", to_section_id=M1, created_by=ADMIN_ID,
                            remarks="Arcing again", created_at=_at(9, 10, 15)),
    ])
    db.commit()

    # BL-DCMS: models and checksheets.
    bl = mock_bldcms_client
    bl.locomotive_models.update({"39015": "WAP-7", "30476": "WAG9HC", "22560": "WAP-4"})
    bl.add_checksheet(V_MINOR_OPEN, checksheet_id=501, template_id=1, section_id=M1, section_name="M1-HR",
                      workflow_stage_type="TEST_BEFORE", status="APPROVED", is_final=True,
                      digital_signature=_signature(), signed_document_available=True,
                      signed_by_name="Anil M1", approved_by_name="Anil M1", created_by_name="Tech Ravi")
    bl.add_checksheet(V_MINOR_OPEN, checksheet_id=502, template_id=2, section_id=M2, section_name="M2-HR",
                      workflow_stage_type="SCHEDULE_INSPECTION", status="SUBMITTED")
    bl.add_checksheet(V_MINOR_OPEN, checksheet_id=503, template_id=3, section_id=M1, section_name="M1-HR",
                      schedule_family="TI", schedule_variant="TI", workflow_stage_type=None)
    bl.add_checksheet(V_MINOR_OPEN, checksheet_id=504, template_id=4, section_id=M1,
                      workflow_stage_type="TEST_AFTER", status="APPROVED", digital_signature=_signature(),
                      signed_document_available=False)
    bl.add_checksheet(V_MINOR_OPEN, checksheet_id=505, template_id=5, section_id=M2,
                      workflow_stage_type="SCHEDULE_INSPECTION", status="APPROVED",
                      digital_signature=_signature(), signed_document_available=True)
    bl.add_checksheet(V_MINOR_OPEN, checksheet_id=506, template_id=6, section_id=M1,
                      workflow_stage_type="SCHEDULE_INSPECTION", status="APPROVED",
                      digital_signature=_signature(), signed_document_available=True)
    bl.add_checksheet(V_MAJOR_READY, checksheet_id=601, template_id=7, section_id=M1, schedule_family="MAJOR",
                      schedule_variant="TOH", workflow_stage_type=None, maintenance_type="OVERHAUL",
                      status="APPROVED", digital_signature=_signature(), signed_document_available=True)
    bl.add_checksheet(V_SHED_OUT, checksheet_id=701, template_id=1, section_id=M1, status="APPROVED",
                      digital_signature=_signature(), signed_document_available=True, schedule_variant="IB")
    bl.signed_documents.update({(V_MINOR_OPEN, 501): PDF, (V_MINOR_OPEN, 505): PDF,
                                (V_MAJOR_READY, 601): PDF, (V_SHED_OUT, 701): PDF})
    # 506 claims a signed document that BL-DCMS then cannot find.

    return {"db": db, "client": client, "bl": bl, "admin": admin, "sup_m1": sup_m1, "sup_m2": sup_m2}


def _get(world, url, who="admin", **params):
    return world["client"].get(url, headers=world[who], params=params)


def _detail(world, visit_id, who="admin"):
    resp = _get(world, f"{LIST}/{visit_id}", who)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ================================================================================ the list ==

def test_1_every_visit_is_listed_newest_first(world):
    resp = _get(world, LIST)
    assert _ids(resp) == [V_MINOR_OPEN, V_MAJOR_READY, V_RESET, V_SHED_OUT]
    assert resp.json()["total"] == 4


def test_2_an_admin_reset_is_labelled_as_such_and_never_as_a_shed_out(world):
    rows = {r["shed_visit_id"]: r for r in _get(world, LIST).json()["items"]}
    reset = rows[V_RESET]
    assert reset["closure"]["kind"] == "ADMIN_RESET"
    assert reset["display_label"] == "Closed administratively (system reset)"
    assert reset["closure"]["reason"] == "Production go-live reset"
    assert "Shed Out" not in reset["display_label"]
    assert rows[V_SHED_OUT]["closure"]["kind"] == "SHED_OUT"
    assert rows[V_MINOR_OPEN]["closure"] is None and rows[V_MAJOR_READY]["closure"] is None


def test_3_summary_rows_carry_model_counts_and_timestamps(world):
    rows = {r["shed_visit_id"]: r for r in _get(world, LIST).json()["items"]}
    row = rows[V_MINOR_OPEN]
    assert (row["loco_number"], row["loco_model"], row["technology"]) == ("39015", "WAP-7", "3_PHASE")
    assert (row["schedule_family"], row["schedule_variant"], row["status"]) == ("MINOR", "IA", "IN_SHED")
    assert row["booking_count"] == 3
    assert row["checksheet_count"] == 6
    assert row["arrival_at"].startswith("2026-09-10")
    assert row["departed_at"] is None
    assert rows[V_MAJOR_READY]["ready_at"].startswith("2026-09-12")
    assert rows[V_SHED_OUT]["booking_count"] == 0 and rows[V_SHED_OUT]["checksheet_count"] == 1


def test_4_pagination_is_server_side(world):
    resp = _get(world, LIST, page=2, page_size=2)
    assert _ids(resp) == [V_RESET, V_SHED_OUT]
    body = resp.json()
    assert (body["total"], body["page"], body["page_size"]) == (4, 2, 2)
    assert _ids(_get(world, LIST, page=3, page_size=2)) == []
    assert _get(world, LIST, page_size=101).status_code == 422
    assert _get(world, LIST, page=0).status_code == 422


def test_5_filter_by_loco_number(world):
    assert _ids(_get(world, LIST, loco_number="390")) == [V_MINOR_OPEN, V_RESET]
    assert _ids(_get(world, LIST, loco_number="22560")) == [V_SHED_OUT]


def test_6_filter_by_loco_model_is_resolved_by_bldcms_and_normalized(world):
    assert _ids(_get(world, LIST, loco_model="wap 7")) == [V_MINOR_OPEN, V_RESET]
    assert _ids(_get(world, LIST, loco_model="WAG9HC")) == [V_MAJOR_READY]
    assert _ids(_get(world, LIST, loco_model="WAP-5")) == []


def test_7_filter_by_family_and_variant(world):
    assert _ids(_get(world, LIST, schedule_family="major")) == [V_MAJOR_READY]
    assert _ids(_get(world, LIST, schedule_family="MINOR", schedule_variant="ib")) == [V_SHED_OUT]
    assert _ids(_get(world, LIST, schedule_family="TI")) == []


def test_8_filter_by_status_and_active(world):
    assert _ids(_get(world, LIST, status="READY")) == [V_MAJOR_READY]
    assert _ids(_get(world, LIST, active="true")) == [V_MINOR_OPEN, V_MAJOR_READY]
    assert _ids(_get(world, LIST, active="false")) == [V_RESET, V_SHED_OUT]
    assert _get(world, LIST, status="DELETED").status_code == 422


def test_9_filter_by_visit_id(world):
    assert _ids(_get(world, LIST, visit_id=V_SHED_OUT)) == [V_SHED_OUT]
    assert _ids(_get(world, LIST, visit_id=99999)) == []


def test_10_filter_by_dates_and_departure_source(world):
    assert _ids(_get(world, LIST, arrived_from="2026-09-01", arrived_to="2026-09-06")) == [V_MAJOR_READY, V_RESET]
    assert _ids(_get(world, LIST, departed_from="2026-08-01", departed_to="2026-08-31")) == [V_SHED_OUT]
    # "In shed on or after": still open, or departed on/after the date.
    assert _ids(_get(world, LIST, date_from="2026-09-03")) == [V_MINOR_OPEN, V_MAJOR_READY]
    assert _ids(_get(world, LIST, date_to="2026-08-31")) == [V_SHED_OUT]
    assert _ids(_get(world, LIST, departure_source="system")) == [V_RESET]
    assert _get(world, LIST, arrived_from="not-a-date").status_code == 422


def test_11_filter_by_section_and_booking_status(world):
    assert _ids(_get(world, LIST, section_id=M2)) == [V_MINOR_OPEN]
    assert _ids(_get(world, LIST, booking_status="reopened")) == [V_MINOR_OPEN]
    assert _ids(_get(world, LIST, booking_status="ATTENDED")) == []


def test_12_bldcms_down_still_lists_with_unknown_models_and_counts(world):
    world["bl"].unavailable = True
    resp = _get(world, LIST)
    assert _ids(resp) == [V_MINOR_OPEN, V_MAJOR_READY, V_RESET, V_SHED_OUT]
    body = resp.json()
    assert body["locomotive_details_available"] is False
    assert body["checksheet_counts_available"] is False
    assert all(r["loco_model"] is None and r["checksheet_count"] is None for r in body["items"])
    # Filtering by model cannot be answered without BL-DCMS - say so, don't return "no matches".
    resp = _get(world, LIST, loco_model="WAP-7")
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "BLDCMS_UNAVAILABLE"
    assert _get(world, f"{LIST}/loco-models").status_code == 502


def test_13_bldcms_is_asked_once_per_page_never_per_row(world, monkeypatch):
    calls = {"briefs": 0, "counts": 0, "checksheets": 0}
    bl = world["bl"]
    for name, key in (("get_locomotive_briefs", "briefs"), ("count_visit_checksheets", "counts"),
                      ("get_visit_checksheets", "checksheets")):
        original = getattr(bl, name)

        def wrapped(*a, _original=original, _key=key, **k):
            calls[_key] += 1
            return _original(*a, **k)

        monkeypatch.setattr(bl, name, wrapped)

    _ids(_get(world, LIST))
    assert calls == {"briefs": 1, "counts": 1, "checksheets": 0}


def test_14_a_supervisors_counts_cover_only_their_own_section(world):
    rows = {r["shed_visit_id"]: r for r in _get(world, LIST, who="sup_m1").json()["items"]}
    # Every visit is still listed - like the live register.
    assert set(rows) == {V_MINOR_OPEN, V_MAJOR_READY, V_RESET, V_SHED_OUT}
    assert rows[V_MINOR_OPEN]["booking_count"] == 1        # 701 only
    assert rows[V_MINOR_OPEN]["checksheet_count"] == 4     # 501, 503, 504, 506
    rows = {r["shed_visit_id"]: r for r in _get(world, LIST, who="sup_m2").json()["items"]}
    assert rows[V_MINOR_OPEN]["booking_count"] == 2        # 701, 702
    assert rows[V_MINOR_OPEN]["checksheet_count"] == 2     # 502, 505


def test_15_loco_models_come_from_bldcms(world):
    resp = _get(world, f"{LIST}/loco-models")
    assert resp.status_code == 200
    assert resp.json() == ["WAG9HC", "WAP-4", "WAP-7"]


# ============================================================================== the detail ==

def test_16_minor_timings_list_every_milestone_and_leave_unrecorded_ones_empty(world):
    timings = _detail(world, V_MINOR_OPEN)["timings"]
    milestones = {m["key"]: m for m in timings["milestones"]}
    assert list(milestones) == [
        "arrival", "test_before_started", "test_before_completed", "schedule_started",
        "inspection_completed", "test_after_started", "test_after_completed", "ready", "departed",
    ]
    assert milestones["test_before_completed"]["actor_name"] == "Anil M1"
    for key in ("inspection_completed", "test_after_started", "test_after_completed", "ready", "departed"):
        assert milestones[key]["at"] is None, key            # not recorded - never a fabricated time
    spans = {s["key"]: s for s in timings["spans"]}
    assert spans["test_before"]["seconds"] == 3600
    assert spans["test_after"]["seconds"] is None            # not recorded - never 0
    assert spans["total_dwell"]["running"] is True           # the visit is still open


def test_17_major_timings_show_only_what_applies_to_major(world):
    timings = _detail(world, V_MAJOR_READY)["timings"]
    assert [m["key"] for m in timings["milestones"]] == ["arrival", "schedule_started", "ready", "departed"]
    spans = {s["key"]: s for s in timings["spans"]}
    assert "schedule" in spans and "test_before" not in spans and "inspection" not in spans
    assert spans["schedule"]["seconds"] == 6 * 86400


def test_18_a_reset_visit_detail_says_closed_administratively_and_never_shed_out(world):
    detail = _detail(world, V_RESET)
    departed = detail["timings"]["milestones"][-1]
    assert departed["label"] == "Closed administratively (system reset)"
    assert departed["note"] == "Not a Shed Out"
    sentences = [e["sentence"] for e in detail["events"]]
    assert "Closed administratively by a system reset - not a Shed Out" in sentences
    # A closed visit never shows a span "still running" up to today.
    assert all(s["running"] is False for s in detail["timings"]["spans"])
    assert {s["key"]: s for s in detail["timings"]["spans"]}["total_dwell"]["seconds"] == 86400


def test_19_bookings_are_grouped_by_section_with_full_history(world):
    detail = _detail(world, V_MINOR_OPEN)
    assert detail["booking_scope"] == "ALL_SECTIONS"
    groups = {g["section_name"]: g for g in detail["bookings"]}
    assert list(groups) == ["M1-HR", "M2-HR", "No responsible section"]
    assert [b["booking_id"] for b in groups["M1-HR"]["bookings"]] == [701]
    assert [b["booking_id"] for b in groups["M2-HR"]["bookings"]] == [701, 702]   # 701 under both
    assert [b["booking_id"] for b in groups["No responsible section"]["bookings"]] == [703]

    booking = groups["M1-HR"]["bookings"][0]
    assert booking["created_by_name"] == "Anil M1"
    assert booking["origin_checksheet_id"] == 501
    assert booking["equipment"] == {"node_id": 1843, "name": "Pantograph", "path": []}
    assert booking["booking_source_label"] == "Log Book"
    assignments = {a["section_name"]: a for a in booking["assignments"]}
    assert assignments["M1-HR"]["assigned_by_name"] == "System (automatic routing)"
    assert assignments["M1-HR"]["attended_by_name"] == "Anil M1"
    assert assignments["M1-HR"]["attendance_remarks"] == "Carbon replaced"
    assert assignments["M1-HR"]["reopens"] == [
        {"at": assignments["M1-HR"]["reopens"][0]["at"], "by_name": "Technical Admin", "reason": "Arcing again"}]
    assert assignments["M2-HR"]["assigned_by_name"] == "Technical Admin"
    assert assignments["M2-HR"]["reopens"] == []
    sentences = [e["sentence"] for e in booking["history"]]
    assert sentences == [
        "Booking raised by Anil M1 from a BL-DCMS checksheet",
        "Routed to M1-HR",
        "Work started in M2-HR by Section Supervisor",
        "Reopened for M1-HR by Technical Admin",
    ]

    unresolved = groups["M2-HR"]["bookings"][1]
    assert unresolved["equipment"]["node_id"] == 9999 and unresolved["equipment"]["name"] is None


def test_20_a_supervisor_sees_only_their_own_sections_bookings_events_and_checksheets(world):
    detail = _detail(world, V_MINOR_OPEN, who="sup_m1")
    assert detail["booking_scope"] == "OWN_SECTION"
    assert [g["section_name"] for g in detail["bookings"]] == ["M1-HR"]
    booking = detail["bookings"][0]["bookings"][0]
    assert booking["booking_id"] == 701
    assert [a["section_name"] for a in booking["assignments"]] == ["M1-HR"]
    assert booking["responsible_sections"] == ["M1-HR"]
    assert all("M2-HR" not in e["sentence"] for e in booking["history"])
    assert sorted(c["checksheet_id"] for c in detail["checksheets"]["items"]) == [501, 503, 504, 506]
    assert detail["visit"]["booking_count"] == 1
    # The visit itself - timings and visit events - stays visible, exactly like the live register.
    assert detail["timings"]["milestones"][0]["at"] is not None
    assert [e["event_type"] for e in detail["events"]] == ["SHED_IN"]


def test_21_raw_event_payloads_are_for_admin_only(world):
    admin = _detail(world, V_RESET)
    assert admin["events"][0]["raw"]["action"] == "ADMIN_RESET_ACTIVE_VISIT"
    admin_open = _detail(world, V_MINOR_OPEN)
    assert admin_open["bookings"][0]["bookings"][0]["history"][0]["raw"]["checksheet_id"] == 501

    for who in ("sup_m1", "sup_m2"):
        body = _detail(world, V_RESET, who)
        assert all(e["raw"] is None for e in body["events"])
        body = _detail(world, V_MINOR_OPEN, who)
        for group in body["bookings"]:
            for booking in group["bookings"]:
                assert all(e["raw"] is None for e in booking["history"])


def test_22_checksheets_are_grouped_and_future_families_are_not_dropped(world):
    items = {c["checksheet_id"]: c for c in _detail(world, V_MINOR_OPEN)["checksheets"]["items"]}
    assert items[501]["workflow_group"] == "TEST_BEFORE"
    assert items[502]["workflow_group"] == "SCHEDULE_INSPECTION"
    assert items[504]["workflow_group"] == "TEST_AFTER"
    assert items[503]["workflow_group"] == "TI"
    major = _detail(world, V_MAJOR_READY)["checksheets"]["items"]
    assert [(c["workflow_group"], c["maintenance_type"]) for c in major] == [("MAJOR", "OVERHAUL")]

    signed = items[501]
    assert signed["signed"] is True and signed["signed_by_name"] == "Anil M1"
    assert signed["approved_by_name"] == "Anil M1" and signed["created_by_name"] == "Tech Ravi"
    assert signed["signature_verification"] == "VALID"
    assert signed["signed_document_url"] == f"{LIST}/{V_MINOR_OPEN}/checksheets/501/signed-document"
    # Signed, but BL-DCMS has no file: no link is offered.
    assert items[504]["signed"] is True and items[504]["signed_document_url"] is None
    assert items[502]["signed_document_url"] is None


def test_23_requirement_flags_annotate_but_never_hide_a_checksheet(world):
    db = world["db"]
    now = datetime.now(timezone.utc)
    db.add(models.ShedVisitChecksheetPackage(id=9001, shed_visit_id=V_MINOR_OPEN, generated_at=now))
    db.commit()
    db.add(models.ShedVisitChecksheetRequirement(
        package_id=9001, workflow_stage_type="SCHEDULE_INSPECTION", applicability_id=1, template_id=2,
        requirement_source="APPLICABILITY", is_required=False, is_active=False,
        template_name_snapshot="T2", technology_snapshot="3_PHASE", section_id_snapshot=M2,
        section_name_snapshot="M2-HR", equipment_id_snapshot=None, equipment_name_snapshot=None,
        maintenance_type_snapshot=None, created_at=now, updated_at=now,
    ))
    db.commit()
    items = {c["checksheet_id"]: c for c in _detail(world, V_MINOR_OPEN)["checksheets"]["items"]}
    assert items[502]["requirement"] == {"is_required": False, "is_active": False}
    assert items[501]["requirement"] is None
    assert len(items) == 6


def test_24_bldcms_down_leaves_the_rest_of_the_detail_readable(world):
    world["bl"].unavailable = True
    detail = _detail(world, V_MINOR_OPEN)
    assert detail["checksheets"]["available"] is False
    assert detail["checksheets"]["items"] == []
    assert "could not be read" in detail["checksheets"]["message"]
    assert detail["visit"]["loco_model"] is None
    assert detail["visit"]["checksheet_count"] is None
    assert len(detail["bookings"]) == 3


def test_25_detail_names_the_event_log_filter_and_404s_an_unknown_visit(world):
    assert _detail(world, V_SHED_OUT)["event_log_filter"] == {"shed_visit_id": V_SHED_OUT}
    assert _get(world, f"{LIST}/99999").status_code == 404


# =========================================================================== authorization ==

def test_26_history_requires_an_operations_user(world):
    client, db = world["client"], world["db"]
    assert client.get(LIST).status_code == 401
    assert client.get(f"{LIST}/{V_MINOR_OPEN}").status_code == 401
    assert client.get(LIST, params={"token": world["admin"]["Authorization"][7:]}).status_code == 401

    make_user(db, 40, "TECH40", "Tech", "Technician", "hash", section_id=M1)
    assert client.get(LIST, headers=auth_header("TECH40", "Technician", 40)).status_code == 403

    # An active Supervisor with no entitlement now has base access, by role. What still denies
    # them is being a Technician (above) or not being authenticated at all.
    no_entitlement = make_section_supervisor_headers(db, 41, M1, employee_id="NOENT", is_enabled=False)
    assert client.get(LIST, headers=no_entitlement).status_code == 200


def test_27_a_supervisor_without_a_section_cannot_be_scoped_and_is_refused(world):
    db = world["db"]
    headers = make_section_supervisor_headers(db, 42, M1, employee_id="NOSEC")
    db.get(models.User, 42).section_id = None
    db.commit()
    assert world["client"].get(LIST, headers=headers).status_code == 403
    assert world["client"].get(f"{LIST}/{V_MINOR_OPEN}", headers=headers).status_code == 403


# ====================================================================== the signed document ==

def _doc(world, visit_id, checksheet_id, who="admin"):
    return _get(world, f"{LIST}/{visit_id}/checksheets/{checksheet_id}/signed-document", who)


def _assert_pdf(resp, checksheet_id):
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == PDF
    assert resp.headers["content-disposition"] == f'inline; filename="checksheet_{checksheet_id}_signed.pdf"'
    assert resp.headers["cache-control"] == "private, no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "storage" not in " ".join(resp.headers.values())


def test_28_admin_may_open_any_signed_checksheet(world):
    _assert_pdf(_doc(world, V_MINOR_OPEN, 505), 505)
    _assert_pdf(_doc(world, V_MAJOR_READY, 601), 601)
    assert world["bl"].signed_document_requests == [(V_MINOR_OPEN, 505), (V_MAJOR_READY, 601)]


def test_29_a_supervisor_may_open_their_own_sections_signed_checksheet(world):
    _assert_pdf(_doc(world, V_MINOR_OPEN, 501, "sup_m1"), 501)


def test_30_another_sections_supervisor_is_refused_before_anything_is_fetched(world):
    resp = _doc(world, V_MINOR_OPEN, 501, "sup_m2")
    assert resp.status_code == 403
    assert world["bl"].signed_document_requests == []


def test_31_a_checksheet_of_another_visit_is_not_found_through_this_visit(world):
    # 601 exists and is signed - but it belongs to the Major visit.
    assert _doc(world, V_MINOR_OPEN, 601).status_code == 404
    assert _doc(world, V_MINOR_OPEN, 99999).status_code == 404
    assert _doc(world, 99999, 501).status_code == 404
    assert world["bl"].signed_document_requests == []


def test_32_an_unsigned_checksheet_has_no_document_and_nothing_is_fetched(world):
    resp = _doc(world, V_MINOR_OPEN, 502)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SIGNED_DOCUMENT_NOT_AVAILABLE"
    resp = _doc(world, V_MINOR_OPEN, 504)          # signed, file known missing
    assert resp.json()["detail"]["code"] == "SIGNED_DOCUMENT_NOT_AVAILABLE"
    assert world["bl"].signed_document_requests == []


def test_33_a_document_bldcms_cannot_find_is_a_clear_404(world):
    resp = _doc(world, V_MINOR_OPEN, 506)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SIGNED_DOCUMENT_NOT_AVAILABLE"


def test_34_bldcms_unreachable_is_a_502_not_a_404(world):
    world["bl"].unavailable = True
    resp = _doc(world, V_MINOR_OPEN, 501)
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "BLDCMS_UNAVAILABLE"


def test_35_bldcms_not_configured_is_a_502(world):
    app.dependency_overrides[get_bldcms_client] = lambda: None
    assert _doc(world, V_MINOR_OPEN, 501).status_code == 502
    detail = _detail(world, V_MINOR_OPEN)
    assert detail["checksheets"]["available"] is False


def test_36_a_closed_visits_signed_document_stays_available(world):
    _assert_pdf(_doc(world, V_SHED_OUT, 701, "sup_m1"), 701)


def test_37_the_document_needs_a_real_authorization_header(world):
    client = world["client"]
    url = f"{LIST}/{V_MINOR_OPEN}/checksheets/501/signed-document"
    assert client.get(url).status_code == 401
    token = world["admin"]["Authorization"].split(" ", 1)[1]
    assert client.get(url, params={"token": token}).status_code == 401
    assert client.get(url, params={"access_token": token}).status_code == 401
    assert world["bl"].signed_document_requests == []


# =================================================================== internal (BL-DCMS) ==

class _StubSettings:
    operations_internal_api_key = INTERNAL_KEY


@pytest.fixture()
def internal(monkeypatch):
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: _StubSettings())
    return {"X-Internal-API-Key": INTERNAL_KEY}


INTERNAL = "/api/internal/shed-visit-history"


def test_38_internal_history_requires_the_service_key(world, internal):
    client = world["client"]
    assert client.get(INTERNAL).status_code == 401
    assert client.get(INTERNAL, headers={"X-Internal-API-Key": "wrong"}).status_code == 401
    assert client.get(f"{INTERNAL}/{V_MINOR_OPEN}").status_code == 401
    # A user's token is not a service key.
    assert client.get(INTERNAL, headers=world["admin"]).status_code == 401


def test_39_internal_history_applies_the_scope_bldcms_passes(world, internal):
    client = world["client"]
    rows = {r["shed_visit_id"]: r for r in client.get(INTERNAL, headers=internal).json()["items"]}
    assert rows[V_MINOR_OPEN]["booking_count"] == 3
    scoped = client.get(INTERNAL, headers=internal, params={"scope_section_id": M2, "active": "true"})
    assert _ids(scoped) == [V_MINOR_OPEN, V_MAJOR_READY]
    assert scoped.json()["items"][0]["booking_count"] == 2

    detail = client.get(f"{INTERNAL}/{V_MINOR_OPEN}", headers=internal, params={"scope_section_id": M1}).json()
    assert [g["section_name"] for g in detail["bookings"]] == ["M1-HR"]
    assert detail["booking_scope"] == "OWN_SECTION"


def test_40_internal_detail_leaves_checksheets_to_bldcms_and_raw_is_opt_in(world, internal, monkeypatch):
    calls = []
    monkeypatch.setattr(world["bl"], "get_visit_checksheets", lambda *a, **k: calls.append(a) or {"items": []})
    client = world["client"]
    detail = client.get(f"{INTERNAL}/{V_RESET}", headers=internal).json()
    assert detail["checksheets"]["available"] is False
    assert calls == []
    assert detail["events"][0]["raw"] is None
    raw = client.get(f"{INTERNAL}/{V_RESET}", headers=internal, params={"include_raw": "true"}).json()
    assert raw["events"][0]["raw"]["action"] == "ADMIN_RESET_ACTIVE_VISIT"


# ======================================================================== the HTTP client ==

@pytest.fixture()
def patch_httpx(monkeypatch):
    def _install(handler):
        transport = httpx.MockTransport(handler)

        def patched_request(method, url, **kwargs):
            with httpx.Client(transport=transport) as c:
                return c.request(method, url, **kwargs)

        import app.clients.bldcms as module

        monkeypatch.setattr(module.httpx, "request", patched_request)

    return _install


def test_41_client_fetches_the_signed_document_by_visit_and_checksheet(patch_httpx):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})

    patch_httpx(handler)
    content = BLDCMSClient(base_url="http://bldcms.invalid", api_key="k").get_signed_document(101, 501)
    assert content == PDF
    assert seen["url"] == "http://bldcms.invalid/integration/checksheets/visit/101/checksheet/501/signed-document"
    assert "?" not in seen["url"]


def test_42_client_maps_404_and_refuses_a_non_pdf(patch_httpx):
    patch_httpx(lambda request: httpx.Response(404, json={"detail": "nope"}))
    with pytest.raises(BLDCMSNotFoundError):
        BLDCMSClient(base_url="http://bldcms.invalid").get_signed_document(101, 501)

    patch_httpx(lambda request: httpx.Response(200, text="<html>login</html>",
                                               headers={"content-type": "text/html"}))
    with pytest.raises(BLDCMSUnavailableError):
        BLDCMSClient(base_url="http://bldcms.invalid").get_signed_document(101, 501)


def test_43_client_batches_counts_and_briefs(patch_httpx):
    seen = []

    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("visit-counts"):
            return httpx.Response(200, json=[{"shed_visit_id": 1, "checksheets": 2},
                                             {"shed_visit_id": 2, "checksheets": 0}])
        return httpx.Response(200, json=[{"loco_number": "39015", "loco_model": "WAP-7", "technology": "3_PHASE"}])

    patch_httpx(handler)
    client = BLDCMSClient(base_url="http://bldcms.invalid")
    assert client.count_visit_checksheets([1, 2], section_id=5) == {1: 2, 2: 0}
    assert client.get_locomotive_briefs(loco_numbers=["39015", "30476"])[0]["loco_model"] == "WAP-7"
    assert client.count_visit_checksheets([]) == {}
    assert seen == [
        ("/integration/checksheets/visit-counts", {"shed_visit_ids": "1,2", "section_id": "5"}),
        ("/integration/locomotives", {"loco_numbers": "39015,30476"}),
    ]


def test_44_internal_detail_carries_the_requirement_flags_for_bldcms(world, internal):
    db = world["db"]
    now = datetime.now(timezone.utc)
    db.add(models.ShedVisitChecksheetPackage(id=9002, shed_visit_id=V_MINOR_OPEN, generated_at=now))
    db.commit()
    db.add(models.ShedVisitChecksheetRequirement(
        package_id=9002, workflow_stage_type="SCHEDULE_INSPECTION", applicability_id=1, template_id=2,
        requirement_source="APPLICABILITY", is_required=False, is_active=True,
        template_name_snapshot="T2", technology_snapshot="3_PHASE", section_id_snapshot=M2,
        section_name_snapshot="M2-HR", equipment_id_snapshot=None, equipment_name_snapshot=None,
        maintenance_type_snapshot=None, created_at=now, updated_at=now,
    ))
    db.commit()
    body = world["client"].get(f"{INTERNAL}/{V_MINOR_OPEN}", headers=internal).json()
    assert body["requirement_flags"] == [{
        "template_id": 2, "workflow_stage_type": "SCHEDULE_INSPECTION", "section_id": M2,
        "equipment_id": None, "maintenance_type": None, "minor_inspection_equipment_id": None,
        "is_required": False, "is_active": True,
    }]
    # The user route composes checksheets itself and does not repeat the flags.
    assert _detail(world, V_MINOR_OPEN)["requirement_flags"] == []
    assert world["client"].get(f"{INTERNAL}/{V_SHED_OUT}", headers=internal).json()["requirement_flags"] == []


def test_45_a_supervisor_cannot_probe_other_sections_through_filters(world, internal):
    # Booking 701 (REOPENED) is routed to M1 and M2; 702 (OPEN) only to M2.
    assert _get(world, LIST, who="sup_m1", section_id=M2).status_code == 403
    assert _ids(_get(world, LIST, who="sup_m1", section_id=M1)) == [V_MINOR_OPEN]
    assert _ids(_get(world, LIST, who="sup_m1", booking_status="OPEN")) == []
    assert _ids(_get(world, LIST, who="sup_m2", booking_status="OPEN")) == [V_MINOR_OPEN]
    assert _ids(_get(world, LIST, who="sup_m1", booking_status="REOPENED")) == [V_MINOR_OPEN]
    assert _ids(_get(world, LIST, booking_status="OPEN")) == [V_MINOR_OPEN]          # Admin: unscoped
    # The internal route applies the same rule to the scope BL-DCMS passes.
    resp = world["client"].get(INTERNAL, headers=internal, params={"scope_section_id": M1, "section_id": M2})
    assert resp.status_code == 403
