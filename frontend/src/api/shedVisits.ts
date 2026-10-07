import { ApiError, apiFetch } from './client'
import type {
  BookingBlocker,
  CurrentShedVisit,
  ShedInErrorDetail,
  ShedInRequest,
  ShedInResponse,
  ShedOutEligibility,
  ShedOutRequestPayload,
  ShedOutResult,
  ScheduleTransitionResult,
  StageBlocker,
} from '../types'

export function createShedIn(payload: ShedInRequest): Promise<ShedInResponse> {
  return apiFetch<ShedInResponse>('/api/shed-visits/in', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listCurrentShedVisits(): Promise<CurrentShedVisit[]> {
  return apiFetch<CurrentShedVisit[]>('/api/shed-visits/current')
}

/** Marks the authoritative start of schedule work (Spare -> In Progress). The timestamp is
 *  caller-supplied: the UI defaults it to now but keeps it editable. */
export function startSchedule(visitId: number, startedAt: string): Promise<ScheduleTransitionResult> {
  return apiFetch<ScheduleTransitionResult>(`/api/shed-visits/${visitId}/start-schedule`, {
    method: 'POST',
    body: JSON.stringify({ started_at: startedAt }),
  })
}

/** MINOR: the actual inspection is complete (In Progress -> Inspection Complete) - Test After and
 *  Mark Ready come next. MAJOR: the schedule is complete (In Progress -> Ready). Shed Out still
 *  independently enforces its own booking/checksheet gates. */
export function completeSchedule(visitId: number, completedAt: string): Promise<ScheduleTransitionResult> {
  return apiFetch<ScheduleTransitionResult>(`/api/shed-visits/${visitId}/complete-schedule`, {
    method: 'POST',
    body: JSON.stringify({ completed_at: completedAt }),
  })
}

/** MINOR only: Inspection Complete -> Ready. The backend requires Test After to be completed AND
 *  every required checksheet for the visit to be satisfied - see asChecksheetsIncompleteDetail. */
export function markReady(visitId: number, readyAt: string): Promise<ScheduleTransitionResult> {
  return apiFetch<ScheduleTransitionResult>(`/api/shed-visits/${visitId}/mark-ready`, {
    method: 'POST',
    body: JSON.stringify({ ready_at: readyAt }),
  })
}

export function getShedOutEligibility(visitId: number): Promise<ShedOutEligibility> {
  return apiFetch<ShedOutEligibility>(`/api/shed-visits/${visitId}/shed-out-eligibility`)
}

export function shedOut(visitId: number, payload: ShedOutRequestPayload): Promise<ShedOutResult> {
  return apiFetch<ShedOutResult>(`/api/shed-visits/${visitId}/out`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** Shed In failures carry a structured {code, message, booking_index, ...}
 * detail (see backend app/services/shed_visit_service.py). Extracts it so
 * the form can highlight the specific booking row that failed. */
export function asShedInErrorDetail(err: unknown): ShedInErrorDetail | null {
  if (err instanceof ApiError && err.detail && typeof err.detail === 'object' && 'code' in err.detail) {
    return err.detail as ShedInErrorDetail
  }
  return null
}


/** One required checksheet that is still outstanding, as the backend reports it. */
export interface OutstandingChecksheet {
  requirement_id: number | null
  section: string | null
  label: string | null
  template_name: string | null
  stage: string | null
  maintenance_type: string | null
  status: string | null
  /** MISSING when no checksheet exists for it, otherwise its lifecycle status (DRAFT/REJECTED). */
  reason: string
}

/** The structured refusal shared by every Ready-capable transition (MINOR Mark Ready and MAJOR
 * Complete Schedule) - backend CHECKSHEETS_INCOMPLETE. */
export interface ChecksheetsIncompleteDetail {
  code: 'CHECKSHEETS_INCOMPLETE'
  message: string
  work_package_generated: boolean
  checksheets_readable: boolean
  total_required: number
  satisfied: number
  outstanding: number
  optional: number
  deactivated: number
  outstanding_entries: OutstandingChecksheet[]
  outstanding_entries_truncated: boolean
}

/** Ready was refused because required checksheets are outstanding. Returns null for every other
 * failure, so an unrelated error is never rendered as a checksheet blocker. */
export function asChecksheetsIncompleteDetail(err: unknown): ChecksheetsIncompleteDetail | null {
  if (
    err instanceof ApiError &&
    err.detail &&
    typeof err.detail === 'object' &&
    (err.detail as { code?: string }).code === 'CHECKSHEETS_INCOMPLETE'
  ) {
    return err.detail as ChecksheetsIncompleteDetail
  }
  return null
}


/** Shed Out's structured refusal (backend SHED_OUT_BLOCKED). `message` names the actual gate(s);
 * `checksheets` is the same CHECKSHEETS_INCOMPLETE payload Ready refuses with, present only when
 * checksheets are part of the problem. Every field beyond `code`/`message` is optional so an older
 * backend's response still renders. */
export interface ShedOutBlockedDetail {
  code: 'SHED_OUT_BLOCKED'
  message: string
  blocked_by?: ('STAGES' | 'BOOKINGS' | 'CHECKSHEETS')[]
  stage_blockers?: StageBlocker[]
  booking_blockers?: BookingBlocker[]
  checksheets?: ChecksheetsIncompleteDetail | null
}

/** Shed Out was refused by one of its gates. Null for every other failure - including a
 * VISIT_NOT_READY refusal, which carries its own specific message - so nothing unrelated is ever
 * rendered as a gate blocker. */
export function asShedOutBlockedDetail(err: unknown): ShedOutBlockedDetail | null {
  if (
    err instanceof ApiError &&
    err.detail &&
    typeof err.detail === 'object' &&
    (err.detail as { code?: string }).code === 'SHED_OUT_BLOCKED'
  ) {
    return err.detail as ShedOutBlockedDetail
  }
  return null
}
