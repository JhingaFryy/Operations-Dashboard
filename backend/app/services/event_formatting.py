"""Human-readable sentences for Operations Dashboard's audit events.

ONE translation of internal event codes into words, shared by everything that shows history - the
shed visit history page today, the Event Log next. Callers pass the rows and a name lookup; this
module never queries anything, so it is trivially reusable and testable.

Rules:
  * The primary rendering is a sentence ("Work started in M1-HR by Anil"), never raw JSON.
  * An actor that was never recorded reads "Not recorded" - a name is never invented. An event the
    system wrote with no actor reads "System".
  * Unknown codes still render readably ("Schedule Changed") rather than disappearing, so a new
    event type is visible the day it is introduced.
  * The raw event_data is carried separately and only for callers who decide to show it (Admin).
"""

from dataclasses import dataclass, field
from datetime import datetime

NOT_RECORDED = "Not recorded"
SYSTEM_ACTOR = "System"

BOOKING_SOURCE_LABELS = {
    "LOG_BOOK": "Log Book",
    "TEST_BEFORE": "Test Before",
    "TEST_AFTER": "Test After",
    "SCHEDULE_INSPECTION": "Minor Inspection",
    "SPECIAL_CHECKING": "Special Checking",
    "MANUAL": "Manual",
    "TRIP_INSPECTION": "Trip Inspection",
    "GENERAL_CHECKING": "General Checking",
}

ADMIN_RESET_ACTION = "ADMIN_RESET_ACTIVE_VISIT"


def words(code: str | None) -> str:
    """UPPER_SNAKE -> "Upper Snake"; the readable fallback for any code without a phrase."""
    if not code:
        return ""
    return code.replace("_", " ").title()


def source_label(source: str | None) -> str:
    return BOOKING_SOURCE_LABELS.get(source or "", words(source))


@dataclass
class FormattedEvent:
    at: datetime
    event_type: str
    sentence: str
    actor_name: str
    section_name: str | None = None
    remarks: str | None = None
    raw: dict | None = field(default=None)


def _actor(user_id: int | None, names: dict[int, str], *, system_source: bool = False) -> str:
    if user_id is None:
        return SYSTEM_ACTOR if system_source else NOT_RECORDED
    return names.get(user_id, NOT_RECORDED)


def format_booking_event(event, *, names: dict[int, str], sections: dict[int, str],
                         include_raw: bool = False) -> FormattedEvent:
    """A booking_events row as a sentence. `names` maps user id -> name, `sections` section id ->
    display name; both are supplied by the caller in one batched lookup."""
    actor = _actor(event.created_by, names)
    to_section = sections.get(event.to_section_id) if event.to_section_id else None
    from_section = sections.get(event.from_section_id) if event.from_section_id else None
    data = event.event_data or {}
    code = event.event_type

    if code == "CREATED":
        origin = " from a BL-DCMS checksheet" if data.get("origin") == "BLDCMS_CHECKSHEET" else ""
        sentence = f"Booking raised by {actor}{origin}"
    elif code == "AUTO_ROUTED":
        sentence = f"Routed to {to_section or 'a section'}"
    elif code == "FORWARDED":
        sentence = f"Forwarded from {from_section or 'a section'} to {to_section or 'a section'} by {actor}"
    elif code == "STARTED":
        sentence = f"Work started in {to_section or 'a section'} by {actor}"
    elif code == "ATTENDED":
        sentence = f"Marked attended in {to_section or 'a section'} by {actor}"
    elif code == "REOPENED":
        sentence = f"Reopened for {to_section or 'a section'} by {actor}"
    else:
        sentence = f"{words(code)} by {actor}"

    return FormattedEvent(
        at=event.created_at,
        event_type=code,
        sentence=sentence,
        actor_name=actor,
        section_name=to_section,
        remarks=event.remarks,
        raw=data if include_raw else None,
    )


def is_admin_reset(event) -> bool:
    return (
        event.event_type == "MANUAL_CORRECTION"
        and (event.event_data or {}).get("action") == ADMIN_RESET_ACTION
    )


def format_visit_event(event, *, names: dict[int, str], include_raw: bool = False) -> FormattedEvent:
    """A shed_visit_events row as a sentence."""
    system = event.source == "SYSTEM"
    actor = _actor(event.created_by, names, system_source=system)
    data = event.event_data or {}
    code = event.event_type
    remarks = event.remarks

    if code == "SHED_IN":
        sentence = f"Shed In recorded by {actor}"
    elif code == "SCHEDULE_STARTED":
        sentence = f"Schedule started by {actor}"
    elif code == "SCHEDULE_COMPLETED":
        if data.get("milestone") == "INSPECTION_COMPLETED":
            sentence = f"Minor Inspection completed by {actor}"
        else:
            sentence = f"Schedule completed - locomotive Ready - by {actor}"
    elif code == "MARK_READY":
        sentence = f"Marked Ready by {actor}"
    elif code == "SHED_OUT":
        sentence = f"Shed Out by {actor}"
    elif code == "TEST_BEFORE_SKIPPED":
        sentence = f"Test Before skipped by {actor}"
        remarks = remarks or data.get("reason")
    elif is_admin_reset(event):
        sentence = "Closed administratively by a system reset - not a Shed Out"
        remarks = remarks or data.get("reason")
    elif code == "MANUAL_CORRECTION":
        sentence = f"Manual correction by {actor}"
    else:
        sentence = f"{words(code)} by {actor}"

    return FormattedEvent(
        at=event.event_time,
        event_type=code,
        sentence=sentence,
        actor_name=actor,
        remarks=remarks,
        raw=data if include_raw else None,
    )
