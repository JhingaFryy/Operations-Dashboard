from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.db.models import DashboardAccess, User
from app.schemas.dashboard_access import (
    DashboardAccessUpdateRequest,
    DashboardAccessUserOut,
    SectionBrief,
)


def _to_out(user: User) -> DashboardAccessUserOut:
    access = user.dashboard_access
    return DashboardAccessUserOut(
        id=user.id,
        employee_id=user.employee_id,
        name=user.name,
        role=user.role,
        section=SectionBrief(id=user.section.id, code=user.section.code, name=user.section.name)
        if user.section
        else None,
        is_active=bool(user.is_active),
        dashboard_access_enabled=bool(access and access.is_enabled),
        can_add_booking_sections=bool(access and access.can_add_booking_sections),
        can_route_bookings=bool(access and access.can_route_bookings),
        can_manage_equipment_mapping=bool(access and access.can_manage_equipment_mapping),
        granted_at=access.granted_at if access else None,
        revoked_at=access.revoked_at if access else None,
    )


def list_users(
    db: Session,
    q: str | None = None,
    role: str | None = None,
    section_id: int | None = None,
) -> list[DashboardAccessUserOut]:
    query = db.query(User).options(
        joinedload(User.section), joinedload(User.dashboard_access)
    )
    if q:
        like = f"%{q}%"
        query = query.filter((User.name.ilike(like)) | (User.employee_id.ilike(like)))
    if role:
        query = query.filter(User.role == role)
    if section_id is not None:
        query = query.filter(User.section_id == section_id)

    users = query.order_by(User.name).all()
    return [_to_out(u) for u in users]


def update_access(
    db: Session, target_user_id: int, payload: DashboardAccessUpdateRequest, admin: User
) -> DashboardAccessUserOut:
    user = (
        db.query(User)
        .options(joinedload(User.section), joinedload(User.dashboard_access))
        .filter(User.id == target_user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.is_enabled and not user.is_active:
        raise HTTPException(
            status_code=422,
            detail="Cannot grant Dashboard access to an inactive user.",
        )

    now = datetime.now(timezone.utc)
    access = user.dashboard_access

    if access is None:
        access = DashboardAccess(
            user_id=user.id,
            is_enabled=payload.is_enabled,
            can_add_booking_sections=payload.can_add_booking_sections,
            can_manage_equipment_mapping=payload.can_manage_equipment_mapping,
            can_route_bookings=payload.can_route_bookings,
            granted_at=now,
            granted_by=admin.id,
            updated_at=now,
        )
        db.add(access)
    else:
        was_enabled = access.is_enabled
        access.can_add_booking_sections = payload.can_add_booking_sections
        access.can_manage_equipment_mapping = payload.can_manage_equipment_mapping
        access.can_route_bookings = payload.can_route_bookings

        if payload.is_enabled and not was_enabled:
            access.granted_at = now
            access.granted_by = admin.id
            access.revoked_at = None
            access.revoked_by = None
        elif not payload.is_enabled and was_enabled:
            access.revoked_at = now
            access.revoked_by = admin.id

        access.is_enabled = payload.is_enabled
        access.updated_at = now

    db.commit()
    db.refresh(user)
    return _to_out(user)
