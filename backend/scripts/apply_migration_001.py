"""
Applies migrations/001_shed_visit_checksheet_work_package.sql against the live rdcms database.

Run once: venv/bin/python scripts/apply_migration_001.py

Creates the two new Operations Dashboard-owned tables (shed_visit_checksheet_packages,
shed_visit_checksheet_requirements) - see the migration file's own header comment for the full
rationale. Purely additive: touches zero existing rows in any existing table, no foreign key
into or out of any BL-DCMS-owned table.

Not run automatically by anything - app/db/session.py's engine is never used with
Base.metadata.create_all() against this database (see that module's own docstring), and this
script must be invoked deliberately, exactly once.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import engine

MIGRATION_FILE = Path(__file__).resolve().parent.parent / "migrations" / "001_shed_visit_checksheet_work_package.sql"


def main():
    sql = MIGRATION_FILE.read_text()

    lines = sql.splitlines()
    stripped = "\n".join(line for line in lines if not line.strip().startswith("--"))
    statements = [s.strip() for s in stripped.split(";") if s.strip()]

    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)

    print(f"Applied {MIGRATION_FILE.name} ({len(statements)} statement(s)).")


if __name__ == "__main__":
    main()
