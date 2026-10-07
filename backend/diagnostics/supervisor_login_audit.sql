-- Operations Dashboard: why some Supervisors are refused at login (READ ONLY).
--
-- THE LOGIN RULE, read off the code (app/services/auth_service.py:38-60):
--
--   1. no such employee_id, OR is_active is false/NULL   -> 401 "Invalid credentials"
--   2. password does not verify                          -> 401 "Invalid credentials"
--   3. role == 'Technician'                              -> 403
--   4. role == 'Supervisor' AND NOT has_dashboard_access -> 403  <-- the reported symptom
--   5. otherwise a token is issued
--
--   has_dashboard_access(user) = (role == 'Admin') OR (a dashboard_access row exists AND
--                                                      is_enabled is true)
--
-- Nothing else gates login. section_id, can_manage_equipment_mapping,
-- can_add_booking_sections, SHIFT membership and any movement permission are NOT consulted.
--
-- So a Supervisor seeing "You don't have permission to do that." (the frontend's generic 403
-- text) has no dashboard_access row, or has one with is_enabled = false. Section 1 proves it.
--
--   psql "$PGURL" -X -v ON_ERROR_STOP=1 -f diagnostics/supervisor_login_audit.sql
--
-- Writes nothing. No password hashes, tokens or secrets are selected anywhere below.

\echo '=== 1. Every Supervisor, and whether the login rule lets them in ==='
\echo '    can_login_now = what auth_service.login() will decide, reproduced exactly.'
SELECT u.employee_id,
       u.name,
       u.role,
       coalesce(u.is_active::text, 'NULL')                       AS is_active,
       s.name                                                    AS section,
       CASE WHEN da.id IS NULL THEN 'no row' ELSE da.is_enabled::text END AS dashboard_access,
       da.revoked_at,
       CASE
           WHEN u.is_active IS NOT TRUE                       THEN 'NO - 401 invalid credentials (inactive)'
           WHEN u.role = 'Technician'                         THEN 'NO - 403 technician'
           WHEN u.role = 'Supervisor'
                AND NOT coalesce(da.is_enabled, false)        THEN 'NO - 403 dashboard access not granted'
           ELSE 'YES'
       END                                                       AS can_login_now
FROM users u
LEFT JOIN sections s        ON s.id = u.section_id
LEFT JOIN dashboard_access da ON da.user_id = u.id
WHERE u.role = 'Supervisor'
ORDER BY can_login_now, u.employee_id;

\echo ''
\echo '=== 2. The A/B comparison, summarised ==='
SELECT CASE
           WHEN u.is_active IS NOT TRUE                THEN 'inactive'
           WHEN coalesce(da.is_enabled, false)         THEN 'CAN log in  (dashboard access enabled)'
           WHEN da.id IS NULL                          THEN 'CANNOT log in (no dashboard_access row at all)'
           ELSE                                             'CANNOT log in (dashboard_access row present but disabled)'
       END                                             AS outcome,
       count(*)                                        AS supervisors,
       count(*) FILTER (WHERE u.section_id IS NOT NULL) AS with_section,
       count(*) FILTER (WHERE u.section_id IS NULL)     AS without_section
FROM users u
LEFT JOIN dashboard_access da ON da.user_id = u.id
WHERE u.role = 'Supervisor'
GROUP BY 1
ORDER BY 1;

\echo ''
\echo '=== 3. Is a missing SECTION correlated with the failures? (it must NOT be) ==='
\echo '    Login never reads section_id. If every refused account also happens to lack a section'
\echo '    that is a coincidence of how the accounts were set up, not the cause.'
SELECT coalesce(da.is_enabled, false)              AS dashboard_access_enabled,
       (u.section_id IS NOT NULL)                  AS has_section,
       count(*)                                    AS supervisors
FROM users u
LEFT JOIN dashboard_access da ON da.user_id = u.id
WHERE u.role = 'Supervisor' AND u.is_active IS TRUE
GROUP BY 1, 2
ORDER BY 1, 2;

\echo ''
\echo '=== 4. Are the Admin-only capabilities correlated with the failures? (they must NOT be) ==='
\echo '    can_manage_equipment_mapping and can_add_booking_sections are page permissions.'
\echo '    Neither is consulted by login. This confirms it in the data.'
SELECT coalesce(da.is_enabled, false)                       AS dashboard_access_enabled,
       coalesce(da.can_manage_equipment_mapping, false)     AS can_manage_equipment_mapping,
       coalesce(da.can_add_booking_sections, false)         AS can_add_booking_sections,
       count(*)                                             AS supervisors
FROM users u
LEFT JOIN dashboard_access da ON da.user_id = u.id
WHERE u.role = 'Supervisor' AND u.is_active IS TRUE
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

\echo ''
\echo '=== 5. ROLE SPELLING - a separate, fail-OPEN problem ==='
\echo '    Login compares role with exact, case-sensitive equality and OD normalizes role'
\echo '    nowhere. A row spelled "supervisor" or "SUPERVISOR" matches NEITHER the Technician'
\echo '    check NOR the Supervisor check, so it skips the dashboard-access gate and logs IN.'
\echo '    Anything below other than the three canonical spellings needs attention.'
SELECT u.role,
       count(*)                                          AS users,
       count(*) FILTER (WHERE u.is_active IS TRUE)       AS active,
       CASE WHEN u.role IN ('Admin', 'Supervisor', 'Technician')
            THEN 'canonical'
            ELSE 'NON-CANONICAL - bypasses the Supervisor dashboard-access gate'
       END                                               AS assessment
FROM users u
GROUP BY u.role
ORDER BY 3 DESC, 1;

\echo ''
\echo '=== 6. Accounts whose is_active is NULL rather than true/false ==='
\echo '    The column is nullable. "not user.is_active" treats NULL as inactive, so these get'
\echo '    401 "Invalid credentials" - NOT the 403 message - which looks like a wrong password.'
SELECT employee_id, name, role
FROM users
WHERE is_active IS NULL
ORDER BY employee_id;
