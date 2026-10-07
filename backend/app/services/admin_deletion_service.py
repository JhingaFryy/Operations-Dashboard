"""Admin destructive deletion of one booking, or one entire shed visit.

This module permanently destroys operational records. Everything in it is arranged around two
questions: is this exactly the right data, and will the record of its destruction outlive it.

-------------------------------------------------------------------------------------------------
WHY THE DELETION IS EXPLICIT AND ORDERED, NEVER A CASCADE
-------------------------------------------------------------------------------------------------
The production catalog was read directly (diagnostics/visit_deletion_audit.sql section 2) and it does
NOT agree with either service's models:

    bookings.shed_visit_id        -> shed_visits          RESTRICT   (models say nothing)
    bookings.stage_id             -> shed_visit_stages    RESTRICT
    booking_events.booking_id     -> bookings             RESTRICT
    booking_section_assignments   -> bookings             RESTRICT
    shed_visit_events             -> shed_visits          RESTRICT
    shed_visit_stages             -> shed_visits          RESTRICT
    shed_visit_checksheet_packages-> shed_visits          CASCADE
    ..._requirements              -> ..._packages         CASCADE
    checksheet_value              -> checksheet_header    CASCADE
    digital_signatures            -> checksheet_header    CASCADE

Only four of those cascade, and the two that matter most - checksheet values and digital signatures -
cascade SILENTLY the instant a checksheet header is deleted. Relying on model metadata would
therefore destroy 16,000 values and 850 signature records with no statement naming them and no
chance to snapshot them. So: every child is snapshotted first, then deleted explicitly, in order.

Two orderings are load-bearing and not obvious:
  * bookings BEFORE shed_visit_stages, because bookings.stage_id references a stage.
  * every snapshot BEFORE any delete, because of the two cascades above.

-------------------------------------------------------------------------------------------------
THE CROSS-SYSTEM PROBLEM
-------------------------------------------------------------------------------------------------
BL-DCMS references a shed visit by plain integer with no foreign key, by design - shed_visits belongs
to this service. Postgres will therefore happily delete a visit and leave every BL checksheet,
section sign-off and signoff-mode marker pointing at a dead id, without error or warning. Production
currently has ZERO such orphans; this service must not create the first. Both services address the
same database, so all of it happens in ONE transaction (see app/db/shared_tables.py).

-------------------------------------------------------------------------------------------------
THE FILESYSTEM CANNOT JOIN THE TRANSACTION
-------------------------------------------------------------------------------------------------
So it is never touched before the commit. The transaction records a PLAN; the commit makes that plan
durable; only then are files acted on, and the outcome is written back. A crash between commit and
destruction leaves files on disk with a committed record saying what was meant to happen - the safe
direction. The reverse order could destroy a signed PDF with no record that it ever existed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.db import models, shared_tables as bl
from app.db.models import (
    AdminDeletionEvent,
    AdminDeletionItem,
    Booking,
    BookingEvent,
    BookingSectionAssignment,
    ShedVisit,
    ShedVisitChecksheetPackage,
    ShedVisitChecksheetRequirement,
    ShedVisitEvent,
    ShedVisitStage,
    User,
)

logger = logging.getLogger("app.security")

DELETION_TYPE_BOOKING = "BOOKING"
DELETION_TYPE_SHED_VISIT = "SHED_VISIT"

#: The session variable migration 078 requires before the signoff-mode marker may be deleted. Its
#: value is the admin_deletion_event id, so the marker cannot be removed unless an authorised,
#: password-verified, reasoned ledger row already exists.
SIGNOFF_MODE_GUARD_SETTING = "rdcms.admin_deletion_event_id"


# =============================================================================== errors =========


def _refuse(code: str, message: str, http_status: int = 400) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": message})


# ========================================================================= hashing ==============


def canonical_json(payload: Any) -> str:
    """The one JSON spelling used for every hash in this module.

    Deterministic by construction: keys sorted, separators fixed, non-ASCII preserved rather than
    escaped, and datetimes rendered ISO-8601 in UTC. Without this, two runs over identical data
    could produce different hashes and the hashes would prove nothing.
    """

    def default(value: Any) -> Any:
        if isinstance(value, datetime):
            # Naive timestamps in this database are IST wall-clock (see app/core/ist_time.py); they
            # are stamped UTC here only so the RENDERING is stable, never to reinterpret the value.
            moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return moment.astimezone(timezone.utc).isoformat()
        if isinstance(value, (bytes, bytearray)):
            return value.hex()
        return str(value)

    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default
    )


def jsonable(value: Any) -> Any:
    """The same value, using only JSON primitives.

    Needed because a snapshot is STORED in a JSON column, and SQLAlchemy's serializer cannot encode a
    datetime - the row would simply fail to insert. Converting here rather than at the column means
    the stored form and the hashed form are the same bytes, so a content_hash always describes
    exactly what a reader will see.

    Timestamps become ISO-8601 in UTC. Naive values in this database are IST wall-clock (see
    app/core/ist_time.py); they are stamped UTC only so the RENDERING is stable, never to reinterpret
    the instant they record.
    """
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def content_hash(payload: Any) -> str:
    """SHA-256 over the canonical JSON of the JSON-SAFE form - the same form that is stored."""
    return hashlib.sha256(canonical_json(jsonable(payload)).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    """SHA-256 of a file, streamed. None when it cannot be read - a missing file is recorded as
    missing rather than failing the deletion, because the database row is still being destroyed and
    that fact must be recorded either way."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


# ================================================================== snapshot allow-lists ========
#
# EVERY column copied into the ledger is named here. Not "all columns": an allow-list cannot start
# silently copying something sensitive that a later migration adds, and reviewing this dict is how
# anyone checks what the ledger retains. Columns absent from a table at runtime are skipped rather
# than failing - the snapshot is best-effort about SHAPE and exact about CONTENT.
#
# There is nothing secret to exclude from the signature tables: BL-DCMS stores only PUBLIC
# certificate material - subject, issuer, serial, thumbprint, validity - and never a private key or
# token. The DSC private material never leaves the signer's USB token.

