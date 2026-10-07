-- =================================================================================================
-- SHED VISIT DELETION - PRODUCTION READ-ONLY DEPENDENCY AUDIT
-- =================================================================================================
--
-- PURPOSE. Validates the deletion dependency graph against the LIVE rdcms schema before any
-- destructive code is written. The graph derived from the two codebases' SQLAlchemy metadata is a
-- claim about production; this script is how that claim is checked. Both Operations Dashboard and
-- BL-DCMS share this one database, so a single run covers both.
--
-- READ ONLY. Every statement is a SELECT. No DDL, no DML, no temporary tables, no settings
-- changes. Safe to run on production at any time, and safe to interrupt.
--
-- HOW TO RUN
--     psql "$DATABASE_URL" -f diagnostics/visit_deletion_audit.sql > visit_deletion_audit.txt
--
-- WHAT TO LOOK FOR, in order of importance:
--   Section 2 - any FK into shed_visits/bookings/checksheet_header that the implementation does
--               not know about. A single unknown FK is enough to make deletion fail or orphan data.
--   Section 3 - the cross-system columns that have NO foreign key. These are the ones Postgres
--               will NOT protect; deleting a visit leaves them dangling unless code handles them.
--   Section 6 - the write-once trigger on shed_visit_checksheet_signoff_mode, which currently
--               REFUSES DELETE and therefore blocks visit deletion outright.
--   Section 10 - the candidate visits to build scratch fixtures from.
-- =================================================================================================

\echo ''
\echo '=== 1. SCHEMA INVENTORY ======================================================='
-- Every base table in the shared database, with row counts, so an unmodelled table cannot hide.
SELECT c.relname                        AS table_name,
       c.reltuples::bigint              AS est_rows,
       obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname;

\echo ''
\echo '=== 2. EVERY FOREIGN KEY INTO THE DELETION TARGETS ============================'
-- The authoritative dependency graph. ON DELETE action is spelled out, because the implementation
-- must NOT assume CASCADE: 'a' = NO ACTION, 'r' = RESTRICT, 'c' = CASCADE, 'n' = SET NULL,
-- 'd' = SET DEFAULT. Anything that is not 'c' must be deleted explicitly, child-first.
SELECT ct.relname                              AS child_table,
       con.conname                             AS constraint_name,
       pg_get_constraintdef(con.oid)           AS definition,
       pt.relname                              AS parent_table,
       CASE con.confdeltype
            WHEN 'a' THEN 'NO ACTION'  WHEN 'r' THEN 'RESTRICT'
            WHEN 'c' THEN 'CASCADE'    WHEN 'n' THEN 'SET NULL'
            WHEN 'd' THEN 'SET DEFAULT' END    AS on_delete,
       con.condeferrable                       AS deferrable
FROM pg_constraint con
JOIN pg_class ct ON ct.oid = con.conrelid
JOIN pg_class pt ON pt.oid = con.confrelid
WHERE con.contype = 'f'
  AND pt.relname IN ('shed_visits', 'bookings', 'checksheet_header', 'shed_visit_stages',
                     'shed_visit_checksheet_packages', 'minor_inspection_section_signoff',
                     'locomotives')
ORDER BY pt.relname, ct.relname, con.conname;

\echo ''
\echo '=== 2b. FULL FK GRAPH (every table), for anything section 2 missed ============'
SELECT ct.relname AS child_table, pt.relname AS parent_table,
       pg_get_constraintdef(con.oid) AS definition,
       CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
            WHEN 'c' THEN 'CASCADE' WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' END
         AS on_delete
FROM pg_constraint con
JOIN pg_class ct ON ct.oid = con.conrelid
JOIN pg_class pt ON pt.oid = con.confrelid
WHERE con.contype = 'f'
ORDER BY pt.relname, ct.relname;

\echo ''
\echo '=== 3. CROSS-SYSTEM COLUMNS WITH NO FOREIGN KEY (THE DANGEROUS ONES) =========='
-- BL-DCMS references a shed visit by plain integer, deliberately: shed_visits belongs to
-- Operations Dashboard. Postgres therefore enforces NOTHING here. Deleting a visit will silently
-- leave every row below pointing at an id that no longer exists, unless the deletion code removes
-- them itself. This is the single most important section of this audit.
SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
       EXISTS (
           SELECT 1 FROM pg_constraint con
           JOIN pg_class ct ON ct.oid = con.conrelid
           JOIN unnest(con.conkey) AS k(attnum) ON TRUE
           JOIN pg_attribute a ON a.attrelid = ct.oid AND a.attnum = k.attnum
           WHERE con.contype = 'f' AND ct.relname = c.table_name AND a.attname = c.column_name
       ) AS has_fk
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.column_name IN ('shed_visit_id', 'booking_id', 'checksheet_id', 'checksheet_header_id',
                        'visit_id')
