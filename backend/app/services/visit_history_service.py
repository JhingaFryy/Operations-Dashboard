"""Shed visit history - the permanent, searchable archive of every shed visit.

OWNERSHIP (nothing is copied between systems)
  Operations Dashboard - shed visits, timings, stages, bookings, assignments, booking and visit
                         events: read here, from this app's own tables.
  BL-DCMS              - checksheets, their lifecycle and signed documents, and each locomotive's
                         model: asked over the internal channel, in batches, never stored here.
  Loco Master          - equipment names/paths for bookings: asked per distinct node, detail only.
The archive IS the shed_visits table, which never deletes a visit - administratively reset ones
included - so there is no second history store to keep in step.

COST
  The list is one paged query plus, for that page only, one batched booking count, one reset-event
  lookup, one BL-DCMS call for locomotive models and one for checksheet counts - never a call per
  row. If BL-DCMS is unreachable the list still loads, with models and counts marked unknown
  rather than zero. Timings, bookings, checksheets and events are assembled only for the one visit
  that is opened.

AUTHORIZATION (the existing Operations Dashboard rule, not a new one)
  Every operations user may see the visit list and a visit's timings and events - exactly like
  the live register. Booking and checksheet DETAIL is scoped: Admin sees every section; a
  Supervisor (movement Supervisors included - can_access_all_sections is Admin-only) sees only
  their own section's bookings, assignments and checksheets, and their own section's counts.

HISTORY IS NEVER HIDDEN
  Active, READY, CLOSED and administratively reset visits are all listed. A reset visit is shown
  as "Closed administratively", never as a Shed Out. Optional and deactivated requirements do not
  remove a checksheet from history; they only annotate it.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from fastapi import HTTPException, status
from fastapi.responses import Response
from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSNotFoundError, BLDCMSUnavailableError
from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
)
from app.core.authz import can_access_all_sections
from app.db.models import (
    Booking,
    BookingDefectType,
    BookingEvent,
    BookingSectionAssignment,
    Section,
    ShedVisit,
    ShedVisitEvent,
    ShedVisitStage,
    User,
)
from app.schemas.visit_history import (
    AssignmentHistory,
    BookingHistory,
    BookingSectionGroup,
    ChecksheetHistory,
    ChecksheetRequirementFlags,
    EquipmentRef,
    ReopenRecord,
    RequirementFlagEntry,
    TimingMilestone,
    TimingSpan,
    VisitChecksheets,
    VisitClosure,
    VisitEventOut,
    VisitHistoryDetail,
    VisitHistoryPage,
    VisitHistoryRow,
    VisitTimings,
)
from app.services.checksheet_work_package_service import _get_existing_package
from app.services.event_formatting import (
    NOT_RECORDED,
    format_booking_event,
    format_visit_event,
    is_admin_reset,
    source_label,
)
from app.services.shed_visit_phase import (
    compute_minor_workflow_analytics,
    compute_timings,
    derive_phase,
    phase_display_label,
)

OPEN_STATUSES = ("IN_SHED", "READY")
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
SIGNED_DOCUMENT_ROUTE = "/api/shed-visit-history/{visit_id}/checksheets/{checksheet_id}/signed-document"
UNASSIGNED_GROUP = "No responsible section"


@dataclass
class HistoryFilters:
    loco_number: str | None = None
    loco_model: str | None = None
    schedule_family: str | None = None
    schedule_variant: str | None = None
    status: str | None = None
    active: bool | None = None
    visit_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    arrived_from: date | None = None
    arrived_to: date | None = None
    departed_from: date | None = None
    departed_to: date | None = None
    departure_source: str | None = None
    section_id: int | None = None
    booking_status: str | None = None


def scope_for(user: User) -> int | None:
    """None = every section (Admin). Otherwise the one section this user may see in detail."""
    if can_access_all_sections(user):
        return None
    if user.section_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has no section assigned, so visit details cannot be scoped.",
        )
    return user.section_id


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=timezone.utc)


def _unavailable(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"code": "BLDCMS_UNAVAILABLE", "message": f"{what} could not be read from BL-DCMS right now."},
    )


# ============================================================================== the list ==

def search_visits(
    db: Session,
    filters: HistoryFilters,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    scope_section_id: int | None = None,
    bldcms: BLDCMSClient | None = None,
) -> VisitHistoryPage:
    page = max(page, 1)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    query = db.query(ShedVisit)

    if filters.loco_number:
        query = query.filter(ShedVisit.loco_number.ilike(f"%{filters.loco_number.strip()}%"))
    if filters.loco_model:
        # Resolved server-side against BL-DCMS's master data - never by loading every visit.
        if bldcms is None:
            raise _unavailable("Locomotive models")
        try:
            numbers = [b["loco_number"] for b in bldcms.get_locomotive_briefs(loco_model=filters.loco_model)]
        except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError):
            raise _unavailable("Locomotive models")
        query = query.filter(ShedVisit.loco_number.in_(numbers or ["\x00"]))
    if filters.schedule_family:
        query = query.filter(ShedVisit.schedule_family == filters.schedule_family.upper())
    if filters.schedule_variant:
        query = query.filter(ShedVisit.schedule_variant == filters.schedule_variant.upper())
    if filters.status:
        query = query.filter(ShedVisit.status == filters.status.upper())
    if filters.active is True:
        query = query.filter(ShedVisit.status.in_(OPEN_STATUSES))
    elif filters.active is False:
        query = query.filter(ShedVisit.status == "CLOSED")
    if filters.visit_id is not None:
        query = query.filter(ShedVisit.id == filters.visit_id)
    if filters.arrived_from:
        query = query.filter(ShedVisit.arrival_at >= _day_start(filters.arrived_from))
    if filters.arrived_to:
        query = query.filter(ShedVisit.arrival_at <= _day_end(filters.arrived_to))
    if filters.departed_from:
        query = query.filter(ShedVisit.departed_at >= _day_start(filters.departed_from))
    if filters.departed_to:
        query = query.filter(ShedVisit.departed_at <= _day_end(filters.departed_to))
    if filters.date_from:
        # "In shed at any point on or after": still open, or departed on/after the date.
        query = query.filter(or_(ShedVisit.departed_at.is_(None),
                                 ShedVisit.departed_at >= _day_start(filters.date_from)))
    if filters.date_to:
        query = query.filter(ShedVisit.arrival_at <= _day_end(filters.date_to))
    if filters.departure_source:
        query = query.filter(ShedVisit.departure_source == filters.departure_source.upper())
    if (scope_section_id is not None and filters.section_id is not None
            and filters.section_id != scope_section_id):
        # A scoped user may not probe which visits another section worked on.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only filter visit history by your own section.",
        )
    if filters.section_id is not None:
        query = query.filter(exists().where(
            BookingSectionAssignment.booking_id == Booking.id,
            Booking.shed_visit_id == ShedVisit.id,
            BookingSectionAssignment.section_id == filters.section_id,
        ))
    if filters.booking_status:
        booking_match = [Booking.shed_visit_id == ShedVisit.id, Booking.status == filters.booking_status.upper()]
        if scope_section_id is not None:
            # Only the user's own section's bookings count, exactly like their booking counts.
            booking_match += [BookingSectionAssignment.booking_id == Booking.id,
                              BookingSectionAssignment.section_id == scope_section_id]
        query = query.filter(exists().where(*booking_match))

    total = query.count()
    # Newest first, deterministically: arrival, then id as the tie-breaker.
    visits = (
        query.order_by(ShedVisit.arrival_at.desc(), ShedVisit.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    ids = [v.id for v in visits]

    booking_counts = _booking_counts(db, ids, scope_section_id)
    resets = _reset_events(db, ids)

    locomotive_ok = True
    models: dict[str, dict] = {}
    counts_ok = True
    checksheet_counts: dict[int, int] = {}
    if visits:
        if bldcms is None:
            locomotive_ok = counts_ok = False
        else:
            try:
                numbers = sorted({v.loco_number for v in visits})
                models = {b["loco_number"]: b for b in bldcms.get_locomotive_briefs(loco_numbers=numbers)}
            except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError):
                locomotive_ok = False
            try:
                checksheet_counts = bldcms.count_visit_checksheets(ids, section_id=scope_section_id)
            except (BLDCMSUnavailableError, BLDCMSAuthError):
                counts_ok = False

    items = [
        _row(v, models.get(v.loco_number), booking_counts.get(v.id, 0),
             checksheet_counts.get(v.id, 0) if counts_ok else None, resets.get(v.id))
        for v in visits
    ]
    return VisitHistoryPage(
        items=items, total=total, page=page, page_size=page_size,
        locomotive_details_available=locomotive_ok, checksheet_counts_available=counts_ok,
    )


def _booking_counts(db: Session, ids: list[int], scope_section_id: int | None) -> dict[int, int]:
    if not ids:
        return {}
    query = db.query(Booking.shed_visit_id, func.count(func.distinct(Booking.id))).filter(
        Booking.shed_visit_id.in_(ids)
    )
    if scope_section_id is not None:
        query = query.join(BookingSectionAssignment, BookingSectionAssignment.booking_id == Booking.id).filter(
            BookingSectionAssignment.section_id == scope_section_id
        )
    return dict(query.group_by(Booking.shed_visit_id).all())


def _reset_events(db: Session, ids: list[int]) -> dict[int, ShedVisitEvent]:
    """The administrative reset event of each visit on the page, in one query."""
    if not ids:
        return {}
    rows = (
        db.query(ShedVisitEvent)
        .filter(ShedVisitEvent.shed_visit_id.in_(ids), ShedVisitEvent.event_type == "MANUAL_CORRECTION")
        .order_by(ShedVisitEvent.event_time)
        .all()
    )
    return {e.shed_visit_id: e for e in rows if is_admin_reset(e)}


def _closure(visit: ShedVisit, reset_event: ShedVisitEvent | None) -> VisitClosure | None:
    if visit.status != "CLOSED":
        return None
    if visit.departure_source == "SYSTEM" and reset_event is not None:
        data = reset_event.event_data or {}
        return VisitClosure(
            kind="ADMIN_RESET", label="Closed administratively (system reset)",
            at=visit.departed_at, source=visit.departure_source, actor_name="System",
            reason=reset_event.remarks or data.get("reason"),
        )
    if visit.departure_source in (None, "DASHBOARD"):
        return VisitClosure(kind="SHED_OUT", label="Shed Out", at=visit.departed_at,
                            source=visit.departure_source)
    return VisitClosure(kind="OTHER", label=f"Closed ({visit.departure_source})",
                        at=visit.departed_at, source=visit.departure_source)


def _row(visit: ShedVisit, loco: dict | None, booking_count: int, checksheet_count: int | None,
         reset_event: ShedVisitEvent | None) -> VisitHistoryRow:
    phase = derive_phase(
        schedule_started_at=visit.schedule_started_at,
        inspection_completed_at=visit.inspection_completed_at,
        ready_at=visit.ready_at,
        departed_at=visit.departed_at,
    )
    closure = _closure(visit, reset_event)
    label = (
        closure.label if closure and closure.kind == "ADMIN_RESET"
        else phase_display_label(phase, visit.schedule_variant)
    )
    return VisitHistoryRow(
        shed_visit_id=visit.id,
        loco_number=visit.loco_number,
        loco_model=(loco or {}).get("loco_model"),
        technology=(loco or {}).get("technology"),
        schedule_family=visit.schedule_family,
        schedule_variant=visit.schedule_variant,
        visit_type=visit.visit_type,
        status=visit.status,
        operational_phase=phase,
        display_label=label,
        arrival_at=visit.arrival_at,
        arrival_condition=visit.arrival_condition,
        schedule_started_at=visit.schedule_started_at,
        ready_at=visit.ready_at,
        departed_at=visit.departed_at,
        departure_source=visit.departure_source,
        closure=closure,
        booking_count=booking_count,
        checksheet_count=checksheet_count,
    )


def list_locomotive_models(bldcms: BLDCMSClient | None) -> list[str]:
    if bldcms is None:
        raise _unavailable("Locomotive models")
    try:
        return bldcms.get_locomotive_models()
    except (BLDCMSUnavailableError, BLDCMSAuthError):
        raise _unavailable("Locomotive models")


# ============================================================================ the detail ==

def _visit_or_404(db: Session, visit_id: int) -> ShedVisit:
    visit = db.get(ShedVisit, visit_id)
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shed visit not found")
    return visit


def _names(db: Session, ids: set) -> dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {uid: name for uid, name in db.query(User.id, User.name).filter(User.id.in_(wanted)).all()}


def get_visit_detail(
    db: Session,
    visit_id: int,
    *,
    scope_section_id: int | None,
    include_raw: bool = False,
    include_checksheets: bool = True,
    bldcms: BLDCMSClient | None = None,
    loco: LocoMasterClient | None = None,
) -> VisitHistoryDetail:
    visit = _visit_or_404(db, visit_id)
    stages = {s.stage_type: s for s in db.query(ShedVisitStage).filter_by(shed_visit_id=visit.id).all()}
    visit_events = (
        db.query(ShedVisitEvent).filter_by(shed_visit_id=visit.id)
        .order_by(ShedVisitEvent.event_time, ShedVisitEvent.id).all()
    )
    bookings = (
        db.query(Booking).filter(Booking.shed_visit_id == visit.id)
        .order_by(Booking.created_at, Booking.id).all()
    )
    booking_ids = [b.id for b in bookings] or [-1]
    assignments = (
        db.query(BookingSectionAssignment)
        .filter(BookingSectionAssignment.booking_id.in_(booking_ids))
        .order_by(BookingSectionAssignment.id).all()
    )
    booking_events = (
        db.query(BookingEvent)
        .filter(BookingEvent.booking_id.in_(booking_ids))
        .order_by(BookingEvent.created_at, BookingEvent.id).all()
    )

    # One batched lookup for every actor this visit mentions.
    actor_ids = {e.created_by for e in visit_events} | {e.created_by for e in booking_events}
    actor_ids |= {b.created_by for b in bookings}
    actor_ids |= {a.assigned_by for a in assignments} | {a.started_by for a in assignments}
    actor_ids |= {a.attended_by for a in assignments}
    for s in stages.values():
        actor_ids |= {s.started_by, s.completed_by, s.skipped_by}
    names = _names(db, actor_ids)
    sections = {s.id: s for s in db.query(Section).all()}

    reset_event = next((e for e in visit_events if is_admin_reset(e)), None)
    loco_brief = None
    if bldcms is not None:
        try:
            loco_brief = next(iter(bldcms.get_locomotive_briefs(loco_numbers=[visit.loco_number])), None)
        except (BLDCMSUnavailableError, BLDCMSAuthError):
            loco_brief = None

    booking_count = sum(
        1 for b in bookings
        if scope_section_id is None
        or any(a.booking_id == b.id and a.section_id == scope_section_id for a in assignments)
    )
    row = _row(visit, loco_brief, booking_count, None, reset_event)

    checksheets = (
        _checksheets(db, visit, scope_section_id, bldcms)
        if include_checksheets
        else VisitChecksheets(available=False, message="Checksheets are provided by BL-DCMS directly.")
    )
    if include_checksheets and checksheets.available:
        row.checksheet_count = len(checksheets.items)

    return VisitHistoryDetail(
        visit=row,
        timings=_timings(visit, stages, names, reset_event),
        bookings=_booking_groups(bookings, assignments, booking_events, names, sections,
                                 scope_section_id, include_raw, db, loco),
        booking_scope="ALL_SECTIONS" if scope_section_id is None else "OWN_SECTION",
        checksheets=checksheets,
        events=[VisitEventOut(**vars(format_visit_event(e, names=names, include_raw=include_raw)))
                for e in visit_events],
        event_log_filter={"shed_visit_id": visit.id},
        requirement_flags=[] if include_checksheets else _requirement_entries(db, visit),
    )


# --------------------------------------------------------------------------- timings --

def _timings(visit: ShedVisit, stages: dict, names: dict[int, str],
             reset_event: ShedVisitEvent | None) -> VisitTimings:
    def who(user_id):
        return names.get(user_id, NOT_RECORDED) if user_id is not None else None

    minor = visit.schedule_family == "MINOR"
    milestones = [TimingMilestone(key="arrival", label="Shed In (arrival)", at=visit.arrival_at)]
    tb, ta = stages.get("TEST_BEFORE"), stages.get("TEST_AFTER")
    if minor:
        if tb is not None and tb.status == "SKIPPED":
            milestones.append(TimingMilestone(
                key="test_before_skipped", label="Test Before skipped (Admin)", at=tb.skipped_at,
                actor_name=who(tb.skipped_by), note=tb.skip_reason))
        else:
            milestones += [
                TimingMilestone(key="test_before_started", label="Test Before started",
                                at=tb.started_at if tb else None,
                                actor_name=who(tb.started_by) if tb else None),
                TimingMilestone(key="test_before_completed", label="Test Before completed",
                                at=tb.completed_at if tb and tb.status == "COMPLETED" else None,
                                actor_name=who(tb.completed_by) if tb else None),
            ]
    milestones.append(TimingMilestone(key="schedule_started", label="Schedule started",
                                      at=visit.schedule_started_at))
    if minor:
        milestones += [
            TimingMilestone(key="inspection_completed", label="Minor Inspection completed",
                            at=visit.inspection_completed_at),
            TimingMilestone(key="test_after_started", label="Test After started",
                            at=ta.started_at if ta else None,
                            actor_name=who(ta.started_by) if ta else None),
            TimingMilestone(key="test_after_completed", label="Test After completed",
                            at=ta.completed_at if ta and ta.status == "COMPLETED" else None,
                            actor_name=who(ta.completed_by) if ta else None),
        ]
    milestones.append(TimingMilestone(key="ready", label="Ready", at=visit.ready_at))
    if reset_event is not None and visit.departure_source == "SYSTEM":
        milestones.append(TimingMilestone(
            key="departed", label="Closed administratively (system reset)", at=visit.departed_at,
            actor_name="System", note="Not a Shed Out"))
    else:
        milestones.append(TimingMilestone(key="departed", label="Shed Out", at=visit.departed_at))

    generic = compute_timings(
        arrival_at=visit.arrival_at, schedule_started_at=visit.schedule_started_at,
        ready_at=visit.ready_at, departed_at=visit.departed_at,
        inspection_completed_at=visit.inspection_completed_at,
    )
    is_open = visit.status != "CLOSED"

    def span(key, label, seconds, running=False):
        # A span still "running" is only meaningful while the visit is open. On a closed visit
        # an unfinished span is simply not recorded - never a figure measured up to today.
        if running and not is_open:
            seconds = None
        return TimingSpan(key=key, label=label, seconds=seconds, running=running and is_open)

    spans = [span("waiting", "Arrival to schedule start", generic.waiting_seconds, generic.waiting_running)]
    if minor:
        analytics = compute_minor_workflow_analytics(visit, stages)
        spans += [
            span("arrival_to_test_before", "Arrival to Test Before start",
                 analytics.arrival_to_test_before_start_seconds),
            span("test_before", "Test Before duration", analytics.test_before_seconds),
            span("test_before_to_schedule", "Test Before complete/skip to schedule start",
                 analytics.test_before_to_schedule_start_seconds),
            span("inspection", "Minor Inspection duration", generic.schedule_seconds, generic.schedule_running),
            span("inspection_to_test_after", "Inspection complete to Test After start",
                 analytics.inspection_to_test_after_start_seconds),
            span("test_after", "Test After duration", analytics.test_after_seconds),
            span("test_after_to_ready", "Test After complete to Ready", analytics.test_after_to_ready_seconds),
        ]
    else:
        spans.append(span("schedule", "Schedule duration", generic.schedule_seconds, generic.schedule_running))
    spans += [
        span("ready_to_departure", "Ready to Shed Out", generic.ready_delay_seconds, generic.ready_delay_running),
        span("total_dwell", "Total shed dwell", generic.total_seconds, generic.total_running),
    ]
    return VisitTimings(milestones=milestones, spans=spans)


# -------------------------------------------------------------------------- bookings --

def _equipment_lookup(loco: LocoMasterClient | None, node_ids: set[int]) -> dict[int, EquipmentRef]:
    """Authoritative Loco Master names/paths, one call per DISTINCT node of this visit. A node that
    cannot be resolved still shows its id - never a made-up name."""
    out: dict[int, EquipmentRef] = {}
    for node_id in sorted(node_ids):
        ref = EquipmentRef(node_id=node_id)
        if loco is not None:
            try:
                node = loco.get_equipment_node(node_id)
                ref.name = node.get("name")
                ref.path = [p.get("name") for p in node.get("path", []) if p.get("name")]
            except (LocoMasterNotFoundError, LocoMasterUnavailableError, LocoMasterAuthError):
                pass
        out[node_id] = ref
    return out


def _booking_groups(bookings, assignments, events, names, sections, scope_section_id,
                    include_raw, db, loco) -> list[BookingSectionGroup]:
    section_names = {sid: s.name for sid, s in sections.items()}
    defect_types = dict(db.query(BookingDefectType.id, BookingDefectType.name).all())
    by_booking: dict[int, list] = {}
    for a in assignments:
        by_booking.setdefault(a.booking_id, []).append(a)
    events_by_booking: dict[int, list] = {}
    for e in events:
        events_by_booking.setdefault(e.booking_id, []).append(e)
    equipment = _equipment_lookup(loco, {b.equipment_node_id for b in bookings if b.equipment_node_id})

    def who(user_id):
        return names.get(user_id, NOT_RECORDED) if user_id is not None else NOT_RECORDED

    groups: dict[int | None, list[BookingHistory]] = {}
    for booking in bookings:
        own = by_booking.get(booking.id, [])
        visible = [a for a in own if scope_section_id is None or a.section_id == scope_section_id]
        if scope_section_id is not None and not visible:
            continue   # another section's booking: not this Supervisor's to see
        booking_events = events_by_booking.get(booking.id, [])
        visible_events = [
            e for e in booking_events
            if scope_section_id is None or e.to_section_id in (None, scope_section_id)
        ]
        created = next((e for e in booking_events if e.event_type == "CREATED"), None)
        origin = (created.event_data or {}).get("checksheet_id") if created is not None else None

        assignment_rows = []
        for a in visible:
            reopens = [
                ReopenRecord(at=e.created_at, by_name=who(e.created_by), reason=e.remarks)
                for e in booking_events if e.event_type == "REOPENED" and e.to_section_id == a.section_id
            ]
            section = sections.get(a.section_id)
            if a.assigned_by is not None:
                assigned_by = who(a.assigned_by)
            elif a.assignment_source == "AUTO_MAPPING":
                assigned_by = "System (automatic routing)"
            else:
                assigned_by = NOT_RECORDED
            assignment_rows.append(AssignmentHistory(
                assignment_id=a.id, section_id=a.section_id,
                section_code=section.code if section else None,
                section_name=section.name if section else None,
                status=a.status, assignment_source=a.assignment_source,
                assigned_at=a.assigned_at, assigned_by_name=assigned_by,
                started_at=a.started_at,
                started_by_name=who(a.started_by) if a.started_at else None,
                attended_at=a.attended_at,
                attended_by_name=who(a.attended_by) if a.attended_at else None,
                attendance_remarks=a.attendance_remarks,
                reopens=reopens,
            ))

        stage_type = booking.booking_source if booking.booking_source in (
            "TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER") else None
        history = BookingHistory(
            booking_id=booking.id,
            booking_source=booking.booking_source,
            booking_source_label=source_label(booking.booking_source),
            stage_type=stage_type,
            equipment=equipment.get(booking.equipment_node_id, EquipmentRef()),
            defect_type=defect_types.get(booking.defect_type_id),
            remarks=booking.description,
            status=booking.status,
            created_at=booking.created_at,
            created_by_name=who(booking.created_by),
            origin_checksheet_id=origin,
            responsible_sections=[section_names.get(a.section_id, str(a.section_id)) for a in visible],
            assignments=assignment_rows,
            history=[
                VisitEventOut(**vars(format_booking_event(e, names=names, sections=section_names,
                                                         include_raw=include_raw)))
                for e in visible_events
            ],
        )
        # A booking routed to several sections appears under each of them.
        for section_id in ([a.section_id for a in visible] or [None]):
            groups.setdefault(section_id, []).append(history)

    def sort_key(item):
        section_id = item[0]
        return (section_id is None, sections[section_id].name if section_id in sections else "")

    out = []
    for section_id, items in sorted(groups.items(), key=sort_key):
        section = sections.get(section_id) if section_id is not None else None
        out.append(BookingSectionGroup(
            section_id=section_id,
            section_code=section.code if section else None,
            section_name=section.name if section else UNASSIGNED_GROUP,
            booking_count=len(items),
            bookings=items,
        ))
    return out


# ------------------------------------------------------------------------ checksheets --

def workflow_group(family: str | None, stage: str | None) -> str:
    """TEST_BEFORE / SCHEDULE_INSPECTION / TEST_AFTER for Minor, MAJOR for Major, and the family
    itself for anything else - a future TI or GC workflow is grouped, never dropped."""
    if family == "MINOR" and stage:
        return stage
    if family == "MAJOR":
        return "MAJOR"
    return family or "UNCLASSIFIED"


def _requirement_flags(db: Session, visit: ShedVisit) -> dict[tuple, ChecksheetRequirementFlags]:
    from app.services.checksheet_requirement_completion_service import _requirement_identity

    package = _get_existing_package(db, visit.id)
    if package is None:
        return {}
    return {
        _requirement_identity(r): ChecksheetRequirementFlags(is_required=r.is_required, is_active=r.is_active)
        for r in package.requirements
    }


def _requirement_entries(db: Session, visit: ShedVisit) -> list[RequirementFlagEntry]:
    package = _get_existing_package(db, visit.id)
    if package is None:
        return []
    return [
        RequirementFlagEntry(
            template_id=r.template_id, workflow_stage_type=r.workflow_stage_type,
            section_id=r.section_id_snapshot, equipment_id=r.equipment_id_snapshot,
            maintenance_type=r.maintenance_type_snapshot,
            minor_inspection_equipment_id=r.minor_inspection_equipment_id,
            is_required=r.is_required, is_active=r.is_active,
        )
        for r in package.requirements
    ]


def _checksheets(db: Session, visit: ShedVisit, scope_section_id: int | None,
                 bldcms: BLDCMSClient | None) -> VisitChecksheets:
    from app.services.checksheet_requirement_completion_service import _identity

    if bldcms is None:
        return VisitChecksheets(available=False, message="BL-DCMS is not configured, so checksheets cannot be shown.")
    try:
        items = bldcms.get_visit_checksheets(visit.id)["items"]
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError):
        return VisitChecksheets(available=False, message="Checksheets could not be read from BL-DCMS right now.")

    flags = _requirement_flags(db, visit)
    out = []
    for item in items:
        if scope_section_id is not None and item.get("section_id") != scope_section_id:
            continue
        signature = item.get("digital_signature") or {}
        signed_available = bool(item.get("signed_document_available"))
        identity = _identity(item.get("template_id"), item.get("workflow_stage_type"), item.get("section_id"),
                             item.get("equipment_id"), item.get("maintenance_type"),
                             item.get("minor_inspection_equipment_id"))
        out.append(ChecksheetHistory(
            checksheet_id=item["checksheet_id"],
            template_id=item.get("template_id"),
            template_name=item.get("template_name"),
            schedule_family=item.get("schedule_family"),
            workflow_stage_type=item.get("workflow_stage_type"),
            workflow_group=workflow_group(item.get("schedule_family"), item.get("workflow_stage_type")),
            section_id=item.get("section_id"),
            section_name=item.get("section_name"),
            equipment_label=item.get("minor_inspection_equipment_label") or item.get("equipment_name"),
            maintenance_type=item.get("maintenance_type"),
            status=item.get("status") or "UNKNOWN",
            created_at=item.get("created_at"),
            created_by_name=item.get("created_by_name"),
            submitted_at=item.get("submitted_at"),
            submitted_by_name=item.get("submitted_by_name"),
            approved_at=item.get("approved_at"),
            approved_by_name=item.get("approved_by_name"),
            rejected_at=item.get("rejected_at"),
            rejected_by_name=item.get("rejected_by_name"),
            rejection_reason=item.get("rejection_reason"),
            signed=bool(signature.get("signed")),
            signed_at=signature.get("signing_timestamp"),
            signed_by_name=item.get("signed_by_name"),
            signature_verification=signature.get("verification_status"),
            signed_document_available=signed_available,
            signed_document_url=(
                SIGNED_DOCUMENT_ROUTE.format(visit_id=visit.id, checksheet_id=item["checksheet_id"])
                if signed_available else None
            ),
            requirement=flags.get(identity),
        ))
    return VisitChecksheets(available=True, items=out)


# ------------------------------------------------------------------- signed document --

def signed_document(db: Session, visit_id: int, checksheet_id: int, user: User,
                    bldcms: BLDCMSClient | None) -> Response:
    """Authorize THIS user for THIS checksheet of THIS visit, then relay the PDF bytes.

    Order: visit exists -> checksheet belongs to the visit -> the user may see its section -> a
    signed document exists -> fetch. Nothing about where BL-DCMS stores the file reaches the
    caller, and the only credential is the caller's normal Authorization header."""
    _visit_or_404(db, visit_id)
    if bldcms is None:
        raise _unavailable("The signed checksheet")
    try:
        items = bldcms.get_visit_checksheets(visit_id)["items"]
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError):
        raise _unavailable("The signed checksheet")

    item = next((i for i in items if i.get("checksheet_id") == checksheet_id), None)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Checksheet not found for this shed visit")

    scope = scope_for(user)
    if scope is not None and item.get("section_id") != scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You may only view checksheets of your own section.")
    if not item.get("signed_document_available"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={
            "code": "SIGNED_DOCUMENT_NOT_AVAILABLE",
            "message": "This checksheet has no digitally signed document.",
        })
    try:
        content = bldcms.get_signed_document(visit_id, checksheet_id)
    except BLDCMSNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={
            "code": "SIGNED_DOCUMENT_NOT_AVAILABLE",
            "message": "The signed document for this checksheet could not be found.",
        })
    except (BLDCMSUnavailableError, BLDCMSAuthError):
        raise _unavailable("The signed checksheet")

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="checksheet_{checksheet_id}_signed.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
