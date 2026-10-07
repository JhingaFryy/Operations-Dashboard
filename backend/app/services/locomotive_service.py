from fastapi import HTTPException, status

from app.clients.loco_master import (
    LocoMasterAuthError,
    LocoMasterClient,
    LocoMasterNotFoundError,
    LocoMasterUnavailableError,
)


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="Loco Master is unavailable."
    )


def search_locomotives(client: LocoMasterClient, query: str, limit: int | None = None) -> list[dict]:
    try:
        return client.search_locomotives(query, limit=limit)
    except (LocoMasterUnavailableError, LocoMasterAuthError):
        raise _unavailable()


def get_locomotive(client: LocoMasterClient, loco_number: str) -> dict:
    try:
        return client.get_locomotive(loco_number)
    except LocoMasterNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Locomotive not found")
    except (LocoMasterUnavailableError, LocoMasterAuthError):
        raise _unavailable()
