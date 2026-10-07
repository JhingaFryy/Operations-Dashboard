"""THE DESTRUCTIVE PROOF: Admin deletion run against a production-faithful PostgreSQL database.

WHY THIS SUITE EXISTS SEPARATELY FROM THE SQLITE TESTS. The entire question this feature turns on is
what the DATABASE does: whether RESTRICT blocks a parent delete, whether CASCADE fires before a
snapshot can be taken, whether a trigger refuses, whether a partial unique index catches a second
deletion. SQLite answers none of those the way production does. So this suite runs against a schema
loaded verbatim from `pg_dump --schema-only` of rdcms:

    37 tables | 77 foreign keys with their real ON DELETE actions | 71 CHECK constraints | 2 triggers

That is a direct answer to how migration 077 failed in production: its proof used a scratch schema
built by hand WITHOUT constraints, which removed the only thing that could have caught the collision.
Building fixtures here surfaced four production constraints a hand-written schema would have missed -
uq_checksheet_minor_inspection_instance, uq_one_open_shed_visit_per_loco,
uq_checksheet_requirement_identity and chk_checksheet_requirement_model_coherent - every one of which
changed the fixtures.

HOW TO RUN. The suite skips unless pointed at a scratch database, so an ordinary `pytest` run is
unaffected and no developer can accidentally run destructive tests against anything real:

    SCRATCH=/path/to/cluster scripts/rebuild_deletion_scratch.sh
    psql ... -f ../../Checksheet/backend/migrations/078_*.sql
    psql ... -f migrations/013_admin_deletion_ledger.sql
    ADMIN_DELETION_SCRATCH_DSN='postgresql+psycopg://...' pytest tests/test_admin_deletion_scratch.py

Every test rebuilds the fixtures first, so each runs against the same known starting state and the
order of tests cannot matter.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import shared_tables as bl
from app.db.models import AdminDeletionEvent, AdminDeletionItem, User
from app.services import admin_deletion_service as svc

DSN = os.environ.get("ADMIN_DELETION_SCRATCH_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ADMIN_DELETION_SCRATCH_DSN to run the destructive PostgreSQL proof"
)

# The fixture visit ids, from scripts/build_deletion_scratch_fixtures.py.
V_SIMPLE, V_BOOKINGS_ONLY, V_LEGACY_DSC = 9001, 9002, 9003
V_ALL_STATUSES, V_TB_TA, V_SECTION_SIGNOFF = 9004, 9005, 9006
V_TWIN_CLOSED, V_TWIN_OPEN, V_MAJOR = 9007, 9008, 9009

#: Tables that must be completely untouched by any deletion. Checked by exact row count before and
#: after, so a deletion that reached into a master table fails loudly rather than subtly.
_MASTER_TABLES = (
    "locomotives", "equipment", "sections", "users", "checksheet_templates", "template_fields",
    "checksheet_template_applicability", "minor_inspection_equipment", "booking_defect_types",
    "section_equipment_map", "minor_inspection_checkpoint", "minor_inspection_checkpoint_schedule",
    "minor_inspection_instance_field", "system_settings",
)


@pytest.fixture()
def engine():
    return create_engine(DSN, future=True)


@pytest.fixture()
def rebuilt(engine):
    """Rebuild the schema and fixtures, then re-materialise the PDF files, before every test."""
    scratch = os.environ["ADMIN_DELETION_SCRATCH_DIR"]
    # The rebuild drops the database, which cannot happen while this engine holds a pooled
    # connection to it. DROP DATABASE ... WITH (FORCE) handles other sessions; this handles ours.
    engine.dispose()
    subprocess.run(
        ["scripts/rebuild_deletion_scratch.sh"],
        check=True, capture_output=True,
        env={**os.environ, "SCRATCH": scratch},
    )
    # The migrations are applied by rebuild_deletion_scratch.sh itself, in order, so they are NOT
    # re-applied here. Doing both failed: 013's CREATE TRIGGER is not idempotent, so a second pass
    # aborted the whole script. One place owns the sequence.
    store = Path(scratch) / "storage" / "pdfs"
    store.mkdir(parents=True, exist_ok=True)
    with engine.begin() as conn:
        for table, column in (("checksheet_header", "pdf_path"),
                              ("minor_inspection_section_signoff", "signed_pdf_path")):
            conn.execute(text(
                f"UPDATE {table} SET {column} = replace({column}, '__STORAGE__', :root) "
                f"WHERE {column} IS NOT NULL"
            ), {"root": str(store)})
            for (path,) in conn.execute(text(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
            )):
                Path(path).write_text(f"fixture pdf {path}")
    os.environ["ADMIN_DELETION_STORAGE_ROOTS"] = str(store)
    return store


@pytest.fixture()
def db(engine, rebuilt):
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def admin(db) -> User:
    return db.query(User).filter(User.employee_id == "ADM1").one()


def _count(db, table: str, where: str = "TRUE", **params) -> int:
    return db.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params).scalar_one()


def _master_counts(db) -> dict[str, int]:
    return {t: _count(db, t) for t in _MASTER_TABLES}


def _delete_visit(db, visit_id: int, admin: User, *, confirmation: str | None = None):
    visit = db.get(svc.ShedVisit, visit_id)
    event = svc.delete_shed_visit(
        db, visit_id, actor=admin,
        reason="fixture cleanup during destructive proof",
        confirmation=confirmation if confirmation is not None else svc.required_confirmation(visit),
    )
    db.commit()
    return event


# ============================================================== the schema is what we think ======


def test_the_scratch_schema_is_production_faithful(db):
    """Guards every other test here. If the schema were permissive, the RESTRICT and CASCADE tests
    below would pass vacuously and prove nothing."""
    # 37 production tables, plus admin_deletion_event and admin_deletion_item from migration 013.
    assert _count(db, "pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace",
                  "n.nspname='public' AND c.relkind='r'") == 39
    # 77 production foreign keys, plus the ledger's one internal key, plus the two migration 014 adds
    # (amc_marked_by and amc_cleared_by -> users).
    assert _count(db, "pg_constraint", "contype='f'") == 80
    assert _count(db, "pg_trigger", "NOT tgisinternal") >= 4  # 2 production + 2 ledger guards
    actions = dict(db.execute(text("""
        SELECT ct.relname||'.'||a.attname,
               CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
                    WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' END
        FROM pg_constraint con JOIN pg_class ct ON ct.oid=con.conrelid
        JOIN pg_class pt ON pt.oid=con.confrelid
        JOIN unnest(con.conkey) k(attnum) ON TRUE
        JOIN pg_attribute a ON a.attrelid=ct.oid AND a.attnum=k.attnum
        WHERE con.contype='f'
    """)).all())
    # The four that drive the whole deletion order.
    assert actions["bookings.shed_visit_id"] == "RESTRICT"
    assert actions["bookings.stage_id"] == "RESTRICT"
    assert actions["checksheet_value.checksheet_id"] == "CASCADE"
    assert actions["digital_signatures.checksheet_id"] == "CASCADE"


def test_the_snapshot_allow_lists_match_the_production_schema(db):
    """A migration that adds a column must fail HERE, not silently drop that column from every future
    deletion record."""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(db.bind)
    for table, allowed in svc._SNAPSHOT_COLUMNS.items():
        real = {c["name"] for c in insp.get_columns(table)}
        assert set(allowed) == real, (
            f"{table}: allow-list and schema disagree -> {sorted(set(allowed) ^ real)}"
        )


def test_the_ledger_model_types_match_the_migration(db):
    """Column NAMES matching is not enough. The model first declared operation_id as String(36)
    against a UUID column, and PostgreSQL does not compare the two at all - every deletion failed
    with `operator does not exist: uuid = character varying` on the idempotency lookup. SQLite had
    no opinion, so the unit tests passed. This asserts the types agree where it matters.
    """
    from sqlalchemy import inspect as sa_inspect

    from app.db.models import AdminDeletionEvent, AdminDeletionItem

    insp = sa_inspect(db.bind)
    dialect = db.bind.dialect
    for model in (AdminDeletionEvent, AdminDeletionItem):
        actual = {c["name"]: str(c["type"]).upper() for c in insp.get_columns(model.__tablename__)}
        for column in model.__table__.columns:
            declared = column.type.dialect_impl(dialect)
            compiled = str(declared.compile(dialect)).upper()
            got = actual[column.name]
            # Compare the type FAMILY: PostgreSQL reports TIMESTAMP WITH TIME ZONE where the model
            # compiles TIMESTAMP, and JSONB vs JSON differ harmlessly in name only.
            family = lambda t: (
                "JSON" if "JSON" in t else
                "TIMESTAMP" if "TIMESTAMP" in t else
                "UUID" if "UUID" in t else
                "TEXT" if ("TEXT" in t or "VARCHAR" in t or "CHARACTER" in t) else
                "INT" if "INT" in t or "SERIAL" in t else t
            )
            assert family(compiled) == family(got), (
                f"{model.__tablename__}.{column.name}: model {compiled} vs database {got}"
            )


def test_restrict_really_blocks_a_naive_delete(db):
    """Proves the deletion order is necessary rather than defensive. Deleting the visit row first -
    what a careless implementation would do - is refused by the database."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM shed_visits WHERE id = :v"), {"v": V_LEGACY_DSC})
        db.flush()
    db.rollback()


