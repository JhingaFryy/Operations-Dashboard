-- Additive idempotency key for externally-originated booking creation.
--
-- ============================ WHY =============================================================
-- POST /api/internal/bookings (app/api/internal.py) lets a semi-trusted internal caller
-- (BL-DCMS, on behalf of an Android technician whose staged bookings were held locally until the
-- originating checksheet was submitted) create a booking. That call crosses two networks and is
-- retried by the client whenever it cannot confirm the outcome - a timeout, a dropped
-- connection, a process death between the write and the response. Without a stable
-- client-supplied key, every such retry creates a SECOND real booking for the same defect, with
-- its own booking_section_assignments rows, in front of the same section.
--
-- Android already generates and stores a stable UUID per staged booking for exactly this purpose
-- (StagedBooking.localBookingId, Android/app/src/main/java/com/checksheet/android/domain/booking/
-- StagedBooking.kt). This column is where that same value lands, unchanged, end to end.
--
-- ============================ WHAT THIS IS NOT ===============================================
-- This is NOT a business-level duplicate check. Two different technicians, or the same
-- technician on two different visits or two different checksheets, may legitimately book the
-- same defect wording against the same equipment - and must still get two bookings. There is
-- deliberately NO uniqueness constraint on (equipment_node_id, description) or anything like it.
-- The only thing this column deduplicates is a retransmission of one single client-side row.
-- Android's own local duplicate detection (equipment identity + normalized remarks, within one
-- draft) is a separate, unrelated concern and stays entirely on the device.
--
-- ============================ SEMANTICS ======================================================
-- NULLABLE. Every booking created by an existing flow (Shed In's Log Book bookings, Test Before/
-- Schedule Inspection/Test After findings - all Admin-JWT routes) leaves it NULL, unchanged, and
-- so does every one of the rows already in this table.
--
-- UNIQUE among NON-NULL values only, via a partial unique index. PostgreSQL's plain UNIQUE
-- constraint would also permit many NULLs, but a partial index states the intent explicitly and
-- keeps the index off the (overwhelming majority of) NULL rows.
--
--   * first request with a given client_booking_id -> booking created, row carries the id
--   * retry with the SAME id and the SAME logical payload -> the existing booking is returned;
--     nothing is written, no second assignment, no second booking_events row
--   * retry with the SAME id and a CONFLICTING payload -> rejected 409 IDEMPOTENCY_CONFLICT;
--     the stored booking is never silently mutated to match the newer claim
--
-- The comparison and the 409 live in app/services/booking_creation_service.py
-- (create_booking_idempotent); this index is the last line of defence against two concurrent
-- retries racing past that read.
--
-- ============================ WHAT THIS DOES NOT TOUCH =======================================
-- No lifecycle or status semantics change. bookings.status remains the derived aggregate over
-- booking_section_assignments (see migration 003); the assignment transition rules
-- (OPEN -> IN_PROGRESS -> ATTENDED -> REOPENED -> IN_PROGRESS -> ATTENDED) are untouched, and no
-- CHECK constraint on bookings, booking_events or booking_section_assignments is added, dropped
-- or altered here. Shed Out gating (app/services/shed_out_service.py) reads
-- booking_section_assignments statuses and is entirely unaffected - a booking created through the
-- internal route blocks Shed Out exactly like any other booking, no more and no less.
--
-- No other table is touched.
--
-- ============================ PRE-APPLICATION CHECK =========================================
-- Existing rows trivially satisfy the new index: the column does not exist yet, so every
-- existing row gets NULL, and the index only constrains non-NULL values. Verified read-only
-- against the live rdcms database before applying:
--
--   rdcms=> SELECT count(*) FROM bookings;
--    count
--   -------
--        3
--
--   rdcms=> SELECT count(*) FROM information_schema.columns
--           WHERE table_name = 'bookings' AND column_name = 'client_booking_id';
--    count
--   -------
--        0
--
-- Idempotent (IF NOT EXISTS on both statements) and transactional (PostgreSQL DDL is
-- transactional; the explicit BEGIN/COMMIT below makes the boundary unambiguous). Safe to
-- re-run.
--
-- NOT executed automatically by this application - Base.metadata.create_all() never targets the
-- real rdcms database (see app/db/session.py). Apply with psql.

BEGIN;

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS client_booking_id VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_bookings_client_booking_id
    ON bookings (client_booking_id)
    WHERE client_booking_id IS NOT NULL;

COMMENT ON COLUMN bookings.client_booking_id IS
    'Stable client-generated idempotency key for externally-originated bookings (Android StagedBooking.localBookingId, relayed by BL-DCMS through POST /api/internal/bookings). NULL for every booking created by a Dashboard-native flow. Unique among non-NULL values only. Deduplicates a retransmission of one client-side row - NOT a business-level duplicate-defect check.';

COMMIT;
