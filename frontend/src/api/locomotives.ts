import { apiFetch } from './client'
import type { Locomotive } from '../types'

export function searchLocomotives(query: string, limit?: number): Promise<Locomotive[]> {
  const params = new URLSearchParams({ q: query })
  if (limit != null) params.set('limit', String(limit))
  return apiFetch<Locomotive[]>(`/api/locomotives/search?${params.toString()}`)
}
