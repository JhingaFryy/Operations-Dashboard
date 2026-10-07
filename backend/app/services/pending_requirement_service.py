"""Operational Control phase: the Pending Checksheets panel.

Reads the frozen per-visit requirement snapshot (shed_visit_checksheet_requirements) for every
CURRENTLY ACTIVE shed visit and left-joins BL-DCMS's checksheet instances onto it.

Direction matters: this starts from requirements, not from checksheets. Work nobody has started
has no checksheet_header row anywhere, and that is precisely the work the panel exists to show -
querying checksheets first would make "not started" invisible.

Never re-resolves applicability and never mutates the snapshot: a later master-configuration
change in BL-DCMS is deliberately invisible to an already-open visit, exactly as it already is to
the snapshot itself.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSUnavailableError
from app.db.models import (
    ShedVisit,
    ShedVisitChecksheetPackage,
    ShedVisitChecksheetRequirement,
    ShedVisitStage,
    User,
)
from app.schemas.pending_requirements import (
    DISPLAY_AWAITING_INSPECTION,
    DISPLAY_DRAFT,
    DISPLAY_LEGACY_WAIVED,
    DISPLAY_NEEDS_CORRECTION,
    DISPLAY_PENDING,
    DISPLAY_SKIPPED,
    SATISFYING_STATUSES,
    PendingRequirementCounts,
    PendingRequirementOut,
    PendingRequirementsResponse,
    PendingTestBeforeOut,
    PendingVisitGroupOut,
)
from app.services.workflow_common import NON_CONFIGURABLE_STAGES, test_before_legacy_waived
from app.services.shed_visit_service import MINOR_STAGE_SEQUENCE, OPEN_VISIT_STATUSES


def _identity(template_id, stage, section_id, equipment_id, maintenance_type, minor_inspection_equipment_id=None) -> tuple:
    """Full requirement identity, within one shed visit.

    Never template name, never loco number. `maintenance_type` is part of the key because MAJOR
    Pattern C resolves one equipment to several templates distinguished only by it (Traction
    Motor GC vs OVERHAUL) - dropping it would let a GC checksheet satisfy an Overhaul requirement.
    """
    if minor_inspection_equipment_id is not None:
        # One Minor Inspection equipment = one instance per visit, whichever alternative performa.
        return ("MINOR_INSPECTION_EQUIPMENT", stage or None, minor_inspection_equipment_id)
    return (template_id, stage or None, section_id, equipment_id, maintenance_type or None, None)


def _display_status(checksheet_status: str | None) -> str:
    if checksheet_status is None:
        return DISPLAY_PENDING
    if checksheet_status == "REJECTED":
        # BL-DCMS has no distinct REOPENED state - a checksheet sent back for correction returns
        # to REJECTED and is editable/resubmittable from there. The real existing state is used
        # rather than inventing a duplicate one.
        return DISPLAY_NEEDS_CORRECTION
    if checksheet_status == "DRAFT":
        return DISPLAY_DRAFT
    return checksheet_status


def get_pending_requirements(
    db: Session,
    bldcms_client: BLDCMSClient | None,
    *,
    loco_number: str | None = None,
    schedule_variant: str | None = None,
    section_id: int | None = None,
    equipment_id: int | None = None,
    display_status: str | None = None,
    technology: str | None = None,
    include_deactivated: bool = False,
    include_satisfied: bool = False,
    scope_section_id: int | None = None,
) -> PendingRequirementsResponse:
    """`scope_section_id` restricts EVERYTHING section-specific - rows, counts, and the visit-level
    Test Before block - to that one section, BEFORE anything is counted, so a section-scoped caller
    (a Supervisor, via BL-DCMS) can never infer another section's work from this response. Every
    active visit is still listed. Unlike the `section_id` filter, it cannot be widened by the caller:
    BL-DCMS sets it from the authenticated user's own section."""
    now = datetime.now(timezone.utc)

    # ACTIVE visits only. A closed / shed-out visit leaves OPEN_VISIT_STATUSES, so none of its
    # requirements can appear here - historical reporting belongs in Reports, not this queue.
    visit_query = db.query(ShedVisit).filter(ShedVisit.status.in_(OPEN_VISIT_STATUSES))
    if loco_number:
        visit_query = visit_query.filter(ShedVisit.loco_number == loco_number)
    if schedule_variant:
        visit_query = visit_query.filter(ShedVisit.schedule_variant == schedule_variant)
    visits = visit_query.order_by(ShedVisit.arrival_at.desc()).all()

    bldcms_available = True
    groups: list[PendingVisitGroupOut] = []

    for visit in visits:
        package = (
            db.query(ShedVisitChecksheetPackage)
            .options(joinedload(ShedVisitChecksheetPackage.requirements))
            .filter(ShedVisitChecksheetPackage.shed_visit_id == visit.id)
            .one_or_none()
        )

        all_rows: list[ShedVisitChecksheetRequirement] = list(package.requirements) if package else []
        rows = (
            all_rows if scope_section_id is None
            else [r for r in all_rows if r.section_id_snapshot == scope_section_id]
        )

        is_minor = visit.schedule_family == "MINOR"
        stages = (
            {s.stage_type: s for s in db.query(ShedVisitStage).filter(ShedVisitStage.shed_visit_id == visit.id).all()}
            if is_minor else {}
        )
        test_before_stage = stages.get("TEST_BEFORE")
        test_before_skipped = test_before_stage is not None and test_before_stage.status == "SKIPPED"
        legacy_waived = test_before_legacy_waived(visit, test_before_stage)
        # Test After is operational only once the actual inspection is complete (Complete Schedule;
        # or a visit completed under the pre-012 workflow, which only recorded ready_at).
        inspection_completed = visit.inspection_completed_at is not None or visit.ready_at is not None

        # Any row (even deactivated) means the stage was configured at generation time; zero rows
        # means it was not. Evaluated before filters, so a section/status filter can't fake it -
        # and over the WHOLE package: whether a stage is configured is visit-level, not section detail.
        unconfigured_stages: list[str] = []
        partially_configured_stages: list[str] = []
        if package is not None and is_minor:
            configured = {r.workflow_stage_type for r in all_rows}
            unconfigured_stages = [s for s in MINOR_STAGE_SEQUENCE if s not in configured]
            # Some Inspection sections configured, but not every required one (migration 010).
            if "SCHEDULE_INSPECTION" in configured and package.minor_inspection_configuration_complete is not True:
                partially_configured_stages = ["SCHEDULE_INSPECTION"]

        # One BL-DCMS call per visit, never one per requirement.
        instances_by_identity: dict[tuple, dict] = {}
        if rows and bldcms_client is not None:
            try:
                payload = bldcms_client.get_visit_checksheets(visit.id)
                for item in payload.get("items", []) if isinstance(payload, dict) else []:
                    key = _identity(
                        item.get("template_id"),
                        item.get("workflow_stage_type"),
                        item.get("section_id"),
                        item.get("equipment_id"),
                        item.get("maintenance_type"),
                        item.get("minor_inspection_equipment_id"),
                    )
                    existing = instances_by_identity.get(key)
                    # Several checksheets can share an identity over a visit's life (a rejected
                    # one plus its resubmission). Prefer whichever is furthest along, so a
                    # satisfied requirement is never shown as still pending because an older
                    # rejected row happened to be encountered first.
                    if existing is None or _precedence(item.get("status")) > _precedence(existing.get("status")):
                        instances_by_identity[key] = item
            except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError, AttributeError):
                bldcms_available = False

        counts = PendingRequirementCounts()
        out_rows: list[PendingRequirementOut] = []

        for row in rows:
            instance = instances_by_identity.get(
                _identity(
                    row.template_id,
                    row.workflow_stage_type,
                    row.section_id_snapshot,
                    row.equipment_id_snapshot,
                    row.maintenance_type_snapshot,
                    row.minor_inspection_equipment_id,
                )
            )
            status_value = instance.get("status") if instance else None
            satisfied = status_value in SATISFYING_STATUSES

            configurable = row.workflow_stage_type not in NON_CONFIGURABLE_STAGES
            # Test Before / Test After are always required and active (migration 012).
            effective_required = row.is_required if configurable else True
            effective_active = row.is_active if configurable else True
            skipped = row.workflow_stage_type == "TEST_BEFORE" and test_before_skipped
            waived = (
                row.workflow_stage_type == "TEST_BEFORE" and not skipped and legacy_waived and not satisfied
            )
            awaiting_inspection = (
                is_minor and row.workflow_stage_type == "TEST_AFTER" and not inspection_completed and not satisfied
            )

            counts.total += 1
            if skipped:
                counts.skipped += 1
            elif waived:
                counts.legacy_waived += 1
            elif not effective_active:
                counts.deactivated += 1
            elif satisfied:
                counts.satisfied += 1
            elif awaiting_inspection:
                counts.awaiting_inspection += 1
            else:
                counts.pending += 1
                if not effective_required:
                    counts.optional += 1

            # --- visibility ---------------------------------------------------------------
            # Deactivated rows are hidden unless explicitly asked for (the "Deactivated" view).
            if not effective_active and not include_deactivated:
                continue
            # Submitted / under review / approved leave the operational queue. A skipped Test Before
            # stays listed, as Skipped, so the skip is visible where the work would have been.
            if satisfied and not skipped and not include_satisfied:
                continue
            # Optional rows deliberately REMAIN visible with a badge: staff may still choose to
            # perform them. They simply stop blocking completion.

            if section_id is not None and row.section_id_snapshot != section_id:
                continue
            if equipment_id is not None and row.equipment_id_snapshot != equipment_id:
                continue
            if technology and (row.technology_snapshot or "") != technology:
                continue

            if skipped:
                display = DISPLAY_SKIPPED
            elif waived:
                display = DISPLAY_LEGACY_WAIVED
            elif awaiting_inspection:
                display = DISPLAY_AWAITING_INSPECTION
            else:
                display = _display_status(status_value)
            if display_status and display != display_status:
                continue

            out_rows.append(
                PendingRequirementOut(
                    requirement_id=row.id,
                    shed_visit_id=visit.id,
                    loco_number=visit.loco_number,
                    schedule_family=visit.schedule_family,
                    schedule_variant=visit.schedule_variant,
                    section_id=row.section_id_snapshot,
                    section_name=row.section_name_snapshot,
                    equipment_id=row.equipment_id_snapshot,
                    equipment_name=row.equipment_name_snapshot,
                    template_id=row.template_id,
                    template_name=row.template_name_snapshot,
                    workflow_stage_type=row.workflow_stage_type,
                    maintenance_type=row.maintenance_type_snapshot,
                    minor_inspection_equipment_id=row.minor_inspection_equipment_id,
                    minor_inspection_equipment_code=row.minor_inspection_equipment_code_snapshot,
                    minor_inspection_equipment_name=row.minor_inspection_equipment_name_snapshot,
                    minor_inspection_equipment_label=row.minor_inspection_equipment_label_snapshot,
                    requirement_source=row.requirement_source,
                    is_required=effective_required,
                    is_active=effective_active,
                    configurable=configurable,
                    **amc_fields(row, visit),
                    checksheet_id=instance.get("checksheet_id") if instance else None,
                    checksheet_status=status_value,
                    display_status=display,
                    updated_at=row.updated_at,
                    changed_by=row.changed_by,
                )
            )

        # Stable, operator-meaningful ordering: section, then stage, then equipment, then name.
        out_rows.sort(
            key=lambda r: (
                r.section_name or "",
                _stage_order(r.workflow_stage_type),
                r.minor_inspection_equipment_code or "",
                r.equipment_name or "",
                r.template_name,
            )
        )

        groups.append(
            PendingVisitGroupOut(
                shed_visit_id=visit.id,
                loco_number=visit.loco_number,
                schedule_family=visit.schedule_family,
                schedule_variant=visit.schedule_variant,
                technology=all_rows[0].technology_snapshot if all_rows else None,
                arrival_at=visit.arrival_at,
                status=visit.status,
                work_package_generated=package is not None,
                generation_note=(
                    None if package is not None
                    else "No checksheet requirements have been generated for this visit yet."
                ),
                unconfigured_stages=unconfigured_stages,
                partially_configured_stages=partially_configured_stages,
                counts=counts,
                requirements=out_rows,
                test_before=(
                    PendingTestBeforeOut(
                        stage_status=test_before_stage.status if test_before_stage is not None else None,
                        skipped_at=test_before_stage.skipped_at if test_before_stage is not None else None,
                        skipped_by=test_before_stage.skipped_by if test_before_stage is not None else None,
                        skip_reason=test_before_stage.skip_reason if test_before_stage is not None else None,
                        can_skip=(
                            test_before_stage is not None
                            and test_before_stage.status not in ("COMPLETED", "SKIPPED")
                            and visit.schedule_started_at is None
                        ),
                        legacy_waived=legacy_waived,
                    )
                    if is_minor and scope_section_id is None
                    else None
                ),
                inspection_completed=is_minor and inspection_completed,
            )
        )

    return PendingRequirementsResponse(
        generated_at=now, bldcms_available=bldcms_available, scope_section_id=scope_section_id, groups=groups
    )


