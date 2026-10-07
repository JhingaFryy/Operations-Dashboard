"""Shed Out: the final backend-enforced gate for a Minor Schedule visit
(Phase 3E), plus the read-only eligibility check the workflow page's
readiness panel polls before offering the action.

evaluate_shed_out_eligibility() is the single source of truth for "is this
visit allowed to Shed Out" — both the eligibility GET and the Shed Out
POST call it, so the gate logic exists in exactly one place. It evaluates
only the active Minor workflow (see app/services/shed_visit_service.py's
MINOR_STAGE_SEQUENCE): TEST_BEFORE, SCHEDULE_INSPECTION, TEST_AFTER. A
historical SPECIAL_CHECKING row (legacy, no longer created — Phase 3D)
is deliberately never queried or considered here.

Non-Minor schedule_family has no defined Shed Out semantics in this
codebase (Shed In can now also create MAJOR/IOH/TOH visits — see
app/domain/schedule.py's SCHEDULE_MATRIX — but no Major checksheet
workflow or completion rule exists yet), so evaluating eligibility for
one raises a structured 409 rather than silently returning eligible=false
or inventing a completion rule.

Booking gate (Business Rule Alignment — restores the assignment-based gate): every Booking
belonging to this shed_visit_id — regardless of booking_source (LOG_BOOK, TEST_BEFORE,
SCHEDULE_INSPECTION, TEST_AFTER, MANUAL, or a legacy source) — must have at least one
booking_section_assignments row, AND every one of that booking's assignments must be ATTENDED.
Booking.status alone is never trusted for this decision (even though
booking_assignment_service.recompute_booking_status keeps it in sync with assignments on every
mutation, so it's also shown here for the human-readable blocker string) - the gate itself always
re-derives from the assignment rows directly, inside this same row-locked transaction. A booking
with zero assignments blocks Shed Out (booking creation always creates at least one - see
booking_creation_service.py - so zero here means a genuine routing gap, not an expected state).
"""

from datetime import datetime, timezone

from fastapi import status
from sqlalchemy.orm import Session

from app.domain.schedule import is_valid_schedule_pair
from app.domain.booking_resolution import (
    UNRESOLVED_ASSIGNMENT_STATUSES,
    unresolved_display_status,
)
from app.db.models import Booking, BookingSectionAssignment, ShedVisit, ShedVisitEvent, ShedVisitStage, User
from app.schemas.shed_visits import (
    BookingBlockerOut,
    ChecksheetRequirementBlockerOut,
    ChecksheetRequirementSummaryOut,
    ShedOutEligibilityOut,
    ShedOutRequest,
    ShedOutResponse,
    StageBlockerOut,
)
from app.services.checksheet_requirement_completion_service import (
    RequirementCompletion,
    build_ready_blocker_detail,
    evaluate_requirement_completion,
)
from app.services.workflow_common import error, get_visit_or_404, test_before_legacy_waived

# Re-exported, not redefined. The gate below asks the shared domain helper whether any row
# is unresolved; this name is kept because the module docstring and tests refer to it, but
# it must stay the SAME three values as every other unresolved check in the codebase.
NOT_YET_ATTENDED_ASSIGNMENT_STATUSES = UNRESOLVED_ASSIGNMENT_STATUSES

REQUIRED_ACTIVE_STAGES = ("TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER")


def _require_supported_schedule_for_shed_out(visit: ShedVisit) -> None:
    """MINOR and MAJOR are both defined now. MAJOR is gated on bookings plus its visit-level
    checksheet requirements; it has no stages, and none are invented for it."""
    if visit.schedule_family in ("MINOR", "MAJOR") and is_valid_schedule_pair(
        visit.schedule_family, visit.schedule_variant
    ):
        return
    raise error(
        status.HTTP_409_CONFLICT,
        "UNSUPPORTED_SCHEDULE_FAMILY",
        "Shed Out eligibility is only defined for Minor (IA/IA0/IB/IC/IC0) and Major (IOH/TOH) visits; "
        f"this visit is {visit.schedule_family!r}/{visit.schedule_variant!r}.",
    )


def evaluate_shed_out_eligibility(
    db: Session, visit: ShedVisit, bldcms_client=None
) -> ShedOutEligibilityOut:
    """Public read-only eligibility (see _evaluate for the gates)."""
    eligibility, _completion = _evaluate(db, visit, bldcms_client)
    return eligibility


