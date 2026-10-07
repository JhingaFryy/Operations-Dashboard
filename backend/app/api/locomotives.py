from fastapi import APIRouter, Depends, Query

from app.clients.loco_master import LocoMasterClient
from app.core.dependencies import get_current_user
from app.core.authz import require_operations_user
from app.schemas.locomotives import LocomotiveOut
from app.services import locomotive_service
from app.services.loco_master_client import get_loco_master_client

router = APIRouter(
    prefix="/api/locomotives", tags=["locomotives"], dependencies=[Depends(require_operations_user)]
)


@router.get("/search", response_model=list[LocomotiveOut])
def search_locomotives(
    q: str = Query(..., min_length=1),
    limit: int | None = Query(default=None, ge=1),
    client: LocoMasterClient = Depends(get_loco_master_client),
):
    return locomotive_service.search_locomotives(client, query=q, limit=limit)


@router.get("/{loco_number}", response_model=LocomotiveOut)
def get_locomotive(loco_number: str, client: LocoMasterClient = Depends(get_loco_master_client)):
    return locomotive_service.get_locomotive(client, loco_number)
