import { useEffect, useState } from 'react'
import { getChecksheetRequirementProgress } from '../../api/workflow'
import { friendlyErrorMessage } from '../../api/client'
import { stageLabel } from '../../lib/format'
import { ChecksheetProgressRow } from './ChecksheetProgressRow'
import { ErrorState, LoadingState } from '../ui/States'
import { Tag } from '../ui/StatusBadge'
import type { VisitChecksheetProgress } from '../../types'

/** Operations Dashboard Phase 5B.2, refactored by Business Rule Alignment: merges the frozen
 * "Required Checksheets" work-package snapshot (Phase 5B.1) with live BL-DCMS submission
 * correlation into one operational view. "Satisfied" means the technician has submitted the
 * checksheet (SUBMITTED/UNDER_REVIEW/APPROVED) - Supervisor DSC approval is no longer required
 * for maintenance-workflow completion, though each checksheet's own signature/record status is
 * still always shown. Deliberately never claims a Dashboard stage is "Completed" - only that the
 * required rows for a stage are currently "ready_for_completion". The actual shed_visit_stage
 * status (the workflow stage strip) is a completely separate, independently-visible signal. */
export function ChecksheetRequirementProgress({
  visitId,
  refreshKey,
  onLoaded,
}: {
  visitId: number
  /** Bump this (e.g. after a reconciliation call) to force a re-fetch without waiting on visitId
   * to change - see ShedVisitWorkflowPage's "Reconcile Workflow" action. */
  refreshKey?: number
  /** Lets the page reuse this component's already-fetched figures in the stage strip above
   * instead of issuing a second identical request. Presentation sharing only - the payload is
   * passed through untouched. */
  onLoaded?: (progress: VisitChecksheetProgress) => void
}) {
  const [progress, setProgress] = useState<VisitChecksheetProgress | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    getChecksheetRequirementProgress(visitId)
      .then((result) => {
        setProgress(result)
        onLoaded?.(result)
      })
      .catch((err) => setError(friendlyErrorMessage(err)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visitId, refreshKey])

  return (
    <section className="panel checksheet-requirement-progress">
      <div className="panel-header">
        <h2>Required Checksheet Progress</h2>
      </div>
      <p className="field-hint">
        Correlates this visit's required checksheets against BL-DCMS's submission status
        (technician submission satisfies — digital-signature approval is not required). Never
        changes the workflow stage status shown above.
      </p>

      {loading && <LoadingState label="Loading checksheet progress…" />}

      {error && <ErrorState message={error} />}

      {!loading && !error && progress && !progress.work_package_generated && (
        <p className="field-hint">No checksheet work package has been generated for this visit yet.</p>
      )}

      {!loading && !error && progress && progress.work_package_generated && !progress.bldcms_available && (
        <div className="form-error" role="alert">
          BL-DCMS checksheet status cannot currently be checked. This does not mean requirements
          are incomplete — it means submission status cannot be verified right now.
        </div>
      )}

      {!loading && !error && progress && progress.work_package_generated && progress.bldcms_available && (
        <ul className="checksheet-progress-stage-list">
          {progress.stages.map((stage) => (
            <li key={stage.workflow_stage_type}>
              <div className="checksheet-progress-stage-header">
                <h3>{stageLabel(stage.workflow_stage_type)}</h3>
                <Tag tone={stage.ready_for_completion ? 'success' : 'neutral'}>
                  {stage.ready_for_completion ? 'Ready for completion' : `${stage.required_remaining} remaining`}
                </Tag>
              </div>
              <p className="field-hint">
                Required: {stage.required_satisfied} / {stage.required_total} submitted
                {stage.optional_total > 0 &&
                  ` · Optional: ${stage.optional_satisfied} / ${stage.optional_total} submitted`}
              </p>
              <ul className="checksheet-progress-row-list">
                {stage.requirements.map((item) => (
                  <ChecksheetProgressRow key={item.requirement_id} item={item} />
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