# VERIFIED AGAINST THE PRODUCTION SCHEMA. These lists were generated from rdcms_schema_only.sql and
# are every column of each table minus the forbidden set below, so the ledger records the whole row
# and nothing secret. A hand-written list drifted twice while this was being built - it named
# shed_visits.remarks and bookings.workflow_stage_type, which do not exist, while silently omitting
# shed_visit_events.remarks, checksheet_header.supervisor_mobile and eight others that do. A test
# asserts this parity against a live schema (see test_admin_deletion_scratch.py), so a future
# migration that adds a column fails that test instead of quietly dropping it from the ledger.
_SNAPSHOT_COLUMNS: dict[str, tuple[str, ...]] = {
    "shed_visits": (
        "id", "loco_number", "arrival_at", "arrival_source", "schedule_family",
        "schedule_variant", "visit_type", "status", "ready_at", "ready_source", "departed_at",
        "departure_source", "created_by", "created_at", "updated_by", "updated_at",
        "arrival_condition", "schedule_started_at", "inspection_completed_at",
    ),
    "shed_visit_events": (
        "id", "shed_visit_id", "event_type", "event_time", "source", "remarks", "event_data",
        "created_by", "created_at",
    ),
    "shed_visit_stages": (
        "id", "shed_visit_id", "stage_type", "stage_order", "status", "started_at", "started_by",
        "completed_at", "completed_by", "completion_remarks", "created_at", "updated_at",
        "skipped_at", "skipped_by", "skip_reason",
    ),
    "shed_visit_checksheet_packages": (
        "id", "shed_visit_id", "generated_by", "generated_at",
        "minor_inspection_configuration_complete",
    ),
    "shed_visit_checksheet_requirements": (
        "id", "package_id", "workflow_stage_type", "applicability_id", "template_id",
        "is_required", "template_name_snapshot", "technology_snapshot", "section_id_snapshot",
        "section_name_snapshot", "equipment_id_snapshot", "equipment_name_snapshot",
        "maintenance_type_snapshot", "created_at", "is_active", "updated_at", "changed_by",
        "requirement_source", "minor_inspection_equipment_id",
        "minor_inspection_equipment_code_snapshot", "minor_inspection_equipment_name_snapshot",
        "minor_inspection_equipment_label_snapshot",
        # Migration 014. A deleted visit's ledger must record that a requirement was excused to an
        # AMC contractor, and who excused it - otherwise the snapshot would show a requirement that
        # simply never blocked, with no explanation of why.
        "under_amc", "amc_marked_by", "amc_marked_at", "amc_cleared_by", "amc_cleared_at",
    ),
    "bookings": (
        "id", "shed_visit_id", "stage_id", "booking_source", "description", "equipment_node_id",
        "status", "created_by", "created_at", "started_by", "started_at", "attended_by",
        "attended_at", "attendance_remarks", "reopened_by", "reopened_at", "updated_by",
        "updated_at", "defect_type_id", "started_by_section_id", "attended_by_section_id",
        "client_booking_id",
    ),
    "booking_section_assignments": (
        "id", "booking_id", "section_id", "assignment_source", "status", "assigned_by",
        "assigned_at", "started_by", "started_at", "attended_by", "attended_at",
        "attendance_remarks", "updated_at",
    ),
    "booking_events": (
        "id", "booking_id", "event_type", "from_section_id", "to_section_id", "remarks",
        "event_data", "created_by", "created_at",
    ),
    "checksheet_header": (
        "id", "locomotive_id", "section_id", "equipment_id", "template_id", "technician_mobile",
        "work_type", "status", "submitted_at", "supervisor_mobile", "approved_at", "pdf_path",
        "created_at", "submitted_by", "approved_by", "rejected_at", "rejected_by",
        "rejection_reason", "last_modified_at", "last_modified_by", "traction_motor_number",
        "maintenance_type", "shed_visit_id", "schedule_family", "schedule_variant",
        "workflow_stage_type", "minor_inspection_equipment_id",
    ),
    "checksheet_value": (
        "id", "checksheet_id", "field_id", "field_value", "created_at",
    ),
    "digital_signatures": (
        "id", "checksheet_id", "supervisor_id", "supervisor_name", "supervisor_employee_id",
        "certificate_subject", "certificate_issuer", "certificate_serial_number",
        "certificate_thumbprint", "certificate_valid_from", "certificate_valid_to",
        "signing_timestamp", "signature_hash", "verification_status", "provider", "created_at",
    ),
    "notifications": (
        "id", "user_id", "title", "message", "type", "checksheet_id", "is_read", "created_at",
    ),
    "minor_inspection_section_signoff": (
        "id", "shed_visit_id", "section_id", "schedule_variant", "technology", "locomotive_id",
        "locomotive_number_snapshot", "section_name_snapshot", "status", "signed_by_user_id",
        "signed_by_name", "signed_by_employee_id", "signed_at", "signed_pdf_path",
        "document_hash", "certificate_subject", "certificate_issuer", "certificate_serial_number",
        "certificate_thumbprint", "certificate_valid_from", "certificate_valid_to",
        "signature_hash", "verification_status", "provider", "created_at", "updated_at",
    ),
    "minor_inspection_section_signoff_checksheet": (
        "signoff_id", "checksheet_header_id", "display_order", "created_at",
    ),
    "shed_visit_checksheet_signoff_mode": (
        "shed_visit_id", "mode", "created_at",
    ),
}

#: Names that must never appear in a snapshot. Asserted at runtime, so an allow-list edit that added
#: one would fail loudly rather than quietly persisting a credential.
_FORBIDDEN_SNAPSHOT_KEYS = frozenset(
    {"password", "passwd", "password_hash", "token", "jwt", "secret", "otp", "otp_secret",
     "private_key", "pin", "code_hash", "access_token", "refresh_token"}
)

