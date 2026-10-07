"""Phase 5B.3: Authoritative Checksheet Stage Reconciliation.

The single authoritative place that decides whether a shed_visit_stage may transition to
COMPLETED. A stage completes only when ALL of:

  1. a work package exists for the visit
  2. that stage has at least one REQUIRED work-package requirement
  3. every REQUIRED requirement for that stage is SATISFIED (a BL-DCMS checksheet matching
     (shed_visit_id, workflow_stage_type, template_id) has reached a submission-satisfying status
     - SUBMITTED, UNDER_REVIEW, or APPROVED; DRAFT/REJECTED are not satisfying) - reuses
     app/services/checksheet_progress_service.py's exact correlation helpers, never a second
     copy of that matching algorithm. Business Rule Alignment: digital-signature/DSC approval
     (APPROVED) is no longer required - technician submission is what "maintenance complete"
     means here.
  4. every booking whose booking_source equals that stage's own workflow_stage_type
     (TEST_BEFORE/SCHEDULE_INSPECTION/TEST_AFTER - never LOG_BOOK/MANUAL) has at least one
     booking_section_assignments row AND every one of that booking's assignments is ATTENDED
     (Business Rule Alignment restores the assignment-based gate here too - Booking.status is
     kept in sync by booking_assignment_service.recompute_booking_status but is never trusted
     directly; see _stage_bookings_ready below)

Zero bookings for a stage trivially satisfies condition 4 (nothing to attend). Zero REQUIRED
requirements never satisfies condition 2 - that is a configuration problem
(NO_REQUIRED_CHECKSHEETS), not automatic success.

Sequencing: SCHEDULE_INSPECTION can only complete if TEST_BEFORE was ALREADY COMPLETED before
this reconciliation call started (and likewise TEST_AFTER after SCHEDULE_INSPECTION) - based on
each stage's status as loaded at the start of this call, not on a stage this same call just
completed. A single reconciliation call therefore completes at most one "new" stage per call in
the common case where stages are worked through in order one at a time; it never cascades
straight through multiple pending stages just because their evidence all happens to already be
ready. Call reconciliation again to pick up the next stage.

Idempotent: a stage that is already COMPLETED is never re-evaluated or rewritten - its
completed_at/completed_by are never touched again, and no BL-DCMS/booking work happens for it.

System-derived audit: completion is evidence-driven, not a personal judgement call, so
completed_by (and started_by, if this call is what advances a still-PENDING stage) are left NULL
- never the identity of whichever Admin happened to click the button that triggered this call.
Both started_by and completed_by are already nullable columns (see app/db/models.py), so no
migration is needed - see the Phase 5B.3 report for the full reasoning.

Phase 5B.4A: callers now pass an explicit ReconciliationActor (actor_type=USER with an
actor_user_id, from the Admin UI's own reconciliation button and the per-stage manual-complete
thin wrappers; actor_type=SYSTEM with actor_user_id=None, from BL-DCMS's internal service-to-
service trigger - see app/api/internal.py) instead of a raw current_user. This is bookkeeping
only: the actor is never consulted when deciding completed_by/started_by, which stay NULL either
way, so there is no fake Admin database user standing in for the automatic trigger.

CLOSED visits are immutable: reconciliation raises the same 409 VISIT_CLOSED contract other
mutation routes already use and changes nothing.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi import status
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSUnavailableError
from app.db.models import Booking, BookingSectionAssignment, ShedVisit, ShedVisitStage
from app.schemas.checksheet_reconciliation import (
    REASON_ALREADY_COMPLETED,
    REASON_SKIPPED,
    REASON_BLDCMS_UNAVAILABLE,
    REASON_BOOKINGS_PENDING,
    REASON_COMPLETED,
    REASON_NO_BLOCKING_REQUIREMENTS,
    REASON_STAGE_CONFIGURATION_INCOMPLETE,
    REASON_STAGE_NOT_CONFIGURED,
    REASON_PREVIOUS_STAGE_NOT_COMPLETED,
    REASON_REQUIRED_CHECKSHEETS_PENDING,
    REASON_SECTION_SIGNOFF_PENDING,
    REASON_SECTION_SIGNOFF_UNKNOWN,
    REASON_WORK_PACKAGE_MISSING,
    StageReconciliationOut,
    VisitReconciliationOut,
)
from app.services.checksheet_progress_service import _correlate_requirement, _fetch_checksheet_index
from app.services.checksheet_work_package_service import _get_existing_package
from app.services.shed_visit_service import MINOR_STAGE_SEQUENCE
from app.services.workflow_common import (
    NON_CONFIGURABLE_STAGES,
    STAGE_SATISFIED_STATUSES,
    error,
    test_before_legacy_waived,
    get_visit_or_404,
    require_minor,
)

NOT_YET_ATTENDED_ASSIGNMENT_STATUSES = ("OPEN", "IN_PROGRESS", "REOPENED")


@dataclass(frozen=True)
class ReconciliationActor:
    """Phase 5B.4A: who/what asked for this reconciliation - a human Admin via the Dashboard UI,
    or the internal service-to-service trigger BL-DCMS calls after a checksheet is APPROVED.
    Purely a bookkeeping/context value for the caller; it never changes completed_by/started_by,
    which stay NULL for evidence-driven completion regardless of actor (see module docstring) -
    there is no fake Admin user and no attribution of automatic completion to a human."""

    actor_type: Literal["USER", "SYSTEM"]
    actor_user_id: int | None = None


def _stage_bookings_ready(db: Session, visit_id: int, stage_type: str) -> bool:
    """Exact booking_source -> stage mapping: only bookings whose booking_source literally equals
    this stage's own workflow_stage_type participate. LOG_BOOK/MANUAL bookings never affect an
    individual stage's readiness - they may still block Shed Out separately (see
    shed_out_service.py).

    Business Rule Alignment: the gate is assignment-based again - every one of this stage's
    bookings must have at least one booking_section_assignments row, and every one of those
    assignments must be ATTENDED. A booking with zero assignments blocks (booking creation always
    creates at least one - see booking_creation_service.py - so zero here means a genuine routing
    gap). Booking.status is never consulted directly, even though it's kept in sync by
    booking_assignment_service.recompute_booking_status."""
    booking_ids = [
        row[0]
        for row in db.query(Booking.id)
        .filter(Booking.shed_visit_id == visit_id, Booking.booking_source == stage_type)
        .all()
    ]
    if not booking_ids:
        return True

    for booking_id in booking_ids:
        assignments = (
            db.query(BookingSectionAssignment.status)
            .filter(BookingSectionAssignment.booking_id == booking_id)
            .all()
        )
        if not assignments:
            return False
        if any(a[0] in NOT_YET_ATTENDED_ASSIGNMENT_STATUSES for a in assignments):
            return False

    return True


def _section_signoff_reason(client: BLDCMSClient | None, visit_id: int) -> str | None:
    """None when signing does not block SCHEDULE_INSPECTION, otherwise the reason it does.

    Three distinct answers, which must not be conflated:

      * the visit predates the section sign-off cutover -> None, the previous rule applies;
      * BL-DCMS cannot resolve its own sign-off state, or is unreachable -> UNKNOWN, which BLOCKS.
        Treating an unanswered question as "nothing to sign" would complete the stage on the
        strength of a failed request;
      * the workflow applies and something is still unsigned -> PENDING.

    A visit whose package names no section owing required Inspection work reports
    all_sections_signed=false with sections_total=0 from BL-DCMS; that is not a signing problem,
    so it does not block here - the stage's own NO_REQUIRED_CHECKSHEETS/configuration handling
    upstream already decides what such a visit means.
    """
    if client is None:
        return REASON_SECTION_SIGNOFF_UNKNOWN

    try:
        state = client.get_minor_section_signoffs(visit_id)
    except (BLDCMSUnavailableError, BLDCMSAuthError):
        return REASON_SECTION_SIGNOFF_UNKNOWN

    if not state.get("section_signoff_workflow"):
        return None
    if not state.get("resolved"):
        return REASON_SECTION_SIGNOFF_UNKNOWN
    if not state.get("sections_total"):
        return None
    return None if state.get("all_sections_signed") else REASON_SECTION_SIGNOFF_PENDING


def reconcile_visit_checksheet_stages(
    db: Session, client: BLDCMSClient | None, visit_id: int, actor: ReconciliationActor
) -> VisitReconciliationOut:
    del actor  # completion is system/evidence-attributed, never the triggering actor - see module docstring

    get_visit_or_404(db, visit_id)

    try:
        # Row-lock the visit - the same serialization point shed_out_service.shed_out() uses
        # against a second concurrent reconciliation (or a concurrent Shed Out) for the same
        # visit.
        visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).with_for_update().one()

        if visit.status == "CLOSED":
            raise error(
                status.HTTP_409_CONFLICT,
                "VISIT_CLOSED",
                "This shed visit is closed; checksheet stage reconciliation is not permitted.",
            )

        require_minor(visit)

        stages = (
            db.query(ShedVisitStage)
            .filter(ShedVisitStage.shed_visit_id == visit_id, ShedVisitStage.stage_type.in_(MINOR_STAGE_SEQUENCE))
            .with_for_update()
            .all()
        )
        stages_by_type = {s.stage_type: s for s in stages}

        # Ordering is evaluated against status AS LOADED AT THE START of this call, never a
        # status this same call just wrote - see the module docstring's "Sequencing" note.
        original_status = {stage_type: stages_by_type[stage_type].status for stage_type in stages_by_type}
        # Grandfathered legacy start: Test Before does not hold back the later stages (see
        # workflow_common.test_before_legacy_waived). Evaluated before this call changes anything.
        legacy_waived = test_before_legacy_waived(visit, stages_by_type.get("TEST_BEFORE"))

        package = _get_existing_package(db, visit_id)
        index = None
        if package is not None:
            if client is None:
                index = None
            else:
                index = _fetch_checksheet_index(client, visit_id)
        bldcms_reachable = package is not None and client is not None and index is not None

        requirements_by_stage: dict[str, list] = {}
        if package is not None:
            for req in package.requirements:
                requirements_by_stage.setdefault(req.workflow_stage_type, []).append(req)

        now = datetime.now(timezone.utc)
        results: list[StageReconciliationOut] = []
        changed = False

        for position, stage_type in enumerate(MINOR_STAGE_SEQUENCE):
            stage = stages_by_type.get(stage_type)
            previous_status = original_status.get(stage_type, "PENDING")

            if stage is None:
                # Shouldn't happen for a MINOR visit (all 3 stages are created at Shed In), but
                # fail closed rather than crash if a historical visit is somehow missing one.
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status="PENDING",
                        current_status="PENDING",
                        reason=REASON_WORK_PACKAGE_MISSING,
                    )
                )
                continue

            if previous_status == "COMPLETED":
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status="COMPLETED",
                        reason=REASON_ALREADY_COMPLETED,
                    )
                )
                continue

            if previous_status == "SKIPPED":
                # Admin-skipped Test Before (migration 012): never evaluated, never completed.
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status="SKIPPED",
                        reason=REASON_SKIPPED,
                    )
                )
                continue

            previous_stage_type = MINOR_STAGE_SEQUENCE[position - 1] if position > 0 else None
            previous_satisfied = (
                previous_stage_type is None
                or original_status.get(previous_stage_type) in STAGE_SATISFIED_STATUSES
                or (previous_stage_type == "TEST_BEFORE" and legacy_waived)
            )
            if not previous_satisfied:
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status=previous_status,
                        reason=REASON_PREVIOUS_STAGE_NOT_COMPLETED,
                    )
                )
                continue

            if package is None:
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status=previous_status,
                        reason=REASON_WORK_PACKAGE_MISSING,
                    )
                )
                continue

            if not bldcms_reachable:
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status=previous_status,
                        reason=REASON_BLDCMS_UNAVAILABLE,
                    )
                )
                continue

            # Operational Control phase: per-visit requirement flags now decide what blocks.
            #   ACTIVE + REQUIRED    -> blocks completion until satisfied
            #   ACTIVE + OPTIONAL    -> never blocks (may still be filled voluntarily)
            #   INACTIVE/DEACTIVATED -> never blocks
            #   TEST_BEFORE / TEST_AFTER (migration 012): ALWAYS required. Their per-visit flags are
            #   ignored here - the only Test Before exception is the explicit Admin skip handled
            #   above, and Test After has none. A legacy Optional/Deactivated override on such a
            #   row is preserved in the table but no longer changes what blocks.
            stage_reqs = requirements_by_stage.get(stage_type, [])
            if stage_type in NON_CONFIGURABLE_STAGES:
                blocking_reqs = list(stage_reqs)
            else:
                blocking_reqs = [r for r in stage_reqs if r.is_active and r.is_required]

            if not stage_reqs:
                # NOT CONFIGURED: no requirement row exists for this stage at all. This must stay
                # incomplete forever rather than completing vacuously - the original
                # staged-Minor-rollout guarantee. Critically distinct from the branch below: both
                # have zero BLOCKING rows, but only this one means "we don't know what is
                # required here".
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status=previous_status,
                        checksheets_ready=False,
                        reason=REASON_STAGE_NOT_CONFIGURED,
                    )
                )
                continue

            if stage_type == "SCHEDULE_INSPECTION" and package.minor_inspection_configuration_complete is not True:
                # PARTIALLY CONFIGURED: some sections have Inspection requirements, but not every
                # required section of this technology is configured yet. Never completes, even when
                # every snapshotted requirement is satisfied - that would be vacuous for the rest.
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status=previous_status,
                        checksheets_ready=False,
                        reason=REASON_STAGE_CONFIGURATION_INCOMPLETE,
                    )
                )
                continue

            # The stage IS configured; blocking_reqs may legitimately be empty because an Admin
            # marked every row Optional or Deactivated for this visit. all([]) is True here, and
            # that is the DELIBERATE answer - there is nothing outstanding - not the vacuous-truth
            # bug, which was the unconfigured case handled immediately above.
            checksheets_ready = all(_correlate_requirement(r, index).satisfied for r in blocking_reqs)
            # Recorded so "completed with nothing outstanding" is never reported as if work was
            # actually done. It does NOT short-circuit: the booking gate below is independent of
            # checksheets and must still be honoured.
            completion_reason = REASON_NO_BLOCKING_REQUIREMENTS if not blocking_reqs else REASON_COMPLETED

            bookings_ready = _stage_bookings_ready(db, visit_id, stage_type)
            # Test Before findings never hold Test Before open: they are ordinary bookings, tracked
            # and attended as usual and still enforced by Shed Out's booking gate, but they must not
            # keep the locomotive out of the Minor Inspection window (Start Schedule needs Test
            # Before COMPLETED). Inspection and Test After keep their booking gate.
            bookings_gate = bookings_ready if stage_type != "TEST_BEFORE" else True

            if not checksheets_ready:
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status=previous_status,
                        checksheets_ready=False,
                        bookings_ready=bookings_ready,
                        reason=REASON_REQUIRED_CHECKSHEETS_PENDING,
                    )
                )
                continue

            # Minor Inspection section sign-off (BL-DCMS migration 076). Every required
            # checksheet being satisfied is no longer enough for SCHEDULE_INSPECTION: each
            # section's Supervisor signs ONE combined document for their section, and the stage
            # completes only when every required section has been signed.
            #
            # This reverses the earlier Business Rule Alignment decision FOR THIS STAGE ONLY, at
            # the shed's explicit direction. TEST_BEFORE and TEST_AFTER are untouched and still
            # complete on technician submission, and so does SCHEDULE_INSPECTION for any visit
            # BL-DCMS reports as pre-cutover - which is every visit that existed before the
            # feature shipped, so no in-flight or historical visit becomes un-completable.
            if stage_type == "SCHEDULE_INSPECTION":
                signoff_reason = _section_signoff_reason(client, visit_id)
                if signoff_reason is not None:
                    results.append(
                        StageReconciliationOut(
                            workflow_stage_type=stage_type,
                            previous_status=previous_status,
                            current_status=previous_status,
                            checksheets_ready=True,
                            bookings_ready=bookings_ready,
                            reason=signoff_reason,
                        )
                    )
                    continue

            if not bookings_gate:
                results.append(
                    StageReconciliationOut(
                        workflow_stage_type=stage_type,
                        previous_status=previous_status,
                        current_status=previous_status,
                        checksheets_ready=True,
                        bookings_ready=False,
                        reason=REASON_BOOKINGS_PENDING,
                    )
                )
                continue

            # Every gate passed - complete the stage, straight from PENDING if need be. started_at is
            # deliberately NOT back-filled any more (migration 012): stamping the completion instant
            # as the start fabricated a zero-length stage. An unrecorded start stays NULL - see
            # app/services/stage_timing_service.py for how a real start is recorded.
            stage.status = "COMPLETED"
            stage.completed_at = now
            stage.completed_by = None
            stage.updated_at = now
            changed = True

            results.append(
                StageReconciliationOut(
                    workflow_stage_type=stage_type,
                    previous_status=previous_status,
                    current_status="COMPLETED",
                    checksheets_ready=True,
                    bookings_ready=bookings_ready,
                    reason=completion_reason,
                )
            )

        db.commit()
    except Exception:
        db.rollback()
        raise

    return VisitReconciliationOut(shed_visit_id=visit_id, changed=changed, stages=results)
