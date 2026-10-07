"""Admin destructive deletion routes: preview, delete, and the deletion history.

AUTHORIZATION IS DOUBLE, AND DELIBERATELY SO. Every route is `Depends(require_admin)`, and every
destructive route then re-verifies the caller's password through admin_reauth_service, which re-reads
the account and re-checks that it is still Admin and still active. The route guard answers "is this
an Admin session"; the password answers "is this Admin here, now". An unattended desk or a stolen
token satisfies the first and not the second.

Frontend hiding is not part of the defence. A Supervisor or Technician calling these endpoints
directly gets a 403 from require_admin before any handler code runs.

WHY DELETE CARRIES A BODY. The password must not appear in a URL, a query string or a path segment -
all three land in nginx access logs, browser history and Referer headers. DELETE with a body is
unusual but legal, and it is the only placement that keeps the secret out of those.

THE PREVIEW AND THE DELETION SHARE ONE CODE PATH. preview_visit_deletion runs exactly the manifest
computation the deletion runs, minus the DELETE statements. That is what makes the counts shown in
the confirmation dialog trustworthy: they are not an estimate of what will be deleted, they are the
same computation.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.db.models import AdminDeletionEvent, AdminDeletionItem, User
from app.db.session import get_db
from app.schemas.admin_deletion import (
    BookingDeletionPreview,
    DeleteBookingRequest,
    DeleteVisitRequest,
    DeletionEventDetail,
    DeletionEventOut,
    DeletionItemOut,
    DeletionItemPage,
    VisitDeletionPreview,
)
from app.services import admin_deletion_service as deletion
from app.services.admin_reauth_service import verify_destructive_action_password

logger = logging.getLogger("app.security")

router = APIRouter(prefix="/api/admin", tags=["admin-deletion"])


def _event_out(event: AdminDeletionEvent) -> dict:
    """One ledger row flattened for the history list, with the file step summarised."""
    results = event.file_result or []
    return {
        "id": event.id,
        "deletion_type": event.deletion_type,
        "target_id": event.target_id,
        "shed_visit_id": event.shed_visit_id,
        "loco_number": event.loco_number,
        "schedule_family": event.schedule_family,
        "schedule_variant": event.schedule_variant,
        "visit_status": event.visit_status,
        "actor_employee_id": event.actor_employee_id,
        "actor_name": event.actor_name,
        "reason": event.reason,
        "requested_at": event.requested_at,
        "completed_at": event.completed_at,
        "status": event.status,
        "failure_reason": event.failure_reason,
        "record_counts": event.record_counts or {},
        "total_rows": sum((event.record_counts or {}).values()),
        "manifest_hash": event.manifest_hash,
        "files_planned": len(event.file_plan or []),
        # None, not 0, until the filesystem step has run - "none destroyed yet" and "none to destroy"
        # are different facts and the UI shows them differently.
        "files_destroyed": (
            sum(1 for r in results if r.get("result") == "DESTROYED")
            if event.file_result is not None else None
        ),
        "files_completed_at": event.files_completed_at,
    }


# ================================================================================ preview =======


@router.get("/shed-visits/{visit_id}/deletion-preview", response_model=VisitDeletionPreview)
def preview_shed_visit_deletion(
    visit_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Everything deleting this visit would destroy. Read-only; no password needed to look."""
    return deletion.preview_visit_deletion(db, visit_id)


@router.get("/bookings/{booking_id}/deletion-preview", response_model=BookingDeletionPreview)
def preview_booking_deletion(
    booking_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return deletion.preview_booking_deletion(db, booking_id)


# ================================================================================= delete =======


@router.delete("/shed-visits/{visit_id}", response_model=DeletionEventDetail)
def delete_shed_visit(
    visit_id: int,
    payload: DeleteVisitRequest = Body(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Permanently delete one shed visit and every operational record it owns, across both systems.

    The actor is the account the password verified, not the one the token named - they are the same
    row, but using the verified one means the attribution can never drift from the check.
    """
    actor = verify_destructive_action_password(db, admin, payload.password)

    event = deletion.delete_shed_visit(
        db,
        visit_id,
        actor=actor,
        reason=payload.reason,
        confirmation=payload.confirmation,
        operation_id=payload.operation_id,
    )
    db.commit()

    # AFTER the commit, never before. The filesystem cannot join the transaction, so a crash here
    # leaves files on disk with a durable record of what was meant to happen - the safe direction.
    deletion.destroy_planned_files(db, event)

    return _detail_out(db, event)


@router.delete("/bookings/{booking_id}", response_model=DeletionEventDetail)
def delete_booking(
    booking_id: int,
    payload: DeleteBookingRequest = Body(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Delete ONE booking and the rows it owns. The visit, its checksheets, its stages and every
    other booking are untouched.

    No confirmation phrase: a booking is a single row with a visible description, and the preview
    names it. The password and a recorded reason are still required.
    """
    actor = verify_destructive_action_password(db, admin, payload.password)

    event = deletion.delete_booking(
        db, booking_id, actor=actor, reason=payload.reason, operation_id=payload.operation_id
    )
    db.commit()
    return _detail_out(db, event)


# ================================================================================ history =======


def _detail_out(db: Session, event: AdminDeletionEvent) -> dict:
    item_count = db.execute(
        select(func.count())
        .select_from(AdminDeletionItem)
        .where(AdminDeletionItem.deletion_event_id == event.id)
    ).scalar_one()
    return {
        **_event_out(event),
        "manifest": event.manifest or {},
        "file_plan": event.file_plan or [],
        "file_result": event.file_result,
        "confirmation_text": event.confirmation_text,
        "item_count": item_count,
    }


@router.get("/deletion-history", response_model=list[DeletionEventOut])
def list_deletion_history(
    deletion_type: str | None = Query(default=None),
    shed_visit_id: int | None = Query(default=None),
    loco_number: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Every deletion, newest first. Readable long after its subjects are gone - the ledger holds no
    foreign key to operational data, so there is nothing for a deletion to have cascaded into."""
    query = db.query(AdminDeletionEvent)
    if deletion_type:
        query = query.filter(AdminDeletionEvent.deletion_type == deletion_type)
    if shed_visit_id is not None:
        query = query.filter(AdminDeletionEvent.shed_visit_id == shed_visit_id)
    if loco_number:
        query = query.filter(AdminDeletionEvent.loco_number == loco_number.strip())
    events = (
        query.order_by(AdminDeletionEvent.requested_at.desc(), AdminDeletionEvent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_event_out(event) for event in events]


@router.get("/deletion-history/{event_id}", response_model=DeletionEventDetail)
def get_deletion_event(
    event_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    event = db.get(AdminDeletionEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Deletion record not found."
        )
    return _detail_out(db, event)


@router.get("/deletion-history/{event_id}/items", response_model=DeletionItemPage)
def get_deletion_event_items(
    event_id: int,
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """The individual snapshots. Paged, because one visit deletion can carry a couple of thousand -
    the production stress fixture (visit 42) would produce roughly 950."""
    if db.get(AdminDeletionEvent, event_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Deletion record not found."
        )
    query = db.query(AdminDeletionItem).filter(AdminDeletionItem.deletion_event_id == event_id)
    if entity_type:
        query = query.filter(AdminDeletionItem.entity_type == entity_type)
    total = query.count()
    items = query.order_by(AdminDeletionItem.id).offset(offset).limit(limit).all()
    return {
        "items": [DeletionItemOut.model_validate(item) for item in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }
