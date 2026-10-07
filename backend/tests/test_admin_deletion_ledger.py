"""The Admin deletion ledger: append-only, self-contained, and readable after its subject is gone.

This suite exists because the ledger is the ONLY thing that makes a destructive deletion auditable.
If it can be rewritten, or if it cascades away with the data it describes, the deletion feature is
not auditable at all - no amount of care elsewhere compensates.

The guards are enforced twice: by triggers in migration 013 (proven separately against PostgreSQL)
and by SQLAlchemy listeners in app/db/models.py. These tests exercise the listeners, which is what
makes the rule true in the test schema too - a guard that only holds in production is a guard that
is never exercised.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models import (
    AdminDeletionEvent,
    AdminDeletionItem,
    DeletionLedgerImmutableError,
)


def _event(db, **overrides) -> AdminDeletionEvent:
    values = {
        "operation_id": str(uuid.uuid4()),
        "deletion_type": "SHED_VISIT",
        "target_id": 123,
        "shed_visit_id": 123,
        "loco_number": "32032",
        "schedule_family": "MINOR",
        "schedule_variant": "IA",
        "actor_user_id": 1,
        "actor_employee_id": "ADM1",
        "actor_name": "Admin One",
        "reason": "test entry created during training",
    }
    values.update(overrides)
    event = AdminDeletionEvent(**values)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _finalise(db, event, **overrides) -> None:
    event.status = overrides.pop("status", "COMPLETED")
    event.completed_at = overrides.pop("completed_at", datetime.now(timezone.utc))
    event.manifest_hash = overrides.pop("manifest_hash", "a" * 64)
    for key, value in overrides.items():
        setattr(event, key, value)
    db.commit()


# ========================================================= the structural guarantee ==========


def test_the_ledger_has_no_foreign_key_to_operational_data(db_session):
    """THE load-bearing assertion of the whole feature.

    A foreign key from here to shed_visits, bookings, checksheet_header or users would make these
    rows either impossible to write - the target is about to be deleted - or liable to cascade away
    with their own subject. Either way the deletion becomes unauditable. The only permitted FK
    points inside the ledger.
    """
    for table_name in ("admin_deletion_event", "admin_deletion_item"):
        table = models.Base.metadata.tables[table_name]
        external = [
            f"{fk.parent.name} -> {fk.column.table.name}"
            for fk in table.foreign_keys
            if fk.column.table.name != "admin_deletion_event"
        ]
        assert external == [], f"{table_name} must not reference operational data: {external}"


def test_the_only_foreign_key_is_internal_and_restricts(db_session):
    table = models.Base.metadata.tables["admin_deletion_item"]
    fks = list(table.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "admin_deletion_event"
    # RESTRICT, never CASCADE: an event that has snapshots cannot be removed.
    assert fks[0].ondelete == "RESTRICT"


def test_no_column_could_hold_a_credential(db_session):
    """Defence by schema shape. There is no password, token or key column, so the re-authentication
    secret cannot be persisted here even by mistake."""
    for table_name in ("admin_deletion_event", "admin_deletion_item"):
        columns = {c.lower() for c in models.Base.metadata.tables[table_name].columns.keys()}
        for forbidden in ("password", "passwd", "password_hash", "token", "jwt", "secret",
                          "otp", "private_key"):
            assert forbidden not in columns, f"{table_name}.{forbidden} must not exist"


# ============================================================== append-only: events ==========


def test_a_deletion_event_can_never_be_deleted(db_session):
    event = _event(db_session)
    db_session.delete(event)
    with pytest.raises(DeletionLedgerImmutableError, match="cannot be deleted"):
        db_session.commit()
    db_session.rollback()
    assert db_session.get(AdminDeletionEvent, event.id) is not None


def test_finalising_an_in_progress_event_is_permitted(db_session):
    """The one allowed mutation. The row is written twice by design - created IN_PROGRESS when the
    operation starts, finalised when it ends - so a blanket refusal would make the feature
    impossible."""
    event = _event(db_session)
    assert event.status == "IN_PROGRESS"

    _finalise(db_session, event, record_counts={"bookings": 2, "checksheet_header": 7})

    db_session.refresh(event)
    assert event.status == "COMPLETED"
    assert event.manifest_hash == "a" * 64
    assert event.record_counts["checksheet_header"] == 7


@pytest.mark.parametrize(
    "field, value",
    [
        ("reason", "a different reason entirely"),
        ("actor_employee_id", "SOMEONE_ELSE"),
        ("actor_user_id", 999),
        ("actor_name", "Someone Else"),
        ("target_id", 999),
        ("shed_visit_id", 999),
        ("loco_number", "11111"),
        ("deletion_type", "BOOKING"),
        ("operation_id", "11111111-1111-1111-1111-111111111111"),
    ],
)
def test_identity_attribution_and_reason_are_immutable(db_session, field, value):
    """Who deleted what, and why, cannot be rewritten - not even while the event is still
    IN_PROGRESS and otherwise mutable. An audit trail the application may re-attribute afterwards
    records nothing worth having."""
    event = _event(db_session)
    setattr(event, field, value)
    with pytest.raises(DeletionLedgerImmutableError, match="immutable"):
        db_session.commit()
    db_session.rollback()


def test_requested_at_is_immutable(db_session):
    event = _event(db_session)
    event.requested_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(DeletionLedgerImmutableError, match="immutable"):
        db_session.commit()
    db_session.rollback()


def test_a_completed_event_cannot_be_changed_again(db_session):
    event = _event(db_session)
    _finalise(db_session, event)

    event.manifest_hash = "tampered"
    with pytest.raises(DeletionLedgerImmutableError, match="already COMPLETED"):
        db_session.commit()
    db_session.rollback()


def test_a_failed_event_cannot_be_changed_again(db_session):
    """A failure is as much a record as a success. Re-finalising it as COMPLETED would let a failed
    destructive attempt be presented afterwards as a clean one."""
    event = _event(db_session)
    event.status = "FAILED"
    event.completed_at = datetime.now(timezone.utc)
    event.failure_reason = "injected failure during delete"
    db_session.commit()

    event.status = "COMPLETED"
    with pytest.raises(DeletionLedgerImmutableError, match="already FAILED"):
        db_session.commit()
    db_session.rollback()


def test_the_error_names_the_stored_status_not_the_attempted_one(db_session):
    """The message has to name what is actually in the database. Reporting the attempted value would
    read as if the record were already in the state someone is trying to move it to."""
    event = _event(db_session)
    _finalise(db_session, event)
    event.status = "FAILED"
    with pytest.raises(DeletionLedgerImmutableError) as excinfo:
        db_session.commit()
    assert "already COMPLETED" in str(excinfo.value)
    db_session.rollback()


# =============================================================== append-only: items ==========


def test_an_item_snapshot_can_never_be_updated(db_session):
    """No exception at all, not even for the service that wrote it. If a snapshot were editable it
    would prove nothing about what was destroyed."""
    event = _event(db_session)
    item = AdminDeletionItem(
        deletion_event_id=event.id, entity_type="bookings", original_id=55,
        snapshot={"id": 55, "description": "Pan horn fault"}, content_hash="h" * 64,
    )
    db_session.add(item)
    db_session.commit()

    item.snapshot = {"id": 55, "description": "something else"}
    with pytest.raises(DeletionLedgerImmutableError, match="cannot be updated"):
        db_session.commit()
    db_session.rollback()


def test_an_item_snapshot_can_never_be_deleted(db_session):
    event = _event(db_session)
    item = AdminDeletionItem(
        deletion_event_id=event.id, entity_type="checksheet_value", original_id=901,
        snapshot={"id": 901, "value": "OK"}, content_hash="h" * 64,
    )
    db_session.add(item)
    db_session.commit()

    db_session.delete(item)
    with pytest.raises(DeletionLedgerImmutableError, match="cannot be deleted"):
        db_session.commit()
    db_session.rollback()


def test_a_composite_key_row_is_representable(db_session):
    """minor_inspection_section_signoff_checksheet has no surrogate id - its primary key is
    (signoff_id, checksheet_header_id). Without original_key such a row could not be snapshotted,
    and the pins a signature covered would be destroyed unrecorded."""
    event = _event(db_session)
    item = AdminDeletionItem(
        deletion_event_id=event.id,
        entity_type="minor_inspection_section_signoff_checksheet",
        original_id=None,
        original_key={"signoff_id": 4, "checksheet_header_id": 8821},
        snapshot={"signoff_id": 4, "checksheet_header_id": 8821, "display_order": 2},
        content_hash="h" * 64,
    )
    db_session.add(item)
    db_session.commit()

    stored = db_session.get(AdminDeletionItem, item.id)
    assert stored.original_id is None
    assert stored.original_key == {"signoff_id": 4, "checksheet_header_id": 8821}


# ==================================================== survives its subject's deletion ==========


def test_the_ledger_is_readable_after_its_subject_is_gone(db_session):
    """The point of the whole design. The ledger names a visit by plain integer, so there is nothing
    for the visit's deletion to cascade into. Here the subject never existed in the first place,
    which is the strongest form of the same test: the record still reads correctly.
    """
    event = _event(db_session, shed_visit_id=999999, target_id=999999, loco_number="43553")
    db_session.add(
        AdminDeletionItem(
            deletion_event_id=event.id, entity_type="shed_visits", original_id=999999,
            snapshot={"id": 999999, "loco_number": "43553", "schedule_variant": "IC"},
            content_hash="h" * 64,
        )
    )
    _finalise(db_session, event, record_counts={"shed_visits": 1})
    db_session.expire_all()

    reread = db_session.get(AdminDeletionEvent, event.id)
    assert reread.loco_number == "43553"
    assert reread.schedule_variant == "IA"
    assert reread.status == "COMPLETED"
    assert len(reread.items) == 1
    assert reread.items[0].snapshot["loco_number"] == "43553"


def test_an_event_with_items_cannot_be_removed(db_session):
    event = _event(db_session)
    db_session.add(
        AdminDeletionItem(
            deletion_event_id=event.id, entity_type="bookings", original_id=1,
            snapshot={"id": 1}, content_hash="h" * 64,
        )
    )
    db_session.commit()

    db_session.delete(event)
    with pytest.raises(DeletionLedgerImmutableError):
        db_session.commit()
    db_session.rollback()
    assert db_session.get(AdminDeletionEvent, event.id) is not None


# ============================================================ idempotency / defaults ==========


def test_the_operation_id_is_unique(db_session):
    """The idempotency key. A retried request carrying the same key must not be able to create a
    second record of one deletion."""
    from sqlalchemy.exc import IntegrityError

    shared = str(uuid.uuid4())
    _event(db_session, operation_id=shared)
    with pytest.raises(IntegrityError):
        _event(db_session, operation_id=shared, target_id=456, shed_visit_id=456)
    db_session.rollback()


def test_a_new_event_starts_in_progress_with_empty_collections(db_session):
    event = _event(db_session)
    assert event.status == "IN_PROGRESS"
    assert event.completed_at is None
    assert event.manifest_hash is None
    assert event.record_counts == {}
    assert event.manifest == {}
    # The filesystem has not been touched yet, and says so.
    assert event.file_plan == []
    assert event.file_result is None
    assert event.files_completed_at is None