def test_the_signoff_mode_trigger_refuses_without_the_078_guard(db):
    """The blocker, confirmed on this schema. Without migration 078's session setting the marker
    cannot be removed, so a marked visit could not be deleted at all."""
    from sqlalchemy.exc import InternalError, ProgrammingError

    with pytest.raises((InternalError, ProgrammingError)) as excinfo:
        db.execute(text("DELETE FROM shed_visit_checksheet_signoff_mode WHERE shed_visit_id = :v"),
                   {"v": V_LEGACY_DSC})
        db.flush()
    assert "write-once" in str(excinfo.value)
    db.rollback()


# ================================================================= one booking, isolated ========


def test_deleting_one_booking_leaves_everything_else_intact(db, admin):
    before = _master_counts(db)
    visit_checksheets = _count(db, "checksheet_header", "shed_visit_id=:v", v=V_LEGACY_DSC)
    booking_id = db.execute(text(
        "SELECT id FROM bookings WHERE shed_visit_id=:v ORDER BY id LIMIT 1"
    ), {"v": V_LEGACY_DSC}).scalar_one()

    event = svc.delete_booking(
        db, booking_id, actor=admin, reason="duplicate booking raised during training"
    )
    db.commit()

    assert _count(db, "bookings", "id=:b", b=booking_id) == 0
    assert _count(db, "booking_section_assignments", "booking_id=:b", b=booking_id) == 0
    assert _count(db, "booking_events", "booking_id=:b", b=booking_id) == 0
    # The visit, its other bookings, its stages and every checksheet survive.
    assert _count(db, "shed_visits", "id=:v", v=V_LEGACY_DSC) == 1
    assert _count(db, "bookings", "shed_visit_id=:v", v=V_LEGACY_DSC) == 2
    assert _count(db, "shed_visit_stages", "shed_visit_id=:v", v=V_LEGACY_DSC) == 3
    assert _count(db, "checksheet_header", "shed_visit_id=:v", v=V_LEGACY_DSC) == visit_checksheets
    assert _master_counts(db) == before
    assert event.deletion_type == "BOOKING"
    assert event.record_counts == {"booking_events": 1, "booking_section_assignments": 1, "bookings": 1}


