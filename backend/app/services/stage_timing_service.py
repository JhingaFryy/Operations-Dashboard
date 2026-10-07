"""Stage start timing for Test Before / Test After (migration 012).

DEFINITION - a TEST_BEFORE / TEST_AFTER stage STARTS at the first real start of that visit's work:
  * the explicit Start action on the workflow page (test_before_service.start_test_before /
    test_after_service.start_test_after - the pre-existing, already-used stage-start event), or
  * BL-DCMS creating the visit's FIRST checksheet for that stage (this module, called by BL-DCMS
    right after the checksheet_header row is committed, with that creation instant).
Whichever happens first wins. Once started_at is set it is never moved or reset - not by a later
creation, a reopen, a rejection/resubmission, a refresh, or reconciliation.

Never inferred from arrival time, template fetches or page views. A stage that reaches COMPLETED
without any recorded start keeps started_at NULL - its duration is "not recorded", never a
fabricated zero (checksheet_stage_reconciliation_service no longer stamps started_at on completion).

COMPLETION is unchanged: completed_at is written once, when authoritative reconciliation first finds
the stage satisfied.
"""

from datetime import datetime, timedelta, timezone

from fastapi import status
from sqlalchemy.orm import Session

from app.db.models import ShedVisit, ShedVisitStage
from app.schemas.workflow import StageOut
from app.services.workflow_common import error, get_visit_or_404, require_minor, stage_out

TIMED_STAGES = ("TEST_BEFORE", "TEST_AFTER")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def record_stage_started(
    db: Session, visit_id: int, stage_type: str, started_at: datetime
) -> StageOut:
    """Idempotent. PENDING -> IN_PROGRESS with started_at; any other status is returned unchanged."""
    if stage_type not in TIMED_STAGES:
        raise error(
            422,
            "STAGE_NOT_TIMED_HERE",
            "Only TEST_BEFORE and TEST_AFTER record their start from checksheet creation.",
        )

    get_visit_or_404(db, visit_id)
    try:
        visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).with_for_update().one()
        if visit.status == "CLOSED":
            # Closed visits are immutable, stage timing included.
            raise error(
                status.HTTP_409_CONFLICT,
                "VISIT_CLOSED",
                "This shed visit is closed; stage timing can no longer change.",
            )
        require_minor(visit)

        stage = (
            db.query(ShedVisitStage)
            .filter(ShedVisitStage.shed_visit_id == visit_id, ShedVisitStage.stage_type == stage_type)
            .with_for_update()
            .one_or_none()
        )
        if stage is None:
            raise error(status.HTTP_404_NOT_FOUND, "STAGE_NOT_FOUND", f"{stage_type} stage not found")

        if stage.status != "PENDING" or stage.started_at is not None:
            # Already started, completed or skipped: the first recorded start stands.
            db.rollback()
            return stage_out(stage)

        started = _aware(started_at)
        now = datetime.now(timezone.utc)
        # A minute of tolerance for clock skew between the two services; never an hour.
        if started < _aware(visit.arrival_at) or started > now + timedelta(minutes=1):
            raise error(
                422,
                "STAGE_START_OUT_OF_RANGE",
                "A stage cannot start before the locomotive arrived or in the future.",
            )

        stage.status = "IN_PROGRESS"
        stage.started_at = started
        # started_by stays NULL: the start came from a checksheet, recorded by the system.
        stage.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(stage)
    return stage_out(stage)
