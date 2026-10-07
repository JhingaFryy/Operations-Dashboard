-- purge_all_operational_data.sql  -- PREPARED, NOT APPLIED. Run only after explicit approval.
--
-- TRUE fresh operational start: deletes EVERY shed visit and everything a visit owns, across both
-- applications in the shared rdcms database - regardless of status (IN_SHED, READY, CLOSED),
-- schedule family, variant, or whether the visit looks like a test. That total scope is deliberate
-- and is what distinguishes this script from scripts/purge_test_shed_visits.sql, which targeted
-- only active and ADMIN_RESET_ACTIVE_VISIT visits and must not be used for a full reset.
--
-- DELETED (operational):
--   shed_visits, shed_visit_events, shed_visit_stages,
--   shed_visit_checksheet_packages, shed_visit_checksheet_requirements,
--   bookings, booking_section_assignments, booking_events,
--   checksheet_header, checksheet_value, digital_signatures, notifications (checksheet-linked).
--
-- PRESERVED (master / configuration / identity), asserted row-for-row after the deletes:
--   locomotives, sections, equipment, section_equipment_map, equipment_templates,
--   checksheet_templates, template_fields, checksheet_template_applicability,
--   minor_inspection_equipment / _checkpoint / _checkpoint_schedule / _instance_field /
--   _stage_configuration, users, user_sessions, dashboard_access, device_info, system_settings,
--   booking_defect_types, activity_logs.
-- The Loco Master database is a separate database and is not touched by this script at all.
--
-- Confirmation is mandatory - the script refuses to run without it:
--   psql "$PGURL" -X -v ON_ERROR_STOP=1 -v confirm=PURGE-ALL-OPERATIONAL-DATA \
--        -f scripts/purge_all_operational_data.sql
--
-- No TRUNCATE, no sequence reset, no DROP. Signed/generated PDFs are NOT removed here: files are
-- not transactional. Every pdf_path of a deleted checksheet is printed as a NOTICE before the
-- delete; remove exactly those paths after the transaction commits, never by wildcard (the storage
-- directory also holds orphans from earlier resets).

-- Gate 1: the variable must be defined at all.
\if :{?confirm}
\else
\echo '*** REFUSED: re-run with -v confirm=PURGE-ALL-OPERATIONAL-DATA'
\quit
\endif

-- Gate 2: it must carry the exact phrase, not merely be defined. (A psql variable is not
-- substituted inside a dollar-quoted block, so this comparison is made here, before BEGIN.)
SELECT (:'confirm' <> 'PURGE-ALL-OPERATIONAL-DATA') AS wrong_phrase \gset
\if :wrong_phrase
\echo '*** REFUSED: confirm must be exactly PURGE-ALL-OPERATIONAL-DATA'
\quit
\endif

BEGIN;

-- ------------------------------------------------------------------ baseline --------------
CREATE TEMP TABLE _baseline ON COMMIT DROP AS SELECT
    (SELECT count(*) FROM locomotives)                          AS locomotives,
    (SELECT count(*) FROM sections)                             AS sections,
    (SELECT count(*) FROM equipment)                            AS equipment,
    (SELECT count(*) FROM section_equipment_map)                AS section_equipment_map,
    (SELECT count(*) FROM equipment_templates)                  AS equipment_templates,
    (SELECT count(*) FROM checksheet_templates)                 AS templates,
    (SELECT count(*) FROM template_fields)                      AS template_fields,
    (SELECT count(*) FROM checksheet_template_applicability)    AS applicability,
    (SELECT count(*) FROM minor_inspection_equipment)           AS mi_equipment,
    (SELECT count(*) FROM minor_inspection_checkpoint)          AS mi_checkpoint,
    (SELECT count(*) FROM minor_inspection_checkpoint_schedule) AS mi_schedule,
    (SELECT count(*) FROM minor_inspection_instance_field)      AS mi_instance_field,
    (SELECT count(*) FROM minor_inspection_stage_configuration) AS mi_stage_config,
    (SELECT count(*) FROM users)                                AS users,
    (SELECT count(*) FROM user_sessions)                        AS user_sessions,
    (SELECT count(*) FROM dashboard_access)                     AS dashboard_access,
    (SELECT count(*) FROM device_info)                          AS device_info,
    (SELECT count(*) FROM system_settings)                      AS system_settings,
    (SELECT count(*) FROM booking_defect_types)                 AS booking_defect_types,
    (SELECT count(*) FROM activity_logs)                        AS activity_logs;