_STATUS_PRECEDENCE = {"DRAFT": 1, "REJECTED": 2, "SUBMITTED": 3, "UNDER_REVIEW": 4, "APPROVED": 5}
_STAGE_ORDER = {"TEST_BEFORE": 0, "SCHEDULE_INSPECTION": 1, "TEST_AFTER": 2}


def _precedence(status_value: str | None) -> int:
    return _STATUS_PRECEDENCE.get(status_value or "", 0)


def _stage_order(stage: str | None) -> int:
    # Major rows have no stage; they sort ahead of nothing in particular, just consistently.
    return _STAGE_ORDER.get(stage or "", 99)


#: Schedule families whose requirements may be put Under AMC. MINOR and MAJOR only: the brief is
#: explicit, and nothing else in this table represents contracted equipment maintenance.
AMC_SCHEDULE_FAMILIES = ("MINOR", "MAJOR")


def amc_fields(row: ShedVisitChecksheetRequirement, visit: ShedVisit) -> dict:
    """The AMC part of a PendingRequirementOut, in ONE place.

    This exists because there are TWO constructions of PendingRequirementOut - the list builder in
    get_pending_requirements and requirement_to_out for the PATCH responses - and the first attempt at
    this feature extended only the second. Every list row therefore reported amc_eligible=False by
    schema default, the Pending Checksheets page never rendered a toggle, and nothing failed: the
    field was simply absent and the default was a plausible value.

    Splatted into both call sites, so a future field cannot be added to one and missed by the other.
    """
    return {
        "under_amc": row.under_amc,
        "amc_marked_by": row.amc_marked_by,
        "amc_marked_at": row.amc_marked_at,
        "amc_cleared_at": row.amc_cleared_at,
        "effective_state": row.effective_state,
        "amc_eligible": _amc_eligible(row, visit),
    }


