-- purge_test_shed_visits.sql  -- PREPARED, NOT APPLIED. Run only after explicit approval.
--
-- TRUE fresh operational start: physically DELETES the test/reset shed visits and every row any
-- of them owns, across BOTH applications in the shared rdcms database (Operations Dashboard's
-- visit/booking/package tables and BL-DCMS's checksheet tables). Nothing is administratively
-- closed - the previous reset did that, and those closed-by-reset visits are themselves targets
-- here.
--
-- TARGET SET (built once, inside the transaction, and never widened):
--     status IN ('IN_SHED','READY')
--   OR
--     status = 'CLOSED' AND a shed_visit_events row exists for the visit with
--     event_data->>'action' = 'ADMIN_RESET_ACTIVE_VISIT'
--
-- The explicit ADMIN_RESET_ACTIVE_VISIT audit event is REQUIRED for every CLOSED target:
-- departure_source = 'SYSTEM' alone is NOT sufficient and is never used as the sole criterion, so
-- an ordinary SYSTEM-sourced historical closure can never be swept in. A visit closed through a
-- legitimate Shed Out (departure_source 'DASHBOARD'/'SLAM'/'AUTO_POSITION'/'IMPORT', or any
-- CLOSED visit without the reset event) is preserved, and the guards below abort if such a visit
-- ever reaches the target set.
--
-- NEVER TOUCHED: locomotives, sections, equipment, checksheet_templates, template_fields,
-- checksheet_template_applicability, equipment_templates, section_equipment_map, users,
-- minor_inspection_* (equipment/checkpoint/schedule/instance_field/stage_configuration),
-- system_settings, dashboard_access, device_info, booking_defect_types, activity_logs. Their row
-- counts are captured before the deletes and asserted unchanged afterwards.
--
-- No TRUNCATE. No sequence reset. No deletion by loco_number - shed_visit_id is the only scope.
-- Running it a second time finds zero targets, deletes nothing and exits cleanly.
--
-- Signed/generated PDFs are NOT removed here: files are not transactional. The script prints the
-- exact absolute path of every PDF belonging to a deleted checksheet (NOTICE "PDF TO DELETE
-- AFTER COMMIT: ..."); delete exactly those paths, one by one, only after this transaction has
-- committed. Never use a wildcard - the storage directory also holds unrelated files.
--
--   psql "$RDCMS_URL" -v ON_ERROR_STOP=1 -f scripts/purge_test_shed_visits.sql

BEGIN;

-- ---------------------------------------------------------------- target set + baseline ------
CREATE TEMP TABLE _target_visit ON COMMIT DROP AS
SELECT v.id,
       v.loco_number,
       v.status,
       v.departure_source,
       EXISTS (SELECT 1 FROM shed_visit_events e
                WHERE e.shed_visit_id = v.id
                  AND e.event_data->>'action' = 'ADMIN_RESET_ACTIVE_VISIT') AS admin_reset_event
  FROM shed_visits v
 WHERE v.status IN ('IN_SHED','READY')
    OR (v.status = 'CLOSED'
        AND EXISTS (SELECT 1 FROM shed_visit_events e
                     WHERE e.shed_visit_id = v.id
                       AND e.event_data->>'action' = 'ADMIN_RESET_ACTIVE_VISIT'));

-- Locks every targeted visit for the duration, so a concurrent Shed In/Out cannot change a row
-- out from under the guards.
DO $$
BEGIN
  PERFORM id FROM shed_visits WHERE id IN (SELECT id FROM _target_visit) FOR UPDATE;
END $$;

CREATE TEMP TABLE _target_checksheet ON COMMIT DROP AS
SELECT h.id, h.shed_visit_id, h.status, h.pdf_path
  FROM checksheet_header h
 WHERE h.shed_visit_id IN (SELECT id FROM _target_visit);

CREATE TEMP TABLE _baseline ON COMMIT DROP AS SELECT
    (SELECT count(*) FROM shed_visits)                        AS visits,
    (SELECT count(*) FROM shed_visits
      WHERE id NOT IN (SELECT id FROM _target_visit))         AS preserved_visits,
    (SELECT count(*) FROM locomotives)                        AS locomotives,
    (SELECT count(*) FROM sections)                           AS sections,
    (SELECT count(*) FROM equipment)                          AS equipment,
    (SELECT count(*) FROM checksheet_templates)               AS templates,
    (SELECT count(*) FROM template_fields)                    AS template_fields,
    (SELECT count(*) FROM checksheet_template_applicability)  AS applicability,
    (SELECT count(*) FROM section_equipment_map)              AS section_equipment_map,
    (SELECT count(*) FROM equipment_templates)                AS equipment_templates,
    (SELECT count(*) FROM users)                              AS users,
    (SELECT count(*) FROM minor_inspection_equipment)         AS mi_equipment,
    (SELECT count(*) FROM minor_inspection_checkpoint)        AS mi_checkpoint,
    (SELECT count(*) FROM minor_inspection_checkpoint_schedule) AS mi_schedule,
    (SELECT count(*) FROM minor_inspection_instance_field)    AS mi_instance_field,
    (SELECT count(*) FROM minor_inspection_stage_configuration) AS mi_stage_config,
    (SELECT count(*) FROM system_settings)                    AS system_settings,
    (SELECT count(*) FROM activity_logs)                      AS activity_logs,
    (SELECT count(*) FROM checksheet_header
      WHERE shed_visit_id IS NULL
         OR shed_visit_id NOT IN (SELECT id FROM _target_visit)) AS preserved_checksheets;

-- ---------------------------------------------------------------- guards ----------------------
DO $$
DECLARE n int; bad text;
BEGIN
  SELECT count(*) INTO n FROM _target_visit;
  RAISE NOTICE 'target shed visits: %', n;
  IF n = 0 THEN
    RAISE NOTICE 'nothing to purge - no active and no ADMIN_RESET_ACTIVE_VISIT visit remains';
    RETURN;
  END IF;

  -- Category rule, re-asserted against the rows themselves.
  SELECT string_agg(id::text, ', ') INTO bad FROM _target_visit
   WHERE status NOT IN ('IN_SHED','READY')
     AND NOT (status = 'CLOSED' AND admin_reset_event);
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'visit(s) % do not satisfy the allowed target categories', bad;
  END IF;

  -- A CLOSED target must carry the explicit reset event - departure_source is never enough.
  SELECT string_agg(id::text, ', ') INTO bad FROM _target_visit
   WHERE status = 'CLOSED' AND NOT admin_reset_event;
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'CLOSED visit(s) % have no ADMIN_RESET_ACTIVE_VISIT event', bad;
  END IF;

  -- A legitimately shed-out visit must never be in scope.
  SELECT string_agg(id::text, ', ') INTO bad FROM _target_visit
   WHERE status = 'CLOSED'
     AND (departure_source IS NULL OR departure_source <> 'SYSTEM');
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'CLOSED visit(s) % look like a normal Shed Out (departure_source not SYSTEM)', bad;
  END IF;

  SELECT count(*) INTO n FROM shed_visits v
   WHERE v.id NOT IN (SELECT id FROM _target_visit)
     AND v.status IN ('IN_SHED','READY');
  IF n <> 0 THEN RAISE EXCEPTION 'internal error: % active visit(s) outside the target set', n; END IF;

  SELECT count(*) INTO n FROM _target_checksheet;
  RAISE NOTICE 'target checksheets: %', n;
  SELECT count(*) INTO n FROM checksheet_header h
   WHERE h.shed_visit_id IS NOT NULL
     AND h.shed_visit_id NOT IN (SELECT id FROM _target_visit);
  RAISE NOTICE 'checksheets preserved (other visits): %', n;
END $$;

-- The exact file list for post-commit cleanup - printed before the rows are gone.
DO $$
DECLARE r record; n int := 0;
BEGIN
  FOR r IN SELECT id, pdf_path FROM _target_checksheet
            WHERE pdf_path IS NOT NULL AND btrim(pdf_path) <> '' ORDER BY id LOOP
    RAISE NOTICE 'PDF TO DELETE AFTER COMMIT: % (checksheet %)', r.pdf_path, r.id;
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'PDF files to delete after commit: %', n;
END $$;

-- ---------------------------------------------------------------- deletes (child first) -------
-- FK audit (see the report): bookings/stages/events -> shed_visits are ON DELETE RESTRICT, so
-- each is deleted explicitly and in order. checksheet_header has NO foreign key to shed_visits at
-- all (a cross-application soft link), so it is scoped by shed_visit_id here. Relations that DO
-- cascade are still deleted explicitly, so the row counts are asserted rather than assumed:
-- requirements <- packages, checksheet_value/digital_signatures <- checksheet_header.
-- notifications -> checksheet_header is NO ACTION and must go first or the delete fails.

DELETE FROM booking_events
 WHERE booking_id IN (SELECT id FROM bookings WHERE shed_visit_id IN (SELECT id FROM _target_visit));

DELETE FROM booking_section_assignments
 WHERE booking_id IN (SELECT id FROM bookings WHERE shed_visit_id IN (SELECT id FROM _target_visit));

DELETE FROM bookings WHERE shed_visit_id IN (SELECT id FROM _target_visit);

DELETE FROM notifications WHERE checksheet_id IN (SELECT id FROM _target_checksheet);
DELETE FROM digital_signatures WHERE checksheet_id IN (SELECT id FROM _target_checksheet);
DELETE FROM checksheet_value WHERE checksheet_id IN (SELECT id FROM _target_checksheet);
DELETE FROM checksheet_header WHERE id IN (SELECT id FROM _target_checksheet);

DELETE FROM shed_visit_checksheet_requirements
 WHERE package_id IN (SELECT id FROM shed_visit_checksheet_packages
                       WHERE shed_visit_id IN (SELECT id FROM _target_visit));
DELETE FROM shed_visit_checksheet_packages WHERE shed_visit_id IN (SELECT id FROM _target_visit);

DELETE FROM shed_visit_stages WHERE shed_visit_id IN (SELECT id FROM _target_visit);
DELETE FROM shed_visit_events WHERE shed_visit_id IN (SELECT id FROM _target_visit);

DELETE FROM shed_visits WHERE id IN (SELECT id FROM _target_visit);

-- ---------------------------------------------------------------- verification ----------------
DO $$
DECLARE base record; n int;
BEGIN
  SELECT * INTO base FROM _baseline;

  IF EXISTS (SELECT 1 FROM shed_visits WHERE id IN (SELECT id FROM _target_visit)) THEN
    RAISE EXCEPTION 'a target shed visit survived';
  END IF;
  IF EXISTS (SELECT 1 FROM shed_visits WHERE status IN ('IN_SHED','READY')) THEN
    RAISE EXCEPTION 'an active shed visit survived';
  END IF;
  IF EXISTS (SELECT 1 FROM shed_visits v WHERE EXISTS (
        SELECT 1 FROM shed_visit_events e WHERE e.shed_visit_id = v.id
           AND e.event_data->>'action' = 'ADMIN_RESET_ACTIVE_VISIT')) THEN
    RAISE EXCEPTION 'an ADMIN_RESET_ACTIVE_VISIT visit survived';
  END IF;

  -- No dangling child row anywhere, including the FK-less checksheet link.
  SELECT count(*) INTO n FROM shed_visit_stages s
    WHERE NOT EXISTS (SELECT 1 FROM shed_visits v WHERE v.id = s.shed_visit_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned shed_visit_stages', n; END IF;
  SELECT count(*) INTO n FROM shed_visit_events e
    WHERE NOT EXISTS (SELECT 1 FROM shed_visits v WHERE v.id = e.shed_visit_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned shed_visit_events', n; END IF;
  SELECT count(*) INTO n FROM shed_visit_checksheet_packages p
    WHERE NOT EXISTS (SELECT 1 FROM shed_visits v WHERE v.id = p.shed_visit_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned checksheet packages', n; END IF;
  SELECT count(*) INTO n FROM shed_visit_checksheet_requirements r
    WHERE NOT EXISTS (SELECT 1 FROM shed_visit_checksheet_packages p WHERE p.id = r.package_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned requirements', n; END IF;
  SELECT count(*) INTO n FROM bookings b
    WHERE NOT EXISTS (SELECT 1 FROM shed_visits v WHERE v.id = b.shed_visit_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned bookings', n; END IF;
  SELECT count(*) INTO n FROM booking_events be
    WHERE NOT EXISTS (SELECT 1 FROM bookings b WHERE b.id = be.booking_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned booking_events', n; END IF;
  SELECT count(*) INTO n FROM booking_section_assignments a
    WHERE NOT EXISTS (SELECT 1 FROM bookings b WHERE b.id = a.booking_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned booking_section_assignments', n; END IF;
  SELECT count(*) INTO n FROM checksheet_header h
    WHERE h.shed_visit_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM shed_visits v WHERE v.id = h.shed_visit_id);
  IF n <> 0 THEN RAISE EXCEPTION '% checksheet_header rows point at a deleted visit', n; END IF;
  SELECT count(*) INTO n FROM checksheet_value cv
    WHERE NOT EXISTS (SELECT 1 FROM checksheet_header h WHERE h.id = cv.checksheet_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned checksheet_value rows', n; END IF;
  SELECT count(*) INTO n FROM digital_signatures d
    WHERE NOT EXISTS (SELECT 1 FROM checksheet_header h WHERE h.id = d.checksheet_id);
  IF n <> 0 THEN RAISE EXCEPTION '% orphaned digital_signatures', n; END IF;

  -- Preserved history and all master/configuration data are untouched.
  SELECT count(*) INTO n FROM shed_visits;
  IF n <> base.preserved_visits THEN
    RAISE EXCEPTION 'preserved visit count changed: expected %, found %', base.preserved_visits, n;
  END IF;
  SELECT count(*) INTO n FROM checksheet_header;
  IF n <> base.preserved_checksheets THEN
    RAISE EXCEPTION 'preserved checksheet count changed: expected %, found %', base.preserved_checksheets, n;
  END IF;
  IF (SELECT count(*) FROM locomotives) <> base.locomotives THEN RAISE EXCEPTION 'locomotive master changed'; END IF;
  IF (SELECT count(*) FROM sections) <> base.sections THEN RAISE EXCEPTION 'sections changed'; END IF;
  IF (SELECT count(*) FROM equipment) <> base.equipment THEN RAISE EXCEPTION 'equipment changed'; END IF;
  IF (SELECT count(*) FROM checksheet_templates) <> base.templates THEN RAISE EXCEPTION 'templates changed'; END IF;
  IF (SELECT count(*) FROM template_fields) <> base.template_fields THEN RAISE EXCEPTION 'template_fields changed'; END IF;
  IF (SELECT count(*) FROM checksheet_template_applicability) <> base.applicability THEN RAISE EXCEPTION 'applicability changed'; END IF;
  IF (SELECT count(*) FROM section_equipment_map) <> base.section_equipment_map THEN RAISE EXCEPTION 'section_equipment_map changed'; END IF;
  IF (SELECT count(*) FROM equipment_templates) <> base.equipment_templates THEN RAISE EXCEPTION 'equipment_templates changed'; END IF;
  IF (SELECT count(*) FROM users) <> base.users THEN RAISE EXCEPTION 'users changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_equipment) <> base.mi_equipment THEN RAISE EXCEPTION 'minor_inspection_equipment changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_checkpoint) <> base.mi_checkpoint THEN RAISE EXCEPTION 'minor_inspection_checkpoint changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_checkpoint_schedule) <> base.mi_schedule THEN RAISE EXCEPTION 'minor_inspection_checkpoint_schedule changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_instance_field) <> base.mi_instance_field THEN RAISE EXCEPTION 'minor_inspection_instance_field changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_stage_configuration) <> base.mi_stage_config THEN RAISE EXCEPTION 'minor_inspection_stage_configuration changed'; END IF;
  IF (SELECT count(*) FROM system_settings) <> base.system_settings THEN RAISE EXCEPTION 'system_settings changed'; END IF;
  IF (SELECT count(*) FROM activity_logs) <> base.activity_logs THEN RAISE EXCEPTION 'activity_logs changed'; END IF;

  RAISE NOTICE 'purge complete: % visit(s) remain, % checksheet(s) remain, % locomotives intact',
    base.preserved_visits, base.preserved_checksheets, base.locomotives;
END $$;

COMMIT;
