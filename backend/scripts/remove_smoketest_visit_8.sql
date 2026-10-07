-- remove_smoketest_visit_8.sql  -- PREPARED, NOT APPLIED. Run only after explicit approval.
--
-- Target: shed visit 8 - loco 22560 (WAP-4, CONVENTIONAL), MINOR/IA0, created 2026-09-14 09:42 IST
-- by ELSBL during the SCHEDULE_STARTED CheckViolation investigation (the acknowledged leftover
-- smoke-test visit). Audited 2026-09-14: 0 bookings, 0 checksheet packages/requirements,
-- 0 checksheet_header rows; 2 shed_visit_events (SHED_IN, SCHEDULE_STARTED); 3 shed_visit_stages
-- (TEST_BEFORE IN_PROGRESS, SCHEDULE_INSPECTION PENDING, TEST_AFTER PENDING).
--
-- Visit 10 (22236, IC, created 10:34 by the operator) is NOT targeted.
--
-- Same child-first pattern as scripts/remove_smoketest_visit_7.sql. Every assumption is asserted
-- inside the transaction; any mismatch aborts with nothing deleted. Back up the 3 affected tables'
-- rows for visit 8 first.
--
--   psql ... -v ON_ERROR_STOP=1 -f scripts/remove_smoketest_visit_8.sql

BEGIN;

DO $$
DECLARE v record; n int;
BEGIN
  SELECT * INTO v FROM shed_visits WHERE id = 8 FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'visit 8 not found'; END IF;
  IF v.loco_number <> '22560' OR v.schedule_family <> 'MINOR' OR v.schedule_variant <> 'IA0' OR v.status <> 'IN_SHED' THEN
    RAISE EXCEPTION 'visit 8 is not the audited smoke-test visit (% % % %)', v.loco_number, v.schedule_family, v.schedule_variant, v.status;
  END IF;
  SELECT count(*) INTO n FROM bookings WHERE shed_visit_id = 8;
  IF n <> 0 THEN RAISE EXCEPTION 'visit 8 now has % booking(s) - real work, aborting', n; END IF;
  SELECT count(*) INTO n FROM shed_visit_checksheet_packages WHERE shed_visit_id = 8;
  IF n <> 0 THEN RAISE EXCEPTION 'visit 8 now has a checksheet package - aborting'; END IF;
  SELECT count(*) INTO n FROM checksheet_header WHERE shed_visit_id = 8;
  IF n <> 0 THEN RAISE EXCEPTION 'visit 8 now has % checksheet(s) - aborting', n; END IF;
  SELECT count(*) INTO n FROM shed_visit_events WHERE shed_visit_id = 8;
  IF n <> 2 THEN RAISE EXCEPTION 'expected 2 events for visit 8, found %', n; END IF;
  SELECT count(*) INTO n FROM shed_visit_stages WHERE shed_visit_id = 8;
  IF n <> 3 THEN RAISE EXCEPTION 'expected 3 stages for visit 8, found %', n; END IF;
END $$;

DELETE FROM shed_visit_events WHERE shed_visit_id = 8;
DELETE FROM shed_visit_stages WHERE shed_visit_id = 8;
DELETE FROM shed_visits WHERE id = 8;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM shed_visits WHERE id = 8) THEN RAISE EXCEPTION 'visit 8 still present'; END IF;
  IF NOT EXISTS (SELECT 1 FROM shed_visits WHERE id IN (9, 10, 11)) THEN RAISE EXCEPTION 'unrelated visits missing'; END IF;
END $$;

COMMIT;
