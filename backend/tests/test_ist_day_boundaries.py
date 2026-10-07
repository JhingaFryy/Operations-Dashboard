"""Day-based figures mean an Asia/Kolkata calendar day, while storage stays UTC.

The boundary that matters operationally: 2026-09-22 19:00 UTC is 2026-09-23 00:30 IST, so that
event counts towards 23 Sep. An IST day runs 18:30 UTC -> 18:30 UTC.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.ist_time import ist_date_of, ist_day_bounds, ist_today


def test_the_ist_day_runs_from_1830_utc_to_1830_utc():
    start, end = ist_day_bounds(date(2026, 9, 23))
    assert start == datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 23, 18, 30, tzinfo=timezone.utc)
    assert end - start == timedelta(days=1)


@pytest.mark.parametrize("utc,expected", [
    (datetime(2026, 9, 22, 18, 29, tzinfo=timezone.utc), date(2026, 9, 22)),
    (datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc), date(2026, 9, 23)),
    (datetime(2026, 9, 22, 19, 0, tzinfo=timezone.utc), date(2026, 9, 23)),  # 00:30 IST
    (datetime(2026, 9, 23, 18, 29, 59, tzinfo=timezone.utc), date(2026, 9, 23)),
    (None, None),
])
def test_an_instant_lands_on_its_ist_day(utc, expected):
    assert ist_date_of(utc) == expected


def test_the_boundary_instant_is_inside_exactly_one_day():
    boundary = datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc)
    previous_start, previous_end = ist_day_bounds(date(2026, 9, 22))
    start, end = ist_day_bounds(date(2026, 9, 23))
    assert not (previous_start <= boundary < previous_end)
    assert start <= boundary < end


def test_ist_today_does_not_use_the_machines_timezone(monkeypatch):
    import time as time_module

    late_evening_utc = datetime(2026, 9, 22, 19, 0, tzinfo=timezone.utc)
    for zone in ("UTC", "America/New_York", "Asia/Kolkata"):
        monkeypatch.setenv("TZ", zone)
        time_module.tzset()
        assert ist_today(late_evening_utc) == date(2026, 9, 23), zone
    monkeypatch.undo()
    time_module.tzset()


def test_attended_today_uses_the_ist_day(monkeypatch):
    """The one day-based figure in this service reads its bounds from ist_time, not from UTC."""
    import inspect

    from app.services import booking_service

    source = inspect.getsource(booking_service.get_section_summary)
    assert "ist_day_bounds(today)" in source
    assert "datetime.now(timezone.utc).date()" not in source
