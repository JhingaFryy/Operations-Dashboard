"""
Applies migrations/003_decouple_booking_status_from_legacy_timestamps.sql.

*** THIS SCRIPT HAS NOT BEEN RUN AGAINST PRODUCTION. A human/DBA must review the migration and
*** run it deliberately. It is not invoked by anything automatically.

Why it does NOT reuse app/db/session.py's engine (unlike apply_migration_001/002.py):

    `bookings` and `booking_section_assignments` are owned by the `postgres` role in the live
    rdcms database, and the application role `elsbladmin` is neither a superuser nor a member of
    `postgres`. `ALTER TABLE ... DROP CONSTRAINT` requires table ownership, so running this
    migration on the ordinary application connection would fail with
    "must be owner of table bookings" partway through. The GRANT statements likewise require the
    sequence owner.

So this script requires an explicit privileged DSN, and refuses to run without it:

    MIGRATION_DATABASE_URL='postgresql+psycopg://postgres:***@localhost:5432/rdcms' \
        venv/bin/python scripts/apply_migration_003.py

Equivalent and arguably preferable, since it involves no extra credential handling at all:

    sudo -u postgres psql -d rdcms -v ON_ERROR_STOP=1 \
        -f migrations/003_decouple_booking_status_from_legacy_timestamps.sql

The migration is transactional here (engine.begin()) and every statement is idempotent /
re-runnable, so a failed run leaves the schema unchanged.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine

MIGRATION_FILE = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "003_decouple_booking_status_from_legacy_timestamps.sql"
)


def main():
    dsn = os.environ.get("MIGRATION_DATABASE_URL")
    if not dsn:
        sys.exit(
            "MIGRATION_DATABASE_URL is not set.\n"
            "This migration issues ALTER TABLE against tables owned by the `postgres` role; the\n"
            "application role `elsbladmin` cannot execute it. Set MIGRATION_DATABASE_URL to a\n"
            "connection for a role that owns bookings/booking_section_assignments, or run the\n"
            ".sql file directly with `sudo -u postgres psql -d rdcms -v ON_ERROR_STOP=1 -f ...`.\n"
            "Deliberately NOT falling back to app/db/session.py's application engine."
        )

    sql = MIGRATION_FILE.read_text()
    stripped = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    statements = [s.strip() for s in stripped.split(";") if s.strip()]

    # The naive ";"-splitter inherited from apply_migration_001/002.py would
    # shred a statement containing a semicolon inside a string literal (the
    # COMMENT ON COLUMN bodies below are deliberately written without any, for
    # exactly this reason). Fail loudly rather than executing a fragment.
    malformed = [s for s in statements if not s.upper().startswith(("ALTER", "GRANT", "COMMENT"))]
    if malformed:
        sys.exit(
            "Refusing to run: the migration did not split into whole statements "
            f"({len(malformed)} fragment(s), first: {malformed[0][:80]!r}). "
            "Run the .sql file with psql instead."
        )

    engine = create_engine(dsn)
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)

    print(f"Applied {MIGRATION_FILE.name} ({len(statements)} statement(s)).")


if __name__ == "__main__":
    main()
