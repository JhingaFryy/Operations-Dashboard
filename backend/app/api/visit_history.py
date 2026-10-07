"""Shed Visit History - the permanent, searchable archive of every shed visit.

Read-only. Every route requires an operations user (Admin, or an entitled Supervisor); detail
scoping is decided server-side by visit_history_service.scope_for - never by the browser.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.clients.bldcms import BLDCMSClient
from app.clients.loco_master import LocoMasterClient
from app.core.authz import can_use_admin_functions, require_operations_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.visit_history import VisitHistoryDetail, VisitHistoryPage
from app.services import visit_history_service
from app.services.bldcms_client import get_bldcms_client
from app.services.loco_master_client import get_loco_master_client
from app.services.visit_history_service import HistoryFilters

router = APIRouter(prefix="/api/shed-visit-history", tags=["shed-visit-history"])


def history_filters(
    loco_number: str | None = Query(default=None, max_length=20),
    loco_model: str | None = Query(default=None, max_length=40),
    schedule_family: str | None = Query(default=None, max_length=20),
    schedule_variant: str | None = Query(default=None, max_length=30),
    status: str | None = Query(default=None, pattern="^(IN_SHED|READY|CLOSED)$"),
    active: bool | None = Query(default=None),
    visit_id: int | None = Query(default=None, gt=0),
    date_from: date | None = Query(default=None, description="In shed on or after this date"),
    date_to: date | None = Query(default=None, description="Arrived on or before this date"),
    arrived_from: date | None = Query(default=None),
    arrived_to: date | None = Query(default=None),
    departed_from: date | None = Query(default=None),
    departed_to: date | None = Query(default=None),
    departure_source: str | None = Query(default=None, max_length=20),
    section_id: int | None = Query(default=None, gt=0),
    booking_status: str | None = Query(default=None, max_length=20),
) -> HistoryFilters:
    return HistoryFilters(
        loco_number=loco_number, loco_model=loco_model, schedule_family=schedule_family,
        schedule_variant=schedule_variant, status=status, active=active, visit_id=visit_id,
        date_from=date_from, date_to=date_to, arrived_from=arrived_from, arrived_to=arrived_to,
        departed_from=departed_from, departed_to=departed_to, departure_source=departure_source,
        section_id=section_id, booking_status=booking_status,
    )


@router.get("", response_model=VisitHistoryPage)
def list_visit_history(
    filters: HistoryFilters = Depends(history_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=visit_history_service.DEFAULT_PAGE_SIZE, ge=1,
                           le=visit_history_service.MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_operations_user),
):
    """Every shed visit - active, Ready, closed, administratively reset - newest first, paged and
    filtered in the database."""
    return visit_history_service.search_visits(
        db, filters, page=page, page_size=page_size,
        scope_section_id=visit_history_service.scope_for(current_user), bldcms=bldcms_client,
    )


@router.get("/loco-models", response_model=list[str])
def list_loco_models(
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_operations_user),
):
    return visit_history_service.list_locomotive_models(bldcms_client)


@router.get("/{visit_id}", response_model=VisitHistoryDetail)
def get_visit_history(
    visit_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    loco_client: LocoMasterClient = Depends(get_loco_master_client),
    current_user: User = Depends(require_operations_user),
):
    """One visit opened: timings, section-grouped bookings, checksheets and events. The raw
    event payloads are included for an Admin only."""
    return visit_history_service.get_visit_detail(
        db, visit_id,
        scope_section_id=visit_history_service.scope_for(current_user),
        include_raw=can_use_admin_functions(current_user),
        bldcms=bldcms_client, loco=loco_client,
    )


@router.get("/{visit_id}/checksheets/{checksheet_id}/signed-document")
def get_signed_checksheet_document(
    visit_id: int,
    checksheet_id: int,
    db: Session = Depends(get_db),
    bldcms_client: BLDCMSClient | None = Depends(get_bldcms_client),
    current_user: User = Depends(require_operations_user),
):
    """The digitally signed checksheet PDF - streamed only after this user is authorized for
    that checksheet's section. The normal Authorization header is the only credential."""
    return visit_history_service.signed_document(db, visit_id, checksheet_id, current_user, bldcms_client)
