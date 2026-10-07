"""Request/response shapes for Admin destructive deletion.

THE PASSWORD IS A SecretStr, AND THAT IS NOT DECORATION. Pydantic's SecretStr renders as
`**********` in every repr, str and `model_dump()`, so the one way the plaintext can escape is an
explicit `.get_secret_value()`. There is exactly one such call in this codebase
(admin_reauth_service), at the comparison itself. That matters because FastAPI, Starlette and
loggers all stringify models freely - a validation error, a traceback or a debug log would otherwise
carry the password out of the process.

THE PASSWORD IS IN THE BODY, NEVER THE URL. A query parameter or path segment lands in nginx access
logs, browser history, Referer headers and any proxy in between. DELETE with a body is unusual but
legal, and it is the only placement that keeps the secret out of those.

NOTHING HERE ACCEPTS AN IDENTITY. No employee_id, no user_id, no role. The acting Admin comes from
the verified token and nowhere else, so there is no field through which a browser could nominate a
different account - and a future caller cannot pass one by accident, because the field does not
exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VisitBrief(BaseModel):
    id: int
    loco_number: str | None = None
    schedule_family: str | None = None
    schedule_variant: str | None = None
    status: str | None = None
    arrival_at: datetime | None = None


class PlannedFile(BaseModel):
    """One file the deletion would destroy. The hash is computed while the file still exists."""

    entity_type: str
    origin_id: int | None = None
    path: str
    exists: bool
    size_bytes: int | None = None
    sha256: str | None = None
    #: True when a row that SURVIVES this deletion still names the same file. Such a file is never
    #: destroyed - see admin_deletion_service._is_shared_path.
    shared: bool = False


class VisitDeletionPreview(BaseModel):
    visit: VisitBrief
    #: The exact phrase the Admin must type, derived server-side. A human check only: the deletion is
    #: identified by visit_id, never by parsing this string.
    required_confirmation: str
    #: Rows per table. Produced by the SAME code path the deletion uses, so a count shown here cannot
    #: disagree with what is actually removed.
    counts: dict[str, int]
    total_rows: int
    files: list[PlannedFile]
    #: Plain statements of what is about to be lost - signed checksheets, approved work, PDFs.
    warnings: list[str]


class BookingDeletionUnaffected(BaseModel):
    """Stated explicitly rather than left to be inferred. An Admin deleting a booking needs to know
    what a booking deletion does NOT touch."""

    shed_visit: bool
    other_bookings_on_this_visit: int
    checksheets: bool


class BookingBrief(BaseModel):
    id: int
    description: str | None = None
    status: str | None = None
    booking_source: str | None = None
    equipment_node_id: int | None = None


class BookingDeletionPreview(BaseModel):
    booking: BookingBrief
    visit: VisitBrief
    counts: dict[str, int]
    total_rows: int
    unaffected: BookingDeletionUnaffected
    files: list[PlannedFile]
    warnings: list[str]


class DeleteVisitRequest(BaseModel):
    # Forbid unknown keys: a client that sent `employee_id` or `role` hoping to influence the actor
    # gets a 422, rather than having it silently ignored and the request look as though it worked.
    model_config = ConfigDict(extra="forbid")

    password: SecretStr
    reason: str = Field(min_length=10, max_length=2000)
    #: Must equal the preview's required_confirmation. Compared case-insensitively after collapsing
    #: whitespace, because a typed phrase is a human check and not a secret.
    confirmation: str = Field(min_length=1, max_length=200)
    #: Idempotency key. A retry carrying the same value returns the original deletion record instead
    #: of creating a second one.
    operation_id: str | None = Field(default=None, max_length=36)


class DeleteBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: SecretStr
    reason: str = Field(min_length=10, max_length=2000)
    operation_id: str | None = Field(default=None, max_length=36)


class DeletionEventOut(BaseModel):
    """A row of the Deletion History. Readable after its subject is gone, by construction."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    deletion_type: str
    target_id: int
    shed_visit_id: int
    loco_number: str
    schedule_family: str | None = None
    schedule_variant: str | None = None
    visit_status: str | None = None
    actor_employee_id: str
    actor_name: str
    reason: str
    requested_at: datetime
    completed_at: datetime | None = None
    status: str
    failure_reason: str | None = None
    record_counts: dict[str, int] = {}
    total_rows: int = 0
    manifest_hash: str | None = None
    #: Summary of the filesystem step. Null until it has run - the files are acted on only after the
    #: database transaction commits.
    files_planned: int = 0
    files_destroyed: int | None = None
    files_completed_at: datetime | None = None


class DeletionEventDetail(DeletionEventOut):
    """The full manifest, for the "open details" view. Snapshots are returned separately and paged,
    because one visit deletion can carry a couple of thousand of them."""

    manifest: dict[str, Any] = {}
    file_plan: list[PlannedFile] = []
    file_result: list[dict[str, Any]] | None = None
    confirmation_text: str | None = None
    item_count: int = 0


class DeletionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    original_id: int | None = None
    original_key: dict[str, Any] | None = None
    snapshot: dict[str, Any]
    content_hash: str


class DeletionItemPage(BaseModel):
    items: list[DeletionItemOut]
    total: int
    offset: int
    limit: int
