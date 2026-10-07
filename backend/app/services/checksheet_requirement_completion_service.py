"""Operational Control phase: visit-level checksheet-requirement readiness.

The one place that answers "is every checksheet this visit actually requires done?" - for MINOR
and for MAJOR alike - so Shed Out has a single definition to gate on.

WHAT COUNTS AS BLOCKING
  ACTIVE + REQUIRED    -> blocks until operationally satisfied
  ACTIVE + UNDER AMC   -> never blocks (an outside Firm maintains it under contract; migration 014)
  ACTIVE + OPTIONAL    -> never blocks (may still be filled voluntarily)
  INACTIVE/DEACTIVATED -> never blocks

AMC IS NOT A FAKE PASS. An AMC requirement is never counted as satisfied and its checksheet status is
never rewritten to SUBMITTED, APPROVED or SIGNED. It is excluded with its own reason and its own
count, so a report can say "8 satisfied, 2 under AMC" rather than claiming ten were done. AMC can only
ever excuse ITS OWN row: every other required checksheet still has to be satisfied.
Those flags live on the per-visit snapshot, so an Admin's Mark Optional / Deactivate on ONE visit
changes what that visit must do without touching BL-DCMS's global applicability.

WHY "NO BLOCKING ROWS" IS NOT AUTOMATICALLY READY
A visit with no generated work package has an UNKNOWN requirement set, not an empty one. That is
reported as a distinct blocker rather than silently passing - the same not-vacuously-complete rule
the Minor stage reconciler applies per stage.

MAJOR has no TEST_BEFORE/INSPECTION/TEST_AFTER stages and no fake stage is invented for it: its
requirements carry workflow_stage_type NULL and are evaluated directly at visit level.

WHERE THIS IS ENFORCED
Both ends of the visit, from this one definition:
  * READY  - MINOR Mark Ready and MAJOR Complete Schedule (the two, and only two, transitions that
             write status READY / ready_at) call assert_checksheets_complete() below.
  * SHED OUT - re-evaluates from scratch, so a checksheet REJECTED after the visit went Ready blocks
             departure even though the status still says READY.
No caller may relax this: there is no override flag, no Admin exception and no bypass parameter -
adding one would have to happen here, in the open.
"""

from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSAuthError, BLDCMSClient, BLDCMSUnavailableError
from app.db.models import ShedVisit, ShedVisitChecksheetRequirement, ShedVisitStage
from app.services.checksheet_work_package_service import _get_existing_package

SUBMISSION_SATISFYING_STATUSES = ("SUBMITTED", "UNDER_REVIEW", "APPROVED")

BLOCKER_WORK_PACKAGE_MISSING = "WORK_PACKAGE_NOT_GENERATED"
BLOCKER_BLDCMS_UNAVAILABLE = "BLDCMS_UNAVAILABLE"
BLOCKER_REQUIREMENT_PENDING = "REQUIREMENT_PENDING"


@dataclass
class RequirementBlocker:
    kind: str
    requirement_id: int | None = None
    template_id: int | None = None
    template_name: str | None = None
    section_name: str | None = None
    equipment_name: str | None = None
    maintenance_type: str | None = None
    workflow_stage_type: str | None = None
    checksheet_status: str | None = None


@dataclass
class RequirementCompletion:
    work_package_generated: bool = False
    total: int = 0
    blocking: int = 0
    optional: int = 0
    deactivated: int = 0
    satisfied: int = 0
    #: Requirements excused to an AMC contractor. Reported separately from optional/deactivated so a
    #: blocked transition can say exactly why each non-blocking row is non-blocking.
    under_amc: int = 0
    blockers: list[RequirementBlocker] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.work_package_generated and not self.blockers


def _identity(template_id, stage, section_id, equipment_id, maintenance_type, minor_inspection_equipment_id=None) -> tuple:
    """Full authoritative identity, within one shed visit.

    Never template name, never loco number. maintenance_type is part of the key because MAJOR
    Pattern C resolves one equipment to several templates separated only by it - without it a
    Traction Motor GC checksheet could satisfy an Overhaul requirement.
    """
    # Minor Inspection directory equipment (migrations 010/011): satisfied only by a checksheet for that
    # same equipment - and by it whichever alternative performa (template) was used.
    if minor_inspection_equipment_id is not None:
        return ("MINOR_INSPECTION_EQUIPMENT", stage or None, minor_inspection_equipment_id)
    return (template_id, stage or None, section_id, equipment_id, maintenance_type or None, None)


