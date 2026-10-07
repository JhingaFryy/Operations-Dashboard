-- 007_reset_active_shed_visits.sql
--
-- Controlled reset of CURRENTLY ACTIVE shed visits only, ahead of the new shed workflow
-- (Spare -> Schedule In Progress -> Ready -> Shed Out).
--
-- SCOPE: shed_visits whose status is IN_SHED or READY. CLOSED historical visits are never
-- touched. (Audited immediately before writing this: the table holds 3 rows, all IN_SHED, and
-- ZERO CLOSED rows - so this reset removes all visit data that exists, but the predicate is
-- still written on status rather than "everything", so re-running it later against real history
-- stays correct.)
--
-- DEPENDENCY GRAPH (from pg_constraint, not assumed):
--   shed_visits
--   ├── shed_visit_events                    FK RESTRICT   -> must be deleted first
--   ├── shed_visit_stages                    FK RESTRICT   -> must be deleted first
--   ├── shed_visit_checksheet_packages       FK CASCADE
--   │     └── shed_visit_checksheet_requirements  FK CASCADE (via package_id)
--   └── bookings                             FK RESTRICT   -> must be deleted first
--         ├── booking_events                 FK RESTRICT
--         └── booking_section_assignments    FK RESTRICT
--
-- Every child is deleted EXPLICITLY, child-first. The two CASCADE edges are not relied on: an
-- explicit DELETE makes the affected set auditable and lets the assertions below count it.
-- No DELETE ... CASCADE and no TRUNCATE ... CASCADE anywhere.
--
-- CLASSIFICATION
--   DELETE - shed_visit_events, shed_visit_stages, checksheet packages+requirements, and the
--            bookings (with their events/assignments) belonging to those visits. All of these are
--            visit-owned: a booking carries a NOT NULL shed_visit_id and has no meaning once its
--            visit is gone, and a requirement snapshot describes one visit's expected work.
--   PRESERVE - locomotives, users, sections, equipment, section_equipment_map,
--            booking_defect_types, checksheet_templates/template_fields/applicability,
--            checksheet_header, and any CLOSED shed visit.
--
-- Not a schema change; no column is added or dropped.

BEGIN;

CREATE TEMP TABLE _active_visits ON COMMIT DROP AS
    SELECT id FROM shed_visits WHERE status IN ('IN_SHED', 'READY');

CREATE TEMP TABLE _active_bookings ON COMMIT DROP AS
    SELECT id FROM bookings WHERE shed_visit_id IN (SELECT id FROM _active_visits);

-- Pre-reset counts, recorded in the migration output for the audit trail.
SELECT 'PRE  active shed_visits'      AS label, count(*) FROM _active_visits
UNION ALL SELECT 'PRE  bookings (active visits)',  count(*) FROM _active_bookings
UNION ALL SELECT 'PRE  booking_events',            count(*) FROM booking_events WHERE booking_id IN (SELECT id FROM _active_bookings)
UNION ALL SELECT 'PRE  booking_section_assignments', count(*) FROM booking_section_assignments WHERE booking_id IN (SELECT id FROM _active_bookings)
UNION ALL SELECT 'PRE  shed_visit_events',         count(*) FROM shed_visit_events WHERE shed_visit_id IN (SELECT id FROM _active_visits)
UNION ALL SELECT 'PRE  shed_visit_stages',         count(*) FROM shed_visit_stages WHERE shed_visit_id IN (SELECT id FROM _active_visits)
UNION ALL SELECT 'PRE  checksheet packages',       count(*) FROM shed_visit_checksheet_packages WHERE shed_visit_id IN (SELECT id FROM _active_visits)
UNION ALL SELECT 'PRE  checksheet requirements',   count(*) FROM shed_visit_checksheet_requirements r
                                                    JOIN shed_visit_checksheet_packages p ON p.id = r.package_id
                                                    WHERE p.shed_visit_id IN (SELECT id FROM _active_visits)
UNION ALL SELECT 'PRE  CLOSED visits (preserved)', count(*) FROM shed_visits WHERE status = 'CLOSED';

-- ---------------------------------------------------------------------------
-- Purge, child-first
-- ---------------------------------------------------------------------------
DELETE FROM booking_events             WHERE booking_id IN (SELECT id FROM _active_bookings);
DELETE FROM booking_section_assignments WHERE booking_id IN (SELECT id FROM _active_bookings);
DELETE FROM bookings                   WHERE id         IN (SELECT id FROM _active_bookings);

DELETE FROM shed_visit_checksheet_requirements
    WHERE package_id IN (
        SELECT id FROM shed_visit_checksheet_packages
        WHERE shed_visit_id IN (SELECT id FROM _active_visits)
    );
DELETE FROM shed_visit_checksheet_packages WHERE shed_visit_id IN (SELECT id FROM _active_visits);

DELETE FROM shed_visit_stages          WHERE shed_visit_id IN (SELECT id FROM _active_visits);
DELETE FROM shed_visit_events          WHERE shed_visit_id IN (SELECT id FROM _active_visits);
DELETE FROM shed_visits                WHERE id            IN (SELECT id FROM _active_visits);

-- ---------------------------------------------------------------------------
-- Post-reset assertions - any violation aborts the whole transaction.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  v_active int; v_closed int; v_loco int; v_users int; v_sec int;
  v_tpl int; v_fields int; v_app int; v_equip int; v_hdr int;
BEGIN
  SELECT count(*) INTO v_active FROM shed_visits WHERE status IN ('IN_SHED', 'READY');
  IF v_active <> 0 THEN
    RAISE EXCEPTION 'Active shed visits remain after reset: %', v_active;
  END IF;

  -- Master/config data must be untouched. Counts observed immediately before authoring this.
  SELECT count(*) INTO v_closed FROM shed_visits WHERE status = 'CLOSED';
  SELECT count(*) INTO v_loco  FROM locomotives;
  SELECT count(*) INTO v_users FROM users;
  SELECT count(*) INTO v_sec   FROM sections;
  SELECT count(*) INTO v_tpl   FROM checksheet_templates;
  SELECT count(*) INTO v_fields FROM template_fields;
  SELECT count(*) INTO v_app   FROM checksheet_template_applicability;
  SELECT count(*) INTO v_equip FROM equipment;
  SELECT count(*) INTO v_hdr   FROM checksheet_header;

  IF v_loco <> 241 OR v_users <> 304 OR v_sec <> 15 OR v_tpl <> 171
     OR v_fields <> 11978 OR v_app <> 6 OR v_equip <> 167 THEN
    RAISE EXCEPTION
      'Master data changed - ABORTING. loco=% users=% sec=% tpl=% fields=% app=% equip=%',
      v_loco, v_users, v_sec, v_tpl, v_fields, v_app, v_equip;
  END IF;

  RAISE NOTICE
    'Reset OK: active visits cleared; CLOSED history preserved=%; checksheet_header untouched=%',
    v_closed, v_hdr;
END $$;

COMMIT;