def _evaluate(
    db: Session, visit: ShedVisit, bldcms_client=None
) -> tuple[ShedOutEligibilityOut, RequirementCompletion]:
    """Pure read: evaluates the stage gate, the booking status gate and the checksheet
    requirement gate for the given visit. Raises a structured 409 if the visit's schedule_family
    isn't one this gate is defined for. Never mutates anything.

    The booking gate below is completely untouched by the Operational Control phase - checksheet
    readiness is an ADDITIONAL condition, never a replacement for or relaxation of it.
    """
    _require_supported_schedule_for_shed_out(visit)

    stages_by_type = {
        s.stage_type: s
        for s in db.query(ShedVisitStage).filter(ShedVisitStage.shed_visit_id == visit.id).all()
    }

    stage_blockers: list[StageBlockerOut] = []
    # MAJOR is equipment-wise and has no TB/Inspection/TA stages - the stage gate is Minor-only.
    # Its checksheet readiness comes entirely from the visit-level requirement gate below.
    if visit.schedule_family == "MINOR":
        for stage_type in REQUIRED_ACTIVE_STAGES:
            stage = stages_by_type.get(stage_type)
            if stage is None:
                stage_blockers.append(StageBlockerOut(stage_type=stage_type, status="MISSING"))
            elif stage.status == "SKIPPED" and stage_type == "TEST_BEFORE":
                # Admin-skipped Test Before (migration 012). Only Test Before can be skipped.
                continue
            elif stage_type == "TEST_BEFORE" and test_before_legacy_waived(visit, stage):
                # Schedule started under the previous workflow - no retroactive Test Before.
                continue
            elif stage.status != "COMPLETED":
                stage_blockers.append(StageBlockerOut(stage_type=stage_type, status=stage.status))

    bookings = db.query(Booking).filter(Booking.shed_visit_id == visit.id).all()
    booking_ids = [b.id for b in bookings]

    assignments_by_booking: dict[int, list[BookingSectionAssignment]] = {}
    if booking_ids:
        assignments = (
            db.query(BookingSectionAssignment)
            .filter(BookingSectionAssignment.booking_id.in_(booking_ids))
            .all()
        )
        for a in assignments:
            assignments_by_booking.setdefault(a.booking_id, []).append(a)

    booking_blockers: list[BookingBlockerOut] = []
    for b in bookings:
        b_assignments = assignments_by_booking.get(b.id, [])
        if not b_assignments:
            booking_blockers.append(
                BookingBlockerOut(booking_id=b.id, booking_source=b.booking_source, status="NO_ASSIGNMENTS")
            )
        else:
            # DISPLAYED STATUS IS DERIVED FROM THE SAME ROWS AS THE DECISION.
            #
            # This used to show b.status, the stored parent aggregate. That is normally in sync,
            # but it is not guaranteed to be at the moment this runs - a booking that gained a
            # section through planning kept its old ATTENDED parent status, so this list reported
            # "booking 8003 (ATTENDED) is blocking Shed Out", which is self-contradictory and
            # unactionable for the person trying to release the loco.
            #
            # unresolved_display_status() returns None exactly when every assignment is ATTENDED,
            # which is precisely the not-a-blocker case - so the None check IS the gate here,
            # replacing the previous `any(... in NOT_YET_ATTENDED_ASSIGNMENT_STATUSES)` test with
            # its exact complement. The gate is not weakened: both ask "is any row unresolved",
            # now from one shared definition instead of two parallel ones.
            display_status = unresolved_display_status(a.status for a in b_assignments)
            if display_status is not None:
                booking_blockers.append(
                    BookingBlockerOut(
                        booking_id=b.id,
                        booking_source=b.booking_source,
                        status=display_status,
                    )
                )

    completion = evaluate_requirement_completion(db, bldcms_client, visit)
    checksheet_blockers = [
        ChecksheetRequirementBlockerOut(**vars(b)) for b in completion.blockers
    ]

    eligibility = ShedOutEligibilityOut(
        shed_visit_id=visit.id,
        eligible=not stage_blockers and not booking_blockers and not checksheet_blockers,
        stage_blockers=stage_blockers,
        booking_blockers=sorted(booking_blockers, key=lambda b: b.booking_id),
        checksheet_blockers=checksheet_blockers,
        checksheet_summary=ChecksheetRequirementSummaryOut(
            work_package_generated=completion.work_package_generated,
            total=completion.total,
            blocking=completion.blocking,
            optional=completion.optional,
            deactivated=completion.deactivated,
            satisfied=completion.satisfied,
        ),
    )
    return eligibility, completion


_STAGE_WORDS = {
    "TEST_BEFORE": "Test Before",
    "SCHEDULE_INSPECTION": "Inspection",
    "TEST_AFTER": "Test After",
}


