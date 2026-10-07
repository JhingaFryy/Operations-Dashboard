import { apiFetch } from './client'
import type {
  BookingDeletionPreview,
  DeletionEventDetail,
  DeletionEventSummary,
  DeletionItemPage,
  VisitDeletionPreview,
} from '../types'

/** Admin destructive deletion. Every route here is Admin-only server-side.
 *
 * THE PASSWORD GOES IN THE BODY, NEVER THE URL. A query parameter or path segment lands in nginx
 * access logs, browser history and Referer headers; a body does not. DELETE with a body is unusual
 * but legal, and apiFetch passes it through unchanged.
 *
 * NOTHING HERE SENDS AN IDENTITY. No employee id, no user id, no role - the server derives the
 * acting Admin from the session token alone and the request schema forbids unknown keys, so a
 * nominated actor would be a 422 rather than silently ignored.
 */

export function previewVisitDeletion(visitId: number): Promise<VisitDeletionPreview> {
  return apiFetch<VisitDeletionPreview>(`/api/admin/shed-visits/${visitId}/deletion-preview`)
}

export function previewBookingDeletion(bookingId: number): Promise<BookingDeletionPreview> {
  return apiFetch<BookingDeletionPreview>(`/api/admin/bookings/${bookingId}/deletion-preview`)
}

export function deleteShedVisit(
  visitId: number,
  body: { password: string; reason: string; confirmation: string; operationId?: string },
): Promise<DeletionEventDetail> {
  return apiFetch<DeletionEventDetail>(`/api/admin/shed-visits/${visitId}`, {
    method: 'DELETE',
    body: JSON.stringify({
      password: body.password,
      reason: body.reason,
      confirmation: body.confirmation,
      ...(body.operationId ? { operation_id: body.operationId } : {}),
    }),
  })
}

export function deleteBooking(
  bookingId: number,
  body: { password: string; reason: string; operationId?: string },
): Promise<DeletionEventDetail> {
  return apiFetch<DeletionEventDetail>(`/api/admin/bookings/${bookingId}`, {
    method: 'DELETE',
    body: JSON.stringify({
      password: body.password,
      reason: body.reason,
      ...(body.operationId ? { operation_id: body.operationId } : {}),
    }),
  })
}

export interface DeletionHistoryFilters {
  deletionType?: string
  shedVisitId?: number
  locoNumber?: string
  limit?: number
}

export function listDeletionHistory(
  filters: DeletionHistoryFilters = {},
): Promise<DeletionEventSummary[]> {
  const params = new URLSearchParams()
  if (filters.deletionType) params.set('deletion_type', filters.deletionType)
  if (filters.shedVisitId != null) params.set('shed_visit_id', String(filters.shedVisitId))
  if (filters.locoNumber) params.set('loco_number', filters.locoNumber)
  if (filters.limit != null) params.set('limit', String(filters.limit))
  const query = params.toString()
  return apiFetch<DeletionEventSummary[]>(`/api/admin/deletion-history${query ? `?${query}` : ''}`)
}

export function getDeletionEvent(eventId: number): Promise<DeletionEventDetail> {
  return apiFetch<DeletionEventDetail>(`/api/admin/deletion-history/${eventId}`)
}

export function getDeletionItems(
  eventId: number,
  options: { entityType?: string; limit?: number; offset?: number } = {},
): Promise<DeletionItemPage> {
  const params = new URLSearchParams()
  if (options.entityType) params.set('entity_type', options.entityType)
  if (options.limit != null) params.set('limit', String(options.limit))
  if (options.offset != null) params.set('offset', String(options.offset))
  const query = params.toString()
  return apiFetch<DeletionItemPage>(
    `/api/admin/deletion-history/${eventId}/items${query ? `?${query}` : ''}`,
  )
}
