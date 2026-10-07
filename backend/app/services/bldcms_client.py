from functools import lru_cache

from app.clients.bldcms import BLDCMSClient
from app.core.config import get_settings


@lru_cache
def get_bldcms_client() -> BLDCMSClient | None:
    """FastAPI dependency provider. Cached so the app reuses one client
    (and its httpx connection pooling) rather than one per request.

    Returns None when BLDCMS_BASE_URL isn't configured — this integration
    is optional (see Settings.bldcms_base_url's own comment), so callers
    must treat a None client as "integration not configured" and report
    the same controlled unavailable state they'd use for a real connection
    failure, never crash and never silently report zero checksheets."""
    settings = get_settings()
    if not settings.bldcms_base_url:
        return None
    return BLDCMSClient(
        base_url=settings.bldcms_base_url,
        timeout=settings.bldcms_timeout_seconds,
        api_key=settings.bldcms_internal_api_key,
    )
