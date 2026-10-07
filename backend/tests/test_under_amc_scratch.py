"""Under AMC, proven against a production-faithful PostgreSQL schema.

The SQLite suite (test_under_amc.py) proves the service logic. What only PostgreSQL can prove is that
the DATABASE itself refuses the contradictory state - chk_requirement_amc_not_deactivated and
chk_requirement_amc_attributed are real constraints from migration 014, and SQLite's CHECK support
would not exercise the production DDL that was actually written.

That distinction has already mattered twice in this codebase: migration 077 failed in production
because its proof schema had no constraints, and the deletion ledger's operation_id was declared
String(36) against a UUID column, which only PostgreSQL rejected.

    SCRATCH=<cluster dir> scripts/rebuild_deletion_scratch.sh     # loads pg_dump + 078 + 013 + 014
    ADMIN_DELETION_SCRATCH_DSN=... ADMIN_DELETION_SCRATCH_DIR=... pytest tests/test_under_amc_scratch.py
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import models
from app.services.checksheet_requirement_completion_service import evaluate_requirement_completion
from app.services.pending_requirement_service import (
    apply_requirement_override,
    set_requirement_under_amc,
)

DSN = os.environ.get("ADMIN_DELETION_SCRATCH_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ADMIN_DELETION_SCRATCH_DSN to run the production-faithful AMC proof"
)

# From scripts/build_deletion_scratch_fixtures.py: a MINOR visit whose package carries three
# SCHEDULE_INSPECTION requirements.
V_WITH_PACKAGE = 9003


@pytest.fixture()
def engine():
    return create_engine(DSN, future=True)


@pytest.fixture()
def db(engine):
    scratch = os.environ["ADMIN_DELETION_SCRATCH_DIR"]
    engine.dispose()
    subprocess.run(["scripts/rebuild_deletion_scratch.sh"], check=True, capture_output=True,
                   env={**os.environ, "SCRATCH": scratch})
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def admin(db):
    return db.query(models.User).filter(models.User.employee_id == "ADM1").one().id


@pytest.fixture()
def requirement(db):
    """One real requirement from the seeded package, made plainly REQUIRED to start from."""
    row = db.execute(text("""
        SELECT r.id FROM shed_visit_checksheet_requirements r
        JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
        WHERE p.shed_visit_id = :v AND r.workflow_stage_type = 'SCHEDULE_INSPECTION'
        ORDER BY r.id LIMIT 1
    """), {"v": V_WITH_PACKAGE}).scalar_one()
    return row


def _row(db, rid):
    return db.get(models.ShedVisitChecksheetRequirement, rid)


# ============================================================ the database refuses it ===========


def test_migration_014_is_present_with_its_constraints(db):
    """Guards the rest: if the constraints were absent, the refusals below would pass vacuously."""
    names = {
        r[0] for r in db.execute(text("""
            SELECT conname FROM pg_constraint
            WHERE contype = 'c' AND conrelid = 'shed_visit_checksheet_requirements'::regclass
        """))
    }
    for required in ("chk_requirement_amc_not_deactivated", "chk_requirement_amc_marked_pair",
                     "chk_requirement_amc_cleared_pair", "chk_requirement_amc_attributed"):
        assert required in names, f"{required} missing - migration 014 not applied"


def test_the_database_refuses_amc_on_a_deactivated_row(db, requirement):
    """Not only the service. A direct UPDATE - a script, a console, a future bug - cannot create the
    contradiction either."""
    with pytest.raises(IntegrityError, match="chk_requirement_amc_not_deactivated"):
        db.execute(text("""
            UPDATE shed_visit_checksheet_requirements
               SET is_active = false, under_amc = true, amc_marked_by = 1, amc_marked_at = now()
             WHERE id = :r
        """), {"r": requirement})
        db.flush()
    db.rollback()


def test_the_database_refuses_an_unattributed_amc_row(db, requirement):
    """An AMC row must say who excused it, or a requirement could drop out of a completion gate with
    nothing recording who authorised that."""
    with pytest.raises(IntegrityError, match="chk_requirement_amc_attributed"):
        db.execute(text("UPDATE shed_visit_checksheet_requirements SET under_amc = true WHERE id = :r"),
                   {"r": requirement})
        db.flush()
    db.rollback()


def test_the_database_refuses_a_half_written_stamp(db, requirement):
    with pytest.raises(IntegrityError, match="chk_requirement_amc_marked_pair"):
        db.execute(text("""
            UPDATE shed_visit_checksheet_requirements SET amc_marked_by = 1 WHERE id = :r
        """), {"r": requirement})
        db.flush()
    db.rollback()


# ====================================================== the service, on the real schema =========


def test_the_full_cycle_against_the_real_schema(db, requirement, admin):
    out = set_requirement_under_amc(db, requirement, under_amc=True, actor_id=admin)
    assert out.under_amc is True and out.effective_state == "UNDER_AMC"
    stored = _row(db, requirement)
    assert stored.amc_marked_by == admin and stored.amc_marked_at is not None
    # The frozen identity is untouched - this is what lets AMC be lifted and the work return.
    assert stored.template_id and stored.section_id_snapshot and stored.is_required is True

    cleared = set_requirement_under_amc(db, requirement, under_amc=False, actor_id=admin)
    assert cleared.effective_state == "REQUIRED"
    assert _row(db, requirement).amc_marked_by == admin  # history survives the clearing


def test_deactivating_an_amc_row_clears_it_rather_than_violating_the_constraint(db, requirement, admin):
    """The service and the constraint agree: this is the path that proves the clearing is not merely
    tidy but required for the write to succeed at all."""
    set_requirement_under_amc(db, requirement, under_amc=True, actor_id=admin)
    out = apply_requirement_override(db, requirement, is_active=False, actor_id=admin)
    assert out.is_active is False and out.under_amc is False
    assert _row(db, requirement).amc_cleared_at is not None


def test_the_partial_index_only_covers_amc_rows(db, requirement, admin):
    assert db.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_requirement_under_amc'"
    )).scalar_one().lower().endswith("where under_amc")


# ================================================ the completion gate, on real data =============


class _BlClient:
    """Returns APPROVED checksheets matching the given requirements' FULL identity.

    The identity is (template_id, stage, section_id, equipment_id, maintenance_type,
    minor_inspection_equipment_id) - see _identity in the completion service - so a stub that sent
    section_id: None would satisfy nothing and the test would silently prove the opposite of what it
    claims. The rows are therefore echoed from the real requirement snapshots.
    """

    def __init__(self, approved_rows):
        self._rows = list(approved_rows)

    def get_visit_checksheets(self, visit_id: int):
        return {
            "shed_visit_id": visit_id,
            "items": [
                {"checksheet_id": 9000 + r.template_id, "template_id": r.template_id,
                 "status": "APPROVED", "workflow_stage_type": r.workflow_stage_type,
                 "schedule_family": "MINOR", "section_id": r.section_id_snapshot,
                 "equipment_id": r.equipment_id_snapshot,
                 "maintenance_type": r.maintenance_type_snapshot,
                 "minor_inspection_equipment_id": r.minor_inspection_equipment_id}
                for r in self._rows
            ],
        }


def test_an_amc_requirement_stops_blocking_and_the_rest_still_do(db, admin):
    """On the real schema, with the real package: three Schedule Inspection requirements, one put
    Under AMC. The other two must still block until satisfied."""
    rows = (
        db.query(models.ShedVisitChecksheetRequirement)
        .join(models.ShedVisitChecksheetPackage,
              models.ShedVisitChecksheetPackage.id == models.ShedVisitChecksheetRequirement.package_id)
        .filter(models.ShedVisitChecksheetPackage.shed_visit_id == V_WITH_PACKAGE,
                models.ShedVisitChecksheetRequirement.workflow_stage_type == "SCHEDULE_INSPECTION")
        .order_by(models.ShedVisitChecksheetRequirement.id)
        .all()
    )
    assert len(rows) >= 3, "the seeded package should carry at least three inspection requirements"

    visit = db.get(models.ShedVisit, V_WITH_PACKAGE)
    before = evaluate_requirement_completion(db, _BlClient([]), visit)
    assert before.under_amc == 0
    blocking_before = before.blocking

    amc_row = rows[0]
    set_requirement_under_amc(db, amc_row.id, under_amc=True, actor_id=admin)

    after = evaluate_requirement_completion(db, _BlClient([]), visit)
    assert after.under_amc == 1
    assert after.blocking == blocking_before - 1
    assert after.ready is False  # the others still block

    # Satisfy every remaining workshop requirement; now the gate passes with the AMC row excused and
    # never counted as done.
    satisfied = evaluate_requirement_completion(db, _BlClient(rows[1:]), visit)
    assert satisfied.under_amc == 1
    assert satisfied.satisfied == len(rows) - 1
    assert satisfied.ready is True


def test_an_existing_checksheet_is_not_touched_by_the_toggle(db, admin):
    """Data safety, on the real schema: marking AMC must not delete or alter a checksheet, its values,
    its PDF path or its signature."""
    rid, template_id = db.execute(text("""
        SELECT r.id, r.template_id FROM shed_visit_checksheet_requirements r
        JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
        WHERE p.shed_visit_id = :v AND r.workflow_stage_type = 'SCHEDULE_INSPECTION'
        ORDER BY r.id LIMIT 1
    """), {"v": V_WITH_PACKAGE}).one()

    before = db.execute(text("""
        SELECT count(*) AS headers,
               (SELECT count(*) FROM checksheet_value cv JOIN checksheet_header ch ON ch.id = cv.checksheet_id
                 WHERE ch.shed_visit_id = :v) AS values,
               (SELECT count(*) FROM digital_signatures ds JOIN checksheet_header ch ON ch.id = ds.checksheet_id
                 WHERE ch.shed_visit_id = :v) AS signatures,
               (SELECT count(*) FROM checksheet_header WHERE shed_visit_id = :v AND pdf_path IS NOT NULL) AS pdfs
          FROM checksheet_header WHERE shed_visit_id = :v
    """), {"v": V_WITH_PACKAGE}).one()
    assert before.headers > 0 and before.signatures > 0, "fixture should have signed checksheets"

    set_requirement_under_amc(db, rid, under_amc=True, actor_id=admin)
    set_requirement_under_amc(db, rid, under_amc=False, actor_id=admin)

    after = db.execute(text("""
        SELECT count(*) AS headers,
               (SELECT count(*) FROM checksheet_value cv JOIN checksheet_header ch ON ch.id = cv.checksheet_id
                 WHERE ch.shed_visit_id = :v) AS values,
               (SELECT count(*) FROM digital_signatures ds JOIN checksheet_header ch ON ch.id = ds.checksheet_id
                 WHERE ch.shed_visit_id = :v) AS signatures,
               (SELECT count(*) FROM checksheet_header WHERE shed_visit_id = :v AND pdf_path IS NOT NULL) AS pdfs
          FROM checksheet_header WHERE shed_visit_id = :v
    """), {"v": V_WITH_PACKAGE}).one()
    assert tuple(after) == tuple(before), "the AMC toggle changed checksheet data"


def test_existing_production_overrides_are_undisturbed(db):
    """Migration 014 adds a column; it must not reinterpret the 95 Optional rows production already
    has. Here: every pre-existing flag survives the migration unchanged, and nothing is AMC."""
    counts = db.execute(text("""
        SELECT count(*) FILTER (WHERE under_amc) AS amc,
               count(*) FILTER (WHERE NOT is_required) AS optional,
               count(*) FILTER (WHERE NOT is_active) AS deactivated
          FROM shed_visit_checksheet_requirements
    """)).one()
    assert counts.amc == 0
