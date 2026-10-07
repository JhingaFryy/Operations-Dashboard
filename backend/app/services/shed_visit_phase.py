"""The ONE definition of a shed visit's operational phase and its timing metrics.

The shed workflow is:

    MAJOR:  Shed In -> SPARE -> Start Schedule -> SCHEDULE_IN_PROGRESS
                    -> Complete Schedule -> READY -> Shed Out -> SHED_OUT

    MINOR (migration 012):
            Shed In -> SPARE (Test Before: COMPLETED or Admin-SKIPPED)
                    -> Start Schedule -> SCHEDULE_IN_PROGRESS (the actual Minor Inspection)
                    -> Complete Schedule -> INSPECTION_COMPLETED (Test After)
                    -> Mark Ready (Test After COMPLETED) -> READY -> Shed Out -> SHED_OUT

A MINOR visit completed before migration 012 has ready_at but no inspection_completed_at and
simply reads READY, exactly as it did before - no history is reinterpreted.

Phase is DERIVED from timestamps, never stored. The persisted `status` column keeps its existing
physical meaning (IN_SHED / READY / CLOSED) and the status enum is deliberately NOT expanded with
SPARE_IA / SPARE_TOH style values - "Spare IA" is a label composed at render time from the phase
plus the schedule variant, so a schedule rename never requires a data migration.

Every endpoint and the frontend read phase from here. Duplicating this derivation is how the two
drift apart and start disagreeing about what a locomotive is doing.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

PHASE_SPARE = "SPARE"
PHASE_SCHEDULE_IN_PROGRESS = "SCHEDULE_IN_PROGRESS"
PHASE_INSPECTION_COMPLETED = "INSPECTION_COMPLETED"
PHASE_READY = "READY"
PHASE_SHED_OUT = "SHED_OUT"

# Actions each phase legitimately offers. The UI renders from this rather than re-deriving it,
# so an invalid action can never appear next to a locomotive.
PHASE_ACTIONS = {
    PHASE_SPARE: ("START_SCHEDULE",),
    PHASE_SCHEDULE_IN_PROGRESS: ("COMPLETE_SCHEDULE",),
    # MINOR only - a MAJOR visit never has inspection_completed_at, so never reaches this phase.
    PHASE_INSPECTION_COMPLETED: ("MARK_READY",),
    PHASE_READY: ("SHED_OUT",),
    PHASE_SHED_OUT: (),
}


def _aware(value: datetime | None) -> datetime | None:
    """SQLite (tests) round-trips DateTime(timezone=True) as naive; production Postgres does not.
    Normalising here keeps every duration below comparable on both."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def derive_phase(
    *,
    schedule_started_at: datetime | None,
    ready_at: datetime | None,
    departed_at: datetime | None,
    inspection_completed_at: datetime | None = None,
) -> str:
    """Most-advanced-timestamp-wins. Ordering is guaranteed by chk_shed_visit_timestamp_order."""
    if departed_at is not None:
        return PHASE_SHED_OUT
    if ready_at is not None:
        return PHASE_READY
    if inspection_completed_at is not None:
        return PHASE_INSPECTION_COMPLETED
    if schedule_started_at is not None:
        return PHASE_SCHEDULE_IN_PROGRESS
    return PHASE_SPARE


def phase_display_label(phase: str, schedule_variant: str | None) -> str:
    """Human label. The schedule variant carries the identity operators actually say:
        SPARE + IA                -> "Spare IA"
        SCHEDULE_IN_PROGRESS + IA -> "IA In Progress"
        READY                     -> "Ready"
        SHED_OUT                  -> "Shed Out"
    Never "MINOR IA" - the family is an implementation detail, not shed vocabulary.
    """
    variant = (schedule_variant or "").strip()
    if phase == PHASE_SPARE:
        return f"Spare {variant}" if variant else "Spare"
    if phase == PHASE_SCHEDULE_IN_PROGRESS:
        return f"{variant} In Progress" if variant else "Schedule In Progress"
    if phase == PHASE_INSPECTION_COMPLETED:
        return f"{variant} Inspection Complete" if variant else "Inspection Complete"
    if phase == PHASE_READY:
        return "Ready"
    return "Shed Out"


@dataclass(frozen=True)
class VisitTimings:
    """Durations in whole seconds, computed from timestamps - never stored.

    A duration that is still running (the visit has not reached the closing timestamp yet) is
    reported against `now` and flagged via its *_running companion, so a caller can tell an
    elapsed-so-far figure apart from a final one. Persisting a continuously changing value would
    be wrong the moment it was written.
    """

    waiting_seconds: int | None = None          # arrival_at  -> schedule_started_at
    waiting_running: bool = False
    # schedule_started_at -> inspection_completed_at (MINOR, migration 012+), else -> ready_at.
    # For MINOR this is the inspection duration: it excludes Test Before and Test After.
    schedule_seconds: int | None = None
    schedule_running: bool = False
    ready_delay_seconds: int | None = None      # ready_at -> departed_at
    ready_delay_running: bool = False
    total_seconds: int | None = None            # arrival_at -> departed_at
    total_running: bool = False


