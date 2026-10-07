import type { CurrentUser } from '../types'

/** Operations Dashboard is SUPERVISOR-ONLY. Read straight from the server's capabilities rather
 * than re-derived from role/section here - the backend owns this decision, and a second copy of
 * the rule in the frontend is how the two drift apart. */
export function canAccessOperationsDashboard(user: CurrentUser | null): boolean {
  return Boolean(user?.capabilities?.can_access_operations_dashboard)
}

/** Shed In / Start Schedule / Complete Schedule / Shed Out - the shed movement sections only
 * (SHIFT/PPIO by server configuration). Additive to normal Supervisor rights; it confers no
 * admin capability whatsoever. */
export function canManageLocoMovement(user: CurrentUser | null): boolean {
  return Boolean(user?.capabilities?.can_manage_loco_movement)
}

/** Cross-section visibility: may view/operate any section, and may choose one. Admin only. */
export function canAccessAllSections(user: CurrentUser | null): boolean {
  return Boolean(user?.capabilities?.can_access_all_sections)
}

/** Their own section's booking queue. */
export function canManageOwnSectionBookings(user: CurrentUser | null): boolean {
  return Boolean(user?.capabilities?.can_manage_own_section_bookings)
}

/** Admin is the highest operational authority — always true regardless of
 * users.section_id or dashboard_access flags. Every other helper here
 * checks this first. This is a UI convenience only; the backend
 * (app/core/dependencies.py, app/services/section_dashboard_service.py,
 * app/services/booking_service.py) is the real security boundary and
 * re-checks independently on every request. */
export function isAdmin(user: CurrentUser | null): boolean {
  // The genuine Superadmin: full operational access PLUS the Admin-only functions (global
  // booking pool, dashboard-access administration, reopening an attended assignment). Read from
  // the server-issued capability rather than the role string, so the backend stays authoritative.
  return Boolean(user?.capabilities?.can_admin) || user?.role === 'Admin'
}

/** Mirrors the backend's own rule (require_equipment_mapping_permission)
 * — Admin always, Supervisor only with the explicit flag. */
export function canManageEquipmentMapping(user: CurrentUser | null): boolean {
  if (!user) return false
  return isAdmin(user) || user.permissions.can_manage_equipment_mapping
}

/** Mirrors require_add_booking_sections_permission — Admin always,
 * Supervisor only with the explicit flag. Note this only governs adding a
 * section; it never grants permission to operate that section's own
 * assignments afterward (see section_dashboard_service._assert_can_operate_section). */
export function canAddBookingSections(user: CurrentUser | null): boolean {
  if (!user) return false
  return isAdmin(user) || user.permissions.can_add_booking_sections
}

/** Reopen is Admin-only in this phase — Supervisor never sees or can
 * trigger it, regardless of any permission flag. */
export function canReopenAssignments(user: CurrentUser | null): boolean {
  return isAdmin(user)
}

/** Mirrors require_route_bookings - Admin always, anyone else only with the explicit
 *  dashboard_access capability (migration 015). This is the ONE write a planning section (PPIO)
 *  holds anywhere in Operations Dashboard. Deliberately NOT canAddBookingSections above, which
 *  belongs to the retired add-section route and grants nothing. */
export function canRouteBookings(user: CurrentUser | null): boolean {
  if (!user) return false
  return isAdmin(user) || Boolean(user.permissions?.can_route_bookings)
}

/** Mirrors require_booking_pool_read - Admin, or an account that may route. A planner cannot
 *  decide where a finding should go without seeing the pool it lives in. */
export function canReadBookingPool(user: CurrentUser | null): boolean {
  return isAdmin(user) || canRouteBookings(user)
}

/**
 * Whether this account belongs to a PLANNING section, as the server resolved it.
 *
 * READ, NOT INFERRED. This used to be derived as `!canRouteBookings(user)`, and that was wrong in
 * both directions: a normal PPIO Supervisor with no dashboard_access row came out as NOT a
 * planner (so the sidebar offered them a "PPIO Bookings" section work queue), while an
 * exceptional non-planning account granted routing came out as one (so it would have LOST its own
 * Section Dashboard). The backend knows the answer and now says so.
 */
export function isPlanningUser(user: CurrentUser | null): boolean {
  return Boolean(user?.capabilities?.is_planning_section)
}

/** Whether this account may perform booking/assignment WORK (start, attend, reopen, delete).
 *  A planner may not: routing is its only write, so every other control must be absent rather
 *  than disabled. */
export function canOperateBookings(user: CurrentUser | null): boolean {
  if (!user) return false
  if (isAdmin(user)) return true
  return !isPlanningUser(user)
}

/**
 * Whether to offer a section work queue (the Section Dashboard).
 *
 * A planning section has none: nothing is ever routed to it, so the page would be a dashboard
 * for a section that can never have work. The backend refuses those routes independently
 * (section_dashboard_service._assert_can_operate_section) - this only stops the link being
 * offered.
 */
export function hasSectionWorkQueue(user: CurrentUser | null): boolean {
  if (!user) return false
  if (isAdmin(user)) return true
  return !isPlanningUser(user)
}