for _table, _columns in _SNAPSHOT_COLUMNS.items():
    _leak = _FORBIDDEN_SNAPSHOT_KEYS & {c.lower() for c in _columns}
    if _leak:  # pragma: no cover - import-time assertion
        raise RuntimeError(f"snapshot allow-list for {_table} names credential column(s): {_leak}")


def _snapshot_row(table_name: str, row_mapping) -> dict[str, Any]:
    """One row reduced to its allow-listed fields."""
    allowed = _SNAPSHOT_COLUMNS[table_name]
    return {key: row_mapping[key] for key in allowed if key in row_mapping}


# ===================================================================== dependency manifest ======
#
# The ORDER of this list IS the deletion order, child-first. It is data rather than a sequence of
# hand-written statements so the preview, the snapshotting, the deletion and the counts all read from
# one definition and cannot drift apart.


class _Step:
    """One table's worth of a deletion, with how to find its rows and how to delete them."""

    __slots__ = ("entity_type", "selectable", "deleter", "key_columns")

    def __init__(self, entity_type: str, selectable, deleter, key_columns=("id",)) -> None:
        self.entity_type = entity_type
        self.selectable = selectable
        self.deleter = deleter
        self.key_columns = key_columns


def _booking_steps(booking_ids: list[int]) -> list[_Step]:
    """Booking-scoped rows, child-first. booking_events and booking_section_assignments are both
    RESTRICT in production, so neither disappears on its own."""
    if not booking_ids:
        return []
    bse, bsa = BookingEvent.__table__, BookingSectionAssignment.__table__
    bk = Booking.__table__
    return [
        _Step("booking_events",
              select(bse).where(bse.c.booking_id.in_(booking_ids)),
              delete(bse).where(bse.c.booking_id.in_(booking_ids))),
        _Step("booking_section_assignments",
              select(bsa).where(bsa.c.booking_id.in_(booking_ids)),
              delete(bsa).where(bsa.c.booking_id.in_(booking_ids))),
        _Step("bookings",
              select(bk).where(bk.c.id.in_(booking_ids)),
              delete(bk).where(bk.c.id.in_(booking_ids))),
    ]


def _checksheet_steps(checksheet_ids: list[int]) -> list[_Step]:
    """Checksheet-scoped rows across BOTH systems, child-first.

    checksheet_value and digital_signatures are ON DELETE CASCADE in production, so they would vanish
    the moment the header goes. They are deleted explicitly anyway: an explicit DELETE returns a
    rowcount that can be compared against the manifest, whereas a cascade returns nothing and would
    leave the counts unverifiable.
    """
    if not checksheet_ids:
        return []
    return [
        _Step("checksheet_value",
              select(bl.checksheet_value).where(bl.checksheet_value.c.checksheet_id.in_(checksheet_ids)),
              delete(bl.checksheet_value).where(bl.checksheet_value.c.checksheet_id.in_(checksheet_ids))),
        _Step("digital_signatures",
              select(bl.digital_signatures).where(bl.digital_signatures.c.checksheet_id.in_(checksheet_ids)),
              delete(bl.digital_signatures).where(bl.digital_signatures.c.checksheet_id.in_(checksheet_ids))),
        _Step("notifications",
              select(bl.notifications).where(bl.notifications.c.checksheet_id.in_(checksheet_ids)),
              delete(bl.notifications).where(bl.notifications.c.checksheet_id.in_(checksheet_ids))),
        _Step("minor_inspection_section_signoff_checksheet",
              select(bl.minor_inspection_section_signoff_checksheet).where(
                  bl.minor_inspection_section_signoff_checksheet.c.checksheet_header_id.in_(checksheet_ids)),
              delete(bl.minor_inspection_section_signoff_checksheet).where(
                  bl.minor_inspection_section_signoff_checksheet.c.checksheet_header_id.in_(checksheet_ids)),
              key_columns=("signoff_id", "checksheet_header_id")),
        _Step("checksheet_header",
              select(bl.checksheet_header).where(bl.checksheet_header.c.id.in_(checksheet_ids)),
              delete(bl.checksheet_header).where(bl.checksheet_header.c.id.in_(checksheet_ids))),
    ]


