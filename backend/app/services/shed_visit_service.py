from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
)
from app.db.models import (
    Booking,
    BookingSectionAssignment,
    ShedVisit,
    ShedVisitEvent,
    ShedVisitStage,
    User,
)
from dataclasses import asdict

from app.services.workflow_common import test_before_legacy_waived
from app.domain.booking_resolution import UNRESOLVED_ASSIGNMENT_STATUSES

from app.services.shed_visit_phase import (
    PHASE_ACTIONS,
    compute_timings,
    derive_phase,
    phase_display_label,
)
from app.schemas.shed_visits import (
    ActiveShedVisitOut,
    CurrentShedVisitOut,
    VisitTimingsOut,
    ShedInRequest,
    ShedInResponse,
)
from app.services import equipment_family_service
from app.services.booking_creation_service import create_booking, resolve_and_validate_bookings

OPEN_VISIT_STATUSES = ("IN_SHED", "READY")

# Order matters: this is stage_order 1..3. SPECIAL_CHECKING is
# deliberately not part of the active Minor workflow (business decision,
# Phase 3D scope correction) — new visits get exactly these 3 stages.
# Historical rows with a SPECIAL_CHECKING stage still exist and must keep
# reading fine (see workflow_common.py's docstring); nothing here creates
# new ones.
MINOR_STAGE_SEQUENCE = ["TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"]


def _error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def _loco_unavailable() -> HTTPException:
    return _error(
        status.HTTP_502_BAD_GATEWAY,
        "LOCO_MASTER_UNAVAILABLE",
        "Equipment service is currently unavailable.",
    )


def _already_open_error(loco_number: str) -> HTTPException:
    return _error(
        status.HTTP_409_CONFLICT,
        "LOCO_ALREADY_IN_SHED",
        f"{loco_number} already has an open shed visit.",
    )