def test_a_booking_deletion_snapshots_its_children(db, admin):
    booking_id = db.execute(text(
        "SELECT id FROM bookings WHERE shed_visit_id=:v ORDER BY id LIMIT 1"
    ), {"v": V_BOOKINGS_ONLY}).scalar_one()
    event = svc.delete_booking(db, booking_id, actor=admin, reason="mistaken entry by new staff")
    db.commit()

    items = db.query(AdminDeletionItem).filter(
        AdminDeletionItem.deletion_event_id == event.id
    ).all()
    by_type = {}
    for item in items:
        by_type.setdefault(item.entity_type, []).append(item)
    assert set(by_type) == {"bookings", "booking_section_assignments", "booking_events"}
    booking_snapshot = by_type["bookings"][0].snapshot
    # The substance, not just the id: what it was, who raised it, and against which equipment.
    assert booking_snapshot["description"].startswith("Fixture booking")
    assert booking_snapshot["equipment_node_id"] == 1843
    assert booking_snapshot["booking_source"] == "LOG_BOOK"


# ================================================================= full visit deletion ==========


def test_deleting_the_simplest_visit(db, admin):
    before = _master_counts(db)
    event = _delete_visit(db, V_SIMPLE, admin)
    assert _count(db, "shed_visits", "id=:v", v=V_SIMPLE) == 0
    assert _count(db, "shed_visit_stages", "shed_visit_id=:v", v=V_SIMPLE) == 0
    assert _count(db, "shed_visit_events", "shed_visit_id=:v", v=V_SIMPLE) == 0
    assert _master_counts(db) == before
    assert event.status == "COMPLETED"


def test_deleting_a_visit_with_every_checksheet_status(db, admin):
    """DRAFT, SUBMITTED, UNDER_REVIEW, APPROVED and REJECTED all go. Deletion never refuses on
    account of state - the business requirement is to remove mistaken entries, and a mistaken entry
    can be in any state."""
    statuses = dict(db.execute(text(
        "SELECT status, count(*) FROM checksheet_header WHERE shed_visit_id=:v GROUP BY status"
    ), {"v": V_ALL_STATUSES}).all())
    assert set(statuses) == {"DRAFT", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "REJECTED"}

    event = _delete_visit(db, V_ALL_STATUSES, admin)

    assert _count(db, "checksheet_header", "shed_visit_id=:v", v=V_ALL_STATUSES) == 0
    assert event.record_counts["checksheet_header"] == sum(statuses.values())
    snapshotted = {
        item.snapshot["status"]
        for item in db.query(AdminDeletionItem).filter(
            AdminDeletionItem.deletion_event_id == event.id,
            AdminDeletionItem.entity_type == "checksheet_header",
        )
    }
    assert snapshotted == set(statuses)


def test_deleting_a_legacy_individually_signed_visit(db, admin):
    """The signatures and their certificate details must reach the ledger. They are ON DELETE CASCADE
    from checksheet_header, so if the snapshot ran after the header delete they would already be gone
    and this test would find nothing."""
    signatures = _count(db, "digital_signatures ds JOIN checksheet_header ch ON ch.id=ds.checksheet_id",
                        "ch.shed_visit_id=:v", v=V_LEGACY_DSC)
    assert signatures == 4

    event = _delete_visit(db, V_LEGACY_DSC, admin)

    assert _count(db, "checksheet_header", "shed_visit_id=:v", v=V_LEGACY_DSC) == 0
    assert _count(db, "digital_signatures", "TRUE") == _count(
        db, "digital_signatures ds JOIN checksheet_header ch ON ch.id=ds.checksheet_id", "TRUE")
    sig_items = db.query(AdminDeletionItem).filter(
        AdminDeletionItem.deletion_event_id == event.id,
        AdminDeletionItem.entity_type == "digital_signatures",
    ).all()
    assert len(sig_items) == signatures
    one = sig_items[0].snapshot
    for field in ("certificate_subject", "certificate_issuer", "certificate_serial_number",
                  "certificate_thumbprint", "signature_hash", "signing_timestamp",
                  "supervisor_employee_id", "verification_status", "provider"):
        assert one.get(field), f"{field} missing from the signature snapshot"


def test_checksheet_values_are_snapshotted_before_cascade_can_remove_them(db, admin):
    """THE cascade trap. checksheet_value is ON DELETE CASCADE from checksheet_header in production,
    so a single DELETE of the header silently destroys every answer a technician recorded. The ledger
    must already hold them."""
    values = _count(db, "checksheet_value cv JOIN checksheet_header ch ON ch.id=cv.checksheet_id",
                    "ch.shed_visit_id=:v", v=V_LEGACY_DSC)
    assert values == 12

    event = _delete_visit(db, V_LEGACY_DSC, admin)

    value_items = db.query(AdminDeletionItem).filter(
        AdminDeletionItem.deletion_event_id == event.id,
        AdminDeletionItem.entity_type == "checksheet_value",
    ).all()
    assert len(value_items) == values
    # The ANSWER itself, with the field it answered - not merely a row count.
    assert all(i.snapshot.get("field_value") for i in value_items)
    assert all(i.snapshot.get("field_id") for i in value_items)


