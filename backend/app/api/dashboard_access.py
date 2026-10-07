from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.db.models import User
from app.db.session import get_db
from app.schemas.dashboard_access import DashboardAccessUpdateRequest, DashboardAccessUserOut
from app.services.access_service import list_users, update_access

router = APIRouter(prefix="/api/admin/dashboard-access", tags=["admin:dashboard-access"])


@router.get("/users", response_model=list[DashboardAccessUserOut])
def list_users_route(
    q: str | None = Query(default=None),
    role: str | None = Query(default=None),
    section_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return list_users(db, q=q, role=role, section_id=section_id)


@router.put("/users/{user_id}", response_model=DashboardAccessUserOut)
def update_access_route(
    user_id: int,
    payload: DashboardAccessUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return update_access(db, user_id, payload, admin)
