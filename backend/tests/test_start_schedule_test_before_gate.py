"""Start Schedule's MINOR precondition: Test Before completed OR Admin-skipped.

An audited Admin skip is sufficient on its own - no Test Before checksheet is required, and none
is fabricated. What these tests pin beyond that is the TIMESTAMP relationship, which is where the
production failure of 2026-09-18 actually lived: Test Before is skipped with a full-precision
server timestamp (09:27:25.649) while every operator-facing time in this app is entered at MINUTE
precision, so an Admin who skipped Test Before and immediately started the schedule in that same
minute sent started_at=09:27:00 and was refused SCHEDULE_START_BEFORE_TEST_BEFORE for a 25-second
ordering violation that existed only because the two values are recorded at different precisions.
A genuinely out-of-order start (an earlier minute, hour or day) is still refused.

Start Schedule deliberately does NOT require a checksheet work package: the gate is Test Before,
not the package. That is asserted here so the two are never quietly conflated.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from tests.conftest import ensure_section, make_movement_supervisor_headers, make_true_admin_headers

SHIFT, M1 = 18, 9
IST = timezone(timedelta(hours=5, minutes=30))


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def env(db_session, mock_loco_client, mock_bldcms_client):
    ensure_section(db_session, id=SHIFT, code="SHIFT", name="SHIFT")
    ensure_section(db_session, id=M1, code="M1-HR", name="M1-HR")
    mock_loco_client.add_locomotive("41771", loco_type="WAG9H")
    for stage in ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"):
        mock_bldcms_client.add_applicability(
            technology="WAG9H", schedule_family="MINOR", schedule_variant="IA0",
            workflow_stage_type=stage, template_id=179,
            section_id=SHIFT if stage != "SCHEDULE_INSPECTION" else M1)
    return {"movement": make_movement_supervisor_headers(db_session),
            "admin": make_true_admin_headers(db_session)}


def _shed_in(client, headers, arrival, variant="IA0", family="MINOR"):
    resp = client.post("/api/shed-visits/in", json={
        "loco_number": "41771", "arrival_at": arrival.isoformat(), "schedule_family": family,
        "schedule_variant": variant, "arrival_condition": "WORKING", "log_book_bookings": []},
        headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _skip_tb(client, headers, visit_id, reason="For testing"):
    resp = client.post(f"/api/shed-visits/{visit_id}/stages/test-before/skip",
                       json={"reason": reason}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp


def _start(client, headers, visit_id, started_at):
    return client.post(f"/api/shed-visits/{visit_id}/start-schedule",
                       json={"started_at": started_at.isoformat()}, headers=headers)


def _stage(db_session, visit_id, stage_type="TEST_BEFORE"):
    db_session.expire_all()
    return db_session.query(models.ShedVisitStage).filter_by(
        shed_visit_id=visit_id, stage_type=stage_type).one()


def _set_skipped_at(db_session, visit_id, moment):
    stage = _stage(db_session, visit_id)
    stage.skipped_at = moment
    db_session.commit()
    return stage


# --------------------------------------------------------------------- the TB gate -------------

def test_admin_skipped_test_before_allows_start_schedule(client, db_session, env):
    arrival = _now() - timedelta(hours=6)
    visit_id = _shed_in(client, env["movement"], arrival)
    _skip_tb(client, env["admin"], visit_id)

    resp = _start(client, env["movement"], visit_id, _now())

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit_id).schedule_started_at is not None


def test_start_schedule_in_the_same_minute_as_the_skip_is_allowed(client, db_session, env):
    """The production failure: minute-precision start vs sub-second skip instant."""
    arrival = _now() - timedelta(hours=6)
    visit_id = _shed_in(client, env["movement"], arrival)
    _skip_tb(client, env["admin"], visit_id)
    skipped_at = _now().replace(second=25, microsecond=649000)
    _set_skipped_at(db_session, visit_id, skipped_at)

    resp = _start(client, env["movement"], visit_id, skipped_at.replace(second=0, microsecond=0))

    assert resp.status_code == 200, resp.text


def test_start_schedule_a_minute_before_the_skip_is_still_refused(client, db_session, env):
    arrival = _now() - timedelta(hours=6)
    visit_id = _shed_in(client, env["movement"], arrival)
    _skip_tb(client, env["admin"], visit_id)
    skipped_at = _now()
    _set_skipped_at(db_session, visit_id, skipped_at)

    resp = _start(client, env["movement"], visit_id, skipped_at - timedelta(minutes=1))

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "SCHEDULE_START_BEFORE_TEST_BEFORE"
    assert detail["test_before_status"] == "SKIPPED"


def test_test_before_neither_completed_nor_skipped_blocks_start(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], _now() - timedelta(hours=6))

    resp = _start(client, env["movement"], visit_id, _now())

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "TEST_BEFORE_NOT_SATISFIED"
    assert detail["test_before_status"] == "PENDING"


def test_completed_test_before_allows_start(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], _now() - timedelta(hours=6))
    stage = _stage(db_session, visit_id)
    stage.status, stage.completed_at = "COMPLETED", _now() - timedelta(minutes=5)
    db_session.commit()

    resp = _start(client, env["movement"], visit_id, _now())

    assert resp.status_code == 200, resp.text


def test_a_skipped_test_before_needs_no_checksheet_row(client, db_session, env, mock_bldcms_client):
    visit_id = _shed_in(client, env["movement"], _now() - timedelta(hours=6))
    _skip_tb(client, env["admin"], visit_id)

    assert not mock_bldcms_client.visits.get(visit_id)
    assert _start(client, env["movement"], visit_id, _now()).status_code == 200


def test_the_skip_stays_audited_after_the_schedule_starts(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], _now() - timedelta(hours=6))
    _skip_tb(client, env["admin"], visit_id, reason="Loco arrived dead")
    _start(client, env["movement"], visit_id, _now())

    stage = _stage(db_session, visit_id)
    events = db_session.query(models.ShedVisitEvent).filter_by(shed_visit_id=visit_id).all()

    assert stage.status == "SKIPPED" and stage.skipped_at is not None
    assert stage.skipped_by is not None and stage.skip_reason == "Loco arrived dead"
    assert "TEST_BEFORE_SKIPPED" in {e.event_type for e in events}


def test_schedule_started_at_is_written_once(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], _now() - timedelta(hours=6))
    _skip_tb(client, env["admin"], visit_id)
    first = _now()
    assert _start(client, env["movement"], visit_id, first).status_code == 200
    db_session.expire_all()
    recorded = db_session.get(models.ShedVisit, visit_id).schedule_started_at

    resp = _start(client, env["movement"], visit_id, first + timedelta(hours=1))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "SCHEDULE_ALREADY_STARTED"
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit_id).schedule_started_at == recorded


# ------------------------------------------------------- package is NOT the TB gate -------------

def test_start_schedule_does_not_depend_on_the_work_package(client, db_session, env,
                                                            mock_bldcms_client):
    """Whether a package exists is a separate concern (Pending Checksheets reports it); it must
    neither block nor be conflated with the Test Before gate."""
    mock_bldcms_client.unavailable = True
    visit_id = _shed_in(client, env["movement"], _now() - timedelta(hours=6))
    mock_bldcms_client.unavailable = False
    assert db_session.query(models.ShedVisitChecksheetPackage).filter_by(
        shed_visit_id=visit_id).one_or_none() is None
    _skip_tb(client, env["admin"], visit_id)

    resp = _start(client, env["movement"], visit_id, _now())

    assert resp.status_code == 200, resp.text


def test_a_generated_package_with_a_skipped_test_before_starts_normally(client, db_session, env):
    visit_id = _shed_in(client, env["movement"], _now() - timedelta(hours=6))
    assert db_session.query(models.ShedVisitChecksheetPackage).filter_by(
        shed_visit_id=visit_id).one_or_none() is not None
    _skip_tb(client, env["admin"], visit_id)

    assert _start(client, env["movement"], visit_id, _now()).status_code == 200


# ------------------------------------------------------------------- date and time --------------

def test_same_day_shed_in_skip_and_start(client, db_session, env):
    arrival = _now() - timedelta(hours=2)
    visit_id = _shed_in(client, env["movement"], arrival)
    _skip_tb(client, env["admin"], visit_id)

    assert _start(client, env["movement"], visit_id, _now()).status_code == 200


def test_an_explicitly_backdated_start_after_arrival_is_accepted(client, db_session, env):
    arrival = _now() - timedelta(days=1)
    visit_id = _shed_in(client, env["movement"], arrival)
    _skip_tb(client, env["admin"], visit_id)
    _set_skipped_at(db_session, visit_id, arrival + timedelta(hours=1))

    resp = _start(client, env["movement"], visit_id, arrival + timedelta(hours=2))

    assert resp.status_code == 200, resp.text


def test_a_start_before_arrival_is_rejected(client, db_session, env):
    arrival = _now() - timedelta(hours=6)
    visit_id = _shed_in(client, env["movement"], arrival)
    _skip_tb(client, env["admin"], visit_id)

    resp = _start(client, env["movement"], visit_id, arrival - timedelta(minutes=1))

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SCHEDULE_START_BEFORE_ARRIVAL"


def test_an_ist_timestamp_is_compared_by_instant_not_by_wall_clock_day(client, db_session, env):
    """22:00 IST yesterday is 16:30 UTC yesterday: a start sent at 09:30 IST today is after it,
    and the day difference in either rendering must not change that."""
    arrival_ist = (datetime.now(IST) - timedelta(days=1)).replace(hour=22, minute=0, second=0,
                                                                  microsecond=0)
    visit_id = _shed_in(client, env["movement"], arrival_ist)
    _skip_tb(client, env["admin"], visit_id)
    started_ist = datetime.now(IST).replace(second=0, microsecond=0)

    resp = _start(client, env["movement"], visit_id, started_ist)

    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    recorded = db_session.get(models.ShedVisit, visit_id).schedule_started_at
    assert recorded.astimezone(timezone.utc) == started_ist.astimezone(timezone.utc)