def test_deleting_a_section_signoff_visit_including_its_pins(db, admin):
    """The synthesised shape. Production has no section sign-off rows at all, so without this fixture
    the signed-sign-off path would be completely unproven."""
    assert _count(db, "minor_inspection_section_signoff", "shed_visit_id=:v", v=V_SECTION_SIGNOFF) == 1
    pins = _count(db, "minor_inspection_section_signoff_checksheet sc "
                      "JOIN minor_inspection_section_signoff s ON s.id=sc.signoff_id",
                  "s.shed_visit_id=:v", v=V_SECTION_SIGNOFF)
    assert pins == 3

    event = _delete_visit(db, V_SECTION_SIGNOFF, admin)

    assert _count(db, "minor_inspection_section_signoff", "shed_visit_id=:v", v=V_SECTION_SIGNOFF) == 0
    assert _count(db, "minor_inspection_section_signoff_checksheet", "TRUE") == 0
    assert event.record_counts["minor_inspection_section_signoff"] == 1
    assert event.record_counts["minor_inspection_section_signoff_checksheet"] == pins

    signoff_snapshot = db.query(AdminDeletionItem).filter(
        AdminDeletionItem.deletion_event_id == event.id,
        AdminDeletionItem.entity_type == "minor_inspection_section_signoff",
    ).one().snapshot
    assert signoff_snapshot["status"] == "SIGNED"
    assert signoff_snapshot["signed_by_employee_id"] == "SUP1"
    assert signoff_snapshot["document_hash"]
    assert signoff_snapshot["certificate_serial_number"] == "SER-SOF-1"

    # Composite-key pins have no surrogate id, so they are recorded by key.
    pin_items = db.query(AdminDeletionItem).filter(
        AdminDeletionItem.deletion_event_id == event.id,
        AdminDeletionItem.entity_type == "minor_inspection_section_signoff_checksheet",
    ).all()
    assert len(pin_items) == pins
    assert all(i.original_id is None and set(i.original_key) ==
               {"signoff_id", "checksheet_header_id"} for i in pin_items)


def test_deleting_a_visit_with_test_before_and_test_after_checksheets(db, admin):
    tb_ta = _count(db, "checksheet_header",
                   "shed_visit_id=:v AND workflow_stage_type IN ('TEST_BEFORE','TEST_AFTER')",
                   v=V_TB_TA)
    assert tb_ta == 2
    event = _delete_visit(db, V_TB_TA, admin)
    assert _count(db, "checksheet_header", "shed_visit_id=:v", v=V_TB_TA) == 0
    stages = {i.snapshot.get("workflow_stage_type") for i in db.query(AdminDeletionItem).filter(
        AdminDeletionItem.deletion_event_id == event.id,
        AdminDeletionItem.entity_type == "checksheet_header")}
    assert {"TEST_BEFORE", "TEST_AFTER", "SCHEDULE_INSPECTION"} == stages


def test_deleting_a_major_visit(db, admin):
    event = _delete_visit(db, V_MAJOR, admin)
    assert _count(db, "shed_visits", "id=:v", v=V_MAJOR) == 0
    assert event.schedule_family == "MAJOR"
    assert event.schedule_variant == "TOH"


def test_workflow_stage_and_event_records_are_deleted_and_snapshotted(db, admin):
    event = _delete_visit(db, V_LEGACY_DSC, admin)
    assert _count(db, "shed_visit_stages", "shed_visit_id=:v", v=V_LEGACY_DSC) == 0
    assert _count(db, "shed_visit_events", "shed_visit_id=:v", v=V_LEGACY_DSC) == 0
    assert _count(db, "shed_visit_checksheet_packages", "shed_visit_id=:v", v=V_LEGACY_DSC) == 0
    assert event.record_counts["shed_visit_stages"] == 3
    assert event.record_counts["shed_visit_events"] == 1
    assert event.record_counts["shed_visit_checksheet_requirements"] == 3
    stage_types = {i.snapshot["stage_type"] for i in db.query(AdminDeletionItem).filter(
        AdminDeletionItem.deletion_event_id == event.id,
        AdminDeletionItem.entity_type == "shed_visit_stages")}
    assert stage_types == {"TEST_BEFORE", "SCHEDULE_INSPECTION", "TEST_AFTER"}


def test_the_signoff_mode_marker_is_deleted_via_the_078_exemption(db, admin):
    """Proof that the gated exemption works end to end, on the real trigger."""
    assert _count(db, "shed_visit_checksheet_signoff_mode", "shed_visit_id=:v", v=V_LEGACY_DSC) == 1
    event = _delete_visit(db, V_LEGACY_DSC, admin)
    assert _count(db, "shed_visit_checksheet_signoff_mode", "shed_visit_id=:v", v=V_LEGACY_DSC) == 0
    assert event.record_counts["shed_visit_checksheet_signoff_mode"] == 1
    # And the exemption did not survive the transaction: another marker is still protected.
    from sqlalchemy.exc import InternalError, ProgrammingError
    with pytest.raises((InternalError, ProgrammingError)):
        db.execute(text("DELETE FROM shed_visit_checksheet_signoff_mode WHERE shed_visit_id=:v"),
                   {"v": V_TWIN_CLOSED})
        db.flush()
    db.rollback()


# ======================================================================== isolation =============


def test_another_visit_of_the_same_locomotive_survives(db, admin):
    """The isolation guarantee that matters most, and the one production cannot supply a fixture for:
    all 31 production visits are for distinct locomotives."""
    assert _count(db, "shed_visits", "loco_number='99999'") == 2
    before_master = _master_counts(db)

    _delete_visit(db, V_TWIN_OPEN, admin)

    assert _count(db, "shed_visits", "id=:v", v=V_TWIN_OPEN) == 0
    # The other visit of the SAME locomotive is completely untouched.
    assert _count(db, "shed_visits", "id=:v", v=V_TWIN_CLOSED) == 1
    assert _count(db, "checksheet_header", "shed_visit_id=:v", v=V_TWIN_CLOSED) == 2
    assert _count(db, "bookings", "shed_visit_id=:v", v=V_TWIN_CLOSED) == 1
    assert _count(db, "digital_signatures ds JOIN checksheet_header ch ON ch.id=ds.checksheet_id",
                  "ch.shed_visit_id=:v", v=V_TWIN_CLOSED) == 1
    assert _count(db, "shed_visit_checksheet_signoff_mode", "shed_visit_id=:v", v=V_TWIN_CLOSED) == 1
    # And the locomotive master row itself.
    assert _count(db, "locomotives", "loco_number='99999'") == 1
    assert _master_counts(db) == before_master


