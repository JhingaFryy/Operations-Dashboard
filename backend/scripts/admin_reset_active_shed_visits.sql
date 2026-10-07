-- admin_reset_active_shed_visits.sql   -- PREPARED, NOT APPLIED. Run only after explicit approval.
--
-- PURPOSE
-- Administratively close every CURRENTLY ACTIVE shed visit (IN_SHED / READY) ahead of a clean
-- testing cycle, so no locomotive is left active in shed.
--
-- THIS IS NOT migrations/007_reset_active_shed_visits.sql. That migration DELETES active visits
-- and everything hanging off them. This script deletes NOTHING: every visit, event, stage,
-- booking, assignment, work package and requirement row stays exactly where it is, and the visits
-- simply stop being active. Do not run 007 for this purpose.
--
-- WHAT IT WRITES, per targeted visit:
--   shed_visits.status           -> 'CLOSED'
--   shed_visits.departed_at      -> the reset instant (now())            [see WHY A TIMESTAMP]
--   shed_visits.departure_source -> 'SYSTEM'                             [not 'DASHBOARD']
--   shed_visits.updated_at       -> now()
--   one shed_visit_events row    -> 'MANUAL_CORRECTION', source 'SYSTEM', carrying the reason and
--                                   the visit's pre-reset state, so the audit trail says plainly
--                                   that this was an administrative test-environment reset and
--                                   NOT a real Shed Out.
--
-- WHAT IT NEVER TOUCHES:
--   * arrival_at, schedule_started_at, inspection_completed_at, ready_at, ready_source - every
--     historical timestamp is preserved exactly as recorded.
--   * shed_visit_stages - left at whatever they genuinely reached (PENDING / IN_PROGRESS /
--     COMPLETED). Nothing is marked completed or skipped; no stage timing is invented.
--   * bookings, booking_events, booking_section_assignments - untouched, including the one OPEN
--     assignment (see OPEN WORK below).
--   * shed_visit_checksheet_packages / _requirements - the frozen record of what each visit
--     required stays intact.
--   * checksheet_header and every other BL-DCMS table - untouched.
--   * CLOSED visits, locomotives, users, sections, templates, applicability - untouched.
--   * No sequence is reset, no table is truncated, no row is deleted anywhere.
--
-- WHY A TIMESTAMP IS WRITTEN AT ALL
-- chk_closed_has_departure and chk_departure_timestamp_source require a CLOSED visit to carry
-- departed_at together with departure_source. departed_at is therefore set to the moment of the
-- reset - which is true: that is when the visit stopped being active. It is a NEW value on a
-- column that is currently NULL for every target, never an edit of an existing timestamp.
-- 'SYSTEM' (an existing allowed value) distinguishes it from a real 'DASHBOARD' Shed Out.
--
-- OPEN WORK LEFT BEHIND (deliberate, and the reason this is a decision and not a default):
-- visit 13 has one OPEN LOG_BOOK booking with one OPEN section assignment. Closing the visit does
-- not attend it, because marking work attended that nobody attended would be fabrication. It will
-- remain visible in that section's booking pool, attached to a closed visit.
--
-- SAFETY
-- One transaction. Every assumption is asserted inside it; any mismatch aborts with nothing
-- written. Re-running it is a no-op once no active visits remain.
--
-- SUBMITTED WORK GUARD
-- If any visit being reset has a SUBMITTED / UNDER_REVIEW / APPROVED checksheet, the script
-- ABORTS by default: that is real inspection evidence, and closing its visit administratively is
-- a decision for a human, not a default. To proceed after deciding that the work is test data,
-- pass -v allow_submitted_checksheets=1 explicitly. The checksheets are NEVER deleted or altered
-- either way; the override only permits closing the visit they belong to, and every audit event
-- records how many submitted checksheets its visit had and that the override was used.
--
-- HOW TO RUN (psql does not accept SQLAlchemy's "+psycopg" driver suffix, so strip it; the URL is
-- read from the backend's .env and never echoed):
--
--   cd /opt/operations-dashboard/backend
--   PGURL="$(grep -E '^RDCMS_DATABASE_URL=' .env | cut -d= -f2- | sed 's#^postgresql+psycopg://#postgresql://#')"
--   psql "$PGURL" -v ON_ERROR_STOP=1 -f scripts/admin_reset_active_shed_visits.sql
--   # ...or, after deciding the submitted checksheets are test data:
--   psql "$PGURL" -v ON_ERROR_STOP=1 -v allow_submitted_checksheets=1 -f scripts/admin_reset_active_shed_visits.sql

\set reason 'Administrative test-environment reset ahead of a clean testing cycle. Not a Shed Out.'
\if :{?allow_submitted_checksheets}
\else
  \set allow_submitted_checksheets 0
\endif

BEGIN;

-- Transaction-local, so the DO blocks below can read the operator's choice.
SELECT set_config('admin_reset.allow_submitted_checksheets', :'allow_submitted_checksheets', true);

CREATE TEMP TABLE _reset_targets ON COMMIT DROP AS
    SELECT id, loco_number, schedule_family, schedule_variant, status AS status_before,
           arrival_at, schedule_started_at, inspection_completed_at, ready_at
    FROM shed_visits
    WHERE status IN ('IN_SHED', 'READY')
    FOR UPDATE;

-- Pre-reset picture, printed into the run log as the audit record.
SELECT 'PRE  active visits'        AS label, count(*) AS n FROM _reset_targets
UNION ALL SELECT 'PRE  CLOSED visits (preserved)', count(*) FROM shed_visits WHERE status = 'CLOSED'
UNION ALL SELECT 'PRE  bookings on targets',       count(*) FROM bookings WHERE shed_visit_id IN (SELECT id FROM _reset_targets)
UNION ALL SELECT 'PRE  open assignments on targets', count(*) FROM booking_section_assignments a
                                                     JOIN bookings b ON b.id = a.booking_id
                                                     WHERE b.shed_visit_id IN (SELECT id FROM _reset_targets)
                                                       AND a.status IN ('OPEN', 'IN_PROGRESS', 'REOPENED')
UNION ALL SELECT 'PRE  work packages on targets',  count(*) FROM shed_visit_checksheet_packages WHERE shed_visit_id IN (SELECT id FROM _reset_targets)
UNION ALL SELECT 'PRE  requirements on targets',   count(*) FROM shed_visit_checksheet_requirements r
                                                     JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
                                                     WHERE p.shed_visit_id IN (SELECT id FROM _reset_targets)
UNION ALL SELECT 'PRE  checksheets on targets',    count(*) FROM checksheet_header WHERE shed_visit_id IN (SELECT id FROM _reset_targets)
UNION ALL SELECT 'PRE  checksheet_header (all)',   count(*) FROM checksheet_header;

-- Guard: this script is for an environment where the active visits are test/in-flight data. If any
-- target has real submitted checksheet work hanging off it, stop and let a human decide.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n
  FROM checksheet_header
  WHERE shed_visit_id IN (SELECT id FROM _reset_targets)
    AND status IN ('SUBMITTED', 'UNDER_REVIEW', 'APPROVED');
  IF n > 0 AND current_setting('admin_reset.allow_submitted_checksheets') <> '1' THEN
    RAISE EXCEPTION 'ABORTING: % submitted/under-review/approved checksheet(s) belong to the '
                    'visits being reset. Nothing was written. Re-audit, and only if that work is '
                    'test data re-run with -v allow_submitted_checksheets=1.', n;
  END IF;
  IF n > 0 THEN
    RAISE NOTICE 'Proceeding with % submitted checksheet(s) on reset visits: operator override given. '
                 'The checksheets themselves are not touched.', n;
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- The audit event FIRST, so a visit can never be closed without its trail.
-- ---------------------------------------------------------------------------
INSERT INTO shed_visit_events (shed_visit_id, event_type, event_time, source, event_data,
                               created_by, created_at)
SELECT t.id,
       'MANUAL_CORRECTION',
       now(),
       'SYSTEM',
       jsonb_build_object(
         'action', 'ADMIN_RESET_ACTIVE_VISIT',
         'reason', :'reason',
         'not_a_shed_out', true,
         'status_before', t.status_before,
         'schedule_family', t.schedule_family,
         'schedule_variant', t.schedule_variant,
         'arrival_at', t.arrival_at,
         'schedule_started_at', t.schedule_started_at,
         'inspection_completed_at', t.inspection_completed_at,
         'ready_at', t.ready_at,
         'gates_bypassed', jsonb_build_array('CHECKSHEET_COMPLETENESS', 'BOOKING_ATTENDANCE',
                                             'TEST_AFTER', 'READY'),
         'submitted_checksheets_on_visit', (
             SELECT count(*) FROM checksheet_header h
             WHERE h.shed_visit_id = t.id
               AND h.status IN ('SUBMITTED', 'UNDER_REVIEW', 'APPROVED')),
         'submitted_checksheet_override',
             current_setting('admin_reset.allow_submitted_checksheets') = '1'
       ),
       NULL,
       now()
FROM _reset_targets t;

UPDATE shed_visits v
   SET status           = 'CLOSED',
       departed_at      = now(),
       departure_source = 'SYSTEM',
       updated_at       = now()
  FROM _reset_targets t
 WHERE v.id = t.id;

-- ---------------------------------------------------------------------------
-- Post-reset assertions - any violation aborts the whole transaction.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  v_active int; v_targets int; v_closed_targets int; v_events int;
  v_stages int; v_bookings int; v_packages int; v_reqs int; v_headers int;
BEGIN
  SELECT count(*) INTO v_targets FROM _reset_targets;

  SELECT count(*) INTO v_active FROM shed_visits WHERE status IN ('IN_SHED', 'READY');
  IF v_active <> 0 THEN
    RAISE EXCEPTION 'Active shed visits remain after reset: %', v_active;
  END IF;

  -- Every target still EXISTS, now CLOSED: history preserved, not purged.
  SELECT count(*) INTO v_closed_targets FROM shed_visits
   WHERE id IN (SELECT id FROM _reset_targets) AND status = 'CLOSED';
  IF v_closed_targets <> v_targets THEN
    RAISE EXCEPTION 'Expected % reset visits to survive as CLOSED, found %', v_targets, v_closed_targets;
  END IF;

  -- One audit event per reset visit.
  SELECT count(*) INTO v_events FROM shed_visit_events
   WHERE shed_visit_id IN (SELECT id FROM _reset_targets)
     AND event_type = 'MANUAL_CORRECTION'
     AND event_data->>'action' = 'ADMIN_RESET_ACTIVE_VISIT';
  IF v_events <> v_targets THEN
    RAISE EXCEPTION 'Expected % audit events, found %', v_targets, v_events;
  END IF;

  -- Nothing hanging off those visits was removed.
  SELECT count(*) INTO v_stages   FROM shed_visit_stages WHERE shed_visit_id IN (SELECT id FROM _reset_targets);
  SELECT count(*) INTO v_bookings FROM bookings          WHERE shed_visit_id IN (SELECT id FROM _reset_targets);
  SELECT count(*) INTO v_packages FROM shed_visit_checksheet_packages WHERE shed_visit_id IN (SELECT id FROM _reset_targets);
  SELECT count(*) INTO v_reqs     FROM shed_visit_checksheet_requirements r
                                    JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
                                    WHERE p.shed_visit_id IN (SELECT id FROM _reset_targets);
  SELECT count(*) INTO v_headers  FROM checksheet_header;
  RAISE NOTICE 'Reset OK: % visit(s) closed administratively. Preserved: % stage(s), % booking(s), '
               '% package(s), % requirement(s), % checksheet_header row(s).',
               v_targets, v_stages, v_bookings, v_packages, v_reqs, v_headers;
END $$;

-- Post-reset picture for the run log.
SELECT v.id, v.loco_number, v.schedule_family, v.schedule_variant, v.status,
       v.arrival_at, v.schedule_started_at, v.inspection_completed_at, v.ready_at,
       v.departed_at, v.departure_source
  FROM shed_visits v
 WHERE v.id IN (SELECT id FROM _reset_targets)
 ORDER BY v.id;

COMMIT;
