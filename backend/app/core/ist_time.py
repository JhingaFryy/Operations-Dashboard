"""Asia/Kolkata calendar days for operational reporting.

Operations Dashboard stores every timestamp as `timestamp with time zone` - an absolute instant,
with no ambiguity to fix. What this module settles is the other half of the question: which DAY an
instant belongs to. Shed staff read "attended today" standing in the shed, so a day means an IST
calendar day, not a UTC one. An 00:30 IST arrival belongs to that IST day even though UTC still
calls it the previous evening (2026-09-22 19:00 UTC = 2026-09-23 00:30 IST).

Bounds are returned AWARE UTC, because that is what these columns compare against - the conversion
happens here, once, instead of being re-derived per query.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
IST_OFFSET = timedelta(hours=5, minutes=30)


def ist_today(now: datetime | None = None) -> date:
    """Today's date in Asia/Kolkata, independent of the server's own timezone."""
    return (now or datetime.now(timezone.utc)).astimezone(IST).date()


def ist_date_of(dt: datetime | None) -> date | None:
    """The Asia/Kolkata calendar date an instant falls on. A naive value is read as UTC."""
    if dt is None:
        return None
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(IST).date()


def ist_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) for one IST calendar day, as aware UTC instants."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)
    return start, start + timedelta(days=1)
