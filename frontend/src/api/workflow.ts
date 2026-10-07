import { ApiError, apiFetch } from './client'
import type {
  ChecksheetIntegrationListResult,
  ChecksheetSummaryResult,
  ChecksheetWorkPackage,
  StageBlockedDetail,
  TestBeforeBooking,
  VisitChecksheetProgress,
  VisitReconciliationResult,
  Workflow,
  WorkflowStage,
} from '../types'

export type ScheduleInspectionBooking = TestBeforeBooking
export type TestAfterBooking = TestBeforeBooking

export function getWorkflow(visitId: number): Promise<Workflow> {
  return apiFetch<Workflow>(`/api/shed-visits/${visitId}/workflow`)
}

export function startTestBefore(visitId: number): Promise<WorkflowStage> {
  return apiFetch<WorkflowStage>(`/api/shed-visits/${visitId}/stages/test-before/start`, {
    method: 'POST',
  })
}

export function completeTestBefore(visitId: number): Promise<WorkflowStage> {
  return apiFetch<WorkflowStage>(`/api/shed-visits/${visitId}/stages/test-before/complete`, {
    method: 'POST',
  })
}

export function listTestBeforeBookings(visitId: number): Promise<TestBeforeBooking[]> {
  return apiFetch<TestBeforeBooking[]>(`/api/shed-visits/${visitId}/test-before/bookings`)
}

// Schedule Inspection: start/complete, plus the read-only findings list. Minor Inspection checksheets do
// not raise bookings; the list can only show historical rows.

export function startScheduleInspection(visitId: number): Promise<WorkflowStage> {
  return apiFetch<WorkflowStage>(`/api/shed-visits/${visitId}/stages/schedule-inspection/start`, {
    method: 'POST',
  })
}

export function completeScheduleInspection(visitId: number): Promise<WorkflowStage> {
  return apiFetch<WorkflowStage>(`/api/shed-visits/${visitId}/stages/schedule-inspection/complete`, {
    method: 'POST',
  })
}

export function listScheduleInspectionBookings(visitId: number): Promise<ScheduleInspectionBooking[]> {
  return apiFetch<ScheduleInspectionBooking[]>(`/api/shed-visits/${visitId}/schedule-inspection/bookings`)
}

// Test After: start/complete, the read-only findings list, and the read-only Test Before recheck-reference
// list. Test Before / Test After findings are created only from the Android checksheet (via BL-DCMS).

export function startTestAfter(visitId: number): Promise<WorkflowStage> {
  return apiFetch<WorkflowStage>(`/api/shed-visits/${visitId}/stages/test-after/start`, {
    method: 'POST',
  })
}

export function completeTestAfter(visitId: number): Promise<WorkflowStage> {
  return apiFetch<WorkflowStage>(`/api/shed-visits/${visitId}/stages/test-after/complete`, {
    method: 'POST',
  })
}

export function listTestAfterBookings(visitId: number): Promise<TestAfterBooking[]> {
  return apiFetch<TestAfterBooking[]>(`/api/shed-visits/${visitId}/test-after/bookings`)
}

export function listTestAfterReferenceBookings(visitId: number): Promise<TestAfterBooking[]> {
  return apiFetch<TestAfterBooking[]>(`/api/shed-visits/${visitId}/test-after/reference-bookings`)
}

// Operations Dashboard Integration Phase 4: read-only BL-DCMS checksheet status/progress.
// Observation only - never starts/completes a stage. See ChecksheetProgress.tsx.

export function getVisitChecksheets(
  visitId: number,
  workflowStageType?: string,
): Promise<ChecksheetIntegrationListResult> {
  const query = workflowStageType ? `?workflow_stage_type=${encodeURIComponent(workflowStageType)}` : ''
  return apiFetch<ChecksheetIntegrationListResult>(`/api/shed-visits/${visitId}/checksheets${query}`)
}

export function getVisitChecksheetSummary(visitId: number): Promise<ChecksheetSummaryResult> {
  return apiFetch<ChecksheetSummaryResult>(`/api/shed-visits/${visitId}/checksheet-summary`)
}

// Operations Dashboard Integration Phase 5B.1: materialized checksheet work packages. Read is
// available to any authenticated Dashboard user under existing visit visibility rules;
// generation is Admin-only (enforced server-side, not just by hiding the button).

export function getChecksheetWorkPackage(visitId: number): Promise<ChecksheetWorkPackage> {
  return apiFetch<ChecksheetWorkPackage>(`/api/shed-visits/${visitId}/checksheet-work-package`)
}

export function generateChecksheetWorkPackage(visitId: number): Promise<ChecksheetWorkPackage> {
  return apiFetch<ChecksheetWorkPackage>(`/api/shed-visits/${visitId}/checksheet-work-package`, {
    method: 'POST',
  })
}

// Operations Dashboard Phase 5B.2: Required Checksheet vs BL-DCMS APPROVED correlation.
// Read-only, available under the same visit visibility rules as the work package itself. Never
// mutates a stage, a booking, Shed Out eligibility, or the work package.

export function getChecksheetRequirementProgress(visitId: number): Promise<VisitChecksheetProgress> {
  return apiFetch<VisitChecksheetProgress>(`/api/shed-visits/${visitId}/checksheet-requirement-progress`)
}

// Operations Dashboard Phase 5B.3: Authoritative Checksheet Stage Reconciliation. The sole
// mutation path that may transition a shed_visit_stage to COMPLETED - Admin-only, asks the
// system to evaluate evidence (digitally signed BL-DCMS checksheets + Booking.status) rather
// than manually overriding it. See app/services/checksheet_stage_reconciliation_service.py.

export function reconcileChecksheetStages(visitId: number): Promise<VisitReconciliationResult> {
  return apiFetch<VisitReconciliationResult>(`/api/shed-visits/${visitId}/reconcile-checksheet-stages`, {
    method: 'POST',
  })
}

/** Stage-completion failures carry a structured
 * {code, stage, open_bookings} detail (see backend
 * app/services/test_before_service.py:complete_test_before) so the UI can
 * show exactly which bookings/sections are still pending. */
export function asStageBlockedDetail(err: unknown): StageBlockedDetail | null {
  if (err instanceof ApiError && err.detail && typeof err.detail === 'object' && 'code' in err.detail) {
    return err.detail as StageBlockedDetail
  }
  return null
}