ORDER BY has_fk, c.table_name, c.column_name;

\echo ''
\echo '=== 4. ORPHANS THAT ALREADY EXIST ============================================='
-- A baseline. If these are non-zero BEFORE any deletion, the shared database already carries
-- dangling cross-system references, and post-deletion orphan checks must be read against this
-- number rather than against zero.
SELECT 'checksheet_header.shed_visit_id with no shed_visit' AS check_name, count(*) AS rows
FROM checksheet_header ch
WHERE ch.shed_visit_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM shed_visits sv WHERE sv.id = ch.shed_visit_id)
UNION ALL
SELECT 'minor_inspection_section_signoff.shed_visit_id with no shed_visit', count(*)
FROM minor_inspection_section_signoff s
WHERE NOT EXISTS (SELECT 1 FROM shed_visits sv WHERE sv.id = s.shed_visit_id)
UNION ALL
SELECT 'shed_visit_checksheet_signoff_mode.shed_visit_id with no shed_visit', count(*)
FROM shed_visit_checksheet_signoff_mode m
WHERE NOT EXISTS (SELECT 1 FROM shed_visits sv WHERE sv.id = m.shed_visit_id);

\echo ''
\echo '=== 5. FILESYSTEM REFERENCES =================================================='
-- Which rows name a file on disk, and whether the path looks like the private storage directory.
-- Deletion must quarantine these AFTER commit, never unlink before it.
SELECT 'checksheet_header.pdf_path' AS source, count(*) AS total,
       count(*) FILTER (WHERE pdf_path LIKE '%/storage/pdfs/%') AS under_storage_pdfs,
       count(DISTINCT pdf_path)                                 AS distinct_paths
FROM checksheet_header WHERE pdf_path IS NOT NULL
UNION ALL
SELECT 'minor_inspection_section_signoff.signed_pdf_path', count(*),
       count(*) FILTER (WHERE signed_pdf_path LIKE '%/storage/pdfs/%'),
       count(DISTINCT signed_pdf_path)
FROM minor_inspection_section_signoff WHERE signed_pdf_path IS NOT NULL;

\echo ''
\echo '--- 5b. any path SHARED by more than one row (must never be deleted on one row''s behalf)'
SELECT pdf_path, count(*) AS referencing_rows
FROM checksheet_header
WHERE pdf_path IS NOT NULL
GROUP BY pdf_path HAVING count(*) > 1
ORDER BY count(*) DESC
LIMIT 20;

\echo ''
\echo '=== 6. TRIGGERS THAT WILL BLOCK OR ALTER DELETION ============================='
-- trg_signoff_mode_immutable (migration 076) fires BEFORE UPDATE OR DELETE and RAISES. A visit
-- that carries a signoff-mode marker therefore CANNOT be deleted while this trigger stands.
SELECT ct.relname AS table_name, t.tgname AS trigger_name,
       pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t
JOIN pg_class ct ON ct.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = ct.relnamespace
WHERE NOT t.tgisinternal AND n.nspname = 'public'
ORDER BY ct.relname, t.tgname;

\echo ''
\echo '=== 7. VISIT POPULATION ======================================================='
SELECT count(*) AS total_visits,
       count(*) FILTER (WHERE status = 'ACTIVE')    AS active,
       count(*) FILTER (WHERE status <> 'ACTIVE')   AS not_active,
       min(created_at)::date AS earliest, max(created_at)::date AS latest
FROM shed_visits;

\echo ''
\echo '--- 7b. by schedule'
SELECT schedule_family, schedule_variant, count(*) AS visits
FROM shed_visits GROUP BY 1, 2 ORDER BY 1, 2;

