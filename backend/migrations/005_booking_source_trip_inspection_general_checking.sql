-- Widen chk_booking_source to admit TRIP_INSPECTION and GENERAL_CHECKING.
--
-- ============================ WHY =============================================================
-- Bookings raised by a technician while filling a Trip Inspection (TI) or General Checking (GC)
-- performa need a booking_source that says so. The two existing values that a careless mapping
-- might reach for are both WRONG and are explicitly rejected:
--
--   * SCHEDULE_INSPECTION is the Minor-schedule workflow stage between TEST_BEFORE and
--     TEST_AFTER on a shed_visit_stages row (see app/services/schedule_inspection_service.py and
--     checksheet_stage_reconciliation_service._stage_bookings_ready, which matches bookings to
--     stages by literal booking_source == stage_type). Filing a Trip Inspection finding under it
--     would make that finding gate the completion of a Minor schedule stage it has nothing to do
--     with.
--   * MANUAL means "an operator typed this into the Dashboard directly", i.e. no originating
--     performa at all. A GC finding has a very specific origin; calling it MANUAL destroys that.
--
-- ============================ WHAT THIS CHANGES ==============================================
-- Exactly one CHECK constraint, widened to a strict SUPERSET of what it already allowed:
--
--   before: LOG_BOOK, TEST_BEFORE, SCHEDULE_INSPECTION, TEST_AFTER, SPECIAL_CHECKING, MANUAL
--   after:  ... the same six ... + TRIP_INSPECTION, GENERAL_CHECKING
--
-- Because the new set is a superset, no existing row can be invalidated: every row that
-- satisfied the old constraint satisfies the new one by construction. Nothing is backfilled, no
-- row is updated, and no existing booking's booking_source changes. SPECIAL_CHECKING is retained
-- untouched (present in the live constraint, unused by application code) - this migration does
-- not take the opportunity to remove it, since removal would be a narrowing change and is not
-- what was authorized.
--
-- PostgreSQL has no ALTER CONSTRAINT for a CHECK expression, so the only way to widen one is
-- DROP + ADD. That is done here inside a single transaction, so the table is never observably
-- unconstrained by any other session.
--
-- ============================ WHAT THIS DOES NOT TOUCH =======================================
-- No column is added, dropped, retyped or backfilled. No lifecycle/status semantics change -
-- booking_source has never participated in the OPEN/IN_PROGRESS/ATTENDED/REOPENED state machine,
-- which is derived from booking_section_assignments (migration 003). Shed Out gating
-- (app/services/shed_out_service.py) is explicitly booking_source-agnostic - see that module's
-- own docstring - so a TRIP_INSPECTION or GENERAL_CHECKING booking blocks Shed Out on exactly
-- the same terms as any other unattended booking, which is the intended behaviour.
--
-- checksheet_stage_reconciliation_service maps bookings to Minor-schedule stages by literal
-- booking_source == stage_type. TRIP_INSPECTION/GENERAL_CHECKING match no shed_visit_stages
-- stage_type, so such bookings correctly participate in no stage gate - see
-- migrations note in the phase report and app/schemas/booking_pool.py's WORKFLOW-STAGE-TYPE
-- comment.
--
-- No other table is touched.
--
-- Idempotent (DROP ... IF EXISTS then ADD; re-running drops and re-adds the identical
-- constraint) and transactional. Safe to re-run.
--
-- NOT executed automatically by this application - see migration 004's closing note.

BEGIN;

ALTER TABLE bookings
    DROP CONSTRAINT IF EXISTS chk_booking_source;

ALTER TABLE bookings
    ADD CONSTRAINT chk_booking_source CHECK (
        booking_source IN (
            'LOG_BOOK',
            'TEST_BEFORE',
            'SCHEDULE_INSPECTION',
            'TEST_AFTER',
            'SPECIAL_CHECKING',
            'MANUAL',
            'TRIP_INSPECTION',
            'GENERAL_CHECKING'
        )
    );

COMMIT;
