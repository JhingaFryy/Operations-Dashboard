import { useEffect, useState } from 'react'
import { getVisitChecksheetSummary, getVisitChecksheets } from '../../api/workflow'
import { friendlyErrorMessage } from '../../api/client'
import { formatDateTimeShort, stageLabel } from '../../lib/format'
import { ChecksheetStatusBadge } from '../ui/StatusBadge'
import { ErrorState, LoadingState } from '../ui/States'
import type { ChecksheetIntegrationItem, ChecksheetSummaryResult } from '../../types'

const STAGE_TYPES = ['TEST_BEFORE', 'SCHEDULE_INSPECTION', 'TEST_AFTER'] as const

const STAGE_LABELS: Record<string, string> = {
  TEST_BEFORE: 'Test Before',
  SCHEDULE_INSPECTION: 'Schedule Inspection',
  TEST_AFTER: 'Test After',
}

/** Read-only BL-DCMS checksheet status/progress for a shed visit (Operations Dashboard
 * Integration Phase 4). Deliberately never renders "Stage Complete" from these counts alone -
 * BL-DCMS's summary today reflects EXISTING checksheet rows only, not the complete required
 * work package (that materialization is a later phase) - see the field label below. Purely
 * observational: no control here starts/completes a stage, touches a booking, or affects Shed
 * Out eligibility. */
export function ChecksheetProgress({ visitId }: { visitId: number }) {
  const [summary, setSummary] = useState<ChecksheetSummaryResult | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [summaryError, setSummaryError] = useState<string | null>(null)

  const [expanded, setExpanded] = useState(false)
  const [items, setItems] = useState<ChecksheetIntegrationItem[]>([])
  const [itemsLoading, setItemsLoading] = useState(false)
  const [itemsAvailable, setItemsAvailable] = useState(true)
  const [itemsError, setItemsError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    setSummaryLoading(true)
    setSummaryError(null)
    getVisitChecksheetSummary(visitId)
      .then((result) => {
        if (mounted) setSummary(result)
      })
      .catch((err) => {
        if (mounted) setSummaryError(friendlyErrorMessage(err))
      })
      .finally(() => {
        if (mounted) setSummaryLoading(false)
      })
    return () => {
      mounted = false
    }
  }, [visitId])

  function loadItems() {
    setItemsLoading(true)
    setItemsError(null)
    getVisitChecksheets(visitId)
      .then((result) => {
        setItems(result.items)
        setItemsAvailable(result.available)
      })
      .catch((err) => setItemsError(friendlyErrorMessage(err)))
      .finally(() => setItemsLoading(false))
  }

  function toggleExpanded() {
    const next = !expanded
    setExpanded(next)
    if (next && items.length === 0 && itemsAvailable) {
      loadItems()
    }
  }

  const stageByType = new Map((summary?.stages ?? []).map((s) => [s.workflow_stage_type, s]))
  const hasAnyChecksheets = (summary?.stages ?? []).some((s) => s.total_checksheets > 0)

  return (
    <section className="panel checksheet-progress">
      <div className="panel-header">
        <h2>Checksheet Progress</h2>
      </div>
      <p className="field-hint">
        Status of BL-DCMS checksheets linked to this visit. This reflects existing checksheet
        records only, not a complete required work package — it is not a statement that a
        workflow stage is complete.
      </p>

      {summaryLoading && <LoadingState label="Loading checksheet status…" />}

      {summaryError && <ErrorState message={summaryError} />}

      {!summaryLoading && !summaryError && summary && !summary.available && (
        <p className="field-hint" role="status">
          BL-DCMS checksheet status is temporarily unavailable.
        </p>
      )}

      {!summaryLoading && !summaryError && summary && summary.available && (
        <>
          <ul className="checksheet-stage-summary-list">
            {STAGE_TYPES.map((stageType) => {
              const stage = stageByType.get(stageType)
              const approved = stage?.approved_checksheets ?? 0
              const total = stage?.total_checksheets ?? 0
              return (
                <li key={stageType}>
                  <span className="checksheet-stage-name">{STAGE_LABELS[stageType]}</span>
                  <span className="checksheet-stage-count">
                    {approved} / {total} approved
                  </span>
                </li>
              )
            })}
          </ul>

          {!hasAnyChecksheets && (
            <p className="field-hint">No BL-DCMS checksheets linked to this visit yet.</p>
          )}

          <button type="button" className="btn btn-secondary btn-small" onClick={toggleExpanded}>
            {expanded ? 'Hide checksheet list' : 'View checksheet list'}
          </button>

          {expanded && (
            <div className="checksheet-list">
              {itemsLoading && <LoadingState label="Loading checksheets…" />}
              {itemsError && <ErrorState message={itemsError} />}
              {!itemsLoading && !itemsError && !itemsAvailable && (
                <p className="field-hint" role="status">
                  BL-DCMS checksheet status is temporarily unavailable.
                </p>
              )}
              {!itemsLoading && !itemsError && itemsAvailable && items.length === 0 && (
                <p className="field-hint">No BL-DCMS checksheets linked to this visit yet.</p>
              )}
              {!itemsLoading && !itemsError && itemsAvailable && items.length > 0 && (
                <table className="data-table checksheet-list-table">
                  <thead>
                    <tr>
                      <th scope="col">Checksheet</th>
                      <th scope="col">Section</th>
                      <th scope="col">Equipment</th>
                      <th scope="col">Stage</th>
                      <th scope="col">Status</th>
                      <th scope="col">Signed</th>
                      <th scope="col">Signing Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id}>
                        <td>{item.template_name ?? `Checksheet #${item.id}`}</td>
                        <td>{item.section ?? '—'}</td>
                        <td>{item.equipment ?? '—'}</td>
                        <td>{stageLabel(item.workflow_stage_type)}</td>
                        <td>
                          <ChecksheetStatusBadge status={item.status} size="small" />
                        </td>
                        <td>{item.signed ? 'Yes' : 'No'}</td>
                        <td>{formatDateTimeShort(item.signing_timestamp)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
