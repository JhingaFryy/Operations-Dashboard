"""Schema contract for shed_visit_events.event_type.

SCHEDULE_STARTED once passed every SQLite test and failed in production with CheckViolation,
because nothing tied the event types the code emits to the PostgreSQL CHECK. These tests close
that gap from three directions without pretending SQLite is PostgreSQL:

  1. the model's SHED_VISIT_EVENT_TYPES equals the value list in the latest migration that
     (re)defines chk_shed_visit_event_type - the file actually applied to PostgreSQL;
  2. every event_type the application writes (found by static analysis of app/) is in that set;
  3. SQLite's genuine CHECK enforcement of the mirrored constraint, plus the rollback path when a
     commit is rejected.
"""

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models import SHED_VISIT_EVENT_TYPES
from app.services import schedule_lifecycle_service
from tests.conftest import make_movement_supervisor_headers, make_shed_visit, make_stage

BACKEND = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND / "app"
MIGRATIONS_DIR = BACKEND / "migrations"

ARRIVAL = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
STARTED = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _latest_migration_event_types() -> tuple[str, set[str]]:
    defining = [
        p for p in sorted(MIGRATIONS_DIR.glob("*.sql"))
        if re.search(r"ADD\s+CONSTRAINT\s+chk_shed_visit_event_type", p.read_text(), re.I)
    ]
    assert defining, "no migration defines chk_shed_visit_event_type"
    sql = defining[-1].read_text()
    sql = re.sub(r"--[^\n]*", "", sql)  # ignore comments
    match = re.search(
        r"ADD\s+CONSTRAINT\s+chk_shed_visit_event_type\s+CHECK\s*\(\s*event_type\s+IN\s*\(([^)]*)\)",
        sql, re.I,
    )
    assert match, f"could not parse the CHECK in {defining[-1].name}"
    return defining[-1].name, set(re.findall(r"'([A-Z_]+)'", match.group(1)))


def _emitted_event_types() -> set[str]:
    """Every event_type passed to ShedVisitEvent(...) anywhere under app/. Resolves string
    literals, module-level constants, and a parameter of the enclosing helper function (resolved
    through every call of that helper in the same module). Anything unresolvable fails loudly."""
    found: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text())
        constants = {
            t.id: n.value.value
            for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            for t in n.targets if isinstance(t, ast.Name) and isinstance(n.value.value, str)
        }

        def resolve(node, where):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return {node.value}
            if isinstance(node, ast.Name) and node.id in constants:
                return {constants[node.id]}
            raise AssertionError(f"unresolvable event_type {ast.dump(node)} in {where}")

        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
                if not (isinstance(call.func, ast.Name) and call.func.id == "ShedVisitEvent"):
                    continue
                arg = next(k.value for k in call.keywords if k.arg == "event_type")
                params = [a.arg for a in func.args.args]
                if isinstance(arg, ast.Name) and arg.id in params:
                    index = params.index(arg.id)
                    callers = [
                        c for c in ast.walk(tree)
                        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == func.name
                    ]
                    assert callers, f"{func.name} in {path} is never called"
                    for c in callers:
                        passed = c.args[index] if index < len(c.args) else next(
                            k.value for k in c.keywords if k.arg == arg.id)
                        found |= resolve(passed, f"{path}:{c.lineno}")
                else:
                    found |= resolve(arg, f"{path}:{call.lineno}")
    return found


# ------------------------------------------------------------------------ contract --

def test_authoritative_set_includes_schedule_lifecycle_events():
    assert "SCHEDULE_STARTED" in SHED_VISIT_EVENT_TYPES
    assert "SCHEDULE_COMPLETED" in SHED_VISIT_EVENT_TYPES
    assert len(set(SHED_VISIT_EVENT_TYPES)) == len(SHED_VISIT_EVENT_TYPES)


def test_model_set_matches_latest_postgres_migration():
    name, migration_set = _latest_migration_event_types()
    assert migration_set == set(SHED_VISIT_EVENT_TYPES), name


def test_legacy_event_types_are_preserved():
    assert {"SHED_IN", "MARK_READY", "SHED_OUT", "SCHEDULE_CHANGED", "MANUAL_CORRECTION"} <= set(
        SHED_VISIT_EVENT_TYPES
    )


def test_every_emitted_event_type_is_allowed():
    emitted = _emitted_event_types()
    # Guards the scanner itself: if it silently found nothing, the subset check would be vacuous.
    assert emitted == {
        "SHED_IN", "SHED_OUT", "SCHEDULE_STARTED", "SCHEDULE_COMPLETED",
        # migration 012
        "MARK_READY", "TEST_BEFORE_SKIPPED",
    }
    assert emitted <= set(SHED_VISIT_EVENT_TYPES)


def test_model_check_constraint_uses_the_authoritative_set():
    constraint = next(
        c for c in models.ShedVisitEvent.__table__.constraints
        if getattr(c, "name", None) == "chk_shed_visit_event_type"
    )
    assert set(re.findall(r"'([A-Z_]+)'", str(constraint.sqltext))) == set(SHED_VISIT_EVENT_TYPES)


# ----------------------------------------------------- enforcement + rollback path --

def _event(visit_id, event_type):
    now = datetime.now(timezone.utc)
    return models.ShedVisitEvent(shed_visit_id=visit_id, event_type=event_type, event_time=now,
                                 source="DASHBOARD", created_at=now)


@pytest.mark.parametrize("event_type", SHED_VISIT_EVENT_TYPES)
def test_allowed_event_types_insert(db_session, event_type):
    visit = make_shed_visit(db_session, 1, "39018", arrival_at=ARRIVAL)
    db_session.add(_event(visit.id, event_type))
    db_session.commit()


def test_unknown_event_type_is_rejected(db_session):
    visit = make_shed_visit(db_session, 1, "39018", arrival_at=ARRIVAL)
    db_session.add(_event(visit.id, "NOT_A_REAL_EVENT"))
    with pytest.raises(IntegrityError, match="chk_shed_visit_event_type|CHECK constraint failed"):
        db_session.commit()
    db_session.rollback()


def test_rejected_event_rolls_back_the_visit_and_leaves_the_session_usable(
    client_allow_500, db_session, monkeypatch
):
    """A schema rejection surfaces as a 500 (never a fake success), the visit update is rolled
    back, and the same session serves the next request normally."""
    headers = make_movement_supervisor_headers(db_session)
    visit = make_shed_visit(db_session, 900, "39018", arrival_at=ARRIVAL)
    # Migration 012: a MINOR schedule only starts once Test Before is satisfied.
    make_stage(db_session, 9001, visit.id, "TEST_BEFORE", 1, status="COMPLETED",
               started_at=ARRIVAL, completed_at=ARRIVAL)
    url = f"/api/shed-visits/{visit.id}/start-schedule"

    monkeypatch.setattr(schedule_lifecycle_service, "EVENT_SCHEDULE_STARTED", "NOT_A_REAL_EVENT")
    resp = client_allow_500.post(url, json={"started_at": STARTED.isoformat()}, headers=headers)
    assert resp.status_code == 500

    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).schedule_started_at is None
    assert db_session.query(models.ShedVisitEvent).count() == 0

    monkeypatch.undo()
    resp = client_allow_500.post(url, json={"started_at": STARTED.isoformat()}, headers=headers)
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert db_session.get(models.ShedVisit, visit.id).schedule_started_at is not None
    assert db_session.query(models.ShedVisitEvent).filter_by(event_type="SCHEDULE_STARTED").count() == 1
