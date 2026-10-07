-- Decouple parent bookings.status from the legacy parent-level lifecycle timestamps.
--
-- ============================ ROOT CAUSE =====================================================
-- POST /api/section-assignments/{id}/start fails with:
--     new row for relation "bookings" violates check constraint "chk_booking_started"
--
-- The live `bookings` table still carries three CHECK constraints written for a
-- pre-assignment-based architecture in which the PARENT booking row owned the section-execution
-- lifecycle and therefore always had a single authoritative started_at/attended_at/reopened_at:
--
--     chk_booking_started   CHECK (status <> 'IN_PROGRESS' OR started_at  IS NOT NULL)
--     chk_booking_attended  CHECK (status <> 'ATTENDED'    OR attended_at IS NOT NULL)
--     chk_booking_reopened  CHECK (status <> 'REOPENED'    OR reopened_at IS NOT NULL)
--
-- That architecture no longer exists. Under the current (authoritative) model:
--
--   * booking_section_assignments rows own the real per-section execution lifecycle
--     (OPEN -> IN_PROGRESS -> ATTENDED -> REOPENED -> IN_PROGRESS), and they carry their own
--     started_at/started_by/attended_at/attended_by columns, already guarded by their own
--     chk_booking_section_started / chk_booking_section_attended constraints.
--   * bookings.status is a DERIVED aggregate, computed on every assignment mutation by
--     app/services/section_dashboard_service.recompute_booking_status():
--         any REOPENED -> REOPENED; else all ATTENDED -> ATTENDED;
--         else any IN_PROGRESS -> IN_PROGRESS; else OPEN.
--     It is never independently set, and the derivation deliberately writes ONLY `status`.
--
-- So the moment the first assignment of a booking starts, recompute_booking_status() sets
-- bookings.status = 'IN_PROGRESS' while bookings.started_at is (correctly) still NULL, and
-- chk_booking_started rejects the UPDATE. The same failure is latent for chk_booking_attended
-- (first booking whose assignments all reach ATTENDED) and chk_booking_reopened (first Admin
-- reopen) - i.e. every single assignment transition in the product is currently blocked at the
-- database layer.
--
-- The constraint is the stale artifact, not the code. Backfilling a synthetic
-- bookings.started_at would be actively wrong: a booking can route to several sections at once
-- (booking_creation_service.create_booking() creates one assignment per resolved section), so
-- there is no single authoritative section-level start/attend moment to record on the parent
-- row. Verified by grep across app/: NO code path writes bookings.started_at, started_by,
-- attended_at, attended_by, attendance_remarks, reopened_at, reopened_by,
-- started_by_section_id or attended_by_section_id. The only remaining reference is a read in
-- app/services/booking_pool_service.py's Admin display projection.
--
-- ============================ DECISION =======================================================
-- Option B: the legacy parent-level columns are RETAINED physically (nullable, historical /
-- forward-compatibility columns - nothing is dropped and no data is altered), but the CHECK
-- constraints coupling them to bookings.status are REMOVED, because status is now derived from
-- the assignment set rather than tied 1:1 to a single parent timestamp.
--
-- Option A (keep the columns and derive them, e.g. earliest assignment start / latest assignment
-- attend) was rejected: it would misrepresent multi-section reality by implying one section-level
-- event, and would duplicate state that booking_section_assignments already holds authoritatively.
-- Option C (physically drop the columns) is out of scope for this phase by design - the columns
-- stay, all data stays, only the stale coupling goes.
--
-- ============================ REPLACEMENT INTEGRITY =========================================
-- The integrity these constraints used to provide has simply moved down to the assignment row,
-- where it is genuinely meaningful. booking_section_assignments already has
-- chk_booking_section_started and chk_booking_section_attended; this migration adds the two
-- transition invariants the service layer enforces but the database did not, so the DB layer
-- also rejects an assignment row that could only have arrived via a forbidden transition:
--
--   * ATTENDED is only reachable from IN_PROGRESS (direct OPEN -> ATTENDED and
--     REOPENED -> ATTENDED are forbidden), therefore an ATTENDED row must have started_at.
--   * REOPENED is only reachable from ATTENDED, therefore a REOPENED row must have attended_at.
-- Verified read-only against the live rdcms database before writing this file - zero existing
-- rows violate either (see the audit report's "whether existing production rows are safe"
-- section for the exact queries and their 0/0 results).
--
-- A third candidate, CHECK (attended_at >= started_at), was drafted and then REJECTED: it is
-- false across a legitimate reopen cycle. REOPENED -> IN_PROGRESS writes a fresh started_at while
-- deliberately preserving the previous attended_at as audit history, so a restarted assignment
-- correctly carries attended_at < started_at until it is attended again.
--
-- ============================ PRIVILEGES ====================================================
-- The sequence GRANTs at the bottom are idempotent belt-and-braces for the previously observed
-- "permission denied for sequence shed_visits_id_seq". As of this audit that error no longer
-- reproduces: elsbladmin already holds USAGE + SELECT (relacl "elsbladmin=rU/postgres") on every
-- Operations-Dashboard sequence, which is what nextval() requires. Sequence OWNERSHIP is
-- deliberately NOT changed: these sequences are owned by `postgres` because their tables are, and
-- re-owning a sequence away from its table's owner creates a worse inconsistency than it fixes.
-- Only Operations-Dashboard-owned objects appear below; nothing BL-DCMS owns (checksheet_*,
-- equipment, users, sections, locomotives, template_fields, ...) is referenced.
--
-- ============================ HOW TO RUN ====================================================
-- bookings and booking_section_assignments are OWNED BY `postgres`, and the application role
-- `elsbladmin` is neither a superuser nor a member of `postgres`. ALTER TABLE therefore CANNOT
-- be executed by elsbladmin - this migration must be run by a human DBA as `postgres`
-- (or another role that owns these tables). See scripts/apply_migration_003.py, which refuses to
-- run under the ordinary application connection for exactly this reason.
-- ============================================================================================


