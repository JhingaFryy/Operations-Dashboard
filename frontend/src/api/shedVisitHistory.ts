import { ApiError, apiFetch, apiFetchBlob, friendlyErrorMessage } from './client'
import type { VisitHistoryDetail, VisitHistoryPage } from '../types'

/** Shed Visit History - the read-only archive of every shed visit. Filtering and paging happen
 * on the server; nothing here loads the whole history. */
export interface VisitHistoryFilters {
  loco_number?: string
  loco_model?: string
  schedule_family?: string
  schedule_variant?: string
  status?: string
  visit_id?: string
  arrived_from?: string
  arrived_to?: string
  departed_from?: string
  departed_to?: string
  departure_source?: string
  section_id?: string
  booking_status?: string
}

export const HISTORY_PAGE_SIZE = 25

export function historyQuery(filters: VisitHistoryFilters, page: number, pageSize = HISTORY_PAGE_SIZE): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    const trimmed = typeof value === 'string' ? value.trim() : ''
    if (trimmed) params.set(key, trimmed)
  }
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
  return params.toString()
}

export function searchVisitHistory(filters: VisitHistoryFilters, page: number): Promise<VisitHistoryPage> {
  return apiFetch<VisitHistoryPage>(`/api/shed-visit-history?${historyQuery(filters, page)}`)
}

export function getVisitHistory(visitId: number): Promise<VisitHistoryDetail> {
  return apiFetch<VisitHistoryDetail>(`/api/shed-visit-history/${visitId}`)
}

export function listHistoryLocoModels(): Promise<string[]> {
  return apiFetch<string[]>('/api/shed-visit-history/loco-models')
}

/** The signed PDF. `url` is the server-issued route from the checksheet row; only this app's
 * own API paths are accepted, so a row can never point the browser anywhere else. */
export function fetchSignedChecksheet(url: string): Promise<Blob> {
  if (!/^\/api\/shed-visit-history\/\d+\/checksheets\/\d+\/signed-document$/.test(url)) {
    return Promise.reject(new Error('Unexpected document address.'))
  }
  return apiFetchBlob(url)
}

/** Messages the server wrote for this page (scope refusals, missing documents, BL-DCMS down) are
 * already user-facing; anything else goes through the app's generic wording. */
export function historyErrorMessage(err: unknown): string {
  if (err instanceof ApiError && [403, 404, 502].includes(err.status) && err.message) return err.message
  return friendlyErrorMessage(err)
}