def shed_in(
    db: Session,
    client: LocoMasterClient,
    payload: ShedInRequest,
    current_user: User,
    bldcms_client=None,
) -> ShedInResponse:
    """Validates everything it can outside the DB transaction, then performs
    the whole Shed In (visit + event + stages + bookings + assignments +
    booking events) as one atomic unit — see module-level ordering notes in
    the Phase 2B report. Any failure anywhere rolls back everything."""

    # 2. Validate the locomotive against Loco Master.
    try:
        client.get_locomotive(payload.loco_number)
    except LocoMasterNotFoundError:
        raise _error(
            422,
            "UNKNOWN_LOCOMOTIVE",
            f"Unknown or inactive locomotive: {payload.loco_number}",
        )
    except (LocoMasterUnavailableError, LocoMasterAuthError):
        raise _loco_unavailable()

    # 3. schedule_family / schedule_variant / arrival_condition are already
    # validated by ShedInRequest's own validators (pydantic rejects the
    # request with 422 before this function is ever called) — including the
    # centralized family/variant matrix check in app/domain/schedule.py.

    # 4-5. Validate every booking's equipment and defect type — shared
    # with every other booking-creation flow (see
    # booking_creation_service). Equipment->section mapping resolution
    # happens later, inside create_booking() itself.
    # Technology is never asked of the user and never accepted from the browser: the locomotive
    # being shedded in decides which equipment family its Log Book bookings may name, and that is
    # derived here from BL-DCMS's authoritative technology. Resolved only when there is at least
    # one booking, so a Shed In with no Log Book entries is unaffected.
    family_code = (
        equipment_family_service.family_code_for_loco_number(bldcms_client, payload.loco_number)
        if payload.log_book_bookings
        else None
    )
    resolve_and_validate_bookings(db, client, payload.log_book_bookings, family_code=family_code)

    # Cheap common-case check before opening the write transaction — the
    # real race-safety net is the IntegrityError catch below, since two
    # concurrent requests can both pass this check.
    existing_open = (
        db.query(ShedVisit)
        .filter(ShedVisit.loco_number == payload.loco_number, ShedVisit.status.in_(OPEN_VISIT_STATUSES))
        .first()
    )
    if existing_open is not None:
        raise _already_open_error(payload.loco_number)

    now = datetime.now(timezone.utc)
    bookings_created = 0
    section_assignments_created = 0
    stages_created = 0

    # 8-14: one transaction. Any exception (ours or SQLAlchemy's) rolls
    # everything back before propagating.
    try:
        visit = ShedVisit(
            loco_number=payload.loco_number,
            arrival_at=payload.arrival_at,
            arrival_source="DASHBOARD",
            schedule_family=payload.schedule_family,
            schedule_variant=payload.schedule_variant,
            visit_type="SCHEDULED",
            status="IN_SHED",
            arrival_condition=payload.arrival_condition,
            created_by=current_user.id,
            created_at=now,
            updated_by=current_user.id,
            updated_at=now,
        )
        db.add(visit)
        try:
            db.flush()
        except IntegrityError:
            raise _already_open_error(payload.loco_number)

        db.add(
            ShedVisitEvent(
                shed_visit_id=visit.id,
                event_type="SHED_IN",
                event_time=payload.arrival_at,
                source="DASHBOARD",
                event_data={
                    "schedule_family": payload.schedule_family,
                    "schedule_variant": payload.schedule_variant,
                    "arrival_condition": payload.arrival_condition,
                },
                created_by=current_user.id,
                created_at=now,
            )
        )

        # MAJOR (IOH/TOH) visits deliberately create zero stages and no
        # checksheet work package here: MINOR_STAGE_SEQUENCE and the whole
        # generate_work_package() flow (checksheet_work_package_service.py,
        # gated by require_minor()) are Minor-only. This is not a stopgap —
        # no Major checksheet applicability/workflow exists in this
        # codebase yet, so a MAJOR shed_in() call must not fabricate one;
        # it just records the visit itself (arrival, condition, family,
        # variant) and stops there. stages_created stays 0 for MAJOR.
        if payload.schedule_family == "MINOR":
            for order, stage_type in enumerate(MINOR_STAGE_SEQUENCE, start=1):
                db.add(
                    ShedVisitStage(
                        shed_visit_id=visit.id,
                        stage_type=stage_type,
                        stage_order=order,
                        status="PENDING",
                        created_at=now,
                        updated_at=now,
                    )
                )
                stages_created += 1

        for booking_in in payload.log_book_bookings:
            _booking, assignments_created = create_booking(
                db,
                client,
                shed_visit_id=visit.id,
                stage_id=None,
                booking_source="LOG_BOOK",
                equipment_node_id=booking_in.equipment_node_id,
                defect_type_id=booking_in.defect_type_id,
                description=booking_in.remarks,
                actor=current_user,
                now=now,
            )
            bookings_created += 1
            section_assignments_created += assignments_created

        db.commit()
    except Exception:
        db.rollback()
        raise

    # Operational Control phase: snapshot this visit's checksheet requirements immediately, so
    # the Pending Checksheets panel has something to show from the moment the loco is in shed
    # rather than waiting for someone to remember to generate a work package by hand.
    #
    # Deliberately AFTER the commit above and deliberately best-effort. Shed In is a physical
    # fact that must be recordable even when BL-DCMS or Loco Master is down; folding generation
    # into the transaction would make an unreachable downstream service able to block a
    # locomotive from being admitted to the shed. A failure here therefore leaves the visit
    # intact with no package, which the panel reports explicitly as
    # work_package_generated=false - never as "nothing is pending".
    # Imported here, not at module scope: checksheet_work_package_service imports
    # MINOR_STAGE_SEQUENCE from this module, so a top-level import would be circular.
    from app.services.checksheet_work_package_service import generate_requirements_snapshot

    generate_requirements_snapshot(db, client, bldcms_client, visit.id, current_user)

    return ShedInResponse(
        id=visit.id,
        loco_number=visit.loco_number,
        status=visit.status,
        arrival_at=visit.arrival_at,
        schedule_family=visit.schedule_family,
        schedule_variant=visit.schedule_variant,
        arrival_condition=visit.arrival_condition,
        bookings_created=bookings_created,
        section_assignments_created=section_assignments_created,
        stages_created=stages_created,
    )


