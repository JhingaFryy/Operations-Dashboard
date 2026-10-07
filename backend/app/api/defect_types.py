from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.authz import require_operations_user
from app.db.models import BookingDefectType
from app.db.session import get_db
from app.schemas.defect_types import DefectTypeOut

router = APIRouter(prefix="/api", tags=["defect-types"], dependencies=[Depends(require_operations_user)])


@router.get("/booking-defect-types", response_model=list[DefectTypeOut])
def list_booking_defect_types(db: Session = Depends(get_db)):
    return (
        db.query(BookingDefectType)
        .filter(BookingDefectType.is_active.is_(True))
        .order_by(BookingDefectType.sort_order, BookingDefectType.id)
        .all()
    )
