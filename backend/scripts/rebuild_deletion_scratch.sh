#!/usr/bin/env bash
# Rebuild the production-faithful deletion scratch database from scratch, every time.
#
# The schema comes verbatim from `pg_dump --schema-only` of rdcms - 37 tables, 77 foreign keys with
# their real ON DELETE actions, 71 CHECK constraints, both triggers - so the destructive proof runs
# against the same rules production enforces. Nothing here touches production.
set -euo pipefail

SCRATCH="${SCRATCH:?set SCRATCH to the scratch cluster directory}"
PORT="${PORT:-54401}"
DUMP="${DUMP:-/opt/operations-dashboard/backend/rdcms_schema_only.sql}"
BIN=/usr/lib/postgresql/18/bin

"$BIN/psql" -h "$SCRATCH" -p "$PORT" -U postgres -q -c "DROP DATABASE IF EXISTS rdcms_prod WITH (FORCE);"
"$BIN/psql" -h "$SCRATCH" -p "$PORT" -U postgres -q -c "CREATE DATABASE rdcms_prod;"
"$BIN/psql" -h "$SCRATCH" -p "$PORT" -U postgres -d rdcms_prod -q -v ON_ERROR_STOP=1 -f "$DUMP"

# Migrations written but not yet applied in production, in order. The scratch database must carry the
# schema the code expects, or a proof would run against a shape that no longer exists anywhere.
for MIGRATION in \
    /home/elsbl/Checksheet/backend/migrations/078_signoff_mode_admin_deletion_exemption.sql \
    /opt/operations-dashboard/backend/migrations/013_admin_deletion_ledger.sql \
    /opt/operations-dashboard/backend/migrations/014_requirement_under_amc.sql ; do
  if [ -f "$MIGRATION" ]; then
    "$BIN/psql" -h "$SCRATCH" -p "$PORT" -U postgres -d rdcms_prod -q -v ON_ERROR_STOP=1 \
      -f "$MIGRATION" >/dev/null
  fi
done

cd /opt/operations-dashboard/backend
PYTHONPATH=. venv/bin/python scripts/build_deletion_scratch_fixtures.py \
  "postgresql://postgres@/rdcms_prod?host=$SCRATCH&port=$PORT"
