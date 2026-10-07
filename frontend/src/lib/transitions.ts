import type { AssignmentStatus } from '../types'

/** The ONLY assignment transitions the UI may ever offer.
 *
 * This mirrors the backend gate in
 * app/services/section_dashboard_service.py — it does not replace it. The
 * backend re-validates every transition and rejects anything else with 409
 * regardless of what is rendered here. Its purpose is to guarantee the UI
 * never draws a button for a transition the backend forbids:
 *
 *   OPEN        → IN_PROGRESS   (Start Work)
 *   IN_PROGRESS → ATTENDED      (Mark Attended)
 *   REOPENED    → IN_PROGRESS   (Start Work)
 *   ATTENDED    → REOPENED      (Reopen, Admin only)
 *
 * Forbidden and therefore never rendered: OPEN → ATTENDED,
 * REOPENED → ATTENDED, and ATTENDED → REOPENED for a non-Admin.
 */
export type AssignmentAction = 'START' | 'ATTEND' | 'REOPEN'

export function allowedAssignmentActions(
  status: AssignmentStatus,
  options: { canReopen: boolean },
): AssignmentAction[] {
  switch (status) {
    case 'OPEN':
    case 'REOPENED':
      return ['START']
    case 'IN_PROGRESS':
      return ['ATTEND']
    case 'ATTENDED':
      return options.canReopen ? ['REOPEN'] : []
    default:
      return []
  }
}

export function canPerform(
  action: AssignmentAction,
  status: AssignmentStatus,
  options: { canReopen: boolean },
): boolean {
  return allowedAssignmentActions(status, options).includes(action)
}