def _amc_eligible(row: ShedVisitChecksheetRequirement, visit: ShedVisit) -> bool:
    """Whether an AMC toggle may be offered for this requirement at all.

    Computed here rather than in the Dashboard so the page cannot offer a control the server would
    refuse. TB/TA fall out through NON_CONFIGURABLE_STAGES, which migration 012 already made
    always-required - so TEST_BEFORE, TEST_AFTER and anything else non-configurable are excluded by
    the same rule that excludes them from Optional and Deactivate, not by a separate list that could
    drift.
    """
    if visit.schedule_family not in AMC_SCHEDULE_FAMILIES:
        return False
    return row.workflow_stage_type not in NON_CONFIGURABLE_STAGES


def requirement_to_out(
    row: ShedVisitChecksheetRequirement, visit: ShedVisit
) -> PendingRequirementOut:
    return PendingRequirementOut(
        requirement_id=row.id,
        shed_visit_id=visit.id,
        loco_number=visit.loco_number,
        schedule_family=visit.schedule_family,
        schedule_variant=visit.schedule_variant,
        section_id=row.section_id_snapshot,
        section_name=row.section_name_snapshot,
        equipment_id=row.equipment_id_snapshot,
        equipment_name=row.equipment_name_snapshot,
        template_id=row.template_id,
        template_name=row.template_name_snapshot,
        workflow_stage_type=row.workflow_stage_type,
        maintenance_type=row.maintenance_type_snapshot,
        minor_inspection_equipment_id=row.minor_inspection_equipment_id,
        minor_inspection_equipment_code=row.minor_inspection_equipment_code_snapshot,
        minor_inspection_equipment_name=row.minor_inspection_equipment_name_snapshot,
        minor_inspection_equipment_label=row.minor_inspection_equipment_label_snapshot,
        requirement_source=row.requirement_source,
        is_required=row.is_required,
        is_active=row.is_active,
        configurable=row.workflow_stage_type not in NON_CONFIGURABLE_STAGES,
        # The same helper the list builder uses - see amc_fields.
        **amc_fields(row, visit),
        display_status=_display_status(None),
        updated_at=row.updated_at,
        changed_by=row.changed_by,
    )