-- --------------------------------------------------------------------------------------------
-- 1. Drop the three stale status/timestamp-coupling constraints on `bookings`.
--    Stale because bookings.status is now derived from booking_section_assignments and the
--    parent-level timestamp columns are no longer written by any code path.
-- --------------------------------------------------------------------------------------------
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS chk_booking_started;
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS chk_booking_attended;
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS chk_booking_reopened;


-- --------------------------------------------------------------------------------------------
-- 2. Push the equivalent integrity down to booking_section_assignments, where the lifecycle
--    actually lives. Dropped-then-added so this file is safely re-runnable (PostgreSQL has no
--    ADD CONSTRAINT IF NOT EXISTS).
-- --------------------------------------------------------------------------------------------
ALTER TABLE booking_section_assignments
    DROP CONSTRAINT IF EXISTS chk_booking_section_attended_requires_start;
ALTER TABLE booking_section_assignments
    ADD CONSTRAINT chk_booking_section_attended_requires_start
    CHECK (status <> 'ATTENDED' OR started_at IS NOT NULL);

ALTER TABLE booking_section_assignments
    DROP CONSTRAINT IF EXISTS chk_booking_section_reopened_requires_attend;
ALTER TABLE booking_section_assignments
    ADD CONSTRAINT chk_booking_section_reopened_requires_attend
    CHECK (status <> 'REOPENED' OR attended_at IS NOT NULL);

-- Defensive: if an earlier draft of this migration was ever applied, remove the rejected
-- attended_at >= started_at constraint (see the REPLACEMENT INTEGRITY note above).
ALTER TABLE booking_section_assignments
    DROP CONSTRAINT IF EXISTS chk_booking_section_attend_after_start;


-- --------------------------------------------------------------------------------------------
-- 3. Documentation of intent on the retained-but-vestigial parent columns. No data is touched.
--    (COMMENT ON is metadata only and is safe to re-run.)
-- --------------------------------------------------------------------------------------------
COMMENT ON COLUMN bookings.started_at IS
    'Vestigial (pre-assignment architecture). Not written by any code path. The authoritative start is booking_section_assignments.started_at, per assignment. Retained for historical rows only.';
COMMENT ON COLUMN bookings.started_by IS
    'Vestigial - see bookings.started_at.';
COMMENT ON COLUMN bookings.started_by_section_id IS
    'Vestigial - see bookings.started_at. Added by migration 002 (Common Booking Pool reform) and never written since that reform was superseded by the assignment-based model.';
COMMENT ON COLUMN bookings.attended_at IS
    'Vestigial (pre-assignment architecture). Not written by any code path. The authoritative attend is booking_section_assignments.attended_at, per assignment. Retained for historical rows only.';
COMMENT ON COLUMN bookings.attended_by IS
    'Vestigial - see bookings.attended_at.';
COMMENT ON COLUMN bookings.attended_by_section_id IS
    'Vestigial - see bookings.attended_at. Added by migration 002 and never written since.';
COMMENT ON COLUMN bookings.attendance_remarks IS
    'Vestigial - the authoritative attendance note is booking_section_assignments.attendance_remarks, per assignment.';
COMMENT ON COLUMN bookings.reopened_at IS
    'Vestigial - a reopen is recorded on the assignment (status=REOPENED, updated_at) plus a booking_events REOPENED row.';
COMMENT ON COLUMN bookings.reopened_by IS
    'Vestigial - see bookings.reopened_at.';
COMMENT ON COLUMN bookings.status IS
    'DERIVED aggregate over this booking''s booking_section_assignments rows - never set independently. any REOPENED gives REOPENED, else all ATTENDED gives ATTENDED, else any IN_PROGRESS gives IN_PROGRESS, else OPEN. See app/services/section_dashboard_service.recompute_booking_status().';


-- --------------------------------------------------------------------------------------------
-- 4. Sequence privileges for the application role, scoped strictly to Operations-Dashboard-owned
--    sequences. Idempotent; already satisfied as of this audit (USAGE + SELECT present on all
--    ten - the last two are already owned by elsbladmin outright, so those two lines are no-ops
--    kept only for completeness). UPDATE is included only so setval()/reseeding is possible;
--    no application code path calls setval. Ownership is intentionally left with `postgres`.
--    NOTHING BL-DCMS owns is referenced here.
-- --------------------------------------------------------------------------------------------
GRANT USAGE, SELECT ON SEQUENCE bookings_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE booking_events_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE booking_section_assignments_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE booking_defect_types_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE shed_visits_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE shed_visit_events_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE shed_visit_stages_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE dashboard_access_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE shed_visit_checksheet_packages_id_seq TO elsbladmin;
GRANT USAGE, SELECT ON SEQUENCE shed_visit_checksheet_requirements_id_seq TO elsbladmin;
