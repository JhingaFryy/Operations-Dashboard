"""BL-DCMS Integration Phase 5B.1: materialized checksheet work packages.

Generation is deliberately idempotent in the strictest sense: calling it twice for the same
visit never re-reads BL-DCMS or mutates anything on the second call — it just returns the
existing snapshot untouched (Option A from the brief). A later BL-DCMS applicability edit must
never retroactively change what an already-generated visit's requirements were; only a
deliberate, separate, audited "refresh" operation (not built this phase) could ever do that.

Generation runs entirely read-then-write: every BL-DCMS call and every validation happens before
a single row is written, and everything is written in one DB transaction. Any failure anywhere
in that read phase (locomotive unresolved, BL-DCMS unavailable/auth-rejected/malformed, zero or
partial applicability configuration) means nothing was ever written — see the module's own
"EMPTY APPLICABILITY" handling in generate_work_package() below.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSUnavailableError
from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
)
from app.db.models import ShedVisit, ShedVisitChecksheetPackage, ShedVisitChecksheetRequirement, User
from app.schemas.checksheet_work_package import (
    ChecksheetRequirementOut,
    ChecksheetRequirementStageOut,
    ChecksheetWorkPackageOut,
)
from app.services.shed_visit_service import MINOR_STAGE_SEQUENCE
from app.services.workflow_common import assert_visit_view_access, error, get_visit_or_404, require_minor


logger = logging.getLogger(__name__)


def _bldcms_unavailable() -> Exception:
    return error(
        502,
        "BLDCMS_UNAVAILABLE",
        "BL-DCMS checksheet applicability service is currently unavailable.",
    )


def _loco_unavailable() -> Exception:
    return error(
        502,
        "LOCO_MASTER_UNAVAILABLE",
        "Equipment service is currently unavailable.",
    )


def _package_to_response(package: ShedVisitChecksheetPackage | None, shed_visit_id: int) -> ChecksheetWorkPackageOut:
    if package is None:
        return ChecksheetWorkPackageOut(shed_visit_id=shed_visit_id, generated=False, stages=[])

    by_stage: dict[str, list[ShedVisitChecksheetRequirement]] = {}
    for req in package.requirements:
        by_stage.setdefault(req.workflow_stage_type, []).append(req)

    stages = []
    for stage_type in MINOR_STAGE_SEQUENCE:
        reqs = by_stage.get(stage_type, [])
        stages.append(
            ChecksheetRequirementStageOut(
                workflow_stage_type=stage_type,
                requirements=[
                    ChecksheetRequirementOut(
                        applicability_id=r.applicability_id,
                        template_id=r.template_id,
                        template_name=r.template_name_snapshot,
                        technology=r.technology_snapshot,
                        section_id=r.section_id_snapshot,
                        section_name=r.section_name_snapshot,
                        equipment_id=r.equipment_id_snapshot,
                        equipment_name=r.equipment_name_snapshot,
                        maintenance_type=r.maintenance_type_snapshot,
                        minor_inspection_equipment_id=r.minor_inspection_equipment_id,
                        minor_inspection_equipment_code=r.minor_inspection_equipment_code_snapshot,
                        minor_inspection_equipment_name=r.minor_inspection_equipment_name_snapshot,
                        minor_inspection_equipment_label=r.minor_inspection_equipment_label_snapshot,
                        is_required=r.is_required,
                    )
                    for r in sorted(reqs, key=lambda r: (r.template_id, r.applicability_id))
                ],
            )
        )

    return ChecksheetWorkPackageOut(
        shed_visit_id=shed_visit_id,
        generated=True,
        generated_by=package.generated_by,
        generated_at=package.generated_at,
        minor_inspection_configuration_complete=package.minor_inspection_configuration_complete,
        stages=stages,
    )


def _minor_requirement_row(package_id: int, stage_type: str, item: dict, loco_type: str, now: datetime) -> ShedVisitChecksheetRequirement:
    """One frozen MINOR applicability requirement. A Minor Inspection directory equipment (BL-DCMS
    migration 028) is snapshotted into its OWN columns - never into equipment_id_snapshot, which is
    the legacy equipment id and stays whatever BL-DCMS reported for that (NULL for these rows)."""
    minor_id = item.get("minor_inspection_equipment_id")
    return ShedVisitChecksheetRequirement(
        package_id=package_id,
        workflow_stage_type=stage_type,
        applicability_id=item["applicability_id"],
        template_id=item["template_id"],
        is_required=item["is_required"],
        template_name_snapshot=item.get("template_name") or "",
        technology_snapshot=item.get("technology") or loco_type,
        section_id_snapshot=item.get("section_id"),
        section_name_snapshot=item.get("section_name"),
        equipment_id_snapshot=item.get("equipment_id"),
        equipment_name_snapshot=item.get("equipment_name"),
        maintenance_type_snapshot=item.get("maintenance_type"),
        minor_inspection_equipment_id=minor_id,
        minor_inspection_equipment_code_snapshot=item.get("minor_inspection_equipment_code") if minor_id is not None else None,
        minor_inspection_equipment_name_snapshot=item.get("minor_inspection_equipment_name") if minor_id is not None else None,
        minor_inspection_equipment_label_snapshot=item.get("minor_inspection_equipment_label") if minor_id is not None else None,
        requirement_source="APPLICABILITY",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _requirement_identity(template_id, stage, section_id, equipment_id, maintenance_type, minor_equipment_id) -> tuple:
    """A Minor Inspection directory equipment is ONE requirement per stage whichever of its alternative
    performas (templates) is used; everything else keeps its full template-based identity."""
    if minor_equipment_id is not None:
        return ("MINOR_INSPECTION_EQUIPMENT", stage or None, minor_equipment_id)
    return (template_id, stage or None, section_id, equipment_id, maintenance_type or None, minor_equipment_id)


def _collapse_alternative_performas(items: list[dict]) -> list[dict]:
    """BL-DCMS returns one applicability row per performa; an equipment with alternative performas
    (e.g. a pantograph slot with FTRTIL / Schunk performas) must still be one requirement. Keeps the
    lowest template_id as the snapshot's reference template, required if any alternative is required.
    Order of first appearance is preserved."""
    out: list[dict] = []
    by_equipment: dict[int, dict] = {}
    for item in items:
        minor_id = item.get("minor_inspection_equipment_id")
        if minor_id is None:
            out.append(item)
            continue
        kept = by_equipment.get(minor_id)
        if kept is None:
            kept = dict(item)
            by_equipment[minor_id] = kept
            out.append(kept)
            continue
        if item["template_id"] < kept["template_id"]:
            for key in ("applicability_id", "template_id", "template_name"):
                kept[key] = item.get(key)
        kept["is_required"] = bool(kept["is_required"] or item["is_required"])
    return out


def _fetch_minor_inspection_configuration_complete(bldcms_client: BLDCMSClient, loco_type: str, schedule_variant: str) -> bool:
    """Fail closed: any inability to confirm that EVERY required Inspection section is configured
    is recorded as False (the Inspection stage then cannot complete). Never raises - a refresh can
    record a later True."""
    try:
        body = bldcms_client.get_minor_inspection_configuration(loco_type=loco_type, schedule_variant=schedule_variant)
        return body.get("all_required_sections_configured") is True
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError, AttributeError):
        logger.warning("Could not confirm Minor Inspection configuration for loco_type %s; recording as incomplete.", loco_type)
        return False


def _get_existing_package(db: Session, visit_id: int) -> ShedVisitChecksheetPackage | None:
    return (
        db.query(ShedVisitChecksheetPackage)
        .options(joinedload(ShedVisitChecksheetPackage.requirements))
        .filter(ShedVisitChecksheetPackage.shed_visit_id == visit_id)
        .first()
    )


def get_frozen_work_package(db: Session, visit_id: int) -> dict:
    """The visit's frozen work package as BL-DCMS consumes it (read-only). Once a visit has a
    package, that snapshot - with its per-visit Optional / Deactivated overrides - is the visit's
    authoritative scope: BL-DCMS no longer re-resolves live configuration for it, so later
    configuration (new sections, deactivated templates) never changes an existing visit."""
    visit = get_visit_or_404(db, visit_id)
    package = _get_existing_package(db, visit_id)
    if package is None:
        return {"shed_visit_id": visit_id, "schedule_family": visit.schedule_family, "frozen": False, "requirements": []}
    return {
        "shed_visit_id": visit_id,
        "schedule_family": visit.schedule_family,
        "frozen": True,
        "package_id": package.id,
        "generated_at": package.generated_at,
        "minor_inspection_configuration_complete": package.minor_inspection_configuration_complete,
        "requirements": [
            {
                "requirement_id": r.id, "workflow_stage_type": r.workflow_stage_type, "applicability_id": r.applicability_id,
                "template_id": r.template_id, "is_required": r.is_required, "is_active": r.is_active,
                # Migration 014. Travels with the frozen package because BL-DCMS's work list, Test
                # After gate, section-signoff readiness and checksheet-creation refusal all read the
                # package - so one field here reaches every one of them, and none can disagree.
                "under_amc": r.under_amc,
                "section_id": r.section_id_snapshot, "equipment_id": r.equipment_id_snapshot,
                "maintenance_type": r.maintenance_type_snapshot,
                "minor_inspection_equipment_id": r.minor_inspection_equipment_id,
            }
            for r in sorted(package.requirements, key=lambda r: r.id)
        ],
    }


def get_work_package(db: Session, visit_id: int, current_user: User) -> ChecksheetWorkPackageOut:
    get_visit_or_404(db, visit_id)
    assert_visit_view_access(db, visit_id, current_user)
    package = _get_existing_package(db, visit_id)
    return _package_to_response(package, visit_id)


def _resolve_loco_type(loco_client: LocoMasterClient, loco_number: str) -> str:
    """Shared by the Minor and Major generation paths - see generate_work_package's own comment
    for why loco_type (Loco Master's vocabulary) is passed to BL-DCMS verbatim rather than being
    translated to a technology here."""
    try:
        locomotive = loco_client.get_locomotive(loco_number)
    except LocoMasterNotFoundError:
        raise error(
            422,
            "LOCOMOTIVE_TECHNOLOGY_UNRESOLVED",
            f"Could not resolve technology for locomotive {loco_number}: not found in Loco Master.",
        )
    except (LocoMasterUnavailableError, LocoMasterAuthError):
        raise _loco_unavailable()

    loco_type = locomotive.get("loco_type")
    if not loco_type:
        raise error(
            422,
            "LOCOMOTIVE_TECHNOLOGY_UNRESOLVED",
            f"Loco Master did not return a technology/loco_type for locomotive {loco_number}.",
        )
    return loco_type


def generate_major_work_package(
    db: Session, loco_client: LocoMasterClient, bldcms_client: BLDCMSClient | None, visit_id: int, current_user: User
) -> int:
    """MAJOR (IOH/TOH) equivalent of generate_work_package().

    Same contract as the Minor path: strictly read-then-write, idempotent (a second call returns
    the existing snapshot untouched), and nothing is written unless every read succeeded.

    Differs from Minor in what it resolves against: Major has no checksheet_template_applicability
    rows, so it calls BL-DCMS's GET /integration/applicability/resolve-major, which runs BL-DCMS's
    own Pattern A/B/C Major resolver across every section. Requirement rows therefore carry
    applicability_id=NULL and workflow_stage_type=NULL (Major is equipment-wise and stageless) -
    see migration 006's chk_checksheet_requirement_model_coherent.

    Sections BL-DCMS reports as unconfigured contribute zero rows: an unconfigured section is
    never turned into a fabricated requirement.

    Returns the number of requirement rows the visit now has. Deliberately NOT
    ChecksheetWorkPackageOut: that shape groups requirements under MINOR_STAGE_SEQUENCE, so a
    stageless Major package would render as three empty stages - actively misleading. Major
    requirements are read through the operational pending-requirements endpoint instead.
    """
    visit = get_visit_or_404(db, visit_id)
    if visit.schedule_family != "MAJOR":
        raise error(400, "NOT_A_MAJOR_VISIT", "This visit is not a MAJOR (IOH/TOH) visit.")

    existing = _get_existing_package(db, visit_id)
    if existing is not None:
        return len(existing.requirements)

    loco_type = _resolve_loco_type(loco_client, visit.loco_number)

    if bldcms_client is None:
        raise _bldcms_unavailable()

    try:
        payload = bldcms_client.resolve_major_requirements(loco_type=loco_type)
        items = payload["requirements"]
        for candidate in items:
            candidate["template_id"]
            candidate["section_id"]
            candidate["is_required"]
            candidate["source"]
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError):
        raise _bldcms_unavailable()

    if not items:
        raise error(
            409,
            "NO_MAJOR_CONTENT_CONFIGURED",
            "No Major checksheet content is configured in BL-DCMS for this locomotive's technology.",
            loco_type=loco_type,
            schedule_family=visit.schedule_family,
            schedule_variant=visit.schedule_variant,
        )

    now = datetime.now(timezone.utc)
    try:
        package = ShedVisitChecksheetPackage(
            shed_visit_id=visit_id, generated_by=current_user.id if current_user else None,
            generated_at=now,
        )
        db.add(package)
        db.flush()

        for item in items:
            db.add(
                ShedVisitChecksheetRequirement(
                    package_id=package.id,
                    workflow_stage_type=None,
                    applicability_id=None,
                    requirement_source=item["source"],
                    template_id=item["template_id"],
                    is_required=item["is_required"],
                    is_active=True,
                    template_name_snapshot=item.get("template_name") or "",
                    technology_snapshot=item.get("technology") or loco_type,
                    section_id_snapshot=item.get("section_id"),
                    section_name_snapshot=item.get("section_name"),
                    equipment_id_snapshot=item.get("equipment_id"),
                    equipment_name_snapshot=item.get("equipment_name"),
                    maintenance_type_snapshot=item.get("maintenance_type"),
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    generated = _get_existing_package(db, visit_id)
    return len(generated.requirements) if generated else 0


def generate_work_package(
    db: Session, loco_client: LocoMasterClient, bldcms_client: BLDCMSClient | None, visit_id: int, current_user: User
) -> ChecksheetWorkPackageOut:
    visit = get_visit_or_404(db, visit_id)
    require_minor(visit)

    # Idempotency (Option A, preferred by the brief): a second call for an already-generated
    # visit never touches BL-DCMS again and never mutates anything - it just hands back exactly
    # what was snapshotted before.
    existing = _get_existing_package(db, visit_id)
    if existing is not None:
        return _package_to_response(existing, visit_id)

    # --- Read phase: nothing is written to the DB until every one of these succeeds. ---

    try:
        locomotive = loco_client.get_locomotive(visit.loco_number)
    except LocoMasterNotFoundError:
        raise error(
            422,
            "LOCOMOTIVE_TECHNOLOGY_UNRESOLVED",
            f"Could not resolve technology for locomotive {visit.loco_number}: not found in Loco Master.",
        )
    except (LocoMasterUnavailableError, LocoMasterAuthError):
        raise _loco_unavailable()

    # Loco Master's own contract returns {"loco_number": ..., "loco_type": ...} - there is no
    # separate "technology" concept anywhere in this app or in Loco Master's API (confirmed by
    # inspection - see the Phase 5B.1 report). BL-DCMS Integration Phase 5A.1/5A.2 correction:
    # loco_type is passed to BL-DCMS verbatim as `loco_type=`, not `technology=` - BL-DCMS now
    # owns and performs the loco_type -> technology resolution itself (including hyphen/case
    # normalization, e.g. "WAP7"/"WAP-7"), so this app must not invent or duplicate that mapping.
    # If BL-DCMS doesn't recognize a given loco_type at all it returns a controlled error (see the
    # BLDCMSUnavailableError handling below); if it recognizes the loco_type but has nothing
    # configured for this schedule/stage, that's the ordinary "no applicability configured" result
    # below, not a silent wrong answer.
    loco_type = locomotive.get("loco_type")
    if not loco_type:
        raise error(
            422,
            "LOCOMOTIVE_TECHNOLOGY_UNRESOLVED",
            f"Loco Master did not return a technology/loco_type for locomotive {visit.loco_number}.",
        )

    if bldcms_client is None:
        raise _bldcms_unavailable()

    resolved_by_stage: dict[str, list[dict]] = {}
    for stage_type in MINOR_STAGE_SEQUENCE:
        try:
            items = bldcms_client.resolve_applicability(
                loco_type=loco_type,
                schedule_family=visit.schedule_family,
                schedule_variant=visit.schedule_variant,
                workflow_stage_type=stage_type,
            )
            # Defensive shape check ahead of the write phase - a response BL-DCMS's own client
            # already parsed as "a list of dicts" (see BLDCMSClient._parse_json_list) could
            # still be missing the specific keys this service needs. Fail the same clean way as
            # any other malformed-response case: nothing gets written, for any stage.
            for candidate in items:
                candidate["applicability_id"]
                candidate["template_id"]
                candidate["is_required"]
        except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError):
            raise _bldcms_unavailable()
        resolved_by_stage[stage_type] = items

    empty_stages = [s for s, items in resolved_by_stage.items() if not items]

    # Staged Minor Schedule rollout: TEST_BEFORE / SCHEDULE_INSPECTION / TEST_AFTER may each be
    # independently CONFIGURED or NOT_CONFIGURED in BL-DCMS now - the old "all three stages must
    # be configured or none can generate" rule is gone (see the Staged Minor Schedule brief).
    # A stage with zero resolved rows is simply omitted from the package below; it is never
    # fabricated, and - critically - it must never be treated as satisfied/complete downstream.
    # checksheet_stage_reconciliation_service already enforces that: a stage with zero REQUIRED
    # package requirements can never reach COMPLETED (REASON_NO_REQUIRED_CHECKSHEETS), and the
    # MINOR_STAGE_SEQUENCE ordering gate means a stage after it can never complete either - so an
    # unconfigured stage keeps the whole Minor work package (and therefore Shed Out) blocked on
    # checksheet-completion grounds, exactly as before this change.
    #
    # The only case that still fails generation outright is genuinely nothing configured for any
    # stage - there is nothing to generate a work package from at all.
    if len(empty_stages) == len(MINOR_STAGE_SEQUENCE):
        raise error(
            409,
            "NO_APPLICABILITY_CONFIGURED",
            "No checksheet applicability is configured in BL-DCMS for this visit's schedule/technology.",
            loco_type=loco_type,
            schedule_family=visit.schedule_family,
            schedule_variant=visit.schedule_variant,
        )

    inspection_configuration_complete = _fetch_minor_inspection_configuration_complete(
        bldcms_client, loco_type, visit.schedule_variant
    )

    # --- Write phase: one transaction, only reached once every read above has succeeded. ---

    now = datetime.now(timezone.utc)
    try:
        package = ShedVisitChecksheetPackage(
            shed_visit_id=visit_id,
            # NULL for a system/backfill generation with no human actor - generated_by is
            # nullable precisely so automatic generation is never attributed to a person.
            generated_by=current_user.id if current_user else None,
            generated_at=now,
            minor_inspection_configuration_complete=inspection_configuration_complete,
        )
        db.add(package)
        db.flush()

        for stage_type, items in resolved_by_stage.items():
            for item in _collapse_alternative_performas(items):
                db.add(_minor_requirement_row(package.id, stage_type, item, loco_type, now))

        db.commit()
    except Exception:
        db.rollback()
        raise

    generated = _get_existing_package(db, visit_id)
    return _package_to_response(generated, visit_id)


def refresh_work_package(
    db: Session, loco_client: LocoMasterClient, bldcms_client: BLDCMSClient | None, visit_id: int, current_user: User | None,
) -> dict:
    """Reconcile or recover a MINOR visit's work package - never expand it.

    A visit's package is frozen when it is generated (normally at Shed In) and is the visit's
    authoritative scope from then on, here and in BL-DCMS. Configuration that became applicable
    afterwards (a new section, a new equipment slot) is NEVER appended: refresh does not re-resolve
    BL-DCMS applicability for a visit that already has a package, and never inserts, deletes or
    rewrites a requirement row. Per-visit decisions go through the Pending Checksheets overrides.

    What refresh still does:
      * reconcile - the package's minor_inspection_configuration_complete flag is re-read from
        BL-DCMS. It adds no work: it only lets an open visit's Inspection stage complete once its
        technology's Inspection configuration has been declared complete, scoped to the frozen
        requirements the visit already has;
      * recover - a visit whose package was never generated (Shed In could not reach BL-DCMS or Loco
        Master) gets its first package by ordinary generation, frozen from then on.
    """
    visit = get_visit_or_404(db, visit_id)
    if visit.status == "CLOSED":
        raise error(409, "VISIT_CLOSED", "This shed visit is closed; its work package cannot be refreshed.")
    require_minor(visit)

    package = _get_existing_package(db, visit_id)
    if package is None:
        generate_work_package(db, loco_client, bldcms_client, visit_id, current_user)
        package = _get_existing_package(db, visit_id)
        return {
            "shed_visit_id": visit_id, "generated": True, "frozen": True, "added": _describe(package.requirements),
            "retained": 0, "skipped_completed_stages": [],
            "minor_inspection_configuration_complete_before": None,
            "minor_inspection_configuration_complete_after": package.minor_inspection_configuration_complete,
        }

    loco_type = _resolve_loco_type(loco_client, visit.loco_number)
    if bldcms_client is None:
        raise _bldcms_unavailable()
    configuration_complete = _fetch_minor_inspection_configuration_complete(bldcms_client, loco_type, visit.schedule_variant)
    before = package.minor_inspection_configuration_complete
    try:
        package.minor_inspection_configuration_complete = configuration_complete
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Work package %s of shed visit %s is frozen: 0 requirement(s) added, %d retained, "
                "configuration_complete %s -> %s.", package.id, visit_id, len(package.requirements), before,
                configuration_complete)
    return {
        "shed_visit_id": visit_id, "generated": False, "frozen": True, "added": [],
        "retained": len(package.requirements), "skipped_completed_stages": [],
        "minor_inspection_configuration_complete_before": before,
        "minor_inspection_configuration_complete_after": configuration_complete,
    }


def _describe(rows) -> list[dict]:
    return [
        {
            "workflow_stage_type": r.workflow_stage_type,
            "template_id": r.template_id,
            "applicability_id": r.applicability_id,
            "section": r.section_name_snapshot,
            "minor_inspection_equipment_id": r.minor_inspection_equipment_id,
            "minor_inspection_equipment_code": r.minor_inspection_equipment_code_snapshot,
            "minor_inspection_equipment_name": r.minor_inspection_equipment_name_snapshot,
        }
        for r in sorted(rows, key=lambda r: (MINOR_STAGE_SEQUENCE.index(r.workflow_stage_type), r.minor_inspection_equipment_code_snapshot or "", r.template_id))
    ]


def requirement_count(db: Session, visit_id: int) -> int:
    """How many requirement rows this visit's package holds (0 when it has no package)."""
    package = _get_existing_package(db, visit_id)
    return len(package.requirements) if package else 0


def generate_requirements_snapshot(
    db: Session,
    loco_client: LocoMasterClient,
    bldcms_client: BLDCMSClient | None,
    visit_id: int,
    current_user: User,
) -> int:
    """Best-effort requirement snapshot for a freshly-created visit (called from shed_in()).

    Routes to the Minor or Major generator by the visit's own schedule_family - the two resolve
    against completely different models (applicability vs section_equipment_map).

    Swallows every failure by design. Shed In is a physical fact that must stay recordable when
    BL-DCMS or Loco Master is unavailable, or when no content is configured for that
    schedule/technology yet. Any of those leaves the visit with no package, which the Pending
    Checksheets panel surfaces explicitly as work_package_generated=false - a state an Admin can
    resolve by generating manually, and which is never silently presented as "nothing pending".

    Returns the number of requirement rows created (0 if generation did not happen).

    The BL-DCMS client is INJECTED, never fetched from module scope. Reaching for a global client
    here would bypass FastAPI's dependency overrides entirely - which in practice meant the test
    suite opening real HTTP connections to the production BL-DCMS service and snapshotting live
    shed data into unit-test fixtures.
    """
    visit = db.query(ShedVisit).filter(ShedVisit.id == visit_id).one_or_none()
    if visit is None:
        return 0

    try:
        if bldcms_client is None:
            return 0
        if visit.schedule_family == "MAJOR":
            return generate_major_work_package(db, loco_client, bldcms_client, visit_id, current_user)
        if visit.schedule_family == "MINOR":
            generate_work_package(db, loco_client, bldcms_client, visit_id, current_user)
            package = _get_existing_package(db, visit_id)
            return len(package.requirements) if package else 0
        return 0
    except Exception:  # noqa: BLE001 - deliberately broad; see the docstring.
        db.rollback()
        logger.warning(
            "Checksheet requirement snapshot could not be generated for shed visit %s; "
            "the visit was created without one.",
            visit_id,
        )
        return 0