def apply_requirement_override(
    db: Session,
    requirement_id: int,
    *,
    is_required: bool | None = None,
    is_active: bool | None = None,
    actor_id: int | None = None,
) -> PendingRequirementOut:
    """The ONE write path for Mark Optional / Deactivate, shared by the human-authenticated and
    service-authenticated routes.

    Writes only to this visit's requirement row. BL-DCMS's global
    checksheet_template_applicability is never reachable from here - deactivating
    39018/IA/Test Before cannot deactivate that template for future IA visits.
    """
    from app.services.workflow_common import error

    row = (
        db.query(ShedVisitChecksheetRequirement)
        .filter(ShedVisitChecksheetRequirement.id == requirement_id)
        .one_or_none()
    )
    if row is None:
        raise error(404, "REQUIREMENT_NOT_FOUND", "Checksheet requirement not found.")

    # Migration 012 - enforced here, on the ONE write path shared by the human and the internal
    # routes, so no client can get round it. Checked before anything else about the visit.
    if row.workflow_stage_type == "TEST_AFTER":
        raise error(
            409,
            "TEST_AFTER_ALWAYS_REQUIRED",
            "Test After is always required for a Minor schedule. It cannot be made optional, "
            "deactivated, reactivated or skipped.",
        )
    if row.workflow_stage_type == "TEST_BEFORE":
        raise error(
            409,
            "TEST_BEFORE_NOT_CONFIGURABLE",
            "Test Before is always required. Its only exception is Skip Test Before, an Admin action "
            "on the shed visit - it cannot be made optional or deactivated.",
        )

    visit = (
        db.query(ShedVisit)
        .join(ShedVisitChecksheetPackage, ShedVisitChecksheetPackage.shed_visit_id == ShedVisit.id)
        .filter(ShedVisitChecksheetPackage.id == row.package_id)
        .one_or_none()
    )
    if visit is None or visit.status not in OPEN_VISIT_STATUSES:
        # A closed visit's requirements are history; editing them would rewrite what a completed
        # visit had required.
        raise error(
            409, "VISIT_NOT_ACTIVE", "This requirement belongs to a visit that is no longer active."
        )

    # ---- the two interactions with Under AMC (migration 014) -------------------------------
    # Both live here, on the shared write path, so neither the human nor the internal route can get
    # round them - the same reason the TB/TA guards above are here.

    if is_required is False and row.under_amc:
        # Marking an AMC requirement Optional would leave two badges describing one row and two
        # different reasons for the same exemption. Clear AMC first, deliberately.
        raise error(
            409,
            "AMC_REQUIREMENT_ACTIVE",
            "This requirement is Under AMC. Remove it from AMC before marking it Optional.",
        )

    if is_active is False and row.under_amc:
        # NOT an error: deactivating is a strictly wider statement than AMC ("not expected at all"
        # supersedes "expected, but Firm Staff own it"), so it is honoured and AMC is cleared in the
        # SAME transaction. chk_requirement_amc_not_deactivated would otherwise reject the row, which
        # is exactly the guarantee that this cannot be forgotten.
        row.under_amc = False
        row.amc_cleared_by = actor_id
        row.amc_cleared_at = datetime.now(timezone.utc)

    if is_required is not None:
        row.is_required = is_required
    if is_active is not None:
        row.is_active = is_active
    row.changed_by = actor_id
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return requirement_to_out(row, visit)