def build_visit_steps(db: Session, visit_id: int) -> list[_Step]:
    """Every row a shed visit owns, in the order it must be deleted.

    Nothing outside the visit appears here. Locomotives, equipment, sections, users, templates,
    template fields, defect types and the Minor Inspection equipment directory are all referenced BY
    a visit and owned by nobody's visit, so they are absent by construction rather than by a filter
    that could be got wrong.
    """
    booking_ids = [
        r[0] for r in db.execute(
            select(Booking.__table__.c.id).where(Booking.__table__.c.shed_visit_id == visit_id)
        )
    ]
    checksheet_ids = [
        r[0] for r in db.execute(
            select(bl.checksheet_header.c.id).where(bl.checksheet_header.c.shed_visit_id == visit_id)
        )
    ]
    signoff_ids = [
        r[0] for r in db.execute(
            select(bl.minor_inspection_section_signoff.c.id).where(
                bl.minor_inspection_section_signoff.c.shed_visit_id == visit_id
            )
        )
    ]

    pkg, req = ShedVisitChecksheetPackage.__table__, ShedVisitChecksheetRequirement.__table__
    package_ids = [r[0] for r in db.execute(select(pkg.c.id).where(pkg.c.shed_visit_id == visit_id))]
    ev, st, sv = ShedVisitEvent.__table__, ShedVisitStage.__table__, ShedVisit.__table__
    sof, sofc = bl.minor_inspection_section_signoff, bl.minor_inspection_section_signoff_checksheet
    mode = bl.shed_visit_checksheet_signoff_mode

    steps: list[_Step] = []
    steps += _booking_steps(booking_ids)
    steps += _checksheet_steps(checksheet_ids)

    if signoff_ids:
        # Pins reachable via the SIGN-OFF but NOT via this visit's own checksheets. A sign-off can in
        # principle pin a checksheet outside the visit's header set, and such a row must still go or
        # it would survive pointing at a deleted sign-off.
        #
        # The checksheet_header_id exclusion is essential, not tidiness. Without it the pins matched
        # by _checksheet_steps above are matched AGAIN here, so the manifest claims twice as many
        # rows as exist and the count verification aborts the whole deletion. The scratch proof caught
        # exactly that: "expected to delete 6 ... but the database reported 3".
        orphan_pins = sofc.c.signoff_id.in_(signoff_ids)
        if checksheet_ids:
            orphan_pins = orphan_pins & sofc.c.checksheet_header_id.notin_(checksheet_ids)
        steps.append(
            _Step("minor_inspection_section_signoff_checksheet",
                  select(sofc).where(orphan_pins),
                  delete(sofc).where(orphan_pins),
                  key_columns=("signoff_id", "checksheet_header_id"))
        )
        steps.append(
            _Step("minor_inspection_section_signoff",
                  select(sof).where(sof.c.id.in_(signoff_ids)),
                  delete(sof).where(sof.c.id.in_(signoff_ids)))
        )

    # The write-once marker. Deleting it needs migration 078's session guard; see _delete_rows.
    steps.append(
        _Step("shed_visit_checksheet_signoff_mode",
              select(mode).where(mode.c.shed_visit_id == visit_id),
              delete(mode).where(mode.c.shed_visit_id == visit_id),
              key_columns=("shed_visit_id",))
    )

    if package_ids:
        steps.append(
            _Step("shed_visit_checksheet_requirements",
                  select(req).where(req.c.package_id.in_(package_ids)),
                  delete(req).where(req.c.package_id.in_(package_ids)))
        )
        steps.append(
            _Step("shed_visit_checksheet_packages",
                  select(pkg).where(pkg.c.id.in_(package_ids)),
                  delete(pkg).where(pkg.c.id.in_(package_ids)))
        )

    steps.append(
        _Step("shed_visit_events",
              select(ev).where(ev.c.shed_visit_id == visit_id),
              delete(ev).where(ev.c.shed_visit_id == visit_id))
    )
    # AFTER bookings: bookings.stage_id references a stage, RESTRICT in production.
    steps.append(
        _Step("shed_visit_stages",
              select(st).where(st.c.shed_visit_id == visit_id),
              delete(st).where(st.c.shed_visit_id == visit_id))
    )
    steps.append(
        _Step("shed_visits",
              select(sv).where(sv.c.id == visit_id),
              delete(sv).where(sv.c.id == visit_id))
    )
    return steps


def build_booking_steps(db: Session, booking_id: int) -> list[_Step]:
    """A single booking and the rows it owns. Nothing else - not the visit, not its stages, not any
    checksheet, not another booking."""
    return _booking_steps([booking_id])


# ============================================================================ preview ===========


def _row_mappings(db: Session, step: _Step) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in db.execute(step.selectable)]


def _visit_or_404(db: Session, visit_id: int) -> ShedVisit:
    visit = db.get(ShedVisit, visit_id)
    if visit is None:
        raise _refuse(
            "VISIT_NOT_FOUND",
            "This shed visit no longer exists. It may already have been deleted.",
            status.HTTP_404_NOT_FOUND,
        )
    return visit


