from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.authz import require_operations_user
from app.db.models import Section, User
from app.db.session import get_db
from app.schemas.sections import SectionOut

router = APIRouter(prefix="/api/sections", tags=["sections"])


@router.get("", response_model=list[SectionOut])
def list_sections(
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_operations_user),
):
    query = db.query(Section)
    if q:
        query = query.filter(Section.code.ilike(f"%{q}%") | Section.name.ilike(f"%{q}%"))
    return query.order_by(Section.name).all()