def compute_timings(
    *,
    arrival_at: datetime,
    schedule_started_at: datetime | None,
    ready_at: datetime | None,
    departed_at: datetime | None,
    now: datetime | None = None,
    inspection_completed_at: datetime | None = None,
) -> VisitTimings:
    arrival = _aware(arrival_at)
    started = _aware(schedule_started_at)
    ready = _aware(ready_at)
    inspection_completed = _aware(inspection_completed_at)
    departed = _aware(departed_at)
    current = _aware(now) or datetime.now(timezone.utc)

    def span(start: datetime | None, end: datetime | None) -> tuple[int | None, bool]:
        if start is None:
            return None, False
        if end is not None:
            return max(int((end - start).total_seconds()), 0), False
        return max(int((current - start).total_seconds()), 0), True

    waiting, waiting_running = span(arrival, started)
    schedule, schedule_running = span(started, inspection_completed or ready)
    ready_delay, ready_delay_running = span(ready, departed)
    total, total_running = span(arrival, departed)

    return VisitTimings(
        waiting_seconds=waiting,
        waiting_running=waiting_running,
        schedule_seconds=schedule,
        schedule_running=schedule_running,
        ready_delay_seconds=ready_delay,
        ready_delay_running=ready_delay_running,
        total_seconds=total,
        total_running=total_running,
    )


@dataclass(frozen=True)
class MinorWorkflowAnalytics:
    """The MINOR workflow's analytic spans, in whole seconds, from recorded timestamps only.

    None means "not measurable": the closing timestamp has not happened yet, a start was never
    recorded (e.g. a stage completed before stage timing existed), the stage was skipped, or the
    recorded timestamps are out of order. A skipped Test Before therefore has a NULL duration -
    never a fabricated 0. Nothing here is persisted or back-filled.
    """

    arrival_to_test_before_start_seconds: int | None = None
    test_before_seconds: int | None = None
    test_before_skipped: bool = False
    test_before_skipped_at: datetime | None = None
    test_before_to_schedule_start_seconds: int | None = None
    inspection_seconds: int | None = None
    inspection_to_test_after_start_seconds: int | None = None
    test_after_seconds: int | None = None
    test_after_to_ready_seconds: int | None = None
    ready_to_shed_out_seconds: int | None = None
    total_dwell_seconds: int | None = None


def compute_minor_workflow_analytics(visit, stages_by_type: dict) -> MinorWorkflowAnalytics:
    """`visit` is a ShedVisit; `stages_by_type` maps stage_type -> ShedVisitStage (missing = absent).

    Definitions (see migrations/012):
      1 arrival_to_test_before_start  = test_before.started_at   - arrival_at
      2 test_before                   = test_before.completed_at - test_before.started_at
      3 test_before_to_schedule_start = schedule_started_at      - (test_before.completed_at | skipped_at)
      4 inspection                    = inspection_completed_at  - schedule_started_at
      5 inspection_to_test_after_start= test_after.started_at    - inspection_completed_at
      6 test_after                    = test_after.completed_at  - test_after.started_at
      7 test_after_to_ready           = ready_at                 - test_after.completed_at
      8 ready_to_shed_out             = departed_at              - ready_at
      9 total_dwell                   = departed_at              - arrival_at
    """
    tb = stages_by_type.get("TEST_BEFORE")
    ta = stages_by_type.get("TEST_AFTER")

    def gap(start, end) -> int | None:
        start, end = _aware(start), _aware(end)
        if start is None or end is None or end < start:
            return None
        return int((end - start).total_seconds())

    skipped = tb is not None and tb.status == "SKIPPED"
    tb_started = tb.started_at if tb is not None else None
    tb_completed = tb.completed_at if tb is not None and tb.status == "COMPLETED" else None
    tb_satisfied_at = tb.skipped_at if skipped else tb_completed
    ta_started = ta.started_at if ta is not None else None
    ta_completed = ta.completed_at if ta is not None and ta.status == "COMPLETED" else None

    return MinorWorkflowAnalytics(
        arrival_to_test_before_start_seconds=gap(visit.arrival_at, tb_started),
        test_before_seconds=None if skipped else gap(tb_started, tb_completed),
        test_before_skipped=skipped,
        test_before_skipped_at=tb.skipped_at if skipped else None,
        test_before_to_schedule_start_seconds=gap(tb_satisfied_at, visit.schedule_started_at),
        inspection_seconds=gap(visit.schedule_started_at, visit.inspection_completed_at),
        inspection_to_test_after_start_seconds=gap(visit.inspection_completed_at, ta_started),
        test_after_seconds=gap(ta_started, ta_completed),
        test_after_to_ready_seconds=gap(ta_completed, visit.ready_at),
        ready_to_shed_out_seconds=gap(visit.ready_at, visit.departed_at),
        total_dwell_seconds=gap(visit.arrival_at, visit.departed_at),
    )