CREATE TEMP TABLE _doomed_checksheet ON COMMIT DROP AS
SELECT id, shed_visit_id, status, pdf_path FROM checksheet_header;

-- Locks every visit for the duration so a concurrent Shed In/Out cannot race the purge.
DO $$
BEGIN
  PERFORM id FROM shed_visits FOR UPDATE;
END $$;

DO $$
DECLARE v record; r record; n int := 0;
BEGIN
  SELECT count(*) AS visits,
         count(*) FILTER (WHERE status = 'IN_SHED') AS in_shed,
         count(*) FILTER (WHERE status = 'READY') AS ready,
         count(*) FILTER (WHERE status = 'CLOSED') AS closed
    INTO v FROM shed_visits;
  RAISE NOTICE 'purging % shed visit(s): % IN_SHED, % READY, % CLOSED',
    v.visits, v.in_shed, v.ready, v.closed;
  RAISE NOTICE 'purging % checksheet(s), % value(s), % signature(s)',
    (SELECT count(*) FROM checksheet_header), (SELECT count(*) FROM checksheet_value),
    (SELECT count(*) FROM digital_signatures);

  FOR r IN SELECT id, pdf_path FROM _doomed_checksheet
            WHERE pdf_path IS NOT NULL AND btrim(pdf_path) <> '' ORDER BY id LOOP
    RAISE NOTICE 'PDF TO DELETE AFTER COMMIT: % (checksheet %)', r.pdf_path, r.id;
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'PDF files to delete after commit: %', n;
END $$;

-- ------------------------------------------------------------------ deletes ----------------
-- Child-first. bookings/stages/events -> shed_visits are ON DELETE RESTRICT and
-- notifications -> checksheet_header is NO ACTION, so each is deleted explicitly. Relations that
-- DO cascade (requirements <- packages, checksheet_value/digital_signatures <- checksheet_header,
-- packages <- shed_visits) are deleted explicitly too, so their row counts are asserted rather
-- than assumed. checksheet_header has NO foreign key to shed_visits (a cross-application soft
-- link), which is why the whole table goes rather than a visit-scoped subset.

DELETE FROM booking_events;
DELETE FROM booking_section_assignments;
DELETE FROM bookings;

DELETE FROM notifications WHERE checksheet_id IS NOT NULL;
DELETE FROM digital_signatures;
DELETE FROM checksheet_value;
DELETE FROM checksheet_header;

DELETE FROM shed_visit_checksheet_requirements;
DELETE FROM shed_visit_checksheet_packages;
DELETE FROM shed_visit_stages;
DELETE FROM shed_visit_events;
DELETE FROM shed_visits;