def _booking_or_404(db: Session, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise _refuse(
            "BOOKING_NOT_FOUND",
            "This booking no longer exists. It may already have been deleted.",
            status.HTTP_404_NOT_FOUND,
        )
    return booking


def _is_shared_path(db: Session, entity: str, path_value: str, deleted_ids: set[Any]) -> bool:
    """Whether any row that SURVIVES this deletion still names the same file.

    Asked of the database rather than inferred from the rows in hand, because the referencing row may
    belong to an entirely different visit - which is exactly the case that matters. Both path columns
    are checked for every candidate, not just the column the path came from, since nothing stops a
    checksheet PDF and a section sign-off PDF being the same file.

    The production audit found 892 paths across 892 rows with no sharing at all. This is a RUNTIME
    guard rather than a reliance on that: a regeneration bug could introduce sharing tomorrow, and
    destroying a shared file would take an unrelated visit's evidence with it.
    """
    checksheet_ids = deleted_ids if entity == "checksheet_header" else set()
    signoff_ids = deleted_ids if entity == "minor_inspection_section_signoff" else set()

    others = select(func.count()).select_from(bl.checksheet_header).where(
        bl.checksheet_header.c.pdf_path == path_value
    )
    if checksheet_ids:
        others = others.where(bl.checksheet_header.c.id.notin_(checksheet_ids))
    if db.execute(others).scalar_one():
        return True

    sof = select(func.count()).select_from(bl.minor_inspection_section_signoff).where(
        bl.minor_inspection_section_signoff.c.signed_pdf_path == path_value
    )
    if signoff_ids:
        sof = sof.where(bl.minor_inspection_section_signoff.c.id.notin_(signoff_ids))
    return bool(db.execute(sof).scalar_one())


def _file_plan_from(
    db: Session, rows_by_entity: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Which files the deletion will destroy, with their hashes computed WHILE THEY STILL EXIST.

    The hash is taken here, before the commit, because after destruction it can never be computed
    again. A path that is still referenced by a row this deletion is NOT removing is marked
    `shared: true` and skipped - the production audit found 892 paths across 892 rows with no sharing
    at all, but a regeneration bug could introduce it later and one shared file wrongly destroyed
    would take an unrelated visit's evidence with it.
    """
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    sources = (
        ("checksheet_header", "pdf_path"),
        ("minor_inspection_section_signoff", "signed_pdf_path"),
    )
    for entity, column in sources:
        # The ids being deleted, so a surviving reference can be told apart from one of our own.
        deleted_ids = {r.get("id") for r in rows_by_entity.get(entity, []) if r.get("id") is not None}
        for row in rows_by_entity.get(entity, []):
            raw = row.get(column)
            if not raw or str(raw) in seen:
                continue
            seen.add(str(raw))
            path = Path(str(raw))
            exists = path.is_file()
            plan.append(
                {
                    "entity_type": entity,
                    "origin_id": jsonable(row.get("id")),
                    "path": str(raw),
                    "exists": exists,
                    "size_bytes": (path.stat().st_size if exists else None),
                    "sha256": (file_sha256(path) if exists else None),
                    # Computed BEFORE the deletion, while the surviving rows are still visible.
                    "shared": _is_shared_path(db, entity, str(raw), deleted_ids),
                }
            )
    return plan


def preview_visit_deletion(db: Session, visit_id: int) -> dict[str, Any]:
    """Exactly what deleting this visit would destroy. Read-only.

    Built from the SAME step list the deletion uses, so a count shown to an Admin cannot disagree
    with what is actually removed. That is the point of the preview: not reassurance, but the same
    computation run without the DELETE.
    """
    visit = _visit_or_404(db, visit_id)
    steps = build_visit_steps(db, visit_id)

    rows_by_entity: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for step in steps:
        rows = _row_mappings(db, step)
        rows_by_entity.setdefault(step.entity_type, []).extend(rows)
        counts[step.entity_type] = counts.get(step.entity_type, 0) + len(rows)

    file_plan = _file_plan_from(db, rows_by_entity)
    warnings = _warnings_for(rows_by_entity, counts, file_plan)

    return {
        "visit": {
            "id": visit.id,
            "loco_number": visit.loco_number,
            "schedule_family": visit.schedule_family,
            "schedule_variant": visit.schedule_variant,
            "status": visit.status,
            "arrival_at": visit.arrival_at,
        },
        # The string the Admin must type. Derived server-side so the dialog cannot invent an easier
        # one, though the DELETE is still identified by visit_id alone - see delete_shed_visit.
        "required_confirmation": required_confirmation(visit),
        "counts": {k: v for k, v in counts.items() if v},
        "total_rows": sum(counts.values()),
        "files": file_plan,
        "warnings": warnings,
    }


def _warnings_for(
    rows_by_entity: dict[str, list[dict[str, Any]]],
    counts: dict[str, int],
    file_plan: list[dict[str, Any]],
) -> list[str]:
    """Plain statements of what is about to be lost. Not a nag - each line names something an Admin
    could not otherwise see from a row count."""
    warnings: list[str] = []

    signatures = counts.get("digital_signatures", 0)
    if signatures:
        warnings.append(
            f"{signatures} digitally signed checksheet(s) will be destroyed. Their signature and "
            f"certificate details are recorded in the deletion ledger, but the signatures "
            f"themselves cannot be recovered."
        )

    signed_signoffs = sum(
        1 for r in rows_by_entity.get("minor_inspection_section_signoff", []) if r.get("status") == "SIGNED"
    )
    if signed_signoffs:
        warnings.append(
            f"{signed_signoffs} signed Minor Inspection section sign-off(s) will be destroyed."
        )

    approved = sum(
        1 for r in rows_by_entity.get("checksheet_header", []) if r.get("status") == "APPROVED"
    )
    if approved:
        warnings.append(f"{approved} APPROVED checksheet(s) will be destroyed.")

    destroyable = [f for f in file_plan if f["exists"]]
    if destroyable:
        warnings.append(
            f"{len(destroyable)} PDF file(s) will be permanently deleted from disk after the "
            f"database changes are committed. This cannot be undone."
        )
    missing = [f for f in file_plan if not f["exists"]]
    if missing:
        warnings.append(
            f"{len(missing)} referenced PDF file(s) are already absent from disk and will be "
            f"recorded as missing."
        )

    if rows_by_entity.get("shed_visit_checksheet_signoff_mode"):
        warnings.append(
            "This visit carries a Minor Inspection sign-off mode marker, which is normally "
            "write-once. Deleting it requires migration 078 and is recorded in the ledger."
        )
    return warnings


def preview_booking_deletion(db: Session, booking_id: int) -> dict[str, Any]:
    """What deleting one booking would destroy. The visit, its checksheets and every other booking
    are untouched, and the response says so explicitly rather than leaving it to be inferred."""
    booking = _booking_or_404(db, booking_id)
    visit = db.get(ShedVisit, booking.shed_visit_id)
    steps = build_booking_steps(db, booking_id)

    counts: dict[str, int] = {}
    for step in steps:
        counts[step.entity_type] = len(_row_mappings(db, step))

    sibling_bookings = db.execute(
        select(func.count()).select_from(Booking.__table__).where(
            Booking.__table__.c.shed_visit_id == booking.shed_visit_id,
            Booking.__table__.c.id != booking_id,
        )
    ).scalar_one()

    return {
        "booking": {
            "id": booking.id,
            "description": booking.description,
            "status": booking.status,
            "booking_source": booking.booking_source,
            "equipment_node_id": booking.equipment_node_id,
        },
        "visit": {
            "id": booking.shed_visit_id,
            "loco_number": getattr(visit, "loco_number", None),
            "schedule_variant": getattr(visit, "schedule_variant", None),
        },
        "counts": {k: v for k, v in counts.items() if v},
        "total_rows": sum(counts.values()),
        "unaffected": {
            "shed_visit": True,
            "other_bookings_on_this_visit": sibling_bookings,
            "checksheets": True,
        },
        "files": [],
        "warnings": [],
    }


def required_confirmation(visit: ShedVisit) -> str:
    """The phrase an Admin must type, e.g. "DELETE 32032 IA".

    A human check only. The DELETE is identified by visit_id, never by parsing this string - two
    visits of one locomotive on the same schedule would otherwise be indistinguishable, and the
    production audit shows locomotives do repeat schedules over time.
    """
    parts = ["DELETE", str(visit.loco_number or "").strip()]
    variant = (visit.schedule_variant or "").strip()
    if variant:
        parts.append(variant)
    return " ".join(p for p in parts if p)


# =========================================================================== execution ==========


def _validate_reason(reason: str | None) -> str:
    cleaned = " ".join((reason or "").split())
    if len(cleaned) < 10:
        raise _refuse(
            "REASON_REQUIRED",
            "A deletion reason of at least 10 characters is required, and it is recorded "
            "permanently.",
        )
    # Mirrors chk_admin_deletion_reason_no_secret, which is PostgreSQL-only. Checked here too so the
    # refusal is a clear 400 rather than a database error, and so the SQLite tests exercise the rule.
    lowered = cleaned.lower()
    for marker in ("password", "passwd", "secret", "bearer ", "jwt", "token="):
        if marker in lowered:
            raise _refuse(
                "REASON_REJECTED",
                "The deletion reason must not contain credentials. Describe why the record is "
                "being removed.",
            )
    return cleaned


def _assert_confirmation(visit: ShedVisit, supplied: str | None) -> str:
    expected = required_confirmation(visit)
    given = " ".join((supplied or "").split()).upper()
    if given != expected.upper():
        raise _refuse(
            "CONFIRMATION_MISMATCH",
            f'To confirm, type exactly: {expected}',
        )
    return expected


def _existing_event(db: Session, operation_id: str) -> AdminDeletionEvent | None:
    return (
        db.query(AdminDeletionEvent)
        .filter(AdminDeletionEvent.operation_id == operation_id)
        .one_or_none()
    )


def _is_postgres(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _snapshot_steps(
    db: Session, event_id: int, steps: list[_Step]
) -> tuple[dict[str, int], dict[str, Any], list[dict[str, Any]]]:
    """Write one ledger item per row that is about to be destroyed, and return the manifest.

    BEFORE any delete, deliberately. Two of production's foreign keys cascade from
    checksheet_header, so by the time a header is gone its values and signatures are already gone
    with it - there would be nothing left to snapshot.
    """
    counts: dict[str, int] = {}
    manifest_entities: list[dict[str, Any]] = []
    rows_by_entity: dict[str, list[dict[str, Any]]] = {}

    for order, step in enumerate(steps, start=1):
        rows = _row_mappings(db, step)
        rows_by_entity.setdefault(step.entity_type, []).extend(rows)
        ids: list[Any] = []

        for row in rows:
            snapshot = jsonable(_snapshot_row(step.entity_type, row))
            leaked = _FORBIDDEN_SNAPSHOT_KEYS & {k.lower() for k in snapshot}
            if leaked:  # pragma: no cover - runtime assertion
                raise RuntimeError(
                    f"snapshot of {step.entity_type} would persist credential field(s): {leaked}"
                )

            if step.key_columns == ("id",):
                original_id = row.get("id")
                original_key = None
                ids.append(original_id)
            else:
                original_id = None
                original_key = jsonable({k: row.get(k) for k in step.key_columns})
                ids.append(original_key)

            db.add(
                AdminDeletionItem(
                    deletion_event_id=event_id,
                    entity_type=step.entity_type,
                    original_id=original_id,
                    original_key=original_key,
                    snapshot=snapshot,
                    content_hash=content_hash(snapshot),
                )
            )

        counts[step.entity_type] = counts.get(step.entity_type, 0) + len(rows)
        manifest_entities.append(
            {"order": order, "entity_type": step.entity_type, "row_count": len(rows), "ids": ids}
        )

    manifest = jsonable({"version": 1, "entities": manifest_entities})
    file_plan = _file_plan_from(db, rows_by_entity)
    return counts, manifest, file_plan


def _delete_rows(db: Session, event_id: int, steps: list[_Step]) -> dict[str, int]:
    """Execute the deletes in manifest order and return what the database says it removed.

    The signoff-mode marker needs migration 078's session guard. SET LOCAL is used, so the exemption
    is scoped to THIS transaction and reverts at COMMIT or ROLLBACK - it cannot leak to the next
    statement, let alone the next request on a pooled connection. It is set once, immediately before
    the statement that needs it, and only when there is such a statement.
    """
    deleted: dict[str, int] = {}
    guard_set = False

    for step in steps:
        if step.entity_type == "shed_visit_checksheet_signoff_mode" and not guard_set and _is_postgres(db):
            # Parameter binding is not permitted in SET LOCAL, so the value is interpolated - safe
            # here because event_id is an integer primary key this function just created, never
            # caller input. Formatted via int() so it cannot be anything else.
            db.execute(text(f"SET LOCAL {SIGNOFF_MODE_GUARD_SETTING} = '{int(event_id)}'"))
            guard_set = True
        result = db.execute(step.deleter)
        deleted[step.entity_type] = deleted.get(step.entity_type, 0) + (result.rowcount or 0)
    return deleted


def _verify_counts(expected: dict[str, int], actual: dict[str, int]) -> None:
    """The manifest must equal what was deleted, exactly.

    A mismatch means the data moved between snapshotting and deleting - a concurrent write, or a
    cascade removing more than was recorded. Either way the ledger would be an inaccurate account of
    the destruction, so the transaction is aborted and nothing is lost.

    Note the asymmetry that makes this check real: checksheet_value and digital_signatures CASCADE in
    production, so if the explicit delete somehow ran after the header, their rowcount would come back
    0 against a non-zero manifest and this would catch it.
    """
    for entity, count in expected.items():
        got = actual.get(entity, 0)
        if got != count:
            raise _refuse(
                "MANIFEST_MISMATCH",
                f"Deletion aborted: expected to delete {count} {entity} row(s) but the database "
                f"reported {got}. Nothing was deleted.",
                status.HTTP_409_CONFLICT,
            )
    unexpected = {k: v for k, v in actual.items() if v and k not in expected}
    if unexpected:
        raise _refuse(
            "MANIFEST_MISMATCH",
            f"Deletion aborted: rows were removed that the manifest did not account for "
            f"({unexpected}). Nothing was deleted.",
            status.HTTP_409_CONFLICT,
        )


def _execute(
    db: Session,
    *,
    deletion_type: str,
    target_id: int,
    visit: ShedVisit,
    steps: list[_Step],
    actor: User,
    reason: str,
    confirmation: str | None,
    operation_id: str,
) -> AdminDeletionEvent:
    """One transaction: ledger row, snapshots, deletes, verification, finalisation.

    The caller commits. Any exception leaves the whole thing unwritten, which is the only acceptable
    outcome for a partial visit deletion.
    """
    event = AdminDeletionEvent(
        operation_id=operation_id,
        deletion_type=deletion_type,
        target_id=target_id,
        shed_visit_id=visit.id,
        loco_number=str(visit.loco_number or ""),
        schedule_family=visit.schedule_family,
        schedule_variant=visit.schedule_variant,
        visit_status=visit.status,
        visit_arrival_at=visit.arrival_at,
        actor_user_id=actor.id,
        actor_employee_id=actor.employee_id,
        actor_name=actor.name,
        reason=reason,
        confirmation_text=confirmation,
    )
    db.add(event)
    # Flush, not commit: the ledger row needs an id for its items and for migration 078's guard,
    # while staying inside the one transaction that the deletion can still roll back.
    db.flush()

    expected_counts, manifest, file_plan = _snapshot_steps(db, event.id, steps)
    db.flush()

    actual_counts = _delete_rows(db, event.id, steps)
    _verify_counts(expected_counts, actual_counts)

    event.status = "COMPLETED"
    event.completed_at = datetime.now(timezone.utc)
    event.record_counts = {k: v for k, v in expected_counts.items() if v}
    event.manifest = manifest
    event.manifest_hash = content_hash(manifest)
    # The PLAN is committed; the files are still on disk. destroy_planned_files() acts afterwards.
    event.file_plan = file_plan
    db.flush()
    return event


def delete_booking(
    db: Session,
    booking_id: int,
    *,
    actor: User,
    reason: str,
    operation_id: str | None = None,
) -> AdminDeletionEvent:
    """Delete ONE booking and the rows it owns. The visit, its checksheets, its stages and every
    other booking are untouched."""
    reason = _validate_reason(reason)
    operation_id = operation_id or str(uuid.uuid4())

    existing = _existing_event(db, operation_id)
    if existing is not None:
        return existing

    booking = _booking_or_404(db, booking_id)
    visit = _visit_or_404(db, booking.shed_visit_id)
    # Row lock, so a concurrent request cannot delete the same booking between the check and the
    # delete and leave two ledger events describing one destruction.
    if _is_postgres(db):
        db.execute(
            select(Booking.__table__.c.id)
            .where(Booking.__table__.c.id == booking_id)
            .with_for_update()
        )

    steps = build_booking_steps(db, booking_id)
    event = _execute(
        db,
        deletion_type=DELETION_TYPE_BOOKING,
        target_id=booking_id,
        visit=visit,
        steps=steps,
        actor=actor,
        reason=reason,
        confirmation=None,
        operation_id=operation_id,
    )
    logger.warning(
        "Admin deleted a booking",
        extra={
            "action": "ADMIN_BOOKING_DELETED",
            "success": True,
            "employee_id": actor.employee_id,
            "booking_id": booking_id,
            "shed_visit_id": visit.id,
            "deletion_event_id": event.id,
        },
    )
    return event


def delete_shed_visit(
    db: Session,
    visit_id: int,
    *,
    actor: User,
    reason: str,
    confirmation: str,
    operation_id: str | None = None,
) -> AdminDeletionEvent:
    """Delete ONE shed visit and every operational record it owns, across both systems.

    Never refuses on account of state. DRAFT, SUBMITTED, APPROVED, REJECTED, individually signed and
    section-signed records are all deletable, because the business requirement is to remove mistaken
    entries and a mistaken entry can be in any of those states. What signing changes is not whether
    deletion is permitted but what must be recorded first - every signature and certificate is
    snapshotted into the ledger before anything is removed.
    """
    reason = _validate_reason(reason)
    operation_id = operation_id or str(uuid.uuid4())

    existing = _existing_event(db, operation_id)
    if existing is not None:
        return existing

    visit = _visit_or_404(db, visit_id)
    if _is_postgres(db):
        db.execute(
            select(ShedVisit.__table__.c.id)
            .where(ShedVisit.__table__.c.id == visit_id)
            .with_for_update()
        )
    _assert_confirmation(visit, confirmation)

    # A visit already fully deleted cannot be deleted again. The partial unique index
    # uq_admin_deletion_visit_completed enforces this in the database as well; checking here turns a
    # constraint violation into a clear message.
    already = (
        db.query(AdminDeletionEvent)
        .filter(
            AdminDeletionEvent.shed_visit_id == visit_id,
            AdminDeletionEvent.deletion_type == DELETION_TYPE_SHED_VISIT,
            AdminDeletionEvent.status == "COMPLETED",
        )
        .first()
    )
    if already is not None:
        raise _refuse(
            "ALREADY_DELETED",
            f"This shed visit was already deleted on "
            f"{already.completed_at:%Y-%m-%d %H:%M} by {already.actor_employee_id}.",
            status.HTTP_409_CONFLICT,
        )

    steps = build_visit_steps(db, visit_id)
    event = _execute(
        db,
        deletion_type=DELETION_TYPE_SHED_VISIT,
        target_id=visit_id,
        visit=visit,
        steps=steps,
        actor=actor,
        reason=reason,
        confirmation=confirmation,
        operation_id=operation_id,
    )
    logger.warning(
        "Admin deleted a shed visit",
        extra={
            "action": "ADMIN_SHED_VISIT_DELETED",
            "success": True,
            "employee_id": actor.employee_id,
            "shed_visit_id": visit_id,
            "loco_number": event.loco_number,
            "deletion_event_id": event.id,
            "record_counts": event.record_counts,
        },
    )
    return event


# ========================================================================= filesystem ==========
#
# PHYSICAL DESTRUCTION, at the user's explicit instruction. The signed PDF itself is unlinked and
# cannot afterwards be produced for an audit or inquiry - only its path, SHA-256, signer and
# certificate metadata survive, in the ledger. A hash proves a file WAS what it claimed to be; it
# cannot reconstruct it. That trade was made deliberately and is recorded here so nobody later reads
# this as an accident.
#
# THREE RULES THAT ARE NOT NEGOTIABLE REGARDLESS:
#   1. Nothing is touched before the database transaction has COMMITTED. A crash between commit and
#      destruction leaves files on disk with a durable record of what was meant to happen - the safe
#      direction. The reverse could destroy evidence with no record it existed.
#   2. The SHA-256 is computed during the manifest phase, while the file still exists, and committed
#      with it. Destruction can never outrun the record of it.
#   3. Every path is confined to the configured storage root by resolving it and requiring the root
#      to be a parent. A stored path pointing anywhere else - corrupted data, or a crafted value - is
#      refused, not followed. This is the same rule BL-DCMS's signed_document_service._confined()
#      applies when SERVING these files; deletion must be at least as strict as reading.


def storage_roots() -> list[Path]:
    """Directories a deletion is permitted to remove files from.

    Configured rather than derived, and absolute. An empty list disables file destruction entirely,
    which is the correct fail-closed default for a deployment that has not been told where its
    storage lives: the database rows are still deleted and every path is recorded as skipped.
    """
    raw = os.environ.get("ADMIN_DELETION_STORAGE_ROOTS", "")
    roots: list[Path] = []
    for part in raw.split(os.pathsep):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            roots.append(Path(candidate).resolve(strict=True))
        except (OSError, RuntimeError):
            logger.warning(
                "Configured deletion storage root is not usable and will be ignored",
                extra={"action": "ADMIN_DELETION_ROOT_INVALID", "success": False, "root": candidate},
            )
    return roots


def _confined(path_value: str, roots: Iterable[Path]) -> Path | None:
    """The resolved path, only if it is a regular file strictly inside one of `roots`.

    resolve(strict=True) follows symlinks before the containment test, so a symlink inside the
    storage directory pointing outside it is rejected - checking the unresolved path would let
    `storage/pdfs/evil -> /etc/passwd` through. `root in parents` is a strict-ancestor test, so the
    root directory itself can never be the target.
    """
    try:
        candidate = Path(path_value).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not candidate.is_file():
        return None
    for root in roots:
        if root in candidate.parents:
            return candidate
    return None


def destroy_planned_files(db: Session, event: AdminDeletionEvent) -> AdminDeletionEvent:
    """Act on a COMPLETED event's file plan, then record what happened. Call AFTER the commit.

    Never raises on a filesystem problem. The database deletion has already committed and is correct;
    a failure to unlink one file must be recorded, not turned into an exception that leaves the caller
    believing the whole deletion failed.
    """
    if event.status != "COMPLETED":
        raise _refuse(
            "NOT_COMPLETED",
            "Files are only acted on after the database deletion has committed.",
        )
    if event.file_result is not None:
        # Idempotent: a retry after a crash must not double-report, and the files are already gone.
        return event

    roots = storage_roots()
    results: list[dict[str, Any]] = []

    for planned in event.file_plan or []:
        path_value = planned.get("path")
        outcome: dict[str, Any] = {
            "path": path_value,
            "sha256": planned.get("sha256"),
            "entity_type": planned.get("entity_type"),
        }

        if planned.get("shared"):
            outcome["result"] = "SKIPPED_SHARED"
        elif not roots:
            outcome["result"] = "SKIPPED_NO_STORAGE_ROOT_CONFIGURED"
        elif not planned.get("exists"):
            outcome["result"] = "ALREADY_ABSENT"
        else:
            confined = _confined(str(path_value), roots)
            if confined is None:
                # Refused, not followed. Recorded so the odd path can be investigated.
                outcome["result"] = "REFUSED_OUTSIDE_STORAGE_ROOT"
                logger.error(
                    "Refused to delete a file outside the configured storage root",
                    extra={
                        "action": "ADMIN_DELETION_FILE_REFUSED",
                        "success": False,
                        "deletion_event_id": event.id,
                        "path": str(path_value),
                    },
                )
            else:
                try:
                    confined.unlink()
                    outcome["result"] = "DESTROYED"
                except OSError as exc:
                    outcome["result"] = "FAILED"
                    outcome["error"] = exc.__class__.__name__
                    logger.error(
                        "Failed to delete a file during Admin deletion",
                        extra={
                            "action": "ADMIN_DELETION_FILE_FAILED",
                            "success": False,
                            "deletion_event_id": event.id,
                            "path": str(path_value),
                        },
                    )
        results.append(outcome)

    # The ONE write permitted on an already-finalised event, by both the ORM listener and migration
    # 013's trigger: file_result plus files_completed_at, nothing else alongside them, and only while
    # file_result is still NULL. It has to exist - the files are acted on after the commit, so by then
    # the event is already COMPLETED and its outcome would otherwise be unrecordable. Written as a
    # Core UPDATE naming exactly those two columns, so the statement cannot accidentally carry other
    # dirty attributes from the session.
    db.execute(
        models.AdminDeletionEvent.__table__.update()
        .where(models.AdminDeletionEvent.__table__.c.id == event.id)
        .values(file_result=results, files_completed_at=datetime.now(timezone.utc))
    )
    db.commit()
    db.refresh(event)

    destroyed = sum(1 for r in results if r["result"] == "DESTROYED")
    logger.warning(
        "Admin deletion file step complete",
        extra={
            "action": "ADMIN_DELETION_FILES_DONE",
            "success": True,
            "deletion_event_id": event.id,
            "files_destroyed": destroyed,
            "files_planned": len(results),
        },
    )
    return event
