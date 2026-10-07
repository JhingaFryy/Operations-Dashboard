import { apiFetch } from './client'
import type { SectionAssignment } from '../types'

export function listSectionAssignments(sectionCode: string): Promise<SectionAssignment[]> {
  return apiFetch<SectionAssignment[]>(`/api/sections/${encodeURIComponent(sectionCode)}/assignments`)
}

export function startAssignment(assignmentId: number): Promise<SectionAssignment> {
  return apiFetch<SectionAssignment>(`/api/section-assignments/${assignmentId}/start`, {
    method: 'POST',
  })
}

export function attendAssignment(assignmentId: number, remarks: string): Promise<SectionAssignment> {
  return apiFetch<SectionAssignment>(`/api/section-assignments/${assignmentId}/attend`, {
    method: 'POST',
    body: JSON.stringify({ remarks }),
  })
}

export function reopenAssignment(assignmentId: number, reason: string): Promise<SectionAssignment> {
  return apiFetch<SectionAssignment>(`/api/section-assignments/${assignmentId}/reopen`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}