def _blocked_by(eligibility: ShedOutEligibilityOut) -> list[str]:
    """Which gates refused, in the order the operator should deal with them."""
    blocked = []
    if eligibility.stage_blockers:
        blocked.append("STAGES")
    if eligibility.booking_blockers:
        blocked.append("BOOKINGS")
    if eligibility.checksheet_blockers:
        blocked.append("CHECKSHEETS")
    return blocked


def _blocked_message(eligibility: ShedOutEligibilityOut, completion: RequirementCompletion) -> str:
    """The actual reason(s), in words - never just "not eligible".

    One sentence per refusing gate. The structured lists alongside stay the source of truth for
    any UI; this is what an operator reads when nothing renders them."""
    parts: list[str] = []

    if eligibility.stage_blockers:
        stages = ", ".join(
            f"{_STAGE_WORDS.get(b.stage_type, b.stage_type)} ({b.status})"
            for b in eligibility.stage_blockers
        )
        parts.append(f"Shed Out blocked: workflow stages not complete - {stages}.")

    if eligibility.booking_blockers:
        unassigned = sum(1 for b in eligibility.booking_blockers if b.status == "NO_ASSIGNMENTS")
        pending = len(eligibility.booking_blockers) - unassigned
        if pending:
            parts.append(
                f"Shed Out blocked: {pending} booking{'s' if pending != 1 else ''} "
                f"still {'have' if pending != 1 else 'has'} work outstanding."
            )
        if unassigned:
            parts.append(
                f"Shed Out blocked: {unassigned} booking{'s' if unassigned != 1 else ''} "
                f"{'have' if unassigned != 1 else 'has'} no responsible section assigned."
            )

    if eligibility.checksheet_blockers:
        parts.append(build_ready_blocker_detail(completion, action="Shed Out")["message"])

    return " ".join(parts) or "This visit is not eligible for Shed Out."


def get_shed_out_eligibility(
    db: Session, visit_id: int, current_user: User, bldcms_client=None
) -> ShedOutEligibilityOut:
    """Admin-only for this phase (enforced by the route's require_admin
    dependency, matching every other Admin-only workflow route) — the
    eligibility report can surface cross-section booking/assignment detail
    that a section-scoped Supervisor has no standing operational need to
    see (Supervisors never perform Shed Out, and continue to see their own
    section's work through the existing Section Dashboard). This is a
    deliberate, documented policy choice, not an oversight."""
    del current_user  # authorization already enforced at the route layer
    visit = get_visit_or_404(db, visit_id)
    return evaluate_shed_out_eligibility(db, visit, bldcms_client)


