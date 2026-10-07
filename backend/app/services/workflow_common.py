"""Shared helpers for Minor Schedule workflow stages (Test Before, Schedule
Inspection, ...). Extracted from what was originally private to
test_before_service.py (Phase 3A) so Phase 3B's Schedule Inspection stage
doesn't duplicate the same visit/stage lookups and authorization checks.

Audit note (see Phase 3A report): shed_visit_events' event_type is
constrained to SHED_IN, MARK_READY, SHED_OUT, SCHEDULE_CHANGED,
MANUAL_CORRECTION (confirmed against the live schema) — none of which
honestly describes "a workflow stage started/completed". Stage start/
complete audit therefore lives entirely on the shed_visit_stages row
itself (status, started_at/by, completed_at/by); no shed_visit_events row
is created for stage transitions.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domain.schedule import is_valid_schedule_pair
from app.db.models import ShedVisit, ShedVisitStage, User
from app.services.auth_service import ADMIN_ROLE

NOT_YET_ATTENDED_STATUSES = ("OPEN", "IN_PROGRESS", "REOPENED")

# Migration 012: a stage that no longer holds up the stages after it. SKIPPED exists only for
# TEST_BEFORE (Admin-only, audited); it is never COMPLETED and never carries completed_at, so the two
# stay distinguishable forever. TEST_AFTER can never be SKIPPED (service + chk_stage_skipped).
STAGE_SATISFIED_STATUSES = ("COMPLETED", "SKIPPED")

# The per-visit Optional/Required/Deactivate overrides do not apply to these stages: Test Before is
# always required (its only exception is the explicit Admin skip) and Test After is always required
# with no exception at all.
NON_CONFIGURABLE_STAGES = ("TEST_BEFORE", "TEST_AFTER")

# Retained for call-site compatibility only - NOT the gate. The comment that
# used to sit here ("Common Booking Pool reform: booking.status itself is now
# the authoritative signal") described a superseded architecture and is
# corrected rather than left to mislead: both real gates - shed_out_service's
# booking gate and each stage's STAGE_BLOCKED_BY_BOOKINGS gate
# (checksheet_stage_reconciliation_service._stage_bookings_ready) - re-derive
# from booking_section_assignments rows directly and use their own
# NOT_YET_ATTENDED_ASSIGNMENT_STATUSES. booking.status is a derived aggregate
# (section_dashboard_service.recompute_booking_status) shown to humans, never
# trusted for a gate decision. Nothing currently reads this constant.
NOT_YET_ATTENDED_BOOKING_STATUSES = ("OPEN", "IN_PROGRESS", "REOPENED")


def error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def get_visit_or_404(db: Session, visit_id: int) -> ShedVisit:
    visit = db.get(ShedVisit, visit_id)
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shed visit not found")
    return visit


def assert_visit_open(visit: ShedVisit) -> None:
    """Phase 3E: once a visit is CLOSED (Shed Out), no further workflow
    mutation is permitted. This is checked defensively in finding-creation
    paths — stage *start* already rejects a non-OPEN visit via its own
    VISIT_NOT_OPEN check (CLOSED is not IN_SHED/READY), and completion can
    never race a closure since Shed Out itself requires every active
    stage COMPLETED first. Not every mutation endpoint in the codebase
    calls this yet — see the Phase 3E report for the ones that still
    don't (e.g. manual section-assignment addition, section start/attend/
    reopen) and were deliberately left alone this phase."""
    if visit.status == "CLOSED":
        raise error(
            status.HTTP_409_CONFLICT,
            "VISIT_CLOSED",
            "This shed visit is closed; no further workflow mutations are permitted.",
        )


def require_minor(visit: ShedVisit) -> None:
    if not (visit.schedule_family == "MINOR" and is_valid_schedule_pair("MINOR", visit.schedule_variant)):
        raise error(
            status.HTTP_409_CONFLICT,
            "WORKFLOW_NOT_APPLICABLE",
            "This shed visit is not on a Minor Schedule; this workflow stage does not apply.",
        )


def get_stages(db: Session, visit_id: int) -> list[ShedVisitStage]:
    return (
        db.query(ShedVisitStage)
        .filter(ShedVisitStage.shed_visit_id == visit_id)
        .order_by(ShedVisitStage.stage_order)
        .all()
    )


def get_stage_or_404(
    db: Session, visit_id: int, stage_type: str, *, for_update: bool = False
) -> ShedVisitStage:
    query = db.query(ShedVisitStage).filter(
        ShedVisitStage.shed_visit_id == visit_id, ShedVisitStage.stage_type == stage_type
    )
    if for_update:
        query = query.with_for_update()
    stage = query.first()
    if stage is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{stage_type} stage not found"
        )
    return stage


def assert_no_later_stage_started(db: Session, visit_id: int, stage_order: int, stage_type: str) -> None:
    later_active = (
        db.query(ShedVisitStage)
        .filter(
            ShedVisitStage.shed_visit_id == visit_id,
            ShedVisitStage.stage_order > stage_order,
            ShedVisitStage.status != "PENDING",
        )
        .first()
    )
    if later_active is not None:
        raise error(
            status.HTTP_409_CONFLICT,
            "STAGE_ORDER_VIOLATION",
            f"A later stage ({later_active.stage_type}) has already started; {stage_type} cannot be modified.",
        )


def assert_visit_view_access(db: Session, visit_id: int, current_user: User) -> None:
    """Common Booking Pool reform: bookings are global to the shed visit and
    visible to every authorized section, so a visit's workflow/bookings are no
    longer gated by whether the Supervisor's own section happened to have a
    section assignment on it. Any authenticated Admin or Supervisor may view
    any shed visit's workflow — the only remaining requirement is holding
    Dashboard access at all, already enforced at the route layer
    (require_dashboard_access). Supervisor's section_id is retained
    elsewhere purely for audit (who started/attended a booking), never as a
    view-access gate."""
    del db, visit_id, current_user  # no longer used for this check; kept for call-site compatibility


def stage_out(stage) -> "StageOut":
    """The one ShedVisitStage -> StageOut mapping (previously copied into each stage service)."""
    from app.schemas.workflow import StageOut

    return StageOut(
        id=stage.id,
        stage_type=stage.stage_type,
        stage_order=stage.stage_order,
        status=stage.status,
        started_at=stage.started_at,
        started_by=stage.started_by,
        completed_at=stage.completed_at,
        completed_by=stage.completed_by,
        skipped_at=stage.skipped_at,
        skipped_by=stage.skipped_by,
        skip_reason=stage.skip_reason,
    )


def _aware_ts(value):
    from datetime import timezone

    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def test_before_legacy_waived(visit, test_before_stage) -> bool:
    """Grandfathering for MINOR visits whose schedule was started under the previous workflow,
    when Start Schedule had no Test Before gate (before migration 012 / the refined workflow).

    DETERMINISTIC CUTOFF - taken from visit state, not a date or a locomotive list. Since the refined
    workflow, Start Schedule is only possible once Test Before is COMPLETED or SKIPPED, and never
    dated before that happened (schedule_lifecycle_service._assert_test_before_satisfied). So a MINOR
    visit whose schedule_started_at is set but whose Test Before was NOT completed/skipped at or
    before that start can only have been started under the old workflow. The predicate can never
    become true for a visit started through the current gate.

    For such a visit Test Before is waived downstream (Inspection / Test After sequencing, the
    Test Before stage and requirement blockers at Shed Out, BL-DCMS's Test After lock). Its Test
    Before stage row is left exactly as it is: nothing is marked completed or skipped, and no
    timestamp is written, so Test Before analytics stay unknown where they were never captured.
    """
    if visit is None or visit.schedule_family != "MINOR" or visit.schedule_started_at is None:
        return False
    if test_before_stage is None:
        return True
    if test_before_stage.status == "COMPLETED":
        satisfied_at = test_before_stage.completed_at
    elif test_before_stage.status == "SKIPPED":
        satisfied_at = test_before_stage.skipped_at
    else:
        return True
    satisfied_at = _aware_ts(satisfied_at)
    return satisfied_at is None or satisfied_at > _aware_ts(visit.schedule_started_at)
