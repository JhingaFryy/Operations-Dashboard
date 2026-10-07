-- booking_status_aggregate_drift_audit.sql
--
-- READ ONLY. SELECTs only. No INSERT/UPDATE/DELETE, no DDL, no transaction control.
-- Safe to run against production at any time.
--
-- PURPOSE
-- -------
-- bookings.status is a DERIVED aggregate over that booking's booking_section_assignments rows.
-- The authoritative formula lives in app/domain/booking_resolution.aggregate_booking_status and
-- is written only by section_dashboard_service.recompute_booking_status:
--
--     any REOPENED    -> REOPENED
--     all ATTENDED    -> ATTENDED   (requires at least one assignment row)
--     any IN_PROGRESS -> IN_PROGRESS
--     otherwise       -> OPEN
--
-- booking_routing_service.add_sections() previously inserted a new OPEN assignment row WITHOUT
-- calling that recompute. A booking whose only section was already ATTENDED therefore kept the
-- stored status ATTENDED while carrying unresolved work. That is now fixed in code, but any
-- booking that received a planner addition BEFORE the fix still holds the stale value.
--
-- This script finds those rows and nothing else. It does not repair them: whether to run a
-- one-off recompute is a separate decision, and this is the evidence for making it.
--
-- The three real gates (Shed Out, each stage's booking gate, and the shed-visit pending count)
-- all re-derive from the assignment rows directly, so a stale stored status has NEVER let a
-- locomotive leave with outstanding work. The impact is display and filtering: the global pool's
-- status filter skips such a booking, and before the fix the Shed Out blocker list printed the
-- stale word next to a booking it was blocking.
--
-- HOW TO RUN
--   psql "$RDCMS_DATABASE_URL" -v ON_ERROR_STOP=1 -f diagnostics/booking_status_aggregate_drift_audit.sql
--
-- Expected healthy result: section 2 reports 0 drifted bookings and section 3 returns no rows.


\echo ''
\echo '==================================================================================='
\echo ' SECTION 1. Stored vs recomputed status, for every booking that has drifted.'
\echo '==================================================================================='

WITH expected AS (
    SELECT
        b.id                                                   AS booking_id,
        b.status                                               AS stored_status,
        COUNT(a.id)                                            AS assignment_count,
        -- The aggregate formula, expressed once and reused by every section below.
        CASE
            WHEN COUNT(a.id) = 0 THEN 'OPEN'
            WHEN COUNT(*) FILTER (WHERE a.status = 'REOPENED')    > 0 THEN 'REOPENED'
            WHEN COUNT(*) FILTER (WHERE a.status <> 'ATTENDED')   = 0 THEN 'ATTENDED'
            WHEN COUNT(*) FILTER (WHERE a.status = 'IN_PROGRESS') > 0 THEN 'IN_PROGRESS'
            ELSE 'OPEN'
        END                                                    AS recomputed_status
    FROM bookings b
    LEFT JOIN booking_section_assignments a ON a.booking_id = b.id
    GROUP BY b.id, b.status
)
SELECT
    e.booking_id,
    e.stored_status,
    e.recomputed_status,
    e.assignment_count,
    b.booking_source,
    b.created_at,
    v.id            AS shed_visit_id,
    v.loco_number,
    v.status        AS visit_status,
    -- Why it drifted, inferred from the row shape. A booking that gained a MANUAL assignment
    -- after its AUTO_MAPPING row was already attended is the signature of the add_sections defect.
    CASE
        WHEN e.assignment_count = 0 THEN 'no assignment rows (routing gap)'
        WHEN e.stored_status = 'ATTENDED' AND e.recomputed_status <> 'ATTENDED'
            THEN 'stored says resolved, assignments disagree (add_sections defect signature)'
        WHEN e.stored_status <> 'ATTENDED' AND e.recomputed_status = 'ATTENDED'
            THEN 'stored says unresolved, every assignment is attended'
        ELSE 'other drift'
    END AS likely_cause
FROM expected e
JOIN bookings b   ON b.id = e.booking_id
JOIN shed_visits v ON v.id = b.shed_visit_id
WHERE e.stored_status <> e.recomputed_status
ORDER BY v.status, v.loco_number, e.booking_id;


\echo ''
\echo '==================================================================================='
\echo ' SECTION 2. Totals. drifted_bookings = 0 means nothing to repair.'
\echo '==================================================================================='

WITH expected AS (
    SELECT
        b.id AS booking_id,
        b.status AS stored_status,
        COUNT(a.id) AS assignment_count,
        CASE
            WHEN COUNT(a.id) = 0 THEN 'OPEN'
            WHEN COUNT(*) FILTER (WHERE a.status = 'REOPENED')    > 0 THEN 'REOPENED'
            WHEN COUNT(*) FILTER (WHERE a.status <> 'ATTENDED')   = 0 THEN 'ATTENDED'
            WHEN COUNT(*) FILTER (WHERE a.status = 'IN_PROGRESS') > 0 THEN 'IN_PROGRESS'
            ELSE 'OPEN'
        END AS recomputed_status
    FROM bookings b
    LEFT JOIN booking_section_assignments a ON a.booking_id = b.id
    GROUP BY b.id, b.status
)
SELECT
    COUNT(*)                                                               AS total_bookings,
    COUNT(*) FILTER (WHERE stored_status <> recomputed_status)             AS drifted_bookings,
    COUNT(*) FILTER (WHERE stored_status <> recomputed_status
                       AND stored_status = 'ATTENDED')                     AS drifted_falsely_attended,
    COUNT(*) FILTER (WHERE assignment_count = 0)                           AS bookings_with_no_assignments,
    COUNT(*) FILTER (WHERE assignment_count > 1)                           AS multi_section_bookings
FROM expected;


\echo ''
\echo '==================================================================================='
\echo ' SECTION 3. The highest-impact subset: drifted bookings on an OPEN shed visit.'
\echo '            These are the ones a planner or supervisor is looking at TODAY.'
\echo '==================================================================================='

WITH expected AS (
    SELECT
        b.id AS booking_id,
        b.status AS stored_status,
        COUNT(a.id) AS assignment_count,
        CASE
            WHEN COUNT(a.id) = 0 THEN 'OPEN'
            WHEN COUNT(*) FILTER (WHERE a.status = 'REOPENED')    > 0 THEN 'REOPENED'
            WHEN COUNT(*) FILTER (WHERE a.status <> 'ATTENDED')   = 0 THEN 'ATTENDED'
            WHEN COUNT(*) FILTER (WHERE a.status = 'IN_PROGRESS') > 0 THEN 'IN_PROGRESS'
            ELSE 'OPEN'
        END AS recomputed_status
    FROM bookings b
    LEFT JOIN booking_section_assignments a ON a.booking_id = b.id
    GROUP BY b.id, b.status
)
SELECT
    v.loco_number,
    v.id AS shed_visit_id,
    e.booking_id,
    e.stored_status,
    e.recomputed_status,
    b.description
FROM expected e
JOIN bookings b    ON b.id = e.booking_id
JOIN shed_visits v ON v.id = b.shed_visit_id
WHERE e.stored_status <> e.recomputed_status
  AND v.status <> 'CLOSED'
ORDER BY v.loco_number, e.booking_id;


\echo ''
\echo '==================================================================================='
\echo ' SECTION 4. Per-section detail for each drifted booking, so a human can see WHY.'
\echo '            MANUAL rows added after an AUTO_MAPPING row was attended are the'
\echo '            add_sections signature.'
\echo '==================================================================================='

WITH expected AS (
    SELECT
        b.id AS booking_id,
        b.status AS stored_status,
        CASE
            WHEN COUNT(a.id) = 0 THEN 'OPEN'
            WHEN COUNT(*) FILTER (WHERE a.status = 'REOPENED')    > 0 THEN 'REOPENED'
            WHEN COUNT(*) FILTER (WHERE a.status <> 'ATTENDED')   = 0 THEN 'ATTENDED'
            WHEN COUNT(*) FILTER (WHERE a.status = 'IN_PROGRESS') > 0 THEN 'IN_PROGRESS'
            ELSE 'OPEN'
        END AS recomputed_status
    FROM bookings b
    LEFT JOIN booking_section_assignments a ON a.booking_id = b.id
    GROUP BY b.id, b.status
)
SELECT
    e.booking_id,
    e.stored_status,
    e.recomputed_status,
    s.code              AS section_code,
    a.status            AS assignment_status,
    a.assignment_source,
    a.assigned_at,
    a.attended_at
FROM expected e
JOIN booking_section_assignments a ON a.booking_id = e.booking_id
JOIN sections s                    ON s.id = a.section_id
WHERE e.stored_status <> e.recomputed_status
ORDER BY e.booking_id, a.assigned_at, s.code;


\echo ''
\echo '==================================================================================='
\echo ' SECTION 5. Bookings that received a planner addition (a FORWARDED event), whether'
\echo '            or not they drifted. Context for how often the path is used.'
\echo '==================================================================================='

SELECT
    COUNT(DISTINCT be.booking_id) AS bookings_with_a_forwarded_event,
    MIN(be.created_at)            AS earliest_addition,
    MAX(be.created_at)            AS latest_addition
FROM booking_events be
WHERE be.event_type = 'FORWARDED';

\echo ''
\echo 'END OF AUDIT. Nothing was modified.'
\echo ''
