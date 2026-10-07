import type { StageChecksheetProgress } from '../types'

/** Checksheet figures for one stage, taken verbatim from
 * /checksheet-requirement-progress. Nothing is re-derived here — in
 * particular the SUBMITTED/UNDER_REVIEW/APPROVED satisfaction rule is the
 * backend's, and `required_satisfied` is simply displayed. */
export interface StageChecksheetSummary {
  requiredTotal: number
  requiredSatisfied: number
  rejectedCount: number
  ready: boolean
}

export function checksheetSummaryFor(stage: StageChecksheetProgress): StageChecksheetSummary {
  return {
    requiredTotal: stage.required_total,
    requiredSatisfied: stage.required_satisfied,
    rejectedCount: stage.requirements.filter((r) => r.progress_state === 'REJECTED').length,
    ready: stage.ready_for_completion,
  }
}
