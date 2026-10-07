import { apiFetch } from './client'
import type { Section } from '../types'

export function listSections(q?: string): Promise<Section[]> {
  const query = q ? `?q=${encodeURIComponent(q)}` : ''
  return apiFetch<Section[]>(`/api/sections${query}`)
}