def test_every_other_visit_is_untouched(db, admin):
    others = [V_SIMPLE, V_BOOKINGS_ONLY, V_ALL_STATUSES, V_TB_TA, V_SECTION_SIGNOFF,
              V_TWIN_CLOSED, V_TWIN_OPEN, V_MAJOR]
    before = {v: (_count(db, "checksheet_header", "shed_visit_id=:v", v=v),
                  _count(db, "bookings", "shed_visit_id=:v", v=v)) for v in others}

    _delete_visit(db, V_LEGACY_DSC, admin)

    after = {v: (_count(db, "checksheet_header", "shed_visit_id=:v", v=v),
                 _count(db, "bookings", "shed_visit_id=:v", v=v)) for v in others}
    assert after == before
    assert _count(db, "shed_visits", "TRUE") == len(others)


def test_masters_survive_every_deletion(db, admin):
    """Templates, template fields, equipment, the Minor Inspection directory, sections, users,
    locomotives and defect types. Checked by exact row count after deleting EVERY visit."""
    before = _master_counts(db)
    for visit_id in (V_SIMPLE, V_BOOKINGS_ONLY, V_LEGACY_DSC, V_ALL_STATUSES, V_TB_TA,
                     V_SECTION_SIGNOFF, V_TWIN_OPEN, V_TWIN_CLOSED, V_MAJOR):
        _delete_visit(db, visit_id, admin)

    assert _count(db, "shed_visits", "TRUE") == 0
    assert _master_counts(db) == before
    # Nothing operational is left behind either.
    for table in ("bookings", "booking_events", "booking_section_assignments", "checksheet_header",
                  "checksheet_value", "digital_signatures", "shed_visit_stages",
                  "shed_visit_events", "shed_visit_checksheet_packages",
                  "shed_visit_checksheet_requirements", "minor_inspection_section_signoff",
                  "minor_inspection_section_signoff_checksheet",
                  "shed_visit_checksheet_signoff_mode"):
        assert _count(db, table) == 0, f"{table} still has rows"


# ================================================================= manifest integrity ===========


def test_manifest_counts_equal_the_rows_actually_deleted(db, admin):
    """Not a tautology: record_counts comes from the SNAPSHOT pass and the deletes report their own
    rowcounts, which _verify_counts compares. Here the ledger's own item rows are counted as a third,
    independent tally."""
    event = _delete_visit(db, V_LEGACY_DSC, admin)

    from collections import Counter
    item_counts = Counter(
        entity for (entity,) in db.execute(text(
            "SELECT entity_type FROM admin_deletion_item WHERE deletion_event_id=:e"
        ), {"e": event.id})
    )
    assert dict(item_counts) == event.record_counts
    manifest_counts = {
        e["entity_type"]: e["row_count"] for e in event.manifest["entities"] if e["row_count"]
    }
    # The manifest may list one entity in several steps, so sum per entity before comparing.
    summed: dict[str, int] = {}
    for entry in event.manifest["entities"]:
        if entry["row_count"]:
            summed[entry["entity_type"]] = summed.get(entry["entity_type"], 0) + entry["row_count"]
    assert summed == event.record_counts
    assert manifest_counts  # the manifest is not empty


def test_the_manifest_hash_is_deterministic_and_matches_the_stored_manifest(db, admin):
    event = _delete_visit(db, V_ALL_STATUSES, admin)
    assert event.manifest_hash == svc.content_hash(event.manifest)
    # Recomputing from the stored JSON gives the same digest, so the hash describes what a reader
    # sees rather than some transient in-memory form.
    stored = db.execute(text("SELECT manifest, manifest_hash FROM admin_deletion_event WHERE id=:e"),
                        {"e": event.id}).one()
    assert svc.content_hash(stored.manifest) == stored.manifest_hash
    assert len(stored.manifest_hash) == 64


def test_every_item_content_hash_matches_its_snapshot(db, admin):
    event = _delete_visit(db, V_LEGACY_DSC, admin)
    rows = db.execute(text(
        "SELECT snapshot, content_hash FROM admin_deletion_item WHERE deletion_event_id=:e"
    ), {"e": event.id}).all()
    assert rows
    for row in rows:
        assert svc.content_hash(row.snapshot) == row.content_hash


def test_the_ledger_survives_its_subject(db, admin):
    """The whole point. After the visit is gone the record is still complete and still readable, with
    no join to anything deleted."""
    event = _delete_visit(db, V_SECTION_SIGNOFF, admin)
    event_id = event.id
    db.expire_all()

    row = db.execute(text("""
        SELECT loco_number, schedule_variant, actor_employee_id, reason, status, record_counts,
               manifest_hash,
               (SELECT count(*) FROM admin_deletion_item i WHERE i.deletion_event_id = e.id) AS items
        FROM admin_deletion_event e WHERE e.id = :e
    """), {"e": event_id}).one()
    assert row.loco_number == "30822"
    assert row.schedule_variant == "IC"
    assert row.actor_employee_id == "ADM1"
    assert row.status == "COMPLETED"
    assert row.items > 0
    assert _count(db, "shed_visits", "id=:v", v=V_SECTION_SIGNOFF) == 0


# ===================================================================== no dangling rows =========


