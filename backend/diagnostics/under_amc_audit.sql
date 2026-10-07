-- =================================================================================================
-- "UNDER AMC" REQUIREMENT STATE - PRODUCTION READ-ONLY AUDIT
-- =================================================================================================
--
-- Run BEFORE any migration or code change. Establishes what the existing per-visit requirement
-- override model actually contains, so the AMC design is built on measured facts.
--
-- READ ONLY. Every statement is a SELECT. Safe on production at any time, safe to interrupt.
--
--     psql "$DATABASE_URL" -f diagnostics/under_amc_audit.sql > under_amc_audit.txt
--
-- WHAT MATTERS MOST
--   Section 2 - how much Optional / Deactivated is actually in use. If both are rare, the risk of
--               the new state interacting badly with them is small.
--   Section 4 - whether a pending requirement always has a checksheet_header. If it does NOT (and
--               it should not), AMC cannot be modelled as a checksheet status and must live on the
--               requirement row.
--   Section 6 - that no under_amc column exists anywhere yet, and that nothing on
--               checksheet_templates is being mistaken for one.
-- =================================================================================================

\echo ''
\echo '=== 1. THE PER-VISIT REQUIREMENT TABLE ========================================'
-- shed_visit_checksheet_requirements is the per-visit snapshot. Its override flags are is_required
-- (Optional) and is_active (Deactivated), and it reaches a visit through its package.
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'shed_visit_checksheet_requirements'
ORDER BY ordinal_position;

\echo ''
\echo '=== 2. HOW MUCH OPTIONAL / DEACTIVATED IS IN USE ============================='
SELECT count(*)                                                        AS total_requirements,
       count(*) FILTER (WHERE is_required AND is_active)               AS blocking,
       count(*) FILTER (WHERE NOT is_required AND is_active)           AS optional,
       count(*) FILTER (WHERE NOT is_active)                           AS deactivated,
       count(*) FILTER (WHERE NOT is_required AND NOT is_active)       AS optional_and_deactivated,
       count(*) FILTER (WHERE changed_by IS NOT NULL)                  AS ever_overridden,
       count(DISTINCT package_id)                                      AS packages
FROM shed_visit_checksheet_requirements;

\echo ''
\echo '--- 2b. by schedule family and stage, since AMC applies to MINOR and MAJOR only'
SELECT sv.schedule_family,
       COALESCE(r.workflow_stage_type, '(none - MAJOR is stageless)') AS stage,
       count(*)                                              AS requirements,
       count(*) FILTER (WHERE NOT r.is_required)             AS optional,
       count(*) FILTER (WHERE NOT r.is_active)               AS deactivated
FROM shed_visit_checksheet_requirements r
JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
JOIN shed_visits sv ON sv.id = p.shed_visit_id
GROUP BY 1, 2
ORDER BY 1, 2;

\echo ''
\echo '--- 2c. which visits currently carry an override (the rows AMC must coexist with)'
SELECT p.shed_visit_id, sv.loco_number, sv.schedule_family, sv.schedule_variant,
       count(*) FILTER (WHERE NOT r.is_required) AS optional,
       count(*) FILTER (WHERE NOT r.is_active)   AS deactivated
FROM shed_visit_checksheet_requirements r
JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
JOIN shed_visits sv ON sv.id = p.shed_visit_id
WHERE NOT r.is_required OR NOT r.is_active
GROUP BY 1, 2, 3, 4
ORDER BY 1;

\echo ''
\echo '=== 3. REQUIREMENT IDENTITY, PER FAMILY ======================================'
-- The stable identity an AMC toggle must address. requirement_id (the row's own PK) is the only
-- identifier that is unique per visit AND stable across a toggle - template_id is not, because one
-- visit can hold several requirements for one template at different sections.
SELECT sv.schedule_family,
       count(*)                                                        AS requirements,
       count(DISTINCT r.id)                                            AS distinct_requirement_ids,
       count(*) FILTER (WHERE r.minor_inspection_equipment_id IS NOT NULL) AS minor_equipment_rows,
       count(*) FILTER (WHERE r.equipment_id_snapshot IS NOT NULL)     AS equipment_rows,
       count(*) FILTER (WHERE r.workflow_stage_type IS NULL)           AS stageless_rows
FROM shed_visit_checksheet_requirements r
JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
JOIN shed_visits sv ON sv.id = p.shed_visit_id
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '--- 3b. can one visit hold several requirements for the SAME template?'
-- If yes, a template id is not a usable AMC key and requirement_id is mandatory.
SELECT p.shed_visit_id, r.template_id, count(*) AS rows_for_that_template
FROM shed_visit_checksheet_requirements r
JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
GROUP BY 1, 2 HAVING count(*) > 1
ORDER BY 3 DESC
LIMIT 10;

\echo ''
\echo '=== 4. DOES A PENDING REQUIREMENT ALWAYS HAVE A CHECKSHEET? =================='
-- The decisive question for the data model. A requirement nobody has started has NO
-- checksheet_header row anywhere, so AMC cannot be expressed as a checksheet status and must live
-- on the requirement. Matched on the same identity the completion service uses.
WITH req AS (
    SELECT r.id, p.shed_visit_id, r.template_id, r.workflow_stage_type,
           r.minor_inspection_equipment_id, sv.schedule_family
    FROM shed_visit_checksheet_requirements r
    JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
    JOIN shed_visits sv ON sv.id = p.shed_visit_id
    WHERE r.is_active
)
SELECT req.schedule_family,
       count(*) AS active_requirements,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM checksheet_header ch
           WHERE ch.shed_visit_id = req.shed_visit_id
             AND ch.template_id = req.template_id
             AND ch.minor_inspection_equipment_id IS NOT DISTINCT FROM req.minor_inspection_equipment_id
       )) AS with_a_checksheet,
       count(*) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM checksheet_header ch
           WHERE ch.shed_visit_id = req.shed_visit_id
             AND ch.template_id = req.template_id
             AND ch.minor_inspection_equipment_id IS NOT DISTINCT FROM req.minor_inspection_equipment_id
       )) AS with_NO_checksheet