def shed_out(
    db: Session, visit_id: int, payload: ShedOutRequest, current_user: User, bldcms_client=None
) -> ShedOutResponse:
    """Admin-only (enforced by the route's require_admin dependency).
    Locks the visit (and, transitively via evaluate_shed_out_eligibility,
    re-queries stages/bookings/assignments fresh inside this same
    transaction) before deciding — never trusts a prior eligibility GET.
    Any failure rolls back the whole transaction; the visit is never
    partially closed."""
    visit = get_visit_or_404(db, visit_id)

    try:
        # Row-lock the visit itself first — this is the actual
        # serialization point against a second concurrent Shed Out attempt
        # for the same visit.
        visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).with_for_update().one()

        if visit.status == "CLOSED":
            raise error(
                status.HTTP_409_CONFLICT,
                "VISIT_ALREADY_CLOSED",
                "This shed visit has already been Shed Out.",
            )

        arrival_at = visit.arrival_at
        if arrival_at.tzinfo is None:
            # SQLite (tests) round-trips DateTime(timezone=True) columns as
            # naive; production Postgres does not. Normalize to UTC before
            # comparing so this check behaves the same on both.
            arrival_at = arrival_at.replace(tzinfo=timezone.utc)
        if payload.departed_at < arrival_at:
            raise error(
                422,
                "DEPARTURE_BEFORE_ARRIVAL",
                "departed_at cannot be before arrival_at.",
                arrival_at=visit.arrival_at.isoformat(),
            )

        # New shed workflow: departure cannot precede schedule completion. Without this the
        # ordering is still caught - by chk_shed_visit_timestamp_order - but only as a raw
        # IntegrityError surfacing as a 500, instead of a controlled, explainable 422.
        if visit.ready_at is not None:
            ready_at = visit.ready_at
            if ready_at.tzinfo is None:
                ready_at = ready_at.replace(tzinfo=timezone.utc)
            if payload.departed_at < ready_at:
                raise error(
                    422,
                    "DEPARTURE_BEFORE_READY",
                    "departed_at cannot be before the schedule was completed.",
                    ready_at=visit.ready_at.isoformat(),
                )

        # Row-lock the 3 required active stages too, mirroring the same
        # lock/re-query pattern already proven in TEST_BEFORE/SCHEDULE_
        # INSPECTION/TEST_AFTER's own completion gates — guards against a
        # stage completing concurrently with this Shed Out.
        (
            db.query(ShedVisitStage)
            .filter(
                ShedVisitStage.shed_visit_id == visit_id,
                ShedVisitStage.stage_type.in_(REQUIRED_ACTIVE_STAGES),
            )
            .with_for_update()
            .all()
        )

        # Row-lock every assignment belonging to this visit's bookings too - guards against a
        # concurrent attend/start/reopen racing this same Shed Out decision.
        visit_booking_ids = [
            row[0] for row in db.query(Booking.id).filter(Booking.shed_visit_id == visit_id).all()
        ]
        if visit_booking_ids:
            (
                db.query(BookingSectionAssignment)
                .filter(BookingSectionAssignment.booking_id.in_(visit_booking_ids))
                .with_for_update()
                .all()
            )

        # Re-evaluate eligibility from scratch, inside this transaction,
        # against whatever the DB says right now — never trust a prior GET.
        eligibility, completion = _evaluate(db, visit, bldcms_client)
        if not eligibility.eligible:
            raise error(
                status.HTTP_409_CONFLICT,
                "SHED_OUT_BLOCKED",
                _blocked_message(eligibility, completion),
                blocked_by=_blocked_by(eligibility),
                stage_blockers=[b.model_dump() for b in eligibility.stage_blockers],
                booking_blockers=[b.model_dump() for b in eligibility.booking_blockers],
                checksheet_blockers=[b.model_dump() for b in eligibility.checksheet_blockers],
                # The same structured CHECKSHEETS_INCOMPLETE payload Ready refuses with, so the
                # frontend renders one shape for both. Null when checksheets are not the problem.
                checksheets=(
                    build_ready_blocker_detail(completion, action="Shed Out")
                    if eligibility.checksheet_blockers
                    else None
                ),
            )

        # Migration 012: a MINOR visit on the refined workflow (Complete Schedule recorded the
        # inspection end) leaves only from READY (checked after the stage/booking/checksheet gates, so
        # their detailed blockers are reported first) - i.e. after Mark Ready, which itself requires
        # Test After. Visits completed under the earlier workflow never have inspection_completed_at
        # and are unaffected.
        if visit.schedule_family == "MINOR" and visit.inspection_completed_at is not None and visit.ready_at is None:
            raise error(
                status.HTTP_409_CONFLICT,
                "VISIT_NOT_READY",
                "Mark the locomotive Ready (after Test After) before Shed Out.",
            )
        # MAJOR leaves only from READY too - i.e. after Complete Schedule, which is MAJOR's
        # checksheet-gated Ready transition. The Shed Movement register only offers Shed Out in
        # the READY phase, but that was presentation: without this, a MAJOR visit that never had
        # its schedule completed could be shed out by calling the API directly. Checked after the
        # stage/booking/checksheet gates, exactly like the MINOR rule above, so their detailed
        # blockers are reported first. No Test Before / Test After is involved.
        if visit.schedule_family == "MAJOR" and visit.ready_at is None:
            raise error(
                status.HTTP_409_CONFLICT,
                "VISIT_NOT_READY",
                "Complete the schedule (which makes the locomotive Ready) before Shed Out.",
            )

        booking_count = db.query(Booking).filter(Booking.shed_visit_id == visit_id).count()

        now = datetime.now(timezone.utc)
        visit.status = "CLOSED"
        visit.departed_at = payload.departed_at
        visit.departure_source = "DASHBOARD"
        visit.updated_by = current_user.id
        visit.updated_at = now

        db.add(
            ShedVisitEvent(
                shed_visit_id=visit.id,
                event_type="SHED_OUT",
                event_time=payload.departed_at,
                source="DASHBOARD",
                remarks=payload.remarks,
                event_data={
                    "schedule_family": visit.schedule_family,
                    "schedule_variant": visit.schedule_variant,
                    "booking_count": booking_count,
                },
                created_by=current_user.id,
                created_at=now,
            )
        )

        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(visit)
    return ShedOutResponse(
        id=visit.id,
        loco_number=visit.loco_number,
        status=visit.status,
        departed_at=visit.departed_at,
        departure_source=visit.departure_source,
    )
