-- Common Booking Pool + Equipment-Bifurcated Dashboard Reform.
--
-- Bookings are no longer routed to sections via equipment-section mapping; every booking is
-- globally visible and booking.status itself becomes the authoritative operational lifecycle
-- signal (OPEN -> IN_PROGRESS -> ATTENDED -> REOPENED -> IN_PROGRESS -> ...). This migration adds
-- only the two columns genuinely missing from `bookings` to support that: started_by_section_id
-- and attended_by_section_id, capturing which section the acting user belonged to at the moment
-- they started/attended a booking.
--
-- Everything else this reform needs (started_by, started_at, attended_by, attended_at,
-- attendance_remarks, reopened_by, reopened_at) already exists on `bookings` - see
-- app/db/models.py's Booking class comment. Those columns were present but never written to
-- before this reform (all lifecycle tracking previously lived on booking_section_assignments);
-- reusing them here means no duplicate columns are introduced.
--
-- Purely additive: both new columns are nullable, no default, no backfill, no existing row is
-- touched. booking_section_assignments is deliberately NOT dropped or altered - it remains fully
-- readable for historical/audit purposes; this reform only stops writing new operational meaning
-- into it (see app/services/booking_pool_service.py's module docstring).

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS started_by_section_id INTEGER REFERENCES sections(id);
ALTER TABLE bookings ADD COLUMN IF NOT EXISTS attended_by_section_id INTEGER REFERENCES sections(id);