def list_current_visits(db: Session) -> list[CurrentShedVisitOut]:
    """Every open shed visit, NEWEST ARRIVAL FIRST.

    Ordered by shed_visits.arrival_at descending - Shed-In chronology, so a locomotive that has just
    arrived is at the top of the Overview and Shed Movement as soon as either page re-fetches. Not by
    loco number, schedule, status, ready_at, updated_at or any workflow-stage time: this list answers
    "what came in, and when".

    id DESC is a real tie-break, not decoration. arrival_at is recorded to the second and two
    locomotives genuinely can be shed in within the same second; without it, PostgreSQL may return
    such rows in any order and the list could reshuffle between two refreshes that show identical
    data. The id is monotonic, so the later-created visit wins - which is the same intent as
    newest-first.

    arrival_at is NOT NULL in the schema (see the column definition and chk_departure_after_arrival,
    which depends on it), so there is no null case to place anywhere.
    """
    visits = (
        db.query(ShedVisit)
        .filter(ShedVisit.status.in_(OPEN_VISIT_STATUSES))
        .order_by(ShedVisit.arrival_at.desc(), ShedVisit.id.desc())
        .all()
    )

    results = []
    for visit in visits:
        booking_total = (
            db.query(func.count(Booking.id)).filter(Booking.shed_visit_id == visit.id).scalar() or 0
        )
        # DERIVED FROM ASSIGNMENT ROWS, NOT FROM Booking.status.
        #
        # The comment that used to sit here said pending was derived from booking.status because
        # booking_section_assignments "no longer drives anything operational". That described a
        # superseded architecture and is now simply false: the Shed Out gate and every per-stage
        # gate re-derive from the assignment rows, and booking.status is only a cached aggregate
        # of them. Counting the cache undercounted real work - a booking that gained a section
        # through planning still read ATTENDED and silently dropped out of this count.
        #
        # ONE query, a fixed cost per visit regardless of booking count, and it answers both
        # pending cases at once. The LEFT JOIN yields a NULL assignment row for a booking that
        # has none, so:
        #   assignment id IS NULL        -> zero assignments (a routing gap; Shed Out blocks on it
        #                                   too, so it must count as pending here)
        #   status in UNRESOLVED_*       -> at least one section still has outstanding work
        # COUNT(DISTINCT) because a multi-section booking matches once per unresolved row and must
        # still be counted as a single pending booking - that is exactly the double-count this
        # join would otherwise introduce.
        pending = (
            db.query(func.count(func.distinct(Booking.id)))
            .outerjoin(
                BookingSectionAssignment,
                BookingSectionAssignment.booking_id == Booking.id,
            )
            .filter(Booking.shed_visit_id == visit.id)
            .filter(
                or_(
                    BookingSectionAssignment.id.is_(None),
                    BookingSectionAssignment.status.in_(UNRESOLVED_ASSIGNMENT_STATUSES),
                )
            )
            .scalar()
            or 0
        )
        # Single source of truth for phase/label/actions/timings - never re-derived per endpoint.
        phase = derive_phase(
            schedule_started_at=visit.schedule_started_at,
            inspection_completed_at=visit.inspection_completed_at,
            ready_at=visit.ready_at,
            departed_at=visit.departed_at,
        )
        timings = compute_timings(
            arrival_at=visit.arrival_at,
            schedule_started_at=visit.schedule_started_at,
            inspection_completed_at=visit.inspection_completed_at,
            ready_at=visit.ready_at,
            departed_at=visit.departed_at,
        )
        results.append(
            CurrentShedVisitOut(
                id=visit.id,
                loco_number=visit.loco_number,
                schedule_family=visit.schedule_family,
                schedule_variant=visit.schedule_variant,
                arrival_condition=visit.arrival_condition,
                arrival_at=visit.arrival_at,
                status=visit.status,
                schedule_started_at=visit.schedule_started_at,
                inspection_completed_at=visit.inspection_completed_at,
                ready_at=visit.ready_at,
                departed_at=visit.departed_at,
                operational_phase=phase,
                display_label=phase_display_label(phase, visit.schedule_variant),
                available_actions=list(PHASE_ACTIONS.get(phase, ())),
                timings=VisitTimingsOut(**asdict(timings)),
                booking_total=booking_total,
                pending_booking_count=pending,
            )
        )
    return results


def list_active_visits(db: Session, schedule_family: str | None = None) -> list[ActiveShedVisitOut]:
    """Phase 5B.4B: authoritative "active shed visit" list for BL-DCMS's internal read endpoint
    (app/api/internal.py's GET /api/internal/shed-visits/active). Reuses the exact same
    OPEN_VISIT_STATUSES predicate and arrival_at-descending ordering as list_current_visits()
    above - there is exactly one definition of "active/open visit" in this codebase, not a second
    one invented for BL-DCMS. schedule_family is an optional pass-through equality filter on the
    stored column value only (e.g. "MINOR") - this function never hardcodes IA/IB/IC or any other
    business rule about which families/variants exist."""
    query = db.query(ShedVisit).filter(ShedVisit.status.in_(OPEN_VISIT_STATUSES))
    if schedule_family is not None:
        query = query.filter(ShedVisit.schedule_family == schedule_family)
    visits = query.order_by(ShedVisit.arrival_at.desc()).all()

    test_before_by_visit = {
        stage.shed_visit_id: stage
        for stage in db.query(ShedVisitStage)
        .filter(
            ShedVisitStage.shed_visit_id.in_([v.id for v in visits] or [-1]),
            ShedVisitStage.stage_type == "TEST_BEFORE",
        )
        .all()
    }

    results = []
    for visit in visits:
        test_before = test_before_by_visit.get(visit.id)
        results.append(
            ActiveShedVisitOut(
                shed_visit_id=visit.id,
                loco_number=visit.loco_number,
                schedule_family=visit.schedule_family,
                schedule_variant=visit.schedule_variant,
                status=visit.status,
                arrival_at=visit.arrival_at,
                test_before_status=test_before.status if test_before is not None else None,
                test_before_skipped=test_before is not None and test_before.status == "SKIPPED",
                test_before_legacy_waived=test_before_legacy_waived(visit, test_before),
                inspection_completed=visit.inspection_completed_at is not None or visit.ready_at is not None,
                inspection_completed_at=visit.inspection_completed_at,
            )
        )
    return results