def test_no_dangling_cross_system_references_remain(db, admin):
    """BL-DCMS references a visit by plain integer with NO foreign key, so PostgreSQL would happily
    leave checksheets pointing at a deleted visit. The production audit found zero such orphans today;
    a deletion must not create the first."""
    for visit_id in (V_LEGACY_DSC, V_SECTION_SIGNOFF, V_TB_TA, V_ALL_STATUSES):
        _delete_visit(db, visit_id, admin)

    assert _count(db, "checksheet_header ch",
                  "ch.shed_visit_id IS NOT NULL AND NOT EXISTS "
                  "(SELECT 1 FROM shed_visits sv WHERE sv.id = ch.shed_visit_id)") == 0
    assert _count(db, "minor_inspection_section_signoff s",
                  "NOT EXISTS (SELECT 1 FROM shed_visits sv WHERE sv.id = s.shed_visit_id)") == 0
    assert _count(db, "shed_visit_checksheet_signoff_mode m",
                  "NOT EXISTS (SELECT 1 FROM shed_visits sv WHERE sv.id = m.shed_visit_id)") == 0
    # And within each system.
    assert _count(db, "checksheet_value cv",
                  "NOT EXISTS (SELECT 1 FROM checksheet_header ch WHERE ch.id = cv.checksheet_id)") == 0
    assert _count(db, "digital_signatures ds",
                  "NOT EXISTS (SELECT 1 FROM checksheet_header ch WHERE ch.id = ds.checksheet_id)") == 0
    assert _count(db, "booking_section_assignments a",
                  "NOT EXISTS (SELECT 1 FROM bookings b WHERE b.id = a.booking_id)") == 0
    assert _count(db, "notifications n",
                  "n.checksheet_id IS NOT NULL AND NOT EXISTS "
                  "(SELECT 1 FROM checksheet_header ch WHERE ch.id = n.checksheet_id)") == 0


# ============================================================================ rollback ==========


def test_an_injected_failure_rolls_everything_back(db, admin, monkeypatch):
    """Nothing half-deleted, and no orphan ledger event. The failure is injected AFTER the snapshots
    and the deletes, which is the worst moment - everything is already gone inside the transaction."""
    before = {
        t: _count(db, t) for t in
        ("shed_visits", "bookings", "checksheet_header", "checksheet_value", "digital_signatures",
         "shed_visit_stages", "admin_deletion_event", "admin_deletion_item")
    }

    def boom(expected, actual):
        raise RuntimeError("injected failure after deletion, before commit")

    monkeypatch.setattr(svc, "_verify_counts", boom)
    visit = db.get(svc.ShedVisit, V_LEGACY_DSC)
    with pytest.raises(RuntimeError, match="injected failure"):
        svc.delete_shed_visit(
            db, V_LEGACY_DSC, actor=admin, reason="this deletion must not survive",
            confirmation=svc.required_confirmation(visit),
        )
    db.rollback()

    after = {t: _count(db, t) for t in before}
    assert after == before, "a failed deletion left changes behind"
    # Most importantly: the visit is intact, not partially stripped.
    assert _count(db, "checksheet_header", "shed_visit_id=:v", v=V_LEGACY_DSC) == 4
    assert _count(db, "digital_signatures ds JOIN checksheet_header ch ON ch.id=ds.checksheet_id",
                  "ch.shed_visit_id=:v", v=V_LEGACY_DSC) == 4
    assert _count(db, "admin_deletion_event") == 0


def test_a_count_mismatch_aborts_and_deletes_nothing(db, admin, monkeypatch):
    """The verification is real. Inflating the expected counts must abort rather than proceed."""
    original = svc._snapshot_steps

    def inflated(db_, event_id, steps):
        counts, manifest, plan = original(db_, event_id, steps)
        counts["checksheet_header"] = counts.get("checksheet_header", 0) + 1
        return counts, manifest, plan

    monkeypatch.setattr(svc, "_snapshot_steps", inflated)
    visit = db.get(svc.ShedVisit, V_LEGACY_DSC)
    with pytest.raises(HTTPException) as excinfo:
        svc.delete_shed_visit(db, V_LEGACY_DSC, actor=admin,
                              reason="count mismatch must abort the deletion",
                              confirmation=svc.required_confirmation(visit))
    db.rollback()
    assert excinfo.value.detail["code"] == "MANIFEST_MISMATCH"
    assert _count(db, "shed_visits", "id=:v", v=V_LEGACY_DSC) == 1
    assert _count(db, "checksheet_header", "shed_visit_id=:v", v=V_LEGACY_DSC) == 4


# ====================================================================== confirmation ============


def test_a_wrong_confirmation_refuses_and_deletes_nothing(db, admin):
    with pytest.raises(HTTPException) as excinfo:
        svc.delete_shed_visit(db, V_LEGACY_DSC, actor=admin,
                              reason="valid reason that is long enough",
                              confirmation="DELETE 99999 IA")
    db.rollback()
    assert excinfo.value.detail["code"] == "CONFIRMATION_MISMATCH"
    assert _count(db, "shed_visits", "id=:v", v=V_LEGACY_DSC) == 1
    assert _count(db, "admin_deletion_event") == 0


def test_the_required_confirmation_names_this_visit(db):
    visit = db.get(svc.ShedVisit, V_LEGACY_DSC)
    assert svc.required_confirmation(visit) == "DELETE 43553 IC"


@pytest.mark.parametrize("reason", ["", "   ", "too short", "my password=hunter2 oh dear"])
def test_a_missing_or_unsafe_reason_refuses(db, admin, reason):
    visit = db.get(svc.ShedVisit, V_LEGACY_DSC)
    with pytest.raises(HTTPException) as excinfo:
        svc.delete_shed_visit(db, V_LEGACY_DSC, actor=admin, reason=reason,
                              confirmation=svc.required_confirmation(visit))
    db.rollback()
    assert excinfo.value.detail["code"] in {"REASON_REQUIRED", "REASON_REJECTED"}
    assert _count(db, "shed_visits", "id=:v", v=V_LEGACY_DSC) == 1


