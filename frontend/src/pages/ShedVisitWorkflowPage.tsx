import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  asStageBlockedDetail,
  completeScheduleInspection,
  completeTestAfter,
  completeTestBefore,
  getWorkflow,
  listScheduleInspectionBookings,
  listTestAfterBookings,
  listTestAfterReferenceBookings,
  listTestBeforeBookings,
  reconcileChecksheetStages,
  startScheduleInspection,
  startTestAfter,
  startTestBefore,
} from '../api/workflow'
import { ApiError, friendlyErrorMessage } from '../api/client'
import {
  asShedOutBlockedDetail,
  getShedOutEligibility,
  shedOut,
  type ShedOutBlockedDetail,
} from '../api/shedVisits'
import { ShedOutBlockerNotice } from '../components/shed/ShedOutBlockerNotice'
import { useAuth } from '../auth/useAuth'
import { isAdmin } from '../auth/permissions'
import { DeleteShedVisitDialog } from '../components/admin/DeleteShedVisitDialog'
import { StatusBadge } from '../components/ui/StatusBadge'
import { ChecksheetProgress } from '../components/workflow/ChecksheetProgress'
import { ChecksheetRequirementProgress } from '../components/workflow/ChecksheetRequirementProgress'
import { RequiredChecksheets } from '../components/workflow/RequiredChecksheets'
import { StageProgressCard, StageProgressList } from '../components/workflow/StageProgressCard'
import { checksheetSummaryFor } from '../lib/stageProgress'
import { formatDateTime, stageLabel } from '../lib/format'
import { ShedOutReadinessPanel } from '../components/workflow/ShedOutReadinessPanel'
import { STAGE_STATUS_META, statusMeta, type StatusMeta } from '../lib/status'
import { TestBeforeBookingList } from '../components/workflow/TestBeforeBookingList'
import { LocoContextHeader } from '../components/shed/LocoContextHeader'
import { ConfirmActionDialog } from '../components/ui/ConfirmActionDialog'
import { ButtonLabel, ErrorState, LoadingState, RefreshIndicator } from '../components/ui/States'
import type {
  ShedOutEligibility,
  StageBlockedDetail,
  TestBeforeBooking,
  VisitChecksheetProgress,
  VisitReconciliationResult,
  Workflow,
} from '../types'

/** Counts findings that are not yet attended in every responsible section.
 * A display tally of already-loaded rows — the stage-completion gate itself
 * stays entirely with the backend. */
function outstandingFindingCount(bookings: TestBeforeBooking[]): number {
  return bookings.filter((b) => b.assignments.some((a) => a.status !== 'ATTENDED')).length
}

const RECONCILIATION_REASON_LABELS: Record<string, string> = {
  COMPLETED: 'Completed.',
  ALREADY_COMPLETED: 'Already completed.',
  SKIPPED: 'Skipped by an Admin for this shed visit.',
  WORK_PACKAGE_MISSING: 'No checksheet work package has been generated for this visit yet.',
  NO_REQUIRED_CHECKSHEETS: 'This stage has no required checksheets configured — cannot auto-complete.',
  REQUIRED_CHECKSHEETS_PENDING: 'Not every required checksheet has been submitted in BL-DCMS yet.',
  BOOKINGS_PENDING: 'Not every booking for this stage has been attended yet.',
  PREVIOUS_STAGE_NOT_COMPLETED: 'The previous workflow stage is not yet completed.',
  BLDCMS_UNAVAILABLE: 'BL-DCMS could not be reached — checksheet evidence could not be verified.',
  VISIT_CLOSED: 'This shed visit is closed.',
}

function reconciliationReasonMessage(reason?: string): string {
  if (!reason) return 'This stage is not yet ready for completion.'
  return RECONCILIATION_REASON_LABELS[reason] ?? reason
}


