"""Booking origin (ownership) rules - the one place that says who may CREATE which booking source.

    booking_source        created by                                     route
    -------------------   --------------------------------------------   -----------------------------------
    LOG_BOOK              Operations Dashboard (Shed In Log Book entry)  POST /api/shed-visits/in
    TEST_BEFORE           Android Test Before checksheet, via BL-DCMS    POST /api/internal/bookings
    TEST_AFTER            Android Test After checksheet, via BL-DCMS     POST /api/internal/bookings
    SCHEDULE_INSPECTION   nobody - Minor Inspection checksheets never raise bookings
    TRIP_INSPECTION /     unchanged (future TI/GC; out of scope) - still accepted on the internal route
    GENERAL_CHECKING
    MANUAL                nobody (legacy value, kept only for existing rows)

Management is NOT creation: every booking, whatever its origin, is displayed, assigned and moved
through OPEN -> IN_PROGRESS -> ATTENDED (-> REOPENED) on the Dashboard exactly as before, and every
one of them still gates Shed Out.

The source is never trusted on its own: the internal route also checks the checksheet link, the
visit's schedule family and that the visit actually has the matching workflow stage.
"""

from fastapi import HTTPException

CHECKSHEET_DERIVED_SOURCES = ("TEST_BEFORE", "TEST_AFTER")
# Accepted on the service route but not checksheet-stage-bound (unchanged, out of scope).
UNCHANGED_EXTERNAL_SOURCES = ("TRIP_INSPECTION", "GENERAL_CHECKING")
INTERNAL_ROUTE_ALLOWED_SOURCES = CHECKSHEET_DERIVED_SOURCES + UNCHANGED_EXTERNAL_SOURCES
DASHBOARD_CREATED_SOURCES = ("LOG_BOOK",)


def _error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def dashboard_creation_refused(booking_source: str) -> HTTPException:
    if booking_source == "SCHEDULE_INSPECTION":
        message = "Minor Inspection checksheets do not raise bookings."
    else:
        message = (
            f"{booking_source} bookings are raised by the technician on the Android checksheet. "
            "The Dashboard creates Log Book bookings only, and manages every booking after creation."
        )
    return _error(409, "BOOKING_ORIGIN_NOT_ALLOWED", message, booking_source=booking_source)


def assert_internal_source_allowed(booking_source: str) -> None:
    """POST /api/internal/bookings is the checksheet channel. LOG_BOOK must not masquerade as a
    checksheet booking, and SCHEDULE_INSPECTION / MANUAL are never created here."""
    if booking_source not in INTERNAL_ROUTE_ALLOWED_SOURCES:
        if booking_source == "SCHEDULE_INSPECTION":
            message = "Minor Inspection checksheets do not raise bookings."
        elif booking_source == "LOG_BOOK":
            message = "Log Book bookings are created on the Operations Dashboard, not from a checksheet."
        else:
            message = f"{booking_source} bookings cannot be created through the checksheet channel."
        raise _error(422, "BOOKING_ORIGIN_NOT_ALLOWED", message, booking_source=booking_source)
