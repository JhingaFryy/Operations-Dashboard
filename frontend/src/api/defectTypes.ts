import { apiFetch } from './client'
import type { DefectType } from '../types'

export function listDefectTypes(): Promise<DefectType[]> {
  return apiFetch<DefectType[]>('/api/booking-defect-types')
}
