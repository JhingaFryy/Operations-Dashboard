from functools import lru_cache

from app.clients.loco_master import LocoMasterClient
from app.core.config import get_settings


@lru_cache
def get_loco_master_client() -> LocoMasterClient:
    """FastAPI dependency provider. Cached so the app reuses one client
    (and its httpx connection pooling) rather than one per request."""
    settings = get_settings()
    return LocoMasterClient(
        base_url=settings.loco_master_base_url,
        timeout=settings.loco_master_timeout_seconds,
        api_key=settings.loco_master_api_key,
    )
