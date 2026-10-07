-- =================================================================================================
-- SHED VISIT DELETION - FIXTURE SUPPLEMENT (READ ONLY)
-- =================================================================================================
--
-- The main audit (visit_deletion_audit.sql) reported how MANY visits carry a signoff-mode marker
-- but not WHICH, and those are exactly the visits that cannot be deleted until migration 078 is
-- applied. It also found no locomotive with more than one visit and no section-signoff rows at all,
-- so those two fixtures have to be synthesised - this script confirms that is still true rather
-- than assumed.
--
-- READ ONLY. Every statement is a SELECT.
--
--     psql "$DATABASE_URL" -f diagnostics/visit_deletion_fixtures.sql > visit_deletion_fixtures.txt
-- =================================================================================================

\echo ''
\echo '=== 1. WHICH VISITS CARRY A SIGNOFF-MODE MARKER (= currently undeletable) ======'
-- 16 of 31 visits were reported as marked. Each one of these raises on DELETE today, via
-- trg_signoff_mode_immutable, and is unblocked only by migration 078.
SELECT m.shed_visit_id,
       m.mode,
       sv.loco_number,
       sv.schedule_family,
       sv.schedule_variant,
       sv.status,
       (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = m.shed_visit_id) AS checksheets,
       (SELECT count(*) FROM digital_signatures ds
          JOIN checksheet_header ch ON ch.id = ds.checksheet_id
         WHERE ch.shed_visit_id = m.shed_visit_id) AS signatures,
       (SELECT count(*) FROM minor_inspection_section_signoff s
         WHERE s.shed_visit_id = m.shed_visit_id) AS signoff_rows
FROM shed_visit_checksheet_signoff_mode m
LEFT JOIN shed_visits sv ON sv.id = m.shed_visit_id
ORDER BY m.mode, m.shed_visit_id;

\echo ''
\echo '=== 2. THE ONE SECTION_SIGNOFF VISIT, IN FULL ================================='
-- Section 9 of the audit found exactly one, while section 5 found zero signed_pdf_path values and
-- section 8 zero signoff rows. So this visit has adopted the new workflow but has not yet produced
-- a signature. Worth seeing on its own: it is the only production example of the new path.
SELECT sv.id, sv.loco_number, sv.schedule_family, sv.schedule_variant, sv.status,
       (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
       (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets,
       (SELECT count(*) FROM checksheet_header ch
         WHERE ch.shed_visit_id = sv.id AND ch.minor_inspection_equipment_id IS NOT NULL) AS minor_checksheets,
       (SELECT count(*) FROM checksheet_header ch
         WHERE ch.shed_visit_id = sv.id AND ch.status = 'APPROVED') AS approved
FROM shed_visits sv
JOIN shed_visit_checksheet_signoff_mode m ON m.shed_visit_id = sv.id
WHERE m.mode = 'SECTION_SIGNOFF';

\echo ''
\echo '=== 3. THE 15 VISITS WITH NO MARKER (deletable without 078) ==================='
SELECT sv.id, sv.loco_number, sv.schedule_variant, sv.status,
       (SELECT count(*) FROM bookings b WHERE b.shed_visit_id = sv.id) AS bookings,
       (SELECT count(*) FROM checksheet_header ch WHERE ch.shed_visit_id = sv.id) AS checksheets
FROM shed_visits sv
WHERE NOT EXISTS (
    SELECT 1 FROM shed_visit_checksheet_signoff_mode m WHERE m.shed_visit_id = sv.id
)
ORDER BY sv.id;

\echo ''
\echo '=== 4. CONFIRM: no locomotive has two visits, and no section signoff rows ====='
SELECT 'locomotives with more than one visit' AS fact, count(*) AS value
FROM (SELECT loco_number FROM shed_visits GROUP BY loco_number HAVING count(*) > 1) x
UNION ALL
SELECT 'minor_inspection_section_signoff rows', count(*) FROM minor_inspection_section_signoff
UNION ALL
SELECT 'minor_inspection_section_signoff_checksheet rows', count(*)
FROM minor_inspection_section_signoff_checksheet
UNION ALL
SELECT 'distinct loco_number in shed_visits', count(DISTINCT loco_number) FROM shed_visits
UNION ALL
SELECT 'total shed_visits', count(*) FROM shed_visits;

\echo ''
\echo '=== 5. THE STRESS FIXTURE (visit 42) IN FULL DETAIL ==========================='
-- The largest fanout in production. Every count the deletion preview will have to produce.
SELECT 'bookings' AS entity, count(*) AS rows FROM bookings WHERE shed_visit_id = 42
UNION ALL SELECT 'booking_section_assignments', count(*) FROM booking_section_assignments bsa
    JOIN bookings b ON b.id = bsa.booking_id WHERE b.shed_visit_id = 42
UNION ALL SELECT 'booking_events', count(*) FROM booking_events be
    JOIN bookings b ON b.id = be.booking_id WHERE b.shed_visit_id = 42
UNION ALL SELECT 'shed_visit_events', count(*) FROM shed_visit_events WHERE shed_visit_id = 42
UNION ALL SELECT 'shed_visit_stages', count(*) FROM shed_visit_stages WHERE shed_visit_id = 42
UNION ALL SELECT 'shed_visit_checksheet_packages', count(*) FROM shed_visit_checksheet_packages WHERE shed_visit_id = 42
UNION ALL SELECT 'shed_visit_checksheet_requirements', count(*) FROM shed_visit_checksheet_requirements r
    JOIN shed_visit_checksheet_packages p ON p.id = r.package_id WHERE p.shed_visit_id = 42
UNION ALL SELECT 'checksheet_header', count(*) FROM checksheet_header WHERE shed_visit_id = 42
UNION ALL SELECT 'checksheet_value', count(*) FROM checksheet_value cv
    JOIN checksheet_header ch ON ch.id = cv.checksheet_id WHERE ch.shed_visit_id = 42
UNION ALL SELECT 'digital_signatures', count(*) FROM digital_signatures ds
    JOIN checksheet_header ch ON ch.id = ds.checksheet_id WHERE ch.shed_visit_id = 42
UNION ALL SELECT 'notifications', count(*) FROM notifications nt
    JOIN checksheet_header ch ON ch.id = nt.checksheet_id WHERE ch.shed_visit_id = 42
UNION ALL SELECT 'pdf files referenced', count(*) FROM checksheet_header
    WHERE shed_visit_id = 42 AND pdf_path IS NOT NULL
ORDER BY 2 DESC;

\echo ''
\echo '=== 6. CHECKSHEET STATUS SPREAD ON THE FIXTURE VISITS ========================='
-- Tests must cover DRAFT, SUBMITTED, APPROVED and REJECTED. This says which fixture visit supplies
-- which state, so no state has to be invented that production does not actually produce.
SELECT shed_visit_id, status, count(*) AS rows
FROM checksheet_header
WHERE shed_visit_id IN (40, 42, 43, 45, 46, 47, 57)
GROUP BY shed_visit_id, status
ORDER BY shed_visit_id, status;

\echo ''
\echo '--- 6b. where do the 2 DRAFT and 5 REJECTED checksheets actually live?'
SELECT status, shed_visit_id, count(*) AS rows
FROM checksheet_header
WHERE status IN ('DRAFT', 'REJECTED')
GROUP BY status, shed_visit_id
ORDER BY status, shed_visit_id;

\echo ''
\echo '=== FIXTURE SUPPLEMENT COMPLETE - nothing was modified ========================'