FROM req
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== 5. WHAT AMC WOULD HAVE TO COEXIST WITH, PER VISIT ========================'
-- A realistic picture of the gate AMC modifies: how many requirements block each open visit today,
-- and how many are already satisfied.
SELECT p.shed_visit_id, sv.loco_number, sv.schedule_family, sv.schedule_variant, sv.status,
       count(*) FILTER (WHERE r.is_active AND r.is_required)                      AS blocking_now,
       count(*) FILTER (WHERE r.is_active AND NOT r.is_required)                  AS optional_now,
       count(*) FILTER (WHERE NOT r.is_active)                                    AS deactivated_now,
       count(*) FILTER (WHERE r.workflow_stage_type IN ('TEST_BEFORE','TEST_AFTER')) AS tb_ta_always_required
FROM shed_visit_checksheet_requirements r
JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
JOIN shed_visits sv ON sv.id = p.shed_visit_id
WHERE sv.status IN ('IN_SHED', 'READY')
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1;

\echo ''
\echo '=== 6. NOTHING CALLED under_amc EXISTS YET ==================================='
-- Confirms the column is genuinely new, and that no template-level flag is being mistaken for one.
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND (column_name ILIKE '%amc%'
       OR column_name ILIKE '%contract%'
       OR column_name ~* '(^|_)firm(_|$)')
ORDER BY table_name, column_name;

\echo ''
\echo '--- 6b. the template table, to prove AMC must NOT live here'
-- The same template is reused across every visit of a schedule, so a flag here would mark the
-- equipment AMC for every locomotive at once - the exact thing the requirement forbids.
SELECT count(*) AS templates,
       count(DISTINCT template_code) AS distinct_codes,
       (SELECT count(*) FROM shed_visit_checksheet_requirements r
         WHERE r.template_id = (SELECT min(id) FROM checksheet_templates)) AS visits_using_the_lowest_template_id
FROM checksheet_templates;

\echo ''
\echo '=== 7. MINOR SECTION-SIGNOFF EXPOSURE ========================================'
-- Which open MINOR visits are on the section workflow, since AMC changes section readiness there.
SELECT COALESCE(m.mode, '(none - legacy per-checksheet)') AS signoff_mode,
       count(DISTINCT sv.id)                              AS visits,
       count(DISTINCT r.id)                               AS requirements
FROM shed_visits sv
LEFT JOIN shed_visit_checksheet_signoff_mode m ON m.shed_visit_id = sv.id
LEFT JOIN shed_visit_checksheet_packages p ON p.shed_visit_id = sv.id
LEFT JOIN shed_visit_checksheet_requirements r ON r.package_id = p.id
WHERE sv.schedule_family = 'MINOR' AND sv.status IN ('IN_SHED', 'READY')
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== 8. SECTION SCOPE FOR SUPERVISOR AUTHORIZATION ============================'
-- A Supervisor may toggle AMC only within their own section, so the requirement row must carry a
-- section. Any row with a NULL section_id_snapshot could not be scope-checked and needs a decision.
SELECT count(*)                                              AS requirements,
       count(*) FILTER (WHERE section_id_snapshot IS NOT NULL) AS with_section,
       count(*) FILTER (WHERE section_id_snapshot IS NULL)     AS WITHOUT_section,
       count(DISTINCT section_id_snapshot)                   AS distinct_sections
FROM shed_visit_checksheet_requirements;

\echo ''
\echo '--- 8b. which stages/families have section-less requirements'
SELECT sv.schedule_family, COALESCE(r.workflow_stage_type, '(none)') AS stage, count(*) AS rows
FROM shed_visit_checksheet_requirements r
JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
JOIN shed_visits sv ON sv.id = p.shed_visit_id
WHERE r.section_id_snapshot IS NULL
GROUP BY 1, 2 ORDER BY 3 DESC;

\echo ''
\echo '=== 9. MIGRATION NUMBERING CHECK ============================================='
-- The AMC migration must not collide. 013 (the deletion ledger) is NOT yet applied, so confirm
-- whether its tables exist before choosing a number.
SELECT 'admin_deletion_event exists (migration 013 applied?)' AS fact,
       EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='admin_deletion_event')::text AS value
UNION ALL
SELECT 'shed_visit_checksheet_signoff_mode exists (BL 076 applied)',
       EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='shed_visit_checksheet_signoff_mode')::text;

\echo ''
\echo '=== AUDIT COMPLETE - nothing was modified ===================================='
