-- 009_shed_visit_event_type_schedule_lifecycle.sql
--
-- Schema-contract repair: allow the schedule lifecycle event types in shed_visit_events.
--
-- THE BUG
-- Migration 008 introduced Start Schedule / Complete Schedule, and
-- app/services/schedule_lifecycle_service.py records them as shed_visit_events rows with
-- event_type SCHEDULE_STARTED / SCHEDULE_COMPLETED. The live chk_shed_visit_event_type was never
-- widened, so every Start Schedule failed with CheckViolation -> HTTP 500 (the visit update rolled
-- back correctly). SQLite tests did not catch it because ShedVisitEvent declared no mirror of the
-- CHECK; it now does (app/db/models.py SHED_VISIT_EVENT_TYPES).
--
-- AUDIT (every shed_visit_events writer in app code):
--   SHED_IN             shed_visit_service.shed_in
--   SHED_OUT            shed_out_service.shed_out
--   SCHEDULE_STARTED    schedule_lifecycle_service.start_schedule      <- missing before this
--   SCHEDULE_COMPLETED  schedule_lifecycle_service.complete_schedule   <- missing before this
-- Legacy values not emitted today but PRESERVED (existing rows / other writers may use them):
--   MARK_READY, SCHEDULE_CHANGED, MANUAL_CORRECTION
--
-- Only chk_shed_visit_event_type is replaced. chk_shed_visit_event_source and every other
-- constraint are untouched. Still a closed CHECK list - never free text.
--
-- OWNERSHIP: shed_visit_events is owned by `postgres`; run as the table owner, e.g.
--   sudo -u postgres psql -d rdcms -v ON_ERROR_STOP=1 -f 009_shed_visit_event_type_schedule_lifecycle.sql

BEGIN;

ALTER TABLE shed_visit_events DROP CONSTRAINT IF EXISTS chk_shed_visit_event_type;
ALTER TABLE shed_visit_events
    ADD CONSTRAINT chk_shed_visit_event_type
    CHECK (event_type IN (
        'SHED_IN',
        'MARK_READY',
        'SHED_OUT',
        'SCHEDULE_CHANGED',
        'MANUAL_CORRECTION',
        'SCHEDULE_STARTED',
        'SCHEDULE_COMPLETED'
    ));

COMMIT;