def _index_checksheets(
    client: BLDCMSClient, visit_id: int, schedule_family: str | None
) -> dict[tuple, list[dict]] | None:
    """Single BL-DCMS call per visit, indexed by full identity. None means "could not be read" -
    never a partial index, so an unreadable response can never look like "nothing submitted".

    FAMILY AND VISIT SEPARATION - explicit, not incidental:
      * Only checksheets of THIS visit count. BL-DCMS already scopes the call by shed_visit_id; the
        response's own shed_visit_id is re-checked, and a response for another visit is treated as
        unreadable (fail closed) rather than trusted.
      * Only checksheets of THIS visit's schedule family count. A MAJOR checksheet can never
        satisfy a MINOR requirement or the reverse, and a checksheet of any other family - a
        future TI or GC workflow included - never participates in the Minor/Major gate at all.
        The requirement identity alone would already keep most of these apart (MINOR rows carry a
        stage, MAJOR rows none), but "most" is not a rule: this makes it one.
    A previous visit of the same locomotive and schedule has a different shed_visit_id, so its
    checksheets are never in this response to begin with.
    """
    try:
        raw = client.get_visit_checksheets(visit_id)
        reported_visit = raw.get("shed_visit_id", visit_id)
        if reported_visit != visit_id:
            return None
        items = raw["items"]
        index: dict[tuple, list[dict]] = {}
        for item in items:
            if item.get("schedule_family") != schedule_family:
                continue
            key = _identity(
                item["template_id"],
                item.get("workflow_stage_type"),
                item.get("section_id"),
                item.get("equipment_id"),
                item.get("maintenance_type"),
                item.get("minor_inspection_equipment_id"),
            )
            item["checksheet_id"]
            item["status"]
            index.setdefault(key, []).append(item)
        return index
    except (BLDCMSUnavailableError, BLDCMSAuthError, KeyError, TypeError, AttributeError):
        return None


def _requirement_identity(req: ShedVisitChecksheetRequirement) -> tuple:
    return _identity(
        req.template_id,
        req.workflow_stage_type,
        req.section_id_snapshot,
        req.equipment_id_snapshot,
        req.maintenance_type_snapshot,
        req.minor_inspection_equipment_id,
    )


def evaluate_requirement_completion(
    db: Session, bldcms_client: BLDCMSClient | None, visit: ShedVisit
) -> RequirementCompletion:
    result = RequirementCompletion()

    package = _get_existing_package(db, visit.id)
    if package is None:
        # An ungenerated package means the requirement set is UNKNOWN, not empty. Reporting this
        # as a blocker is what stops "zero generated requirements" reading as "nothing to do".
        result.blockers.append(RequirementBlocker(kind=BLOCKER_WORK_PACKAGE_MISSING))
        return result

    result.work_package_generated = True
    rows = list(package.requirements)
    result.total = len(rows)

    from app.services.workflow_common import test_before_legacy_waived

    test_before_stage = (
        db.query(ShedVisitStage)
        .filter(ShedVisitStage.shed_visit_id == visit.id, ShedVisitStage.stage_type == "TEST_BEFORE")
        .one_or_none()
    )
    # Skipped by an Admin, or waived for a grandfathered legacy start: not outstanding.
    test_before_skipped = (
        (test_before_stage is not None and test_before_stage.status == "SKIPPED")
        or test_before_legacy_waived(visit, test_before_stage)
    )

    blocking_rows: list[ShedVisitChecksheetRequirement] = []
    for row in rows:
        # Migration 012: Test Before / Test After rows are always required - their per-visit flags
        # no longer apply. A skipped Test Before is not outstanding (and not "satisfied" either).
        if row.workflow_stage_type == "TEST_BEFORE" and test_before_skipped:
            continue
        if row.workflow_stage_type in ("TEST_BEFORE", "TEST_AFTER"):
            blocking_rows.append(row)
            continue
        if not row.is_active:
            result.deactivated += 1
        elif row.under_amc:
            # UNDER AMC (migration 014): applicable and expected, but an outside Firm maintains it
            # under contract, so no workshop technician owes a checksheet for it. Counted on its own
            # line - never folded into optional or deactivated, and never reported as satisfied -
            # because "excused to a contractor" is a different fact from "not wanted" and from
            # "done", and an operator reading a blocked Shed Out needs to know which.
            result.under_amc += 1
        elif not row.is_required:
            result.optional += 1
        else:
            blocking_rows.append(row)
    result.blocking = len(blocking_rows)

    if not blocking_rows:
        # Configured, and every row is Optional or Deactivated. Genuinely nothing outstanding -
        # distinct from the unhandled-package case above, which already returned.
        return result

    if bldcms_client is None:
        result.blockers.append(RequirementBlocker(kind=BLOCKER_BLDCMS_UNAVAILABLE))
        return result

    index = _index_checksheets(bldcms_client, visit.id, visit.schedule_family)
    if index is None:
        result.blockers.append(RequirementBlocker(kind=BLOCKER_BLDCMS_UNAVAILABLE))
        return result

    for row in blocking_rows:
        matches = index.get(_requirement_identity(row), [])
        # Any satisfying row wins. A rejection after a submission leaves only non-satisfying
        # statuses behind, so the requirement automatically becomes blocking again with no
        # regeneration and no manual step.
        if any(m["status"] in SUBMISSION_SATISFYING_STATUSES for m in matches):
            result.satisfied += 1
            continue
        latest = matches[-1] if matches else None
        result.blockers.append(
            RequirementBlocker(
                kind=BLOCKER_REQUIREMENT_PENDING,
                requirement_id=row.id,
                template_id=row.template_id,
                template_name=row.template_name_snapshot,
                section_name=row.section_name_snapshot,
                equipment_name=row.equipment_name_snapshot,
                maintenance_type=row.maintenance_type_snapshot,
                workflow_stage_type=row.workflow_stage_type,
                checksheet_status=latest["status"] if latest else None,
            )
        )

    return result


