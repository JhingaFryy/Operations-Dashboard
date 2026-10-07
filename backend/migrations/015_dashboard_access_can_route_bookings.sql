-- 015_dashboard_access_can_route_bookings.sql
--
-- A dedicated Operations Dashboard capability: may manage a booking's SECTION ASSIGNMENTS.
--
-- Forward-only, additive, writes no data. Every existing dashboard_access row gets
-- can_route_bookings = false, which is exactly its present behaviour: nobody can route a booking
-- today, because the only mutation that ever did so is retired (see below).
--
-- ------------------------------------------------------------------------------------------------
-- WHY A NEW FLAG INSTEAD OF REUSING can_add_booking_sections
-- ------------------------------------------------------------------------------------------------
-- dashboard_access.can_add_booking_sections already exists and sounds like the right thing. It is
-- not, and reusing it would be a silent privilege change rather than a new one.
--
-- That flag gates exactly one route, POST /api/bookings/{id}/sections, and that route has been
-- deliberately retired: booking_service.add_section() raises 410 GONE unconditionally and never
-- writes, because "a forward/add-section-style mutation is exactly the kind of legacy operation
-- that could masquerade as an alternate booking workflow" (its own docstring). The flag is
-- therefore inert, and anyone still holding it holds it on the understanding that it does
-- nothing.
--
-- Granting routing through that same flag would retroactively re-arm it for every account that
-- already has it, including ones granted it years ago for a feature that was then removed. A new
-- flag means the new capability is held only by accounts explicitly granted it after this
-- migration - which for now is the PPIO planning section and nobody else.
--
-- The retired route and its flag are left exactly as they are. This migration adds a capability;
-- it un-retires nothing.
--
-- ------------------------------------------------------------------------------------------------
-- WHAT THE CAPABILITY PERMITS
-- ------------------------------------------------------------------------------------------------
-- One new route, PUT /api/bookings/{booking_id}/sections, which replaces a booking's set of
-- maintenance-section assignments. It is the ONLY write PPIO can perform anywhere in Operations
-- Dashboard. The route itself enforces, independently of this flag:
--
--   * booking_source must be LOG_BOOK, TEST_BEFORE or TEST_AFTER - an explicit allow-list, so an
--     unknown or future source fails closed rather than being permitted by omission;
--   * every destination must be an existing MAINTENANCE section - a planning section (PPIO
--     itself) is refused as a destination;
--   * an assignment whose work has started or been attended cannot be removed;
--   * nothing else about the booking may change - not its text, source, equipment, locomotive,
--     status, timestamps or history.
--
-- Admin is unaffected: admin capability is decided by role, never by this table.
--
-- ------------------------------------------------------------------------------------------------
-- IDEMPOTENT. Safe to re-run; adds nothing a second time.
-- ------------------------------------------------------------------------------------------------

BEGIN;

ALTER TABLE dashboard_access
    ADD COLUMN IF NOT EXISTS can_route_bookings boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN dashboard_access.can_route_bookings IS
    'May replace a booking''s maintenance-section assignments via PUT /api/bookings/{id}/sections, '
    'for booking_source in (LOG_BOOK, TEST_BEFORE, TEST_AFTER). Distinct from the inert '
    'can_add_booking_sections, which gates the retired 410 add-section route. Granted to planning '
    'sections (PPIO); false for every pre-existing row.';

-- Proof, not assumption: fail loudly if the column did not end up as intended.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dashboard_access'
          AND column_name = 'can_route_bookings'
          AND is_nullable = 'NO'
          AND column_default = 'false'
    ) THEN
        RAISE EXCEPTION 'can_route_bookings is missing, nullable, or has the wrong default';
    END IF;

    IF EXISTS (SELECT 1 FROM dashboard_access WHERE can_route_bookings IS NOT FALSE) THEN
        RAISE EXCEPTION 'an existing dashboard_access row was granted routing by this migration';
    END IF;
END $$;

COMMIT;
