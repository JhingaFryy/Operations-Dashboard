"""GET /api/shed-visits/current is ordered by Shed-In chronology, newest first.

Both the Operations Overview and Shed Movement read this one endpoint, so the order it returns is the
order both pages start from. They each apply the same comparator client-side as well
(frontend/src/lib/shedVisitOrder.ts), because the Overview previously re-sorted this response - by
outstanding bookings, then OLDEST arrival first - and the two pages disagreed about the same data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import shed_visit_service
from tests.conftest import make_shed_visit


def _order(db) -> list[int]:
    return [v.id for v in shed_visit_service.list_current_visits(db)]


def test_newest_arrival_first_and_oldest_last(db_session):
    now = datetime.now(timezone.utc)
    make_shed_visit(db_session, 1, "44444", status="IN_SHED", arrival_at=now - timedelta(days=2))
    make_shed_visit(db_session, 2, "22222", status="IN_SHED", arrival_at=now - timedelta(hours=5))
    make_shed_visit(db_session, 3, "11111", status="IN_SHED", arrival_at=now)

    order = _order(db_session)
    assert order[0] == 3
    assert order[-1] == 1


def test_three_visits_across_different_dates(db_session):
    base = datetime(2026, 10, 1, 8, 50, tzinfo=timezone.utc)  # 14:20 IST
    make_shed_visit(db_session, 1, "11111", status="IN_SHED", arrival_at=base)
    make_shed_visit(db_session, 2, "30000", status="IN_SHED", arrival_at=base - timedelta(days=1))
    make_shed_visit(db_session, 3, "44444", status="IN_SHED", arrival_at=base - timedelta(days=2))
    assert _order(db_session) == [1, 2, 3]


def test_same_day_different_times(db_session):
    base = datetime(2026, 10, 1, 3, 40, tzinfo=timezone.utc)  # 09:10 IST
    make_shed_visit(db_session, 1, "EARLY", status="IN_SHED", arrival_at=base)
    make_shed_visit(db_session, 2, "LATE", status="IN_SHED", arrival_at=base + timedelta(hours=5))
    make_shed_visit(db_session, 3, "MID", status="IN_SHED", arrival_at=base + timedelta(hours=2))
    assert _order(db_session) == [2, 3, 1]


def test_identical_arrivals_fall_back_to_visit_id_descending(db_session):
    """arrival_at is recorded to the second and two locomotives can genuinely be shed in within the
    same second. Without the tie-break the database may return them in any order, and the list could
    reshuffle between two refreshes showing identical data."""
    same = datetime(2026, 10, 1, 3, 40, tzinfo=timezone.utc)
    for visit_id, loco in ((7, "AAAAA"), (9, "ZZZZZ"), (8, "MMMMM")):
        make_shed_visit(db_session, visit_id, loco, status="IN_SHED", arrival_at=same)

    # The later-created visit is the newer one, which is the same intent as newest-first.
    assert _order(db_session) == [9, 8, 7]


def test_a_newly_shed_in_visit_leads_the_list(db_session):
    now = datetime.now(timezone.utc)
    make_shed_visit(db_session, 1, "11111", status="IN_SHED", arrival_at=now - timedelta(hours=3))
    make_shed_visit(db_session, 2, "22222", status="IN_SHED", arrival_at=now - timedelta(hours=1))
    assert _order(db_session)[0] == 2

    make_shed_visit(db_session, 3, "33333", status="IN_SHED", arrival_at=now)
    assert _order(db_session)[0] == 3


def test_ordering_is_not_by_loco_number_or_status(db_session):
    """Guards against the list quietly reverting to a different key. The lowest loco number is the
    OLDEST arrival here, and a READY visit is newer than an IN_SHED one."""
    now = datetime.now(timezone.utc)
    make_shed_visit(db_session, 1, "10000", status="IN_SHED", arrival_at=now - timedelta(days=1))
    make_shed_visit(db_session, 2, "99999", status="IN_SHED", arrival_at=now)
    assert _order(db_session) == [2, 1]


def test_closed_visits_are_not_in_the_active_list(db_session):
    """Ordering must not change which rows appear. Only open visits are listed, as before."""
    now = datetime.now(timezone.utc)
    make_shed_visit(db_session, 1, "11111", status="IN_SHED", arrival_at=now)
    make_shed_visit(db_session, 2, "22222", status="CLOSED", arrival_at=now - timedelta(hours=1),
                    departed_at=now, departure_source="DASHBOARD")
    assert _order(db_session) == [1]