-- ------------------------------------------------------------------ verification -----------
DO $$
DECLARE base record;
BEGIN
  SELECT * INTO base FROM _baseline;

  IF (SELECT count(*) FROM shed_visits) <> 0 THEN RAISE EXCEPTION 'shed_visits survived'; END IF;
  IF (SELECT count(*) FROM shed_visit_events) <> 0 THEN RAISE EXCEPTION 'shed_visit_events survived'; END IF;
  IF (SELECT count(*) FROM shed_visit_stages) <> 0 THEN RAISE EXCEPTION 'shed_visit_stages survived'; END IF;
  IF (SELECT count(*) FROM shed_visit_checksheet_packages) <> 0 THEN RAISE EXCEPTION 'packages survived'; END IF;
  IF (SELECT count(*) FROM shed_visit_checksheet_requirements) <> 0 THEN RAISE EXCEPTION 'requirements survived'; END IF;
  IF (SELECT count(*) FROM bookings) <> 0 THEN RAISE EXCEPTION 'bookings survived'; END IF;
  IF (SELECT count(*) FROM booking_section_assignments) <> 0 THEN RAISE EXCEPTION 'booking assignments survived'; END IF;
  IF (SELECT count(*) FROM booking_events) <> 0 THEN RAISE EXCEPTION 'booking events survived'; END IF;
  IF (SELECT count(*) FROM checksheet_header) <> 0 THEN RAISE EXCEPTION 'checksheets survived'; END IF;
  IF (SELECT count(*) FROM checksheet_value) <> 0 THEN RAISE EXCEPTION 'checksheet values survived'; END IF;
  IF (SELECT count(*) FROM digital_signatures) <> 0 THEN RAISE EXCEPTION 'digital signatures survived'; END IF;
  IF (SELECT count(*) FROM notifications WHERE checksheet_id IS NOT NULL) <> 0 THEN
    RAISE EXCEPTION 'checksheet notifications survived'; END IF;

  -- Master / configuration / identity: every count must be byte-for-byte what it was.
  IF (SELECT count(*) FROM locomotives) <> base.locomotives THEN RAISE EXCEPTION 'locomotive master changed'; END IF;
  IF (SELECT count(*) FROM sections) <> base.sections THEN RAISE EXCEPTION 'sections changed'; END IF;
  IF (SELECT count(*) FROM equipment) <> base.equipment THEN RAISE EXCEPTION 'equipment changed'; END IF;
  IF (SELECT count(*) FROM section_equipment_map) <> base.section_equipment_map THEN RAISE EXCEPTION 'section_equipment_map changed'; END IF;
  IF (SELECT count(*) FROM equipment_templates) <> base.equipment_templates THEN RAISE EXCEPTION 'equipment_templates changed'; END IF;
  IF (SELECT count(*) FROM checksheet_templates) <> base.templates THEN RAISE EXCEPTION 'templates changed'; END IF;
  IF (SELECT count(*) FROM template_fields) <> base.template_fields THEN RAISE EXCEPTION 'template_fields changed'; END IF;
  IF (SELECT count(*) FROM checksheet_template_applicability) <> base.applicability THEN RAISE EXCEPTION 'applicability changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_equipment) <> base.mi_equipment THEN RAISE EXCEPTION 'minor inspection equipment changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_checkpoint) <> base.mi_checkpoint THEN RAISE EXCEPTION 'minor inspection checkpoints changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_checkpoint_schedule) <> base.mi_schedule THEN RAISE EXCEPTION 'minor inspection schedules changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_instance_field) <> base.mi_instance_field THEN RAISE EXCEPTION 'minor inspection instance fields changed'; END IF;
  IF (SELECT count(*) FROM minor_inspection_stage_configuration) <> base.mi_stage_config THEN RAISE EXCEPTION 'stage configuration changed'; END IF;
  IF (SELECT count(*) FROM users) <> base.users THEN RAISE EXCEPTION 'users changed'; END IF;
  IF (SELECT count(*) FROM user_sessions) <> base.user_sessions THEN RAISE EXCEPTION 'user sessions changed'; END IF;
  IF (SELECT count(*) FROM dashboard_access) <> base.dashboard_access THEN RAISE EXCEPTION 'dashboard access changed'; END IF;
  IF (SELECT count(*) FROM device_info) <> base.device_info THEN RAISE EXCEPTION 'device info changed'; END IF;
  IF (SELECT count(*) FROM system_settings) <> base.system_settings THEN RAISE EXCEPTION 'system settings changed'; END IF;
  IF (SELECT count(*) FROM booking_defect_types) <> base.booking_defect_types THEN RAISE EXCEPTION 'defect types changed'; END IF;
  IF (SELECT count(*) FROM activity_logs) <> base.activity_logs THEN RAISE EXCEPTION 'activity logs changed'; END IF;

  RAISE NOTICE 'purge complete: 0 operational rows remain; % locomotives, % templates, % template fields, % minor inspection equipment and % users preserved',
    base.locomotives, base.templates, base.template_fields, base.mi_equipment, base.users;
END $$;

COMMIT;
