"""
Applies migrations/002_booking_lifecycle_section_columns.sql against the live rdcms database.

Run once: venv/bin/python scripts/apply_migration_002.py

Adds started_by_section_id and attended_by_section_id to the existing `bookings` table (Common
Booking Pool reform) - see the migration file's own header comment for the full rationale. Purely
additive: both new columns are nullable, no existing row is touched, booking_section_assignments
is left completely alone.

Not run automatically by anything - app/db/session.py's engine is never used with
Base.metadata.create_all() against this database (see that module's own docstring), and this
script must be invoked deliberately, exactly once.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import engine

MIGRATION_FILE = Path(__file__).resolve().parent.parent / "migrations" / "002_booking_lifecycle_section_columns.sql"


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
