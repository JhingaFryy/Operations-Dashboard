"""New shed workflow: Spare -> Schedule In Progress -> Ready -> Shed Out.

Covers phase derivation, both new transitions, every timestamp-ordering rule (including manually
edited timestamps), event recording, the timing metrics, and regression cover proving the Shed
Out gates and active-visit queries still behave.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.services.shed_visit_phase import (
    PHASE_INSPECTION_COMPLETED,
    PHASE_READY,
    PHASE_SCHEDULE_IN_PROGRESS,
    PHASE_SHED_OUT,
    PHASE_SPARE,
    compute_timings,
    derive_phase,
    phase_display_label,
)
from tests.conftest import (
    auth_header,
    grant_access,
    make_minor_stages,
    make_non_blocking_checksheet_package,
    make_shed_visit,
    make_movement_supervisor_headers,
    make_stage,
    make_user,
)

ARRIVAL = datetime(2026, 9, 1, 8, 32, tzinfo=timezone.utc)
STARTED = datetime(2026, 9, 1, 9, 10, tzinfo=timezone.utc)
COMPLETED = datetime(2026, 9, 1, 13, 48, tzinfo=timezone.utc)
DEPARTED = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def _headers(db_session):
    """Locomotive movement is restricted to SHIFT/PPIO Supervisors, so the actor driving this
    whole workflow is a movement Supervisor - not an Admin."""
    return make_movement_supervisor_headers(db_session)


def _visit(db_session, with_test_before=True, **kw):
    """A MINOR visit additionally gets a COMPLETED Test Before stage by default, because since
    migration 012 Start Schedule requires Test Before to be satisfied first."""
    visit = make_shed_visit(db_session, kw.pop("id", 900), kw.pop("loco_number", "39018"),
                            arrival_at=ARRIVAL, created_by=None, **kw)
    if with_test_before and visit.schedule_family == "MINOR":
        make_stage(db_session, visit.id * 10 + 1, visit.id, "TEST_BEFORE", 1, status="COMPLETED",
                   started_at=ARRIVAL, completed_at=ARRIVAL)
    return visit


def _all_stages_completed(db_session, visit):
    for stage in make_minor_stages(db_session, visit.id, base_id=9000):
        stage.status = "COMPLETED"
        stage.started_at = stage.completed_at = ARRIVAL
    db_session.commit()


def _mark_ready(client, headers, visit_id, at=COMPLETED + timedelta(minutes=30)):
    return client.post(f"/api/shed-visits/{visit_id}/mark-ready",
                       json={"ready_at": at.isoformat()}, headers=headers)


def _start(client, headers, visit_id, at=STARTED):
    return client.post(f"/api/shed-visits/{visit_id}/start-schedule",
                       json={"started_at": at.isoformat()}, headers=headers)


def _complete(client, headers, visit_id, at=COMPLETED):
    return client.post(f"/api/shed-visits/{visit_id}/complete-schedule",
                       json={"completed_at": at.isoformat()}, headers=headers)


# --------------------------------------------------------------- phase derivation --

@pytest.mark.parametrize("started,ready,departed,expected,label", [
    (None, None, None, PHASE_SPARE, "Spare IA"),
    (STARTED, None, None, PHASE_SCHEDULE_IN_PROGRESS, "IA In Progress"),
    (STARTED, COMPLETED, None, PHASE_READY, "Ready"),
    (STARTED, COMPLETED, DEPARTED, PHASE_SHED_OUT, "Shed Out"),
])
def test_phase_and_label_derivation(started, ready, departed, expected, label):
    phase = derive_phase(schedule_started_at=started, ready_at=ready, departed_at=departed)
    assert phase == expected
    assert phase_display_label(phase, "IA") == label


def test_major_variants_use_the_same_derivation():
    assert phase_display_label(PHASE_SPARE, "IOH") == "Spare IOH"
    assert phase_display_label(PHASE_SCHEDULE_IN_PROGRESS, "TOH") == "TOH In Progress"


# ------------------------------------------------------------------------ SHED IN --

def test_shed_in_creates_a_spare_visit(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type="WAG9HC")
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": "39018", "arrival_at": ARRIVAL.isoformat(),
        "schedule_family": "MINOR", "schedule_variant": "IA",
        "arrival_condition": "WORKING", "log_book_bookings": [],
    }, headers=headers)
    assert resp.status_code == 200, resp.text

    visit = db_session.query(models.ShedVisit).one()
    assert visit.status == "IN_SHED"
    assert visit.schedule_started_at is None and visit.ready_at is None and visit.departed_at is None

    listing = client.get("/api/shed-visits/current", headers=headers).json()
    row = next(r for r in listing if r["id"] == visit.id)
    assert row["operational_phase"] == PHASE_SPARE
    assert row["display_label"] == "Spare IA"
    assert row["available_actions"] == ["START_SCHEDULE"]


def test_shed_in_accepts_a_manually_edited_arrival_time(client, db_session, mock_loco_client):
    headers = _headers(db_session)
    mock_loco_client.add_locomotive("39018", loco_type="WAG9HC")
    backdated = datetime.now(timezone.utc) - timedelta(days=2)
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": "39018", "arrival_at": backdated.isoformat(),
        "schedule_family": "MINOR", "schedule_variant": "IA",
        "arrival_condition": "WORKING", "log_book_bookings": [],
    }, headers=headers)
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------- START SCHEDULE --

def test_start_schedule_succeeds_and_records_an_event(client, db_session, mock_loco_client):
    headers = _headers(db_session)
    visit = _visit(db_session)

    resp = _start(client, headers, visit.id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operational_phase"] == PHASE_SCHEDULE_IN_PROGRESS
    assert body["display_label"] == "IA In Progress"
    assert body["available_actions"] == ["COMPLETE_SCHEDULE"]

    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit.id)
    assert visit.schedule_started_at is not None
    # Physical status is unchanged - only the derived phase moved.
    assert visit.status == "IN_SHED"

    event = db_session.query(models.ShedVisitEvent).filter_by(event_type="SCHEDULE_STARTED").one()
    assert event.shed_visit_id == visit.id
    assert event.created_by == 1
    assert event.event_data["schedule_variant"] == "IA"


def test_start_schedule_accepts_a_manually_edited_timestamp(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session)
    edited = ARRIVAL + timedelta(minutes=5)
    assert _start(client, headers, visit.id, at=edited).status_code == 200


def test_cannot_start_schedule_twice(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session)
    assert _start(client, headers, visit.id).status_code == 200
    resp = _start(client, headers, visit.id)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SCHEDULE_ALREADY_STARTED"


def test_cannot_start_schedule_before_arrival(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session)
    resp = _start(client, headers, visit.id, at=ARRIVAL - timedelta(hours=1))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SCHEDULE_START_BEFORE_ARRIVAL"
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).schedule_started_at is None


def test_cannot_start_schedule_on_a_closed_visit(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session, status="CLOSED", departed_at=DEPARTED, departure_source="DASHBOARD")
    assert _start(client, headers, visit.id).status_code == 409


# -------------------------------------------------------------- COMPLETE SCHEDULE --

def test_complete_schedule_on_minor_completes_the_inspection_not_ready(client, db_session):
    """Migration 012: for MINOR, Complete Schedule is the end of the actual inspection - it writes
    inspection_completed_at, never ready_at, and Test After / Mark Ready come next."""
    headers = _headers(db_session)
    visit = _visit(db_session)
    _start(client, headers, visit.id)

    resp = _complete(client, headers, visit.id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["operational_phase"] == PHASE_INSPECTION_COMPLETED
    assert body["display_label"] == "IA Inspection Complete"
    assert body["available_actions"] == ["MARK_READY"]
    assert body["timings"]["schedule_seconds"] == (4 * 60 + 38) * 60

    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit.id)
    assert visit.inspection_completed_at is not None
    assert visit.ready_at is None
    assert visit.status == "IN_SHED"
    event = db_session.query(models.ShedVisitEvent).filter_by(event_type="SCHEDULE_COMPLETED").one()
    assert event.event_data["milestone"] == "INSPECTION_COMPLETED"


def test_complete_schedule_on_major_still_sets_ready(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session, schedule_family="MAJOR", schedule_variant="IOH")
    # Major becomes READY here, so this transition now carries the checksheet gate; this test's
    # subject is the phase change, so the requirement set is the neutral non-blocking one.
    make_non_blocking_checksheet_package(db_session, visit.id, stage=None)
    assert _start(client, headers, visit.id).status_code == 200   # no Test Before gate for MAJOR

    resp = _complete(client, headers, visit.id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["operational_phase"] == PHASE_READY
    assert resp.json()["available_actions"] == ["SHED_OUT"]
    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit.id)
    assert visit.ready_at is not None and visit.inspection_completed_at is None
    assert visit.status == "READY"


def test_cannot_complete_before_start(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session)
    resp = _complete(client, headers, visit.id)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SCHEDULE_NOT_STARTED"


def test_cannot_complete_twice(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session)
    _start(client, headers, visit.id)
    assert _complete(client, headers, visit.id).status_code == 200
    resp = _complete(client, headers, visit.id)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SCHEDULE_ALREADY_COMPLETED"


def test_cannot_complete_before_the_schedule_started(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session)
    _start(client, headers, visit.id)
    resp = _complete(client, headers, visit.id, at=STARTED - timedelta(minutes=1))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SCHEDULE_COMPLETE_BEFORE_START"
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).inspection_completed_at is None


# --------------------------------------------------------------------- SHED OUT --

def test_shed_out_still_enforces_its_gates_after_complete_schedule(
    client, db_session, mock_loco_client, mock_bldcms_client
):
    """READY means "schedule work done", NOT "cleared to depart". Completing a schedule must not
    let a locomotive out while its stage/booking/checksheet gates are unmet."""
    headers = _headers(db_session)
    visit = _visit(db_session, with_test_before=False)
    stages = make_minor_stages(db_session, visit.id, base_id=9000)
    stages[0].status, stages[0].completed_at = "COMPLETED", ARRIVAL   # Inspection / Test After PENDING
    db_session.commit()
    assert _start(client, headers, visit.id).status_code == 200
    assert _complete(client, headers, visit.id).status_code == 200

    resp = client.post(f"/api/shed-visits/{visit.id}/out",
                       json={"departed_at": DEPARTED.isoformat()}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SHED_OUT_BLOCKED"
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).status == "IN_SHED"


def test_shed_out_succeeds_once_gates_are_met(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _headers(db_session)
    visit = _visit(db_session, with_test_before=False)
    _all_stages_completed(db_session, visit)
    make_non_blocking_checksheet_package(db_session, visit.id)
    assert _start(client, headers, visit.id).status_code == 200
    assert _complete(client, headers, visit.id).status_code == 200
    assert _mark_ready(client, headers, visit.id).status_code == 200

    resp = client.post(f"/api/shed-visits/{visit.id}/out",
                       json={"departed_at": DEPARTED.isoformat()}, headers=headers)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    visit = db_session.get(models.ShedVisit, visit.id)
    assert visit.status == "CLOSED"
    assert visit.departed_at is not None
    assert derive_phase(schedule_started_at=visit.schedule_started_at, ready_at=visit.ready_at,
                        inspection_completed_at=visit.inspection_completed_at,
                        departed_at=visit.departed_at) == PHASE_SHED_OUT


def test_departure_before_ready_is_rejected(client, db_session, mock_loco_client, mock_bldcms_client):
    headers = _headers(db_session)
    visit = _visit(db_session, with_test_before=False)
    _all_stages_completed(db_session, visit)
    make_non_blocking_checksheet_package(db_session, visit.id)
    assert _start(client, headers, visit.id).status_code == 200
    assert _complete(client, headers, visit.id).status_code == 200
    assert _mark_ready(client, headers, visit.id, at=COMPLETED).status_code == 200

    resp = client.post(f"/api/shed-visits/{visit.id}/out",
                       json={"departed_at": (COMPLETED - timedelta(minutes=1)).isoformat()},
                       headers=headers)
    assert resp.status_code in (409, 422)
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).status == "READY"


# ---------------------------------------------------------------------- timings --

def test_timing_metrics():
    t = compute_timings(arrival_at=ARRIVAL, schedule_started_at=STARTED,
                        ready_at=COMPLETED, departed_at=DEPARTED)
    assert t.waiting_seconds == 38 * 60                      # 08:32 -> 09:10
    assert t.schedule_seconds == (4 * 60 + 38) * 60          # 09:10 -> 13:48
    assert t.ready_delay_seconds == (1 * 60 + 12) * 60       # 13:48 -> 15:00
    assert t.total_seconds == (6 * 60 + 28) * 60             # 08:32 -> 15:00
    assert not any([t.waiting_running, t.schedule_running, t.ready_delay_running, t.total_running])


def test_running_durations_are_flagged_and_measured_against_now():
    now = STARTED + timedelta(hours=2, minutes=14)
    t = compute_timings(arrival_at=ARRIVAL, schedule_started_at=STARTED,
                        ready_at=None, departed_at=None, now=now)
    assert t.waiting_seconds == 38 * 60 and not t.waiting_running   # closed span
    assert t.schedule_seconds == (2 * 60 + 14) * 60 and t.schedule_running
    assert t.ready_delay_seconds is None                            # not started yet
    assert t.total_running


def test_spare_visit_waiting_time_is_running():
    now = ARRIVAL + timedelta(hours=3)
    t = compute_timings(arrival_at=ARRIVAL, schedule_started_at=None, ready_at=None,
                        departed_at=None, now=now)
    assert t.waiting_seconds == 3 * 3600 and t.waiting_running
    assert t.schedule_seconds is None


# -------------------------------------------------------------------- regression --

def test_spare_and_in_progress_visits_remain_active_for_integrations(client, db_session):
    """Both phases keep status IN_SHED, so BL-DCMS's active-visit feed must still see them."""
    headers = _headers(db_session)
    spare = _visit(db_session, id=900, loco_number="39018")
    running = _visit(db_session, id=901, loco_number="30634")
    _start(client, headers, running.id)

    from app.services.shed_visit_service import list_active_visits
    active = {v.loco_number for v in list_active_visits(db_session)}
    assert active == {"39018", "30634"}

    rows = client.get("/api/shed-visits/current", headers=headers).json()
    phases = {r["loco_number"]: r["operational_phase"] for r in rows}
    assert phases == {"39018": PHASE_SPARE, "30634": PHASE_SCHEDULE_IN_PROGRESS}


def test_ready_visit_is_still_an_active_visit(client, db_session):
    headers = _headers(db_session)
    visit = _visit(db_session)
    _start(client, headers, visit.id)
    _complete(client, headers, visit.id)

    from app.services.shed_visit_service import list_active_visits
    assert [v.loco_number for v in list_active_visits(db_session)] == ["39018"]