# ============================================================================ Under AMC =========


def _amc_actor(db: Session, actor_id: int | None) -> User:
    """The acting user, re-read here rather than trusted from the caller.

    BL-DCMS proxies this call and does its own authorization first, but a proxy's say-so is not
    authorization: Operations Dashboard owns this row and must decide for itself. So the role and the
    section are read from the database on every call.
    """
    from app.core.roles import ADMIN_ROLE, SUPERVISOR_ROLE, canonical_role
    from app.services.workflow_common import error

    user = db.query(User).filter(User.id == actor_id).one_or_none() if actor_id else None
    if user is None or not user.is_active:
        raise error(403, "ACTOR_NOT_PERMITTED", "This action requires an active Admin or Supervisor.")
    role = canonical_role(user)
    if role not in (ADMIN_ROLE, SUPERVISOR_ROLE):
        # A Technician has no override authority of any kind.
        raise error(403, "ACTOR_NOT_PERMITTED", "This action requires an Admin or a Supervisor.")
    return user


def _assert_amc_scope(db: Session, row: ShedVisitChecksheetRequirement, actor: User) -> None:
    """A Supervisor may toggle AMC only for their own section. Admin may manage any.

    Enforced HERE, against section_id_snapshot on the row itself, for two reasons. The snapshot is
    the section that actually owes this requirement on this visit - not whatever the template says
    now - and it is the only section value a Supervisor's own section can honestly be compared
    against. And doing it in Operations Dashboard means a request that reached this service by any
    route, proxy or not, is scoped the same way.

    A requirement with NO section snapshot is Admin-only. Production currently has zero such rows
    (audit section 8: 2,735 of 2,735 carry a section), so this is a fail-closed path rather than a
    common one - but a row nobody can attribute to a section is a row no Supervisor can be said to
    own, and guessing would be worse than refusing.
    """
    from app.core.roles import ADMIN_ROLE, canonical_role
    from app.services.workflow_common import error

    if canonical_role(actor) == ADMIN_ROLE:
        return
    if row.section_id_snapshot is None:
        raise error(
            403,
            "AMC_SECTION_UNKNOWN",
            "This requirement is not attributed to a section, so only an Admin can change its AMC "
            "state.",
        )
    if actor.section_id is None or actor.section_id != row.section_id_snapshot:
        raise error(
            403,
            "AMC_OUTSIDE_SECTION",
            "Supervisors can only change the AMC state of their own section's checksheets.",
        )


