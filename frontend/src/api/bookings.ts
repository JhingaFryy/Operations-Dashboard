import { apiFetch } from './client'
import type {
  BookingDetail,
  BookingHistoryEvent,
  BookingPoolItem,
  SectionAssignment,
  SectionSummary,
} from '../types'

// Business Rule Alignment: GET /api/bookings is Admin-only global operational visibility now
// (see app/api/bookings.py). The booking-level start/attend/reopen routes below are retired
// server-side (always 410 Gone) - kept here only in case a future admin-only bulk/audit tool
// needs the shape; nothing in the app currently calls them. Use api/sectionDashboard.ts's
// start/attend/reopenAssignment for real operational mutations instead.

export interface BookingPoolFilters {
  shedVisitId?: number
  status?: string
  source?: string
  equipmentNodeId?: number
}

export function listBookings(filters: BookingPoolFilters = {}): Promise<BookingPoolItem[]> {
  const params = new URLSearchParams()
  if (filters.shedVisitId != null) params.set('shed_visit_id', String(filters.shedVisitId))
  if (filters.status) params.set('status', filters.status)
  if (filters.source) params.set('source', filters.source)
  if (filters.equipmentNodeId != null) params.set('equipment_node_id', String(filters.equipmentNodeId))
  const query = params.toString()
  return apiFetch<BookingPoolItem[]>(`/api/bookings${query ? `?${query}` : ''}`)
}

/** @deprecated Retired server-side (410 Gone) - see api/sectionDashboard.ts's startAssignment. */
export function startBooking(bookingId: number): Promise<BookingPoolItem> {
  return apiFetch<BookingPoolItem>(`/api/bookings/${bookingId}/start`, { method: 'POST' })
}

/** @deprecated Retired server-side (410 Gone) - see api/sectionDashboard.ts's attendAssignment. */
export function attendBooking(bookingId: number, remarks: string): Promise<BookingPoolItem> {
  return apiFetch<BookingPoolItem>(`/api/bookings/${bookingId}/attend`, {
    method: 'POST',
    body: JSON.stringify({ remarks }),
  })
}

/** @deprecated Retired server-side (410 Gone) - see api/sectionDashboard.ts's reopenAssignment. */
export function reopenBooking(bookingId: number, reason: string): Promise<BookingPoolItem> {
  return apiFetch<BookingPoolItem>(`/api/bookings/${bookingId}/reopen`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export function addSection(
  bookingId: number,
  sectionId: number,
  reason: string,
): Promise<SectionAssignment> {
  return apiFetch<SectionAssignment>(`/api/bookings/${bookingId}/sections`, {
    method: 'POST',
    body: JSON.stringify({ section_id: sectionId, reason }),
  })
}

export function getBookingHistory(bookingId: number): Promise<BookingHistoryEvent[]> {
  return apiFetch<BookingHistoryEvent[]>(`/api/bookings/${bookingId}/history`)
}

export function getBookingDetail(bookingId: number): Promise<BookingDetail> {
  return apiFetch<BookingDetail>(`/api/bookings/${bookingId}`)
}

export function getSectionSummary(sectionId?: number): Promise<SectionSummary> {
  const query = sectionId != null ? `?section_id=${sectionId}` : ''
  return apiFetch<SectionSummary>(`/api/bookings/summary${query}`)
}

export interface AddSectionsResponse {
  booking_id: number
  booking_source: string
  previous_section_ids: number[]
  new_section_ids: number[]
  added_section_ids: number[]
  already_assigned_section_ids: number[]
  sections: { section_id: number; section_code: string; assignment_source: string; status: string }[]
}

/**
 * ADD maintenance sections to a booking. Never removes, never replaces.
 *
 * Only the NEW sections are sent, so the request cannot be read as a desired set. This replaces
 * an earlier PUT .../sections that took the complete set and could un-route a section by
 * omission. The legacy POST .../sections remains retired at 410 and is untouched.
 */
export function addBookingSections(
  bookingId: number,
  sectionIds: number[],
  reason?: string,
): Promise<AddSectionsResponse> {
  return apiFetch<AddSectionsResponse>(`/api/bookings/${bookingId}/section-assignments`, {
    method: 'POST',
    body: JSON.stringify({ section_ids: sectionIds, ...(reason ? { reason } : {}) }),
  })
}

export interface CreatePlanningBookingRequest {
  shed_visit_id: number
  equipment_node_id: number
  defect_type_id: number
  description: string
  /** Sections to add ON TOP of whatever the equipment auto-maps to. Empty is valid when the
   *  equipment maps; required when it does not. */
  additional_section_ids: number[]
  reason?: string
}

export interface CreatePlanningBookingResponse {
  booking_id: number
  booking_source: string
  sections: { section_id: number; section_code: string; assignment_source: string; status: string }[]
  auto_mapped_section_ids: number[]
  added_section_ids: number[]
}

/**
 * Raise a booking as a planner.
 *
 * Deliberately carries NO booking_source, status, created_by or timestamps: the server fixes the
 * source to MANUAL and stamps the rest, so they are not the client's to send. Reuses the normal
 * booking entity and creation service - there is no parallel planning booking model.
 */
export function createPlanningBooking(
  payload: CreatePlanningBookingRequest,
): Promise<CreatePlanningBookingResponse> {
  return apiFetch<CreatePlanningBookingResponse>('/api/bookings/planning', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

