import type { ReactNode } from 'react'
import { formatDateTimeShort, stageLabel } from '../../lib/format'
import { StageStatusBadge, Tag } from '../ui/StatusBadge'
import type { StageChecksheetSummary } from '../../lib/stageProgress'
import type { WorkflowStage } from '../../types'

/** One workflow stage (TEST_BEFORE / SCHEDULE_INSPECTION / TEST_AFTER, plus
 * SPECIAL_CHECKING where a visit still has one) as a compact card:
 * order, name, state, timings, and — when the page has already loaded them —
 * checksheet and finding figures. It renders no controls; starting and
 * completing a stage stays with the stage's own section below, which owns
 * the Admin-only gating. */
export function StageProgressCard({
  stage,
  checksheets,
  outstandingFindings,
  blocked,
}: {
  stage: WorkflowStage
  checksheets?: StageChecksheetSummary
  /** Findings for this stage not yet attended in every responsible section.
   * Undefined when the page has not loaded that stage's findings. */
  outstandingFindings?: number
  /** True when this stage is PENDING only because an earlier stage in the
   * same visit has not completed yet — i.e. it is not merely "not started",
   * it cannot be started yet. Computed by the caller from the same stage
   * statuses already shown elsewhere on the page (no new business state);
   * purely changes the card's visual weight, never its status text. */
  blocked?: boolean
}) {
  return (
    <li
      className={`stage-list-item stage-progress-card stage-progress-card-${stage.status.toLowerCase()}${blocked ? ' stage-progress-card-blocked' : ''}`}
    >
      <div className="stage-progress-head">
        <span className="stage-list-order">{stage.stage_order}.</span>
        <span className="stage-list-name stage-progress-name">{stageLabel(stage.stage_type)}</span>
        <StageStatusBadge status={stage.status} size="small" />
        {blocked && <Tag tone="neutral">Waiting</Tag>}
      </div>

      <dl className="stage-progress-facts">
        {stage.started_at && (
          <div>
            <dt>Started</dt>
            <dd>{formatDateTimeShort(stage.started_at)}</dd>
          </div>
        )}
        {stage.skipped_at && (
          <div>
            <dt>Skipped</dt>
            <dd>{formatDateTimeShort(stage.skipped_at)}</dd>
          </div>
        )}
        {stage.completed_at && (
          <div>
            <dt>Finished</dt>
            <dd>{formatDateTimeShort(stage.completed_at)}</dd>
          </div>
        )}
        {checksheets && checksheets.requiredTotal > 0 && (
          <div>
            <dt>Checksheets</dt>
            <dd>
              {checksheets.requiredSatisfied} of {checksheets.requiredTotal} required submitted
            </dd>
          </div>
        )}
        {outstandingFindings !== undefined && outstandingFindings > 0 && (
          <div>
            <dt>Findings</dt>
            <dd className="stage-progress-attention">
              {outstandingFindings} awaiting attendance
            </dd>
          </div>
        )}
      </dl>

      {checksheets && checksheets.rejectedCount > 0 && (
        <p className="stage-progress-flag stage-progress-flag-danger">
          {checksheets.rejectedCount === 1
            ? '1 checksheet was rejected and needs resubmission'
            : `${checksheets.rejectedCount} checksheets were rejected and need resubmission`}
        </p>
      )}
    </li>
  )
}

/** The stage strip itself. Keeps the `stage-list` container class so the
 * ordered list remains one addressable workflow overview. */
export function StageProgressList({ children }: { children: ReactNode }) {
  return <ol className="stage-list stage-progress-list">{children}</ol>
}