# ======================================================================= double delete ==========


def test_deleting_the_same_visit_twice_is_refused_clearly(db, admin):
    _delete_visit(db, V_SIMPLE, admin)
    with pytest.raises(HTTPException) as excinfo:
        svc.delete_shed_visit(db, V_SIMPLE, actor=admin, reason="trying the same deletion again",
                              confirmation="DELETE 30635 IC")
    db.rollback()
    # The visit is gone, so the clearest truth is that it no longer exists.
    assert excinfo.value.detail["code"] == "VISIT_NOT_FOUND"
    assert excinfo.value.status_code == 404
    assert _count(db, "admin_deletion_event", "deletion_type='SHED_VISIT'") == 1


def test_a_retried_request_with_the_same_operation_id_does_not_delete_twice(db, admin):
    """Idempotency. A double-click or a network retry must return the original record, not create a
    second one."""
    import uuid as uuidlib

    operation_id = str(uuidlib.uuid4())
    visit = db.get(svc.ShedVisit, V_BOOKINGS_ONLY)
    confirmation = svc.required_confirmation(visit)
    first = svc.delete_shed_visit(db, V_BOOKINGS_ONLY, actor=admin,
                                  reason="first attempt at this deletion",
                                  confirmation=confirmation, operation_id=operation_id)
    db.commit()

    second = svc.delete_shed_visit(db, V_BOOKINGS_ONLY, actor=admin,
                                   reason="first attempt at this deletion",
                                   confirmation=confirmation, operation_id=operation_id)
    db.commit()
    assert second.id == first.id
    assert _count(db, "admin_deletion_event") == 1


def test_the_database_itself_refuses_a_second_completed_visit_deletion(db, admin):
    """Belt and braces: even if the service's own check were bypassed,
    uq_admin_deletion_visit_completed makes two COMPLETED deletions of one visit impossible."""
    from sqlalchemy.exc import IntegrityError

    _delete_visit(db, V_SIMPLE, admin)
    # IntegrityError specifically, and from the unique index - not from chk_admin_deletion_status,
    # which an invalid status string would trip first and would make this test pass for the wrong
    # reason. (It did, briefly: the literal had a stray leading space.)
    with pytest.raises(IntegrityError, match="uq_admin_deletion_visit_completed"):
        db.execute(text("""
            INSERT INTO admin_deletion_event (operation_id, deletion_type, target_id, shed_visit_id,
                loco_number, actor_user_id, actor_employee_id, actor_name, reason, status,
                completed_at, manifest_hash)
            VALUES (gen_random_uuid(),'SHED_VISIT',:v,:v,'30635',1,'ADM1','Admin',
                    'a second completed record','COMPLETED',now(),'h')
        """).bindparams(v=V_SIMPLE))
        db.flush()
    db.rollback()


# ========================================================================= filesystem ==========


def test_files_are_not_touched_before_the_database_commits(db, admin, rebuilt):
    """THE ordering rule. A crash between commit and destruction must leave files on disk with a
    durable record of the intent - never a destroyed file with no record it existed."""
    paths = [
        Path(p) for (p,) in db.execute(text(
            "SELECT pdf_path FROM checksheet_header WHERE shed_visit_id=:v AND pdf_path IS NOT NULL"
        ), {"v": V_LEGACY_DSC})
    ]
    assert paths and all(p.is_file() for p in paths)

    event = _delete_visit(db, V_LEGACY_DSC, admin)

    # The database work has committed and the plan is recorded, with hashes computed while the files
    # still existed - but every file is still on disk, because destroy_planned_files has not run.
    assert event.status == "COMPLETED"
    assert len(event.file_plan) == len(paths)
    assert all(entry["sha256"] and len(entry["sha256"]) == 64 for entry in event.file_plan)
    assert event.file_result is None
    assert event.files_completed_at is None
    assert all(p.is_file() for p in paths), "files were destroyed before the commit"

    svc.destroy_planned_files(db, event)

    assert not any(p.exists() for p in paths)
    assert event.files_completed_at is not None
    assert {r["result"] for r in event.file_result} == {"DESTROYED"}


def test_the_hash_is_recorded_before_the_file_is_destroyed(db, admin):
    """Physical destruction is irreversible, so the hash must be committed first - destruction can
    never outrun the record of it."""
    import hashlib

    path, = db.execute(text(
        "SELECT pdf_path FROM checksheet_header WHERE shed_visit_id=:v AND pdf_path IS NOT NULL LIMIT 1"
    ), {"v": V_TWIN_OPEN}).one()
    expected = hashlib.sha256(Path(path).read_bytes()).hexdigest()

    event = _delete_visit(db, V_TWIN_OPEN, admin)
    recorded = {entry["path"]: entry["sha256"] for entry in event.file_plan}
    assert recorded[path] == expected

    svc.destroy_planned_files(db, event)
    assert not Path(path).exists()
    # The hash survives the file.
    stored = db.execute(text("SELECT file_plan FROM admin_deletion_event WHERE id=:e"),
                        {"e": event.id}).scalar_one()
    assert {e["path"]: e["sha256"] for e in stored}[path] == expected


