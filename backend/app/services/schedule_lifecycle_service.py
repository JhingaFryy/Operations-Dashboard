"""Start Schedule / Complete Schedule / Mark Ready - the shed-workflow phase transitions.

    MAJOR:  SPARE --[Start Schedule]--> SCHEDULE_IN_PROGRESS --[Complete Schedule]--> READY
    MINOR:  SPARE --[Start Schedule]--> SCHEDULE_IN_PROGRESS --[Complete Schedule]-->
            INSPECTION_COMPLETED --[Mark Ready]--> READY
    then    READY --[Shed Out]--> SHED_OUT

Every transition takes a caller-supplied timestamp (the UI defaults it to now but keeps it editable,
because shed staff routinely record an action after the fact), so every ordering rule is enforced
here as well as by the DB CHECK - a manually edited time gets exactly the same validation.

MINOR SEMANTICS (migration 012):
  * Start Schedule is the start of the ACTUAL Minor Inspection. It requires Test Before to be
    COMPLETED (evidence-driven reconciliation) or explicitly SKIPPED by an Admin - DRAFT, REJECTED or
    a partially done Test Before never satisfies it - and cannot be dated before that happened.
  * Complete Schedule is the end of the actual Minor Inspection: it writes inspection_completed_at,
    NOT ready_at. The visit stays IN_SHED; Test After is the next stage.
  * Mark Ready requires Test After COMPLETED - Test After is always required, with no skip and no
    Admin exception - and writes ready_at / status READY.
MAJOR is unchanged in shape: Complete Schedule still writes ready_at.

THE CHECKSHEET GATE ON READY
Checksheets are mandatory for BOTH families, so the two transitions that write status READY /
ready_at - MINOR mark_ready() and the MAJOR branch of complete_schedule() - both refuse while any
required checksheet for that visit is unsatisfied (409 CHECKSHEETS_INCOMPLETE). They are the ONLY
two such writes in this codebase, and both go through
checksheet_requirement_completion_service.assert_checksheets_complete(), which is the same
definition Shed Out gates on. There is no override: not for an Admin, not for a Supervisor, not via
any parameter. MINOR's Complete Schedule is deliberately NOT gated - it records the end of the
physical inspection, not readiness, and tightening it was out of scope.

READY is still NOT departure eligibility: Shed Out re-evaluates its stage, booking and checksheet
gates from scratch, so a checksheet rejected after Ready still blocks departure.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import ShedVisit, ShedVisitEvent, ShedVisitStage, User
from app.services.shed_visit_phase import (
    PHASE_INSPECTION_COMPLETED,
    PHASE_SCHEDULE_IN_PROGRESS,
    PHASE_SPARE,
    derive_phase,
)
from app.services.checksheet_requirement_completion_service import assert_checksheets_complete
from app.services.workflow_common import STAGE_SATISFIED_STATUSES, error, get_visit_or_404

EVENT_SCHEDULE_STARTED = "SCHEDULE_STARTED"
EVENT_SCHEDULE_COMPLETED = "SCHEDULE_COMPLETED"
EVENT_MARK_READY = "MARK_READY"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _record_event(
    db: Session,
    visit: ShedVisit,
    event_type: str,
    event_time: datetime,
    actor: User | None,
    **extra_data,
) -> None:
    """Actor and source live here, not on shed_visits - the established pattern for every other
    transition in this domain, and the reason no schedule_started_by column was added."""
    now = datetime.now(timezone.utc)
    db.add(
        ShedVisitEvent(
            shed_visit_id=visit.id,
            event_type=event_type,
            event_time=event_time,
            source="DASHBOARD",
            event_data={
                "schedule_family": visit.schedule_family,
                "schedule_variant": visit.schedule_variant,
                # Recorded so a back-dated entry is visibly distinguishable from a live one.
                "recorded_at": now.isoformat(),
                **extra_data,
            },
            created_by=actor.id if actor else None,
            created_at=now,
        )
    )


def _phase(visit: ShedVisit) -> str:
    return derive_phase(
        schedule_started_at=visit.schedule_started_at,
        inspection_completed_at=visit.inspection_completed_at,
        ready_at=visit.ready_at,
        departed_at=visit.departed_at,
    )


def _locked_stage(db: Session, visit_id: int, stage_type: str) -> ShedVisitStage | None:
    return (
        db.query(ShedVisitStage)
        .filter(ShedVisitStage.shed_visit_id == visit_id, ShedVisitStage.stage_type == stage_type)
        .with_for_update()
        .one_or_none()
    )


def _minute(moment: datetime) -> datetime:
    """The start of `moment`'s minute.

    Test Before is completed or Admin-skipped with a full-precision server timestamp
    (e.g. 09:27:25.649), while every operator-facing timestamp in this app is entered and sent at
    MINUTE precision - the Start Schedule form defaults to the current local time with seconds
    truncated. An Admin who skips Test Before and immediately starts the schedule in that same
    minute therefore sent started_at=09:27:00 against a skip recorded at 09:27:25.649 and was
    refused with SCHEDULE_START_BEFORE_TEST_BEFORE, for an ordering violation of 25 seconds that
    exists only because the two values are recorded at different precisions.

    Comparing against the start of the satisfying minute is the narrowest fix: a schedule start in
    the same minute as (or after) the Test Before is accepted, while a genuinely out-of-order
    backdated start - an earlier minute, hour or day - is still refused exactly as before. The
    stored schedule_started_at is whatever the operator sent; nothing is rounded or rewritten.
    """
    return moment.replace(second=0, microsecond=0)


def _assert_test_before_satisfied(db: Session, visit: ShedVisit, started: datetime) -> None:
    """MINOR Start Schedule gate. Fails closed: a MINOR visit with no Test Before stage row cannot
    prove Test Before happened, so it cannot start."""
    stage = _locked_stage(db, visit.id, "TEST_BEFORE")
    if stage is None or stage.status not in STAGE_SATISFIED_STATUSES:
        raise error(
            409,
            "TEST_BEFORE_NOT_SATISFIED",
            "Start Schedule requires Test Before to be completed, or skipped by an Admin, first. "
            "A draft, rejected or partly completed Test Before does not count. If the Test Before "
            "checksheet was just submitted, run Reconcile on the workflow page.",
            test_before_status=stage.status if stage is not None else "MISSING",
        )
    satisfied_at = _aware(stage.skipped_at if stage.status == "SKIPPED" else stage.completed_at)
    if satisfied_at is not None and started < _minute(satisfied_at):
        raise error(
            422,
            "SCHEDULE_START_BEFORE_TEST_BEFORE",
            "The actual inspection cannot start before Test Before was completed or skipped.",
            test_before_status=stage.status,
            test_before_satisfied_at=satisfied_at.isoformat(),
        )


def start_schedule(
    db: Session, visit_id: int, started_at: datetime, current_user: User | None
) -> ShedVisit:
    visit = get_visit_or_404(db, visit_id)

    try:
        visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).with_for_update().one()

        if visit.status == "CLOSED":
            raise error(409, "VISIT_ALREADY_CLOSED", "This shed visit has already been Shed Out.")

        phase = _phase(visit)
        if phase != PHASE_SPARE:
            raise error(
                409,
                "SCHEDULE_ALREADY_STARTED",
                "This visit's schedule has already been started.",
                current_phase=phase,
                schedule_started_at=visit.schedule_started_at.isoformat()
                if visit.schedule_started_at
                else None,
            )

        arrival = _aware(visit.arrival_at)
        started = _aware(started_at)
        if started < arrival:
            raise error(
                422,
                "SCHEDULE_START_BEFORE_ARRIVAL",
                "Schedule start cannot be before the locomotive arrived.",
                arrival_at=visit.arrival_at.isoformat(),
            )

        if visit.schedule_family == "MINOR":
            _assert_test_before_satisfied(db, visit, started)

        visit.schedule_started_at = started_at
        visit.updated_by = current_user.id if current_user else None
        visit.updated_at = datetime.now(timezone.utc)
        # status deliberately stays IN_SHED: the locomotive's PHYSICAL state has not changed, only
        # its operational phase, which is derived. Expanding the status enum with SPARE_*/
        # IN_PROGRESS_* values would make every active-visit query in the system need updating.
        _record_event(db, visit, EVENT_SCHEDULE_STARTED, started_at, current_user)
        db.commit()
        db.refresh(visit)
        return visit
    except Exception:
        db.rollback()
        raise


def complete_schedule(
    db: Session,
    visit_id: int,
    completed_at: datetime,
    current_user: User | None,
    bldcms_client=None,
) -> ShedVisit:
    visit = get_visit_or_404(db, visit_id)

    try:
        visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).with_for_update().one()

        if visit.status == "CLOSED":
            raise error(409, "VISIT_ALREADY_CLOSED", "This shed visit has already been Shed Out.")

        phase = _phase(visit)
        if phase == PHASE_SPARE:
            raise error(
                409,
                "SCHEDULE_NOT_STARTED",
                "This visit's schedule has not been started yet.",
                current_phase=phase,
            )
        if phase != PHASE_SCHEDULE_IN_PROGRESS:
            raise error(
                409,
                "SCHEDULE_ALREADY_COMPLETED",
                "This visit's schedule has already been completed.",
                current_phase=phase,
                inspection_completed_at=visit.inspection_completed_at.isoformat()
                if visit.inspection_completed_at
                else None,
                ready_at=visit.ready_at.isoformat() if visit.ready_at else None,
            )

        started = _aware(visit.schedule_started_at)
        completed = _aware(completed_at)
        if completed < started:
            raise error(
                422,
                "SCHEDULE_COMPLETE_BEFORE_START",
                "Schedule completion cannot be before the schedule started.",
                schedule_started_at=visit.schedule_started_at.isoformat(),
            )

        now = datetime.now(timezone.utc)
        if visit.schedule_family == "MINOR":
            # The actual Minor Inspection ended. NOT ready: Test After comes next, and the physical
            # status stays IN_SHED until Mark Ready. Deliberately ungated - readiness is decided at
            # Mark Ready, and this action only records that the physical work stopped.
            visit.inspection_completed_at = completed_at
            milestone = "INSPECTION_COMPLETED"
        else:
            # MAJOR becomes READY here, so this is a Ready-capable write and carries the same
            # checksheet gate as MINOR's Mark Ready. Major checksheets are mandatory too; the
            # requirements are BL-DCMS's own Pattern A/B/C resolution, frozen per visit.
            assert_checksheets_complete(db, bldcms_client, visit)
            visit.ready_at = completed_at
            visit.ready_source = "DASHBOARD"
            visit.status = "READY"
            milestone = "SCHEDULE_COMPLETED"
        visit.updated_by = current_user.id if current_user else None
        visit.updated_at = now
        _record_event(db, visit, EVENT_SCHEDULE_COMPLETED, completed_at, current_user, milestone=milestone)
        db.commit()
        db.refresh(visit)
        return visit
    except Exception:
        db.rollback()
        raise


def mark_ready(
    db: Session,
    visit_id: int,
    ready_at: datetime,
    current_user: User | None,
    bldcms_client=None,
) -> ShedVisit:
    """MINOR only: INSPECTION_COMPLETED -> READY, once Test After is COMPLETED **and** every
    required checksheet for the visit is satisfied.

    Test After has no skip, no Optional and no Admin exception, so there is no path to READY
    without it. Nor is there one past an outstanding Minor Inspection checksheet: a required
    checksheet that is missing, DRAFT or REJECTED refuses this transition with
    CHECKSHEETS_INCOMPLETE. Never closes or departs the visit."""
    visit = get_visit_or_404(db, visit_id)

    try:
        visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).with_for_update().one()

        if visit.status == "CLOSED":
            raise error(409, "VISIT_ALREADY_CLOSED", "This shed visit has already been Shed Out.")
        if visit.schedule_family != "MINOR":
            raise error(
                409,
                "MARK_READY_NOT_APPLICABLE",
                "Mark Ready applies to Minor schedules only; a Major schedule becomes Ready at Complete Schedule.",
            )

        phase = _phase(visit)
        if phase != PHASE_INSPECTION_COMPLETED:
            raise error(
                409,
                "INSPECTION_NOT_COMPLETED" if phase in (PHASE_SPARE, PHASE_SCHEDULE_IN_PROGRESS) else "ALREADY_READY",
                "Mark Ready is only available after Complete Schedule, and only once.",
                current_phase=phase,
            )

        test_after = _locked_stage(db, visit.id, "TEST_AFTER")
        if test_after is None or test_after.status != "COMPLETED":
            raise error(
                409,
                "TEST_AFTER_NOT_SATISFIED",
                "The locomotive cannot be marked Ready until Test After is completed. Test After is "
                "always required for a Minor schedule.",
                test_after_status=test_after.status if test_after is not None else "MISSING",
            )

        # Every required checksheet: Test Before (or its audited Admin skip), every required Minor
        # Inspection checksheet, and Test After. Checked after the stage gate so the more specific
        # Test After message still wins when that alone is missing.
        assert_checksheets_complete(db, bldcms_client, visit)

        ready = _aware(ready_at)
        inspection_completed = _aware(visit.inspection_completed_at)
        if ready < inspection_completed:
            raise error(
                422,
                "READY_BEFORE_INSPECTION_COMPLETED",
                "Ready cannot be before the inspection was completed.",
                inspection_completed_at=visit.inspection_completed_at.isoformat(),
            )
        ta_completed = _aware(test_after.completed_at)
        if ta_completed is not None and ready < ta_completed:
            raise error(
                422,
                "READY_BEFORE_TEST_AFTER",
                "Ready cannot be before Test After was completed.",
                test_after_completed_at=ta_completed.isoformat(),
            )

        visit.ready_at = ready_at
        visit.ready_source = "DASHBOARD"
        visit.status = "READY"
        visit.updated_by = current_user.id if current_user else None
        visit.updated_at = datetime.now(timezone.utc)
        _record_event(db, visit, EVENT_MARK_READY, ready_at, current_user)
        db.commit()
        db.refresh(visit)
        return visit
    except Exception:
        db.rollback()
        raise
