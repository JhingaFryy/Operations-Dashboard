from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Fails fast (pydantic raises on instantiation) when a required value
    is missing — never falls back to a hardcoded default for anything
    security- or connectivity-sensitive."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rdcms_database_url: str = Field(..., alias="RDCMS_DATABASE_URL")
    loco_master_base_url: str = Field(..., alias="LOCO_MASTER_BASE_URL")
    jwt_secret_key: str = Field(..., alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field("HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(480, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    loco_master_timeout_seconds: float = Field(10.0, alias="LOCO_MASTER_TIMEOUT_SECONDS")
    loco_master_api_key: str | None = Field(default=None, alias="LOCO_MASTER_INTERNAL_API_KEY")

    # BL-DCMS Integration Phase 4: a read-only integration, not (yet) load-bearing for core
    # Dashboard function - unlike rdcms_database_url/loco_master_base_url above, deliberately
    # optional (str | None) rather than a required Field(...), so an operator who hasn't wired
    # this up yet doesn't get a hard startup crash for an integration nothing else depends on.
    # No hardcoded default value for either - see app/services/checksheet_integration_service.py
    # for how "not configured" is surfaced to callers as a controlled unavailable state.
    bldcms_base_url: str | None = Field(default=None, alias="BLDCMS_BASE_URL")
    #: The browser origin BL-DCMS is served from, e.g. "http://10.94.36.10". The one-time
    #: handoff POST arrives cross-origin from there, so its Origin is checked against this.
    #: Comma-separated to allow a hostname and an IP for the same deployment. Empty disables
    #: the check - acceptable only where the handoff endpoint is not reachable from outside.
    bldcms_browser_origins: str = Field(default="", alias="BLDCMS_BROWSER_ORIGINS")
    bldcms_internal_api_key: str | None = Field(default=None, alias="BLDCMS_INTERNAL_API_KEY")
    bldcms_timeout_seconds: float = Field(10.0, alias="BLDCMS_TIMEOUT_SECONDS")

    # Phase 5B.4A: the INBOUND counterpart of bldcms_internal_api_key above - this is the key
    # BL-DCMS must present (as X-Internal-API-Key) when it calls this app's own
    # /api/internal/shed-visits/{id}/reconcile-checksheet-stages callback, not a key this app
    # sends elsewhere. Optional/None (not Field(...)) so an operator who hasn't wired up the
    # automatic trigger yet doesn't get a hard startup crash - see
    # app/core/dependencies.py:require_internal_api_key, which fails closed (401) identically
    # whether this is unset or simply doesn't match, so a caller can never distinguish "not
    # configured" from "wrong key".
    operations_internal_api_key: str | None = Field(default=None, alias="OPERATIONS_INTERNAL_API_KEY")

    # Sections whose Supervisors additionally control locomotive MOVEMENT (Shed In, Start
    # Schedule, Complete Schedule, Shed Out). Comma-separated section codes/names, matched
    # case-insensitively after trimming - see app/core/authz.py, which is the only module that
    # interprets this.
    #
    # Configurable rather than hardcoded so a movement section can be added without a code
    # change and without a database id baked into source.
    #
    # PPIO WAS REMOVED FROM THIS DEFAULT (2026-10-05). It was listed here in anticipation of the
    # section being created, on the assumption that PPIO would be a shed-movement section. The
    # PPIO workflow that was actually specified is the opposite: PPIO is a PLANNING section whose
    # entire Operations Dashboard surface is read-only apart from booking section routing, and it
    # must never Shed In, Shed Out, Start Schedule or Complete Schedule.
    #
    # Leaving PPIO here was a LATENT PRIVILEGE GRANT: because this list is matched by section
    # code/name, a PPIO Supervisor would have acquired locomotive movement the instant the
    # sections row was inserted - no code change, no deploy, no review. That is why this change
    # ships BEFORE the section exists. See tests/test_ppio_authorization.py, which asserts PPIO's
    # absence from the default directly rather than trusting this comment.
    loco_movement_section_codes: str = Field(
        "SHIFT", alias="LOCO_MOVEMENT_SECTION_CODES"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