export function ShedVisitWorkflowPage() {
  const { visitId } = useParams()
  const idNum = Number(visitId)
  const { user } = useAuth()
  const admin = isAdmin(user)
  /** Whether the Admin has opened the deletion dialog. Opening it performs nothing: it fetches a
   *  preview and then demands a reason, a visit-specific phrase and the Admin's own password. */
  const [deleteOpen, setDeleteOpen] = useState(false)
  const navigate = useNavigate()

  const [workflow, setWorkflow] = useState<Workflow | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [apiErrorStatus, setApiErrorStatus] = useState<number | null>(null)

  const [tbBookings, setTbBookings] = useState<TestBeforeBooking[]>([])
  const [tbLoading, setTbLoading] = useState(false)

  const [siBookings, setSiBookings] = useState<TestBeforeBooking[]>([])
  const [siLoading, setSiLoading] = useState(false)

  const [taBookings, setTaBookings] = useState<TestBeforeBooking[]>([])
  const [taLoading, setTaLoading] = useState(false)
  const [taReferenceBookings, setTaReferenceBookings] = useState<TestBeforeBooking[]>([])
  const [taReferenceLoading, setTaReferenceLoading] = useState(false)

  const [confirmingStart, setConfirmingStart] = useState(false)
  const [starting, setStarting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const [completing, setCompleting] = useState(false)
  const [completeBlockers, setCompleteBlockers] = useState<StageBlockedDetail | null>(null)

  const [confirmingSiStart, setConfirmingSiStart] = useState(false)
  const [siStarting, setSiStarting] = useState(false)
  const [siCompleting, setSiCompleting] = useState(false)
  const [siCompleteBlockers, setSiCompleteBlockers] = useState<StageBlockedDetail | null>(null)
  const [siActionError, setSiActionError] = useState<string | null>(null)

  const [confirmingTaStart, setConfirmingTaStart] = useState(false)
  const [taStarting, setTaStarting] = useState(false)
  const [taCompleting, setTaCompleting] = useState(false)
  const [taCompleteBlockers, setTaCompleteBlockers] = useState<StageBlockedDetail | null>(null)
  const [taActionError, setTaActionError] = useState<string | null>(null)

  const [reconciling, setReconciling] = useState(false)
  const [reconcileError, setReconcileError] = useState<string | null>(null)
  const [reconcileResult, setReconcileResult] = useState<VisitReconciliationResult | null>(null)
  const [reconcileRefreshKey, setReconcileRefreshKey] = useState(0)

  // Shared with the stage strip so the checksheet figures can be shown beside
  // each stage without issuing a second identical request. Set by the
  // Required Checksheet Progress panel below when its own fetch resolves.
  const [checksheetProgress, setChecksheetProgress] = useState<VisitChecksheetProgress | null>(null)

  const [eligibility, setEligibility] = useState<ShedOutEligibility | null>(null)
  const [eligibilityLoading, setEligibilityLoading] = useState(false)
  const [departedAt, setDepartedAt] = useState('')
  const [shedOutRemarks, setShedOutRemarks] = useState('')
  const [confirmingShedOut, setConfirmingShedOut] = useState(false)
  const [shedOutSubmitting, setShedOutSubmitting] = useState(false)
  const [shedOutError, setShedOutError] = useState<string | null>(null)
  const [shedOutBlockers, setShedOutBlockers] = useState<ShedOutBlockedDetail | null>(null)
  const [shedOutSuccess, setShedOutSuccess] = useState<string | null>(null)

  const loadWorkflow = useCallback(() => {
    setLoading(true)
    setError(null)
    getWorkflow(idNum)
      .then((result) => {
        setWorkflow(result)
        setApiErrorStatus(null)
      })
      .catch((err) => {
        setApiErrorStatus(err instanceof ApiError ? err.status : null)
        setError(friendlyErrorMessage(err))
      })
      .finally(() => setLoading(false))
  }, [idNum])

  const loadTbBookings = useCallback(() => {
    setTbLoading(true)
    listTestBeforeBookings(idNum)
      .then(setTbBookings)
      .catch(() => setTbBookings([]))
      .finally(() => setTbLoading(false))
  }, [idNum])

  const loadSiBookings = useCallback(() => {
    setSiLoading(true)
    listScheduleInspectionBookings(idNum)
      .then(setSiBookings)
      .catch(() => setSiBookings([]))
      .finally(() => setSiLoading(false))
  }, [idNum])

  const loadTaBookings = useCallback(() => {
    setTaLoading(true)
    listTestAfterBookings(idNum)
      .then(setTaBookings)
      .catch(() => setTaBookings([]))
      .finally(() => setTaLoading(false))
  }, [idNum])

  const loadTaReferenceBookings = useCallback(() => {
    setTaReferenceLoading(true)
    listTestAfterReferenceBookings(idNum)
      .then(setTaReferenceBookings)
      .catch(() => setTaReferenceBookings([]))
      .finally(() => setTaReferenceLoading(false))
  }, [idNum])

  useEffect(() => {
    loadWorkflow()
  }, [loadWorkflow])

  const tbStage = workflow?.stages.find((s) => s.stage_type === 'TEST_BEFORE') ?? null
  const siStage = workflow?.stages.find((s) => s.stage_type === 'SCHEDULE_INSPECTION') ?? null
  // A Test Before an Admin skipped (never COMPLETED) releases the later stages exactly like a
  // completed one - the backend applies the same rule.
  const tbLegacyWaived = Boolean(workflow?.test_before_legacy_waived)
  const tbSatisfied = tbStage?.status === 'COMPLETED' || tbStage?.status === 'SKIPPED' || tbLegacyWaived
  const taStage = workflow?.stages.find((s) => s.stage_type === 'TEST_AFTER') ?? null

  useEffect(() => {
    if (tbStage && tbStage.status !== 'PENDING') {
      loadTbBookings()
    }
  }, [tbStage?.status, loadTbBookings])

  useEffect(() => {
    if (siStage && siStage.status !== 'PENDING') {
      loadSiBookings()
    }
  }, [siStage?.status, loadSiBookings])

  useEffect(() => {
    if (taStage && taStage.status !== 'PENDING') {
      loadTaBookings()
      loadTaReferenceBookings()
    }
  }, [taStage?.status, loadTaBookings, loadTaReferenceBookings])

  const loadEligibility = useCallback(() => {
    if (!admin) return
    setEligibilityLoading(true)
    getShedOutEligibility(idNum)
      .then(setEligibility)
      .catch(() => setEligibility(null))
      .finally(() => setEligibilityLoading(false))
  }, [idNum, admin])

  useEffect(() => {
    if (taStage?.status === 'COMPLETED' && workflow?.status !== 'CLOSED') {
      loadEligibility()
    }
  }, [taStage?.status, workflow?.status, loadEligibility])

  function handleStart() {
    setStarting(true)
    setActionError(null)
    startTestBefore(idNum)
      .then(() => {
        setConfirmingStart(false)
        loadWorkflow()
      })
      .catch((err) => setActionError(friendlyErrorMessage(err)))
      .finally(() => setStarting(false))
  }

  function handleComplete() {
    setCompleting(true)
    setActionError(null)
    setCompleteBlockers(null)
    completeTestBefore(idNum)
      .then(() => {
        loadWorkflow()
        loadTbBookings()
      })
      .catch((err) => {
        const blockers = asStageBlockedDetail(err)
        if (blockers?.code === 'STAGE_NOT_READY_FOR_COMPLETION' || blockers?.code === 'STAGE_BLOCKED_BY_BOOKINGS') {
          setCompleteBlockers(blockers)
        } else {
          setActionError(friendlyErrorMessage(err))
        }
      })
      .finally(() => setCompleting(false))
  }

  function handleSiStart() {
    setSiStarting(true)
    setSiActionError(null)
    startScheduleInspection(idNum)
      .then(() => {
        setConfirmingSiStart(false)
        loadWorkflow()
      })
      .catch((err) => setSiActionError(friendlyErrorMessage(err)))
      .finally(() => setSiStarting(false))
  }

  function handleSiComplete() {
    setSiCompleting(true)
    setSiActionError(null)
    setSiCompleteBlockers(null)
    completeScheduleInspection(idNum)
      .then(() => {
        loadWorkflow()
        loadSiBookings()
      })
      .catch((err) => {
        const blockers = asStageBlockedDetail(err)
        if (blockers?.code === 'STAGE_NOT_READY_FOR_COMPLETION' || blockers?.code === 'STAGE_BLOCKED_BY_BOOKINGS') {
          setSiCompleteBlockers(blockers)
        } else {
          setSiActionError(friendlyErrorMessage(err))
        }
      })
      .finally(() => setSiCompleting(false))
  }

  function handleTaStart() {
    setTaStarting(true)
    setTaActionError(null)
    startTestAfter(idNum)
      .then(() => {
        setConfirmingTaStart(false)
        loadWorkflow()
      })
      .catch((err) => setTaActionError(friendlyErrorMessage(err)))
      .finally(() => setTaStarting(false))
  }

  function handleTaComplete() {
    setTaCompleting(true)
    setTaActionError(null)
    setTaCompleteBlockers(null)
    completeTestAfter(idNum)
      .then(() => {
        loadWorkflow()
        loadTaBookings()
      })
      .catch((err) => {
        const blockers = asStageBlockedDetail(err)
        if (blockers?.code === 'STAGE_NOT_READY_FOR_COMPLETION' || blockers?.code === 'STAGE_BLOCKED_BY_BOOKINGS') {
          setTaCompleteBlockers(blockers)
        } else {
          setTaActionError(friendlyErrorMessage(err))
        }
      })
      .finally(() => setTaCompleting(false))
  }

  function handleReconcile() {
    setReconciling(true)
    setReconcileError(null)
    setReconcileResult(null)
    reconcileChecksheetStages(idNum)
      .then((result) => {
        setReconcileResult(result)
        loadWorkflow()
        setReconcileRefreshKey((k) => k + 1)
      })
      .catch((err) => setReconcileError(friendlyErrorMessage(err)))
      .finally(() => setReconciling(false))
  }

  function handleShedOut() {
    if (!departedAt) {
      setShedOutError('Departure date/time is required.')
      return
    }
    setShedOutSubmitting(true)
    setShedOutError(null)
    setShedOutBlockers(null)
    setShedOutSuccess(null)
    shedOut(idNum, { departed_at: new Date(departedAt).toISOString(), remarks: shedOutRemarks.trim() || null })
      .then(() => {
        setConfirmingShedOut(false)
        setShedOutSuccess(`Loco ${workflow?.loco_number} Shed Out successfully.`)
        loadWorkflow()
        loadEligibility()
      })
      .catch((err) => {
        const blockers = asShedOutBlockedDetail(err)
        if (blockers) {
          setShedOutBlockers(blockers)
        } else {
          setShedOutError(friendlyErrorMessage(err))
        }
      })
      .finally(() => setShedOutSubmitting(false))
  }

  // First load: a workflow-shaped skeleton. Every later reload (after a stage action) keeps the
  // page on screen - with its success messages and scroll position - and shows an indicator.
  if (loading && !workflow) return <LoadingState layout="workflow" label="Loading workflow…" />
  if (error) {
    return (
      <ErrorState
        message={error}
        variant={apiErrorStatus === 403 ? 'unauthorized' : 'error'}
        onRetry={loadWorkflow}
      />
    )
  }
  if (!workflow) return null

  const allAttended =
    tbBookings.length === 0 || tbBookings.every((b) => b.assignments.every((a) => a.status === 'ATTENDED'))
  const siAllAttended =
    siBookings.length === 0 || siBookings.every((b) => b.assignments.every((a) => a.status === 'ATTENDED'))
  const taAllAttended =
    taBookings.length === 0 || taBookings.every((b) => b.assignments.every((a) => a.status === 'ATTENDED'))

  const isMinor = workflow.schedule_family === 'MINOR'

  // A stage is "blocked" (as opposed to merely not-yet-started) only when it
  // is PENDING because the stage before it hasn't completed — the exact same
  // condition the field-hint text under each stage panel already states in
  // words. This is a display-only echo of that condition, not a new rule.
  const siBlocked = Boolean(siStage) && siStage!.status === 'PENDING' && !tbSatisfied
  const taBlocked = Boolean(taStage) && taStage!.status === 'PENDING' && siStage?.status !== 'COMPLETED'

  // One glance-level Shed Out state, shared by the hero and the workflow
  // rail below. Built only from data already loaded on this page (the Test
  // After stage status, the visit status, and — for Admins, who are the
  // only role that fetches it — Shed Out eligibility). No eligibility rule
  // is evaluated here; this only picks a label for what those fields
  // already say.
  let shedOutRailMeta: StatusMeta
  if (!taStage) {
    shedOutRailMeta = { label: 'Not Configured', tone: 'neutral', glyph: '·' }
  } else if (workflow.status === 'CLOSED') {
    shedOutRailMeta = { label: 'Shed Out', tone: 'success', glyph: '✓' }
  } else if (taStage.status !== 'COMPLETED') {
    shedOutRailMeta = { label: 'Pending', tone: 'neutral', glyph: '○' }
  } else if (admin && eligibilityLoading) {
    shedOutRailMeta = { label: 'Checking…', tone: 'info', glyph: '◐' }
  } else if (admin && eligibility?.eligible) {
    shedOutRailMeta = { label: 'Ready', tone: 'success', glyph: '✓' }
  } else if (admin && eligibility) {
    shedOutRailMeta = { label: 'Blocked', tone: 'danger', glyph: '✕' }
  } else {
    shedOutRailMeta = { label: 'Awaiting Shed Out', tone: 'active', glyph: '◐' }
  }

  return (
    <div className="workflow-page">
      <LocoContextHeader
        locoNumber={workflow.loco_number}
        scheduleFamily={workflow.schedule_family}
        scheduleVariant={workflow.schedule_variant}
        arrivalAt={workflow.arrival_at}
        status={workflow.status}
        shedOutReadiness={shedOutRailMeta}
      />

      <div className="panel">
        <div className="page-header-row">
          <h2>Workflow {loading && <RefreshIndicator label="Updating workflow…" />}</h2>
          {admin && workflow.status !== 'CLOSED' && (
            <button type="button" className="btn btn-secondary" disabled={reconciling} aria-busy={reconciling} onClick={handleReconcile}>
              <ButtonLabel busy={reconciling} label="Reconcile Workflow" busyLabel="Checking evidence…" />
            </button>
          )}
        </div>
        <p className="field-hint">
          Reconciliation asks the system to evaluate current evidence (submitted BL-DCMS
          checksheets and booking attendance) against each stage — it does not manually complete a
          stage on your behalf. Digital-signature approval remains meaningful recordkeeping but is
          not required for a stage to complete.
        </p>
        <StageProgressList>
          {workflow.stages.map((stage) => {
            const stageProgress = checksheetProgress?.stages.find(
              (s) => s.workflow_stage_type === stage.stage_type,
            )
            const findings =
              stage.stage_type === 'TEST_BEFORE'
                ? outstandingFindingCount(tbBookings)
                : stage.stage_type === 'SCHEDULE_INSPECTION'
                  ? outstandingFindingCount(siBookings)
                  : stage.stage_type === 'TEST_AFTER'
                    ? outstandingFindingCount(taBookings)
                    : undefined
            const blocked =
              stage.stage_type === 'SCHEDULE_INSPECTION' ? siBlocked : stage.stage_type === 'TEST_AFTER' ? taBlocked : false
            return (
              <StageProgressCard
                key={stage.id}
                stage={stage}
                checksheets={stageProgress ? checksheetSummaryFor(stageProgress) : undefined}
                outstandingFindings={findings}
                blocked={blocked}
              />
            )
          })}
        </StageProgressList>

        {isMinor && taStage && (
          <div className="workflow-rail-shedout" aria-hidden="false">
            <span className="workflow-rail-arrow" aria-hidden="true">
              →
            </span>
            <span className="workflow-rail-shedout-label">Shed Out</span>
            <StatusBadge meta={shedOutRailMeta} size="small" />
          </div>
        )}

        {!isMinor && (
          <p className="field-hint">
            This is a MAJOR{workflow.schedule_variant ? ` (${workflow.schedule_variant})` : ''} visit.
            Stage-by-stage Test Before / Schedule Inspection / Test After / Shed Out tracking applies
            to MINOR visits only — MAJOR completeness tracking is not yet implemented. See the
            checksheet and work-package panels below for the readiness information that does exist
            for this visit.
          </p>
        )}

        {reconcileError && (
          <div className="form-error" role="alert">
            {reconcileError}
          </div>
        )}

        {reconcileResult && (
          <ul className="reconciliation-result-list" role="status">
            {reconcileResult.stages.map((s) => (
              <li key={s.workflow_stage_type}>
                {stageLabel(s.workflow_stage_type)}: {statusMeta(STAGE_STATUS_META, s.current_status).label} —{' '}
                {reconciliationReasonMessage(s.reason)}
              </li>
            ))}
          </ul>
        )}
      </div>

      {actionError && (
        <div className="form-error" role="alert">
          {actionError}
        </div>
      )}

      {tbStage && (
        <div className="panel">
          <div className="page-header-row">
            <h2>Test Before</h2>
            {admin && tbStage.status === 'PENDING' && !confirmingStart && (
              <button type="button" className="btn btn-primary" onClick={() => setConfirmingStart(true)}>
                Start Test Before
              </button>
            )}
            {admin && confirmingStart && (
              <ConfirmActionDialog
                title="Start Test Before for this visit?"
                confirmLabel={starting ? 'Starting…' : 'Confirm Start'}
                busy={starting}
                onConfirm={handleStart}
                onCancel={() => setConfirmingStart(false)}
              />
            )}
            {admin && tbStage.status === 'IN_PROGRESS' && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={completing}
                aria-busy={completing}
                onClick={handleComplete}
              >
                <ButtonLabel busy={completing} label="Complete Test Before" busyLabel="Completing…" />
              </button>
            )}
          </div>

          {completeBlockers && (
            <div className="form-error" role="alert">
              <p>TEST_BEFORE cannot be completed — {reconciliationReasonMessage(completeBlockers.reason)}</p>
              {completeBlockers.open_bookings && completeBlockers.open_bookings.length > 0 && (
                <ul>
                  {completeBlockers.open_bookings.map((b) => (
                    <li key={b.booking_id}>
                      Booking #{b.booking_id}: {b.pending_sections.join(', ')}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {tbStage.status === 'PENDING' && !admin && <p className="field-hint">Test Before has not started yet.</p>}

          {tbLegacyWaived && (
            <p className="field-hint" role="note">
              This schedule was started before the Test Before gate existed, so Test Before is not
              required retroactively for this visit.
            </p>
          )}

          {tbStage.status === 'SKIPPED' && (
            <p className="field-hint" role="note">
              Test Before was skipped by an Admin for this shed visit
              {tbStage.skipped_at ? ` on ${formatDateTime(tbStage.skipped_at)}` : ''}
              {tbStage.skip_reason ? ` — ${tbStage.skip_reason}` : ''}. Test After is still required.
            </p>
          )}

          {tbStage.status !== 'PENDING' && tbStage.status !== 'SKIPPED' && (
            <>
              <h3>Findings</h3>
              <p className="field-hint">
                Test Before findings are raised by the technician on the Android Test Before checksheet
                and arrive here when it is submitted. They are assigned and attended below.
              </p>
              {tbLoading ? (
                <LoadingState label="Loading findings…" />
              ) : (
                <TestBeforeBookingList bookings={tbBookings} />
              )}

              {admin && tbStage.status === 'IN_PROGRESS' && (
                <p className="field-hint">
                  {allAttended
                    ? 'All findings are fully attended.'
                    : 'Open findings do not hold Test Before open or delay Start Schedule. They stay outstanding for their sections and must be attended before Shed Out.'}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {siStage && (
        <div className="panel">
          <div className="page-header-row">
            <h2>Schedule Inspection</h2>
            {admin && siStage.status === 'PENDING' && tbSatisfied && !confirmingSiStart && (
              <button type="button" className="btn btn-primary" onClick={() => setConfirmingSiStart(true)}>
                Start Schedule Inspection
              </button>
            )}
            {admin && confirmingSiStart && (
              <ConfirmActionDialog
                title="Start Schedule Inspection for this visit?"
                confirmLabel={siStarting ? 'Starting…' : 'Confirm Start'}
                busy={siStarting}
                onConfirm={handleSiStart}
                onCancel={() => setConfirmingSiStart(false)}
              />
            )}
            {admin && siStage.status === 'IN_PROGRESS' && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={siCompleting}
                aria-busy={siCompleting}
                onClick={handleSiComplete}
              >
                <ButtonLabel busy={siCompleting} label="Complete Schedule Inspection" busyLabel="Completing…" />
              </button>
            )}
          </div>

          {siActionError && (
            <div className="form-error" role="alert">
              {siActionError}
            </div>
          )}

          {siCompleteBlockers && (
            <div className="form-error" role="alert">
              <p>SCHEDULE_INSPECTION cannot be completed — {reconciliationReasonMessage(siCompleteBlockers.reason)}</p>
              {siCompleteBlockers.open_bookings && siCompleteBlockers.open_bookings.length > 0 && (
                <ul>
                  {siCompleteBlockers.open_bookings.map((b) => (
                    <li key={b.booking_id}>
                      Booking #{b.booking_id}: {b.pending_sections.join(', ')}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {siStage.status === 'PENDING' && !tbSatisfied && (
            <p className="field-hint">Schedule Inspection becomes available once Test Before is completed or skipped by an Admin.</p>
          )}
          {siStage.status === 'PENDING' && tbSatisfied && !admin && (
            <p className="field-hint">Schedule Inspection has not started yet.</p>
          )}

          {siStage.status !== 'PENDING' && (
            <>
              <h3>Findings</h3>
              <p className="field-hint">
                Minor Inspection checksheets do not raise bookings. Any listed here were recorded before
                that rule.
              </p>
              {siLoading ? (
                <LoadingState label="Loading findings…" />
              ) : (
                <TestBeforeBookingList
                  bookings={siBookings}
                  emptyMessage="No Schedule Inspection findings recorded yet."
                />
              )}

              {admin && siStage.status === 'IN_PROGRESS' && (
                <p className="field-hint">
                  {siAllAttended
                    ? 'All findings are fully attended — Schedule Inspection can be completed.'
                    : 'Schedule Inspection can only be completed once every finding is fully attended in every responsible section.'}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {taStage && (
        <div className="panel">
          <div className="page-header-row">
            <h2>Test After</h2>
            {admin && taStage.status === 'PENDING' && siStage?.status === 'COMPLETED' && !confirmingTaStart && (
              <button type="button" className="btn btn-primary" onClick={() => setConfirmingTaStart(true)}>
                Start Test After
              </button>
            )}
            {admin && confirmingTaStart && (
              <ConfirmActionDialog
                title="Start Test After for this visit?"
                confirmLabel={taStarting ? 'Starting…' : 'Confirm Start'}
                busy={taStarting}
                onConfirm={handleTaStart}
                onCancel={() => setConfirmingTaStart(false)}
              />
            )}
            {admin && taStage.status === 'IN_PROGRESS' && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={taCompleting}
                aria-busy={taCompleting}
                onClick={handleTaComplete}
              >
                <ButtonLabel busy={taCompleting} label="Complete Test After" busyLabel="Completing…" />
              </button>
            )}
          </div>

          {taActionError && (
            <div className="form-error" role="alert">
              {taActionError}
            </div>
          )}

          {taCompleteBlockers && (
            <div className="form-error" role="alert">
              <p>TEST_AFTER cannot be completed — {reconciliationReasonMessage(taCompleteBlockers.reason)}</p>
              {taCompleteBlockers.open_bookings && taCompleteBlockers.open_bookings.length > 0 && (
                <ul>
                  {taCompleteBlockers.open_bookings.map((b) => (
                    <li key={b.booking_id}>
                      Booking #{b.booking_id}: {b.pending_sections.join(', ')}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {taStage.status === 'PENDING' && siStage?.status !== 'COMPLETED' && (
            <p className="field-hint">Test After becomes available once Schedule Inspection is completed.</p>
          )}
          {taStage.status === 'PENDING' && siStage?.status === 'COMPLETED' && !admin && (
            <p className="field-hint">Test After has not started yet.</p>
          )}

          {taStage.status !== 'PENDING' && (
            <>
              <section className="test-before-reference-section">
                <h3>Test Before Bookings — Recheck Reference</h3>
                <p className="field-hint">
                  These defects were recorded during Test Before for this shed visit. Use them as a
                  reference while performing Test After. This list is read-only — it does not reopen or
                  change anything; if a defect still needs attention, inform the concerned authority so an
                  Admin can reopen it manually through the Section Dashboard.
                </p>
                {taReferenceLoading ? (
                  <LoadingState label="Loading reference bookings…" />
                ) : (
                  <TestBeforeBookingList
                    bookings={taReferenceBookings}
                    emptyMessage="No Test Before findings were recorded for this shed visit."
                  />
                )}
              </section>

              <section className="test-after-findings-section">
                <h4>Test After Findings</h4>
                <p className="field-hint">
                  Test After findings are raised by the technician on the Android Test After checksheet
                  and arrive here when it is submitted.
                </p>
                {taLoading ? (
                  <LoadingState label="Loading findings…" />
                ) : (
                  <TestBeforeBookingList
                    bookings={taBookings}
                    emptyMessage="No Test After findings recorded yet."
                  />
                )}

                {admin && taStage.status === 'IN_PROGRESS' && (
                  <p className="field-hint">
                    {taAllAttended
                      ? 'All findings are fully attended — Test After can be completed.'
                      : 'Test After can only be completed once every finding is fully attended in every responsible section.'}
                  </p>
                )}
              </section>
            </>
          )}
        </div>
      )}

      {taStage?.status === 'COMPLETED' && (
        <div
          className={`panel panel-shedout${
            workflow.status === 'CLOSED'
              ? ' panel-shedout-done'
              : admin && eligibility?.eligible
                ? ' panel-shedout-ready'
                : admin && eligibility
                  ? ' panel-shedout-blocked'
                  : ''
          }`}
        >
          <div className="page-header-row">
            <h2>Shed Out Readiness</h2>
            {admin && workflow.status !== 'CLOSED' && eligibility?.eligible && !confirmingShedOut && (
              <button type="button" className="btn btn-primary" onClick={() => setConfirmingShedOut(true)}>
                Shed Out
              </button>
            )}
            {admin && workflow.status !== 'CLOSED' && !eligibility?.eligible && (
              <button type="button" className="btn btn-primary" disabled title="Not yet eligible for Shed Out">
                Shed Out
              </button>
            )}
          </div>

          {shedOutSuccess && (
            <div className="form-success" role="status">
              {shedOutSuccess}
            </div>
          )}

          {workflow.status === 'CLOSED' && (
            <p className="field-hint">This shed visit has been Shed Out. No further workflow mutations are available.</p>
          )}

          {workflow.status !== 'CLOSED' && (
            <>
              {eligibilityLoading && <LoadingState label="Checking Shed Out readiness…" />}

              {eligibility && <ShedOutReadinessPanel eligibility={eligibility} />}

              {admin && confirmingShedOut && (
                <ConfirmActionDialog
                  title={`Confirm Shed Out for locomotive ${workflow.loco_number}?`}
                  description="Closes this shed visit. The backend re-checks Shed Out eligibility when the request is submitted."
                  confirmLabel={shedOutSubmitting ? 'Shedding Out…' : 'Confirm Shed Out'}
                  busy={shedOutSubmitting}
                  onConfirm={handleShedOut}
                  onCancel={() => setConfirmingShedOut(false)}
                >
                  <div className="shed-out-form">
                    <div className="field">
                      <label className="field-label" htmlFor="shed-out-departed-at">
                        Departure date/time
                      </label>
                      <input
                        id="shed-out-departed-at"
                        type="datetime-local"
                        value={departedAt}
                        onChange={(e) => setDepartedAt(e.target.value)}
                      />
                    </div>
                    <div className="field">
                      <label className="field-label" htmlFor="shed-out-remarks">
                        Remarks (optional)
                      </label>
                      <textarea
                        id="shed-out-remarks"
                        rows={2}
                        value={shedOutRemarks}
                        onChange={(e) => setShedOutRemarks(e.target.value)}
                      />
                    </div>
                    {shedOutError && (
                      <div className="form-error" role="alert">
                        {shedOutError}
                      </div>
                    )}
                    {shedOutBlockers && <ShedOutBlockerNotice detail={shedOutBlockers} />}
                  </div>
                </ConfirmActionDialog>
              )}
            </>
          )}
        </div>
      )}

      <section className="workflow-secondary">
        <h2 className="workflow-secondary-title">Secondary Information</h2>
        <p className="field-hint">
          Read-only checksheet and work-package detail behind the stages above — useful for audit
          and follow-up, not needed to act on the workflow itself.
        </p>
        <div className="workflow-secondary-grid">
          <ChecksheetRequirementProgress
            visitId={idNum}
            refreshKey={reconcileRefreshKey}
            onLoaded={setChecksheetProgress}
          />
          <ChecksheetProgress visitId={idNum} />
          <RequiredChecksheets visitId={idNum} />
        </div>
      </section>

      {/* ADMIN ONLY, and deliberately the LAST thing on the page, in its own separated section
          rather than beside the workflow buttons an operator uses every day. Nothing here is a
          one-click action: the button opens a dialog that shows exactly what would be destroyed and
          then requires a reason, a phrase naming this locomotive and schedule, and the Admin's own
          password. The server enforces all of it again regardless of what this page renders. */}
      {admin && (
        <section className="workflow-section workflow-danger-zone">
          <h2 className="danger-heading">Danger zone</h2>
          <p>
            Permanently delete this shed visit and every record belonging to it — its bookings,
            checksheets, checksheet values, signatures and workflow history. Other visits of this
            locomotive, and the locomotive itself, are not affected. This cannot be undone, and a
            permanent record of the deletion is kept in Deletion History.
          </p>
          <button type="button" className="btn btn-danger" onClick={() => setDeleteOpen(true)}>
            Delete This Shed Visit…
          </button>
        </section>
      )}

      {deleteOpen && (
        <DeleteShedVisitDialog
          visitId={idNum}
          onDeleted={() => {
            setDeleteOpen(false)
            // The visit this page is about no longer exists, so staying here would render a page
            // whose every request 404s. Leave for the history, where the deletion is now recorded.
            navigate('/shed-visit-history')
          }}
          onCancel={() => setDeleteOpen(false)}
        />
      )}
    </div>
  )
}