# --- The READY gate ------------------------------------------------------------------------------

READY_BLOCKED_CODE = "CHECKSHEETS_INCOMPLETE"

# How many outstanding entries travel in the error detail. The count is always exact; the list is
# capped so a 94-requirement Major visit does not answer a Mark Ready click with a wall of JSON.
MAX_REPORTED_OUTSTANDING = 25


def _outstanding_reason(blocker: RequirementBlocker) -> str:
    """Why this requirement is outstanding, in the words the operator needs: MISSING when no
    checksheet exists for it at all, otherwise the lifecycle status that fails to satisfy it
    (DRAFT / REJECTED). Never guessed - it is the status BL-DCMS reported."""
    if blocker.checksheet_status is None:
        return "MISSING"
    return blocker.checksheet_status


def _outstanding_entry(blocker: RequirementBlocker) -> dict:
    """One outstanding requirement, described with what the shed needs to act on it and nothing
    else: no locomotive data, no checksheet contents, no other visit's work."""
    return {
        "requirement_id": blocker.requirement_id,
        "section": blocker.section_name,
        "label": blocker.equipment_name or blocker.template_name,
        "template_name": blocker.template_name,
        "stage": blocker.workflow_stage_type,
        "maintenance_type": blocker.maintenance_type,
        "status": blocker.checksheet_status,
        "reason": _outstanding_reason(blocker),
    }


def build_ready_blocker_detail(completion: RequirementCompletion, action: str = "Ready") -> dict:
    """The structured CHECKSHEETS_INCOMPLETE payload, shared by every path the checksheet gate
    refuses - both Ready-capable transitions AND Shed Out - so the frontend has one shape to render
    whatever refused. `action` only names the refused action in the message ("Ready blocked: ...",
    "Shed Out blocked: ..."); the rule and every count are identical."""
    pending = [b for b in completion.blockers if b.kind == BLOCKER_REQUIREMENT_PENDING]
    unreadable = [b for b in completion.blockers if b.kind == BLOCKER_BLDCMS_UNAVAILABLE]
    unknown_package = [b for b in completion.blockers if b.kind == BLOCKER_WORK_PACKAGE_MISSING]

    if unknown_package:
        message = (
            "This visit has no checksheet work package, so its required checksheets are unknown. "
            f"Generate the work package first; {action} is not possible until every required "
            "checksheet is accounted for."
        )
    elif unreadable:
        message = (
            "The checksheets for this visit could not be read from BL-DCMS just now, so "
            f"{action} cannot be confirmed. Please try again."
        )
    else:
        outstanding = len(pending)
        message = (
            f"{action} blocked: {outstanding} required "
            f"{'checksheet is' if outstanding == 1 else 'checksheets are'} still outstanding."
        )

    return {
        "code": READY_BLOCKED_CODE,
        "message": message,
        "work_package_generated": completion.work_package_generated,
        "checksheets_readable": not unreadable,
        "total_required": completion.blocking,
        "satisfied": completion.satisfied,
        "under_amc": completion.under_amc,
        "outstanding": len(pending),
        "optional": completion.optional,
        "deactivated": completion.deactivated,
        "outstanding_entries": [_outstanding_entry(b) for b in pending[:MAX_REPORTED_OUTSTANDING]],
        "outstanding_entries_truncated": len(pending) > MAX_REPORTED_OUTSTANDING,
    }


def assert_checksheets_complete(
    db: Session, bldcms_client: BLDCMSClient | None, visit: ShedVisit
) -> RequirementCompletion:
    """Refuses the transition unless every checksheet this visit actually requires is satisfied.

    Called by EVERY path that can write status READY / ready_at - MINOR Mark Ready and MAJOR
    Complete Schedule - so checksheets are mandatory for both schedule families and there is no
    route to Ready that skips them. Fails closed: an ungenerated work package (requirements
    unknown) and an unreadable BL-DCMS both block, because neither is evidence that the work is
    done. Optional and deactivated requirements never block; Test Before skipped by an Admin is
    not outstanding.
    """
    completion = evaluate_requirement_completion(db, bldcms_client, visit)
    if completion.ready:
        return completion
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=build_ready_blocker_detail(completion),
    )
