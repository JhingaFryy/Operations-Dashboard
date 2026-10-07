import { useEffect, useState } from 'react'
import { generateChecksheetWorkPackage, getChecksheetWorkPackage } from '../../api/workflow'
import { friendlyErrorMessage } from '../../api/client'
import { stageLabel } from '../../lib/format'
import { ConfirmActionDialog } from '../ui/ConfirmActionDialog'
import { ErrorState, LoadingState } from '../ui/States'
import { useAuth } from '../../auth/useAuth'
import { isAdmin } from '../../auth/permissions'
import type { ChecksheetWorkPackage } from '../../types'

/** Read-only "Required Checksheets" section (Operations Dashboard Integration Phase 5B.1) - the
 * materialized, visit-specific snapshot of BL-DCMS applicability. Deliberately separate from
 * ChecksheetProgress.tsx (Phase 4's "existing BL-DCMS records" panel) - this phase shows the two
 * side by side without correlating them into a completion percentage; that correlation is a
 * later phase. Generation is Admin-only and explicit here, never automatic. */
export function RequiredChecksheets({ visitId }: { visitId: number }) {
  const { user } = useAuth()
  const admin = isAdmin(user)

  const [workPackage, setWorkPackage] = useState<ChecksheetWorkPackage | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [confirming, setConfirming] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)

  function load() {
    setLoading(true)
    setLoadError(null)
    getChecksheetWorkPackage(visitId)
      .then(setWorkPackage)
      .catch((err) => setLoadError(friendlyErrorMessage(err)))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visitId])

  function handleGenerate() {
    setGenerating(true)
    setGenerateError(null)
    generateChecksheetWorkPackage(visitId)
      .then((result) => {
        setWorkPackage(result)
        setConfirming(false)
      })
      .catch((err) => setGenerateError(friendlyErrorMessage(err)))
      .finally(() => setGenerating(false))
  }

  return (
    <section className="panel required-checksheets">
      <div className="panel-header">
        <h2>Required Checksheets</h2>
      </div>
      <p className="field-hint">
        The checksheet requirements BL-DCMS applicability configuration produced for this visit's
        schedule and locomotive technology, frozen at the time it was generated. Editing BL-DCMS
        applicability later does not change this snapshot.
      </p>

      {loading && <LoadingState label="Loading required checksheets…" />}

      {loadError && <ErrorState message={loadError} onRetry={load} />}

      {!loading && !loadError && workPackage && !workPackage.generated && (
        <>
          {admin ? (
            <>
              {!confirming && (
                <button type="button" className="btn btn-primary" onClick={() => setConfirming(true)}>
                  Generate Checksheet Work Package
                </button>
              )}
              {confirming && (
                <ConfirmActionDialog
                  title="Generate the checksheet work package for this visit?"
                  description="The snapshot is frozen at generation time — later BL-DCMS applicability edits will not change it."
                  confirmLabel={generating ? 'Generating…' : 'Confirm Generate'}
                  busy={generating}
                  onConfirm={handleGenerate}
                  onCancel={() => setConfirming(false)}
                />
              )}
              {generateError && (
                <div className="form-error" role="alert">
                  {generateError}
                </div>
              )}
            </>
          ) : (
            <p className="field-hint">Work package not generated yet.</p>
          )}
        </>
      )}

      {!loading && !loadError && workPackage && workPackage.generated && (
        <ul className="required-checksheets-stage-list">
          {workPackage.stages.map((stage) => (
            <li key={stage.workflow_stage_type}>
              <h3>{stageLabel(stage.workflow_stage_type)}</h3>
              <table className="data-table required-checksheets-table">
                <thead>
                  <tr>
                    <th scope="col">Template</th>
                    <th scope="col">Section</th>
                    <th scope="col">Equipment</th>
                    <th scope="col">Required</th>
                  </tr>
                </thead>
                <tbody>
                  {stage.requirements.map((req) => (
                    <tr key={req.applicability_id}>
                      <td>{req.template_name}</td>
                      <td>{req.section_name ?? '—'}</td>
                      <td>{req.equipment_name ?? '—'}</td>
                      <td>
                        {/* Kept on the shared `.badge` vocabulary: this frozen
                            snapshot table is a record view, not an operational
                            status surface. */}
                        <span className={`badge ${req.is_required ? 'badge-direct' : 'badge-neutral'}`}>
                          {req.is_required ? 'Required' : 'Optional'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
