-- 012_minor_workflow_stage_timing_and_test_before_skip.sql
--
-- MINOR workflow refinement: Test Before skip, a distinct inspection-completion timestamp, and
-- honest stage timing. Forward-only. Writes NO data: every new column starts NULL for every row,
-- and no historical/in-progress visit is rewritten or backfilled.
--
-- AUDIT (before this migration)
--   shed_visits.schedule_started_at  Start Schedule (actual inspection start)            - kept
--   shed_visits.ready_at             Complete Schedule wrote it (migration 008 adopted the
--                                    then-vacant column as "schedule complete")          - MINOR no longer
--   shed_visit_events SCHEDULE_COMPLETED  Complete Schedule                              - kept
--   shed_visit_stages started_at/completed_at  per-stage timing (TB / Inspection / TA) already
--                                    exists as the canonical stage structure             - reused, not duplicated
--
-- NEW SEMANTICS (MINOR)
--   arrival_at               physical shed arrival
--   schedule_started_at      actual Minor Inspection begins (Start Schedule; requires TB COMPLETED or SKIPPED)
--   inspection_completed_at  actual Minor Inspection ends (Complete Schedule)            <- NEW
--   ready_at                 locomotive READY after Test After (Mark Ready; requires TA COMPLETED)
--   departed_at              Shed Out
--   inspection_duration = inspection_completed_at - schedule_started_at (excludes TB and TA)
-- MAJOR is unchanged: Complete Schedule still writes ready_at; inspection_completed_at stays NULL.
--
-- STAGE TIMING (shed_visit_stages, already the canonical per-stage structure - no new table)
--   started_at   first real start of the stage: the explicit Start action, or BL-DCMS creating the
--                visit's first checksheet for that stage. Never reset once set.
--   completed_at when the stage first became satisfied (authoritative reconciliation). Unchanged.
--   skipped_at / skipped_by / skip_reason   <- NEW, Test Before only, status SKIPPED.
--
-- chk_stage_started is relaxed for COMPLETED only: reconciliation used to stamp started_at = the
-- completion instant when no start had been recorded, which fabricated a zero-length stage. From
-- now on an unrecorded start stays NULL (duration "not recorded"), never a fake 0.
--
-- OWNERSHIP: shed_visits, shed_visit_stages and shed_visit_events are owned by `postgres`; run as
-- the table owner, e.g.
--   sudo -u postgres psql -d rdcms -v ON_ERROR_STOP=1 -f 012_minor_workflow_stage_timing_and_test_before_skip.sql

BEGIN;

-- ------------------------------------------------------------------------------- shed_visits --
ALTER TABLE shed_visits
    ADD COLUMN IF NOT EXISTS inspection_completed_at timestamptz NULL;

COMMENT ON COLUMN shed_visits.inspection_completed_at IS
    'MINOR: when the actual Minor Inspection was completed (the Complete Schedule action). Inspection '
    'duration = inspection_completed_at - schedule_started_at, excluding Test Before and Test After. '
    'NULL for MAJOR visits and for visits predating migration 012.';
COMMENT ON COLUMN shed_visits.ready_at IS
    'MINOR (migration 012+): when the locomotive became READY after Test After (the Mark Ready action). '
    'MAJOR, and MINOR visits completed before migration 012: when Complete Schedule was recorded. '
    'Never departure eligibility - Shed Out re-evaluates its own gates.';

ALTER TABLE shed_visits DROP CONSTRAINT IF EXISTS chk_shed_visit_timestamp_order;
ALTER TABLE shed_visits
    ADD CONSTRAINT chk_shed_visit_timestamp_order
    CHECK (
        (schedule_started_at IS NULL OR schedule_started_at >= arrival_at)
        AND (ready_at IS NULL OR schedule_started_at IS NULL OR ready_at >= schedule_started_at)
        AND (ready_at IS NULL OR ready_at >= arrival_at)
        AND (departed_at IS NULL OR ready_at IS NULL OR departed_at >= ready_at)
        AND (departed_at IS NULL OR departed_at >= arrival_at)
        AND (inspection_completed_at IS NULL OR inspection_completed_at >= arrival_at)
        AND (inspection_completed_at IS NULL OR schedule_started_at IS NULL
             OR inspection_completed_at >= schedule_started_at)
        AND (ready_at IS NULL OR inspection_completed_at IS NULL OR ready_at >= inspection_completed_at)
        AND (departed_at IS NULL OR inspection_completed_at IS NULL OR departed_at >= inspection_completed_at)
    );

ALTER TABLE shed_visits DROP CONSTRAINT IF EXISTS chk_shed_visit_phase_progression;
ALTER TABLE shed_visits
    ADD CONSTRAINT chk_shed_visit_phase_progression
    CHECK (
        (ready_at IS NULL OR schedule_started_at IS NOT NULL OR status = 'CLOSED')
        AND (departed_at IS NULL OR status = 'CLOSED')
        -- inspection cannot complete before it started (no legacy exemption: the column is new)
        AND (inspection_completed_at IS NULL OR schedule_started_at IS NOT NULL)
    );

-- ------------------------------------------------------------------------- shed_visit_stages --
ALTER TABLE shed_visit_stages
    ADD COLUMN IF NOT EXISTS skipped_at timestamptz NULL,
    ADD COLUMN IF NOT EXISTS skipped_by integer NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS skip_reason text NULL;

COMMENT ON COLUMN shed_visit_stages.skipped_at IS
    'TEST_BEFORE only: when an Admin explicitly skipped Test Before for this shed visit (status SKIPPED). '
    'A skipped stage is never a completed one: completed_at stays NULL and no checksheet is created.';

ALTER TABLE shed_visit_stages DROP CONSTRAINT IF EXISTS chk_shed_visit_stage_status;
ALTER TABLE shed_visit_stages
    ADD CONSTRAINT chk_shed_visit_stage_status
    CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED'));

ALTER TABLE shed_visit_stages DROP CONSTRAINT IF EXISTS chk_stage_started;
ALTER TABLE shed_visit_stages
    ADD CONSTRAINT chk_stage_started
    CHECK (status IN ('PENDING', 'COMPLETED', 'SKIPPED') OR started_at IS NOT NULL);

-- SKIPPED exists only for TEST_BEFORE (Test After can never be skipped - enforced here as well as in
-- the service), always carries skipped_at, and is never also completed.
ALTER TABLE shed_visit_stages DROP CONSTRAINT IF EXISTS chk_stage_skipped;
ALTER TABLE shed_visit_stages
    ADD CONSTRAINT chk_stage_skipped
    CHECK (
        (status = 'SKIPPED' AND stage_type = 'TEST_BEFORE' AND skipped_at IS NOT NULL AND completed_at IS NULL)
        OR (status <> 'SKIPPED' AND skipped_at IS NULL AND skipped_by IS NULL AND skip_reason IS NULL)
    );

-- ------------------------------------------------------------------------- shed_visit_events --
-- TEST_BEFORE_SKIPPED audits the skip. MARK_READY (already allowed, never emitted until now) audits
-- the MINOR READY transition. Every existing value is preserved.
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
        'SCHEDULE_COMPLETED',
        'TEST_BEFORE_SKIPPED'
    ));

COMMIT;
