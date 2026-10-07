-- remove_smoketest_visit_7.sql
--
-- One-off removal of the shed visit created solely to smoke-test production after the
-- active-visit reset (migration 007). Deliberately NOT a numbered migration: it changes data, not
-- schema, and targets one identified row.
--
-- IDENTIFICATION (audited read-only immediately before this was written):
--   * shed_visits held exactly ONE row in the entire table - this one.
--   * shed_visits_id_seq last_value = 7: visits 4/5/6 were removed by migration 007, and this is
--     the only visit created since.
--   * Created 2026-09-12 16:03 by ELSBL (the System Administrator account), after the reset and
--     after the Admin authorization correction, whose report recommended exactly this smoke test.
--     Admin is not the routine production Shed In actor.
--   * Schedule never started; no checksheet_header rows reference it.
--   * Its single booking ("Battery box damaged", LOG_BOOK) is the only booking in the whole table.
--
-- Same child-first pattern as migration 007: every FK child deleted explicitly, no CASCADE, and
-- every identity assumption re-asserted INSIDE the transaction so a changed database aborts
-- rather than deleting the wrong thing.

BEGIN;

DO $$
DECLARE v_loco text; v_family text; v_variant text; v_status text; v_by text; v_hdr int;
BEGIN
  SELECT sv.loco_number, sv.schedule_family, sv.schedule_variant, sv.status, u.employee_id
    INTO v_loco, v_family, v_variant, v_status, v_by
    FROM shed_visits sv LEFT JOIN users u ON u.id = sv.created_by
   WHERE sv.id = 7;
  IF NOT FOUND THEN RAISE EXCEPTION 'Visit 7 not found - nothing to remove; aborting.'; END IF;
  IF v_loco <> '39014' OR v_family <> 'MINOR' OR v_variant <> 'IA' OR v_status <> 'IN_SHED'
     OR coalesce(v_by,'') <> 'ELSBL' THEN
    RAISE EXCEPTION 'Visit 7 no longer matches the audited smoke-test visit (%/%/%/%/%) - aborting.',
      v_loco, v_family, v_variant, v_status, v_by;
  END IF;
  SELECT count(*) INTO v_hdr FROM checksheet_header WHERE shed_visit_id = 7;
  IF v_hdr <> 0 THEN
    RAISE EXCEPTION 'Visit 7 now has % checksheet(s) - real work may exist; aborting.', v_hdr;
  END IF;
END $$;

CREATE TEMP TABLE _b ON COMMIT DROP AS SELECT id FROM bookings WHERE shed_visit_id = 7;

SELECT 'PRE booking_events' label, count(*) FROM booking_events WHERE booking_id IN (SELECT id FROM _b)
UNION ALL SELECT 'PRE booking_section_assignments', count(*) FROM booking_section_assignments WHERE booking_id IN (SELECT id FROM _b)
UNION ALL SELECT 'PRE bookings', count(*) FROM _b
UNION ALL SELECT 'PRE checksheet_requirements', count(*) FROM shed_visit_checksheet_requirements
               WHERE package_id IN (SELECT id FROM shed_visit_checksheet_packages WHERE shed_visit_id = 7)
UNION ALL SELECT 'PRE checksheet_packages', count(*) FROM shed_visit_checksheet_packages WHERE shed_visit_id = 7
UNION ALL SELECT 'PRE shed_visit_stages', count(*) FROM shed_visit_stages WHERE shed_visit_id = 7
UNION ALL SELECT 'PRE shed_visit_events', count(*) FROM shed_visit_events WHERE shed_visit_id = 7
UNION ALL SELECT 'PRE shed_visits', count(*) FROM shed_visits WHERE id = 7;

DELETE FROM booking_events              WHERE booking_id IN (SELECT id FROM _b);
DELETE FROM booking_section_assignments WHERE booking_id IN (SELECT id FROM _b);
DELETE FROM bookings                    WHERE id IN (SELECT id FROM _b);
DELETE FROM shed_visit_checksheet_requirements
      WHERE package_id IN (SELECT id FROM shed_visit_checksheet_packages WHERE shed_visit_id = 7);
DELETE FROM shed_visit_checksheet_packages WHERE shed_visit_id = 7;
DELETE FROM shed_visit_stages           WHERE shed_visit_id = 7;
DELETE FROM shed_visit_events           WHERE shed_visit_id = 7;
DELETE FROM shed_visits                 WHERE id = 7;

DO $$
DECLARE v_loco int; v_users int; v_sec int; v_tpl int; v_fields int; v_app int; v_equip int;
        v_da int; v_orphan int;
BEGIN
  IF EXISTS (SELECT 1 FROM shed_visits WHERE id = 7) THEN
    RAISE EXCEPTION 'Visit 7 still present after delete.';
  END IF;
  SELECT (SELECT count(*) FROM shed_visit_events e LEFT JOIN shed_visits v ON v.id=e.shed_visit_id WHERE v.id IS NULL)
       + (SELECT count(*) FROM shed_visit_stages s LEFT JOIN shed_visits v ON v.id=s.shed_visit_id WHERE v.id IS NULL)
       + (SELECT count(*) FROM bookings b LEFT JOIN shed_visits v ON v.id=b.shed_visit_id WHERE v.id IS NULL)
       + (SELECT count(*) FROM shed_visit_checksheet_packages p LEFT JOIN shed_visits v ON v.id=p.shed_visit_id WHERE v.id IS NULL)
    INTO v_orphan;
  IF v_orphan <> 0 THEN RAISE EXCEPTION 'Orphan visit-owned rows detected: %', v_orphan; END IF;

  -- Unrelated production data must be untouched (counts observed just before authoring).
  SELECT count(*) INTO v_loco FROM locomotives;
  SELECT count(*) INTO v_users FROM users;
  SELECT count(*) INTO v_sec FROM sections;
  SELECT count(*) INTO v_tpl FROM checksheet_templates;
  SELECT count(*) INTO v_fields FROM template_fields;
  SELECT count(*) INTO v_app FROM checksheet_template_applicability;
  SELECT count(*) INTO v_equip FROM equipment;
  SELECT count(*) INTO v_da FROM dashboard_access;
  IF v_loco<>241 OR v_users<>304 OR v_sec<>15 OR v_tpl<>171 OR v_fields<>11978
     OR v_app<>6 OR v_equip<>167 OR v_da<>41 THEN
    RAISE EXCEPTION 'Unrelated data changed - ABORTING. loco=% users=% sec=% tpl=% fields=% app=% equip=% da=%',
      v_loco, v_users, v_sec, v_tpl, v_fields, v_app, v_equip, v_da;
  END IF;
  RAISE NOTICE 'Visit 7 removed; no orphans; unrelated production data verified unchanged.';
END $$;

COMMIT;