def test_a_path_outside_the_storage_root_is_refused_not_followed(db, admin, rebuilt, tmp_path):
    """Path confinement. A corrupted or crafted pdf_path must never cause a deletion outside the
    configured storage directory."""
    outsider = tmp_path / "not-ours.pdf"
    outsider.write_text("a file that does not belong to this visit")
    db.execute(text("UPDATE checksheet_header SET pdf_path=:p WHERE shed_visit_id=:v AND pdf_path IS NOT NULL"),
               {"p": str(outsider), "v": V_TWIN_OPEN})
    db.commit()

    event = _delete_visit(db, V_TWIN_OPEN, admin)
    svc.destroy_planned_files(db, event)

    assert outsider.is_file(), "a file outside the storage root was deleted"
    assert any(r["result"] == "REFUSED_OUTSIDE_STORAGE_ROOT" for r in event.file_result)


def test_a_symlink_escaping_the_storage_root_is_refused(db, admin, rebuilt, tmp_path):
    """resolve(strict=True) runs BEFORE the containment test, so a symlink sitting inside the storage
    directory but pointing outside it is rejected. Checking the unresolved path would follow it."""
    target = tmp_path / "outside-target.pdf"
    target.write_text("must survive")
    link = rebuilt / "escape.pdf"
    link.symlink_to(target)

    db.execute(text("UPDATE checksheet_header SET pdf_path=:p WHERE shed_visit_id=:v AND pdf_path IS NOT NULL"),
               {"p": str(link), "v": V_TWIN_OPEN})
    db.commit()

    event = _delete_visit(db, V_TWIN_OPEN, admin)
    svc.destroy_planned_files(db, event)

    assert target.is_file(), "a symlink was followed out of the storage root"
    assert any(r["result"] == "REFUSED_OUTSIDE_STORAGE_ROOT" for r in event.file_result)
    link.unlink(missing_ok=True)


def test_a_file_shared_with_a_surviving_row_is_never_destroyed(db, admin, rebuilt):
    """The runtime guard. The production audit found 892 paths across 892 rows with no sharing at all,
    but a regeneration bug could introduce it later, and destroying a shared file would take an
    unrelated visit's evidence with it."""
    shared = rebuilt / "shared_between_visits.pdf"
    shared.write_text("referenced by two different visits")
    db.execute(text("UPDATE checksheet_header SET pdf_path=:p WHERE shed_visit_id=:a AND pdf_path IS NOT NULL"),
               {"p": str(shared), "a": V_TWIN_OPEN})
    db.execute(text("UPDATE checksheet_header SET pdf_path=:p WHERE shed_visit_id=:b AND pdf_path IS NOT NULL"),
               {"p": str(shared), "b": V_TWIN_CLOSED})
    db.commit()

    event = _delete_visit(db, V_TWIN_OPEN, admin)
    svc.destroy_planned_files(db, event)

    assert shared.is_file(), "a file still referenced by a surviving row was destroyed"
    assert any(r["result"] == "SKIPPED_SHARED" for r in event.file_result)


def test_an_already_absent_file_is_recorded_rather_than_failing(db, admin, rebuilt):
    path, = db.execute(text(
        "SELECT pdf_path FROM checksheet_header WHERE shed_visit_id=:v AND pdf_path IS NOT NULL LIMIT 1"
    ), {"v": V_TWIN_OPEN}).one()
    Path(path).unlink()

    event = _delete_visit(db, V_TWIN_OPEN, admin)
    svc.destroy_planned_files(db, event)
    assert any(r["result"] == "ALREADY_ABSENT" for r in event.file_result)


def test_the_file_result_can_be_recorded_only_once(db, admin):
    event = _delete_visit(db, V_TWIN_OPEN, admin)
    svc.destroy_planned_files(db, event)
    first = list(event.file_result)
    # Idempotent: a retry after a crash must not double-report, and the files are already gone.
    svc.destroy_planned_files(db, event)
    assert event.file_result == first


def test_with_no_storage_root_configured_nothing_is_deleted_from_disk(db, admin, rebuilt, monkeypatch):
    """Fail closed. A deployment that has not been told where its storage lives must not guess."""
    monkeypatch.delenv("ADMIN_DELETION_STORAGE_ROOTS", raising=False)
    paths = [Path(p) for (p,) in db.execute(text(
        "SELECT pdf_path FROM checksheet_header WHERE shed_visit_id=:v AND pdf_path IS NOT NULL"
    ), {"v": V_TWIN_OPEN})]

    event = _delete_visit(db, V_TWIN_OPEN, admin)
    svc.destroy_planned_files(db, event)

    assert all(p.is_file() for p in paths)
    assert all(r["result"] == "SKIPPED_NO_STORAGE_ROOT_CONFIGURED" for r in event.file_result)


# ===================================================================== no secrets kept ==========


def test_no_password_or_credential_reaches_the_ledger(db, admin):
    """Checked against the ENTIRE ledger as stored text, not field by field - a credential smuggled
    into any snapshot, manifest or reason would show up here."""
    for visit_id in (V_LEGACY_DSC, V_SECTION_SIGNOFF, V_ALL_STATUSES):
        _delete_visit(db, visit_id, admin)

    blob = "\n".join(
        str(row) for row in db.execute(text("""
            SELECT e.*, i.snapshot, i.content_hash FROM admin_deletion_event e
            LEFT JOIN admin_deletion_item i ON i.deletion_event_id = e.id
        """)).all()
    ).lower()
    for forbidden in ("password", "passwd", "secret", "bearer ", "jwt", "token=", "private_key",
                      "otp"):
        assert forbidden not in blob, f"the ledger contains {forbidden!r}"
    # The users table's hashes are never copied either.
    assert "$2b$" not in blob and "$2a$" not in blob


def test_the_ledger_has_no_column_that_could_hold_a_credential(db):
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(db.bind)
    for table in ("admin_deletion_event", "admin_deletion_item"):
        names = {c["name"].lower() for c in insp.get_columns(table)}
        assert not (names & svc._FORBIDDEN_SNAPSHOT_KEYS)