\echo ''
\echo '=== 8. PER-VISIT FANOUT (the maximum one delete must handle) =================='
-- Drives the preview counts and tells us the largest transaction this feature will ever run.
WITH per_visit AS (
    SELECT sv.id,
           sv.loco_number,
           sv.schedule_variant,
           (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
           (SELECT count(*) FROM booking_section_assignments bsa
              JOIN bookings b ON b.id = bsa.booking_id WHERE b.shed_visit_id = sv.id) AS assignments,
           (SELECT count(*) FROM booking_events be
              JOIN bookings b ON b.id = be.booking_id WHERE b.shed_visit_id = sv.id) AS booking_events,
           (SELECT count(*) FROM shed_visit_events e WHERE e.shed_visit_id = sv.id) AS visit_events,
           (SELECT count(*) FROM shed_visit_stages st WHERE st.shed_visit_id = sv.id) AS stages,
           (SELECT count(*) FROM shed_visit_checksheet_packages p WHERE p.shed_visit_id = sv.id) AS packages,
           (SELECT count(*) FROM shed_visit_checksheet_requirements r
              JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
             WHERE p.shed_visit_id = sv.id) AS requirements,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets,
           (SELECT count(*) FROM checksheet_value cv
              JOIN checksheet_header ch ON ch.id = cv.checksheet_id
             WHERE ch.shed_visit_id = sv.id) AS checksheet_values,
           (SELECT count(*) FROM digital_signatures ds
              JOIN checksheet_header ch ON ch.id = ds.checksheet_id
             WHERE ch.shed_visit_id = sv.id) AS signatures,
           (SELECT count(*) FROM minor_inspection_section_signoff s WHERE s.shed_visit_id = sv.id) AS signoffs,
           (SELECT count(*) FROM minor_inspection_section_signoff_checksheet sc
              JOIN minor_inspection_section_signoff s ON s.id = sc.signoff_id
             WHERE s.shed_visit_id = sv.id) AS signoff_pins,
           (SELECT count(*) FROM shed_visit_checksheet_signoff_mode m WHERE m.shed_visit_id = sv.id) AS signoff_mode,
           (SELECT count(*) FROM notifications nt
              JOIN checksheet_header ch ON ch.id = nt.checksheet_id
             WHERE ch.shed_visit_id = sv.id) AS notifications,
           (SELECT count(*) FROM checksheet_header ch
             WHERE ch.shed_visit_id = sv.id AND ch.pdf_path IS NOT NULL) AS checksheet_pdfs
    FROM shed_visits sv
)
SELECT 'MAX across all visits' AS metric,
       max(bookings) AS bookings, max(assignments) AS assignments,
       max(booking_events) AS booking_events, max(visit_events) AS visit_events,
       max(stages) AS stages, max(packages) AS packages, max(requirements) AS requirements,
       max(checksheets) AS checksheets, max(checksheet_values) AS checksheet_values,
       max(signatures) AS signatures, max(signoffs) AS signoffs, max(signoff_pins) AS signoff_pins,
       max(notifications) AS notifications, max(checksheet_pdfs) AS checksheet_pdfs
FROM per_visit
UNION ALL
SELECT 'SUM across all visits',
       sum(bookings), sum(assignments), sum(booking_events), sum(visit_events), sum(stages),
       sum(packages), sum(requirements), sum(checksheets), sum(checksheet_values), sum(signatures),
       sum(signoffs), sum(signoff_pins), sum(notifications), sum(checksheet_pdfs)
FROM per_visit;

\echo ''
\echo '=== 9. SIGNED / SENSITIVE DATA THAT DELETION WOULD DESTROY ===================='
SELECT 'checksheets with a digital signature' AS what, count(*) AS rows FROM digital_signatures
UNION ALL
SELECT 'SIGNED minor section signoffs', count(*) FROM minor_inspection_section_signoff WHERE status = 'SIGNED'
UNION ALL
SELECT 'visits marked PER_CHECKSHEET_DSC', count(*) FROM shed_visit_checksheet_signoff_mode WHERE mode = 'PER_CHECKSHEET_DSC'
UNION ALL
SELECT 'visits marked SECTION_SIGNOFF', count(*) FROM shed_visit_checksheet_signoff_mode WHERE mode = 'SECTION_SIGNOFF'
UNION ALL
SELECT 'checksheets by status: APPROVED', count(*) FROM checksheet_header WHERE status = 'APPROVED'
UNION ALL
SELECT 'checksheets by status: DRAFT', count(*) FROM checksheet_header WHERE status = 'DRAFT'
UNION ALL
SELECT 'checksheets by status: SUBMITTED', count(*) FROM checksheet_header WHERE status = 'SUBMITTED'
UNION ALL
SELECT 'checksheets by status: REJECTED', count(*) FROM checksheet_header WHERE status = 'REJECTED';

\echo ''
\echo '=== 10. SCRATCH FIXTURE CANDIDATES ==========================================='
-- One real visit of each shape the tests must cover. Use these ids to build the scratch dataset.
WITH shaped AS (
    SELECT sv.id, sv.loco_number, sv.schedule_family, sv.schedule_variant, sv.status,
           (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets,
           (SELECT count(*) FROM digital_signatures ds JOIN checksheet_header ch ON ch.id = ds.checksheet_id
             WHERE ch.shed_visit_id = sv.id) AS signatures,
           (SELECT count(*) FROM minor_inspection_section_signoff s WHERE s.shed_visit_id = sv.id) AS signoffs,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id
              AND ch.workflow_stage_type IN ('TEST_BEFORE', 'TEST_AFTER')) AS tb_ta,
           (SELECT m.mode FROM shed_visit_checksheet_signoff_mode m WHERE m.shed_visit_id = sv.id) AS signoff_mode
    FROM shed_visits sv
)
SELECT 'A. simplest - no bookings, no checksheets' AS shape, s.* FROM shaped s
  WHERE s.bookings = 0 AND s.checksheets = 0 ORDER BY s.id LIMIT 2;

\echo ''
\echo '--- 10b. bookings but no checksheets'
WITH shaped AS (
    SELECT sv.id, sv.loco_number, sv.schedule_family, sv.schedule_variant, sv.status,
           (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets
    FROM shed_visits sv
)
SELECT 'B. bookings but no checksheets' AS shape, s.* FROM shaped s
  WHERE s.bookings > 0 AND s.checksheets = 0 ORDER BY s.bookings DESC LIMIT 2;

\echo ''
\echo '--- 10c. legacy per-checksheet DSC'
WITH shaped AS (
    SELECT sv.id, sv.loco_number, sv.schedule_family, sv.schedule_variant, sv.status,
           (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets,
           (SELECT count(*) FROM digital_signatures ds JOIN checksheet_header ch ON ch.id = ds.checksheet_id
             WHERE ch.shed_visit_id = sv.id) AS signatures,
           (SELECT count(*) FROM minor_inspection_section_signoff s WHERE s.shed_visit_id = sv.id) AS signoffs,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id
              AND ch.workflow_stage_type IN ('TEST_BEFORE', 'TEST_AFTER')) AS tb_ta,
           (SELECT m.mode FROM shed_visit_checksheet_signoff_mode m WHERE m.shed_visit_id = sv.id) AS signoff_mode
    FROM shed_visits sv
)
SELECT 'C. legacy per-checksheet DSC' AS shape, s.* FROM shaped s WHERE s.signatures > 0
  ORDER BY s.signatures DESC LIMIT 2;

\echo ''
WITH shaped AS (
    SELECT sv.id, sv.loco_number, sv.schedule_variant, sv.status,
           (SELECT count(*) FROM minor_inspection_section_signoff s WHERE s.shed_visit_id = sv.id) AS signoffs,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id
              AND ch.workflow_stage_type IN ('TEST_BEFORE', 'TEST_AFTER')) AS tb_ta,
           (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets
    FROM shed_visits sv
)
SELECT 'D. Minor section signoff' AS shape, * FROM shaped WHERE signoffs > 0
  ORDER BY signoffs DESC LIMIT 2;

\echo ''
WITH shaped AS (
    SELECT sv.id, sv.loco_number, sv.schedule_variant, sv.status,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id
              AND ch.workflow_stage_type IN ('TEST_BEFORE', 'TEST_AFTER')) AS tb_ta,
           (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
           (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets
    FROM shed_visits sv
)
SELECT 'E. TB/TA checksheets' AS shape, * FROM shaped WHERE tb_ta > 0
  ORDER BY tb_ta DESC LIMIT 2;

\echo ''
WITH shaped AS (
    SELECT sv.id, sv.loco_number, sv.schedule_variant, sv.status,
           (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id)
         + (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id)
         + (SELECT count(*) FROM digital_signatures ds JOIN checksheet_header ch ON ch.id = ds.checksheet_id
             WHERE ch.shed_visit_id = sv.id)
         + (SELECT count(*) FROM minor_inspection_section_signoff s WHERE s.shed_visit_id = sv.id)
             AS total_fanout
    FROM shed_visits sv
)
SELECT 'F. largest fanout overall' AS shape, * FROM shaped ORDER BY total_fanout DESC LIMIT 3;

\echo ''
\echo '=== 11. A LOCOMOTIVE WITH SEVERAL VISITS (isolation fixture) =================='
-- Needed to prove a delete touches ONE visit: the others for the same locomotive must survive.
SELECT loco_number, count(*) AS visits, array_agg(id ORDER BY id) AS visit_ids,
       array_agg(schedule_variant ORDER BY id) AS variants
FROM shed_visits
GROUP BY loco_number HAVING count(*) > 1
ORDER BY count(*) DESC
LIMIT 10;

\echo ''
\echo '=== 12. EXISTING DELETION LEDGER? ============================================='
-- Confirms the ledger does not already exist under some other name before a migration adds it.
SELECT c.relname AS table_name
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND (c.relname LIKE '%deletion%' OR c.relname LIKE '%deleted%' OR c.relname LIKE '%purge%'
       OR c.relname LIKE '%archive%')
ORDER BY c.relname;

\echo ''
\echo '=== AUDIT COMPLETE - nothing was modified ====================================='