def set_requirement_under_amc(
    db: Session,
    requirement_id: int,
    *,
    under_amc: bool,
    actor_id: int | None = None,
) -> PendingRequirementOut:
    """Mark / clear "Under AMC" for ONE requirement on ONE shed visit.

    THE TRANSITION MATRIX, enforced here so no route can bypass it:

        DEACTIVATED  -> AMC      refused. "Not expected on this visit" and "expected, but Firm Staff
                                 own it" are contradictory claims; the database CHECK refuses it too.
        OPTIONAL     -> AMC      refused until made required again. An Optional requirement already
                                 blocks nothing, so AMC would add no effect and leave the panel
                                 showing two badges for one row.
        REQUIRED     -> AMC      allowed. The only transition that does anything.
        AMC          -> clear    allowed. Restores the underlying state EXACTLY: is_required and
                                 is_active are not touched, so a requirement that was required
                                 becomes required again, and nothing is reset.

    Marking AMC -> Optional is refused by set_requirement_required (see the guard added there), and
    deactivating an AMC requirement clears AMC in the same transaction rather than failing.

    THE FROZEN PACKAGE IS NOT MUTATED. Only these flags move. The requirement keeps its template,
    section, equipment and label snapshots, so clearing AMC restores the requirement itself and the
    history still shows it was originally applicable.
    """
    from app.services.workflow_common import error

    actor = _amc_actor(db, actor_id)

    row = (
        db.query(ShedVisitChecksheetRequirement)
        .filter(ShedVisitChecksheetRequirement.id == requirement_id)
        # Locked for the whole transaction: a technician's submission re-reads this same row to
        # decide whether it may still be written, so the two must not interleave.
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise error(404, "REQUIREMENT_NOT_FOUND", "Checksheet requirement not found.")

    visit = (
        db.query(ShedVisit)
        .join(ShedVisitChecksheetPackage, ShedVisitChecksheetPackage.shed_visit_id == ShedVisit.id)
        .filter(ShedVisitChecksheetPackage.id == row.package_id)
        .one_or_none()
    )
    if visit is None or visit.status not in OPEN_VISIT_STATUSES:
        raise error(
            409, "VISIT_NOT_ACTIVE", "This requirement belongs to a visit that is no longer active."
        )

    _assert_amc_scope(db, row, actor)

    if not _amc_eligible(row, visit):
        raise error(
            409,
            "AMC_NOT_APPLICABLE",
            "Under AMC applies to Minor and Major schedule checksheets only. Test Before and Test "
            "After are always required.",
        )

    if under_amc and row.under_amc:
        return requirement_to_out(row, visit)  # idempotent
    if not under_amc and not row.under_amc:
        return requirement_to_out(row, visit)

    now = datetime.now(timezone.utc)
    previous_state = row.effective_state

    if under_amc:
        if not row.is_active:
            raise error(
                409,
                "AMC_REQUIREMENT_DEACTIVATED",
                "This requirement is deactivated for this visit. Re-enable it before marking it "
                "Under AMC.",
            )
        if not row.is_required:
            raise error(
                409,
                "AMC_REQUIREMENT_OPTIONAL",
                "This requirement is Optional for this visit, so it already does not block "
                "completion. Mark it Required before putting it Under AMC.",
            )
        row.under_amc = True
        row.amc_marked_by = actor.id
        row.amc_marked_at = now
        # The previous clear stamps are deliberately dropped: they described the last clearing, and
        # keeping them beside a fresh mark would read as if it had been cleared after being marked.
        row.amc_cleared_by = None
        row.amc_cleared_at = None
    else:
        row.under_amc = False
        row.amc_cleared_by = actor.id
        row.amc_cleared_at = now
        # amc_marked_by/at are NOT cleared: the record of who excused the requirement must outlive
        # the exemption. is_required and is_active are not touched, so the underlying state returns
        # exactly as it was.

    row.updated_at = now
    db.commit()
    db.refresh(row)

    _log_amc_transition(db, row, visit, actor, previous_state=previous_state)
    return requirement_to_out(row, visit)


def _log_amc_transition(
    db: Session,
    row: ShedVisitChecksheetRequirement,
    visit: ShedVisit,
    actor: User,
    *,
    previous_state: str,
) -> None:
    """One structured security-log line per transition, with everything an auditor needs.

    Written AFTER the commit, so a logging problem can never roll back the change it describes.
    """
    import logging

    from app.core.roles import canonical_role

    logging.getLogger("app.security").warning(
        "Requirement AMC state changed",
        extra={
            "action": (
                "CHECKSHEET_MARKED_UNDER_AMC" if row.under_amc else "CHECKSHEET_REMOVED_FROM_AMC"
            ),
            "success": True,
            "employee_id": actor.employee_id,
            "actor_role": canonical_role(actor),
            "shed_visit_id": visit.id,
            "loco_number": visit.loco_number,
            "schedule_family": visit.schedule_family,
            "schedule_variant": visit.schedule_variant,
            "section_id": row.section_id_snapshot,
            "section_name": row.section_name_snapshot,
            "requirement_id": row.id,
            "template_id": row.template_id,
            "minor_inspection_equipment_id": row.minor_inspection_equipment_id,
            "previous_state": previous_state,
            "new_state": row.effective_state,
        },
    )
