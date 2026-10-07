-- 008_shed_visit_schedule_started_at.sql
--
-- Adds the one timestamp the new shed workflow needs, plus ordering constraints.
--
-- NEW LIFECYCLE
--   Shed In            -> arrival_at            (status IN_SHED)   phase SPARE
--   Start Schedule     -> schedule_started_at   (status IN_SHED)   phase SCHEDULE_IN_PROGRESS
--   Complete Schedule  -> ready_at              (status READY)     phase READY
--   Shed Out           -> departed_at           (status CLOSED)    phase SHED_OUT
--
-- WHY ONLY ONE NEW COLUMN
-- An audit of the existing code found that NOTHING writes ready_at/ready_source and nothing ever
-- sets status='READY' - the column and the status exist but are semantically vacant, so there is
-- no established meaning for "Complete Schedule" to collide with. ready_at is therefore adopted
-- as the schedule-completion timestamp rather than adding a redundant schedule_completed_at.
-- Departure ELIGIBILITY stays a separate, computed concept (shed_out_service's stage + booking +
-- checksheet gates); it is not a stored timestamp and is unaffected by this migration.
--
-- schedule_started_by is deliberately NOT added: actor/source for lifecycle transitions is
-- already recorded in shed_visit_events (created_by + event payload), which is the established
-- pattern for every other transition in this table. A second copy on shed_visits would be
-- duplicate metadata that can drift.
--
-- HISTORICAL ROWS: left NULL. No schedule_started_at value is invented for a past visit - a
-- visit closed before this column existed genuinely has no recorded schedule start, and the
-- ordering constraint below tolerates NULL precisely so that history stays valid.

BEGIN;

ALTER TABLE shed_visits
    ADD COLUMN IF NOT EXISTS schedule_started_at timestamptz NULL;

COMMENT ON COLUMN shed_visits.schedule_started_at IS
    'When actual schedule work began (the Start Schedule action). NULL while the locomotive is '
    'in shed but Spare, and NULL for historical visits predating this column.';
COMMENT ON COLUMN shed_visits.ready_at IS
    'When the maintenance schedule was marked complete (the Complete Schedule action). This is '
    'NOT departure eligibility - that remains computed from the stage, booking and checksheet '
    'gates in shed_out_service.evaluate_shed_out_eligibility.';

-- ---------------------------------------------------------------------------
-- Timestamp ordering. Every clause is NULL-tolerant on purpose: a CHECK only fails on FALSE
-- (it PASSES on NULL), so each comparison is written to be either genuinely FALSE for a bad
-- ordering, or skipped entirely when one side is absent. That also keeps every historical row -
-- which has NULL for the new column - valid without rewriting any data.
-- ---------------------------------------------------------------------------
ALTER TABLE shed_visits DROP CONSTRAINT IF EXISTS chk_shed_visit_timestamp_order;
ALTER TABLE shed_visits
    ADD CONSTRAINT chk_shed_visit_timestamp_order
    CHECK (
        (schedule_started_at IS NULL OR schedule_started_at >= arrival_at)
        AND (ready_at IS NULL OR schedule_started_at IS NULL OR ready_at >= schedule_started_at)
        AND (ready_at IS NULL OR ready_at >= arrival_at)
        AND (departed_at IS NULL OR ready_at IS NULL OR departed_at >= ready_at)
        AND (departed_at IS NULL OR departed_at >= arrival_at)
    );

-- A visit cannot be complete without having started, nor departed without being complete. Kept
-- separate from the ordering rule above so a violation says which invariant broke.
ALTER TABLE shed_visits DROP CONSTRAINT IF EXISTS chk_shed_visit_phase_progression;
ALTER TABLE shed_visits
    ADD CONSTRAINT chk_shed_visit_phase_progression
    CHECK (
        -- ready_at requires schedule_started_at, EXCEPT on rows that predate this column
        -- (status CLOSED with no schedule_started_at is legacy history, not a new transition).
        (ready_at IS NULL OR schedule_started_at IS NOT NULL OR status = 'CLOSED')
        AND (departed_at IS NULL OR status = 'CLOSED')
    );

-- Phase derivation and the active-visit queries both filter on these.
CREATE INDEX IF NOT EXISTS ix_shed_visits_phase
    ON shed_visits (status, schedule_started_at, ready_at, departed_at);

COMMIT;
