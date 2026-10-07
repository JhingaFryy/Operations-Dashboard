import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  asChecksheetsIncompleteDetail,
  asShedOutBlockedDetail,
  completeSchedule,
  listCurrentShedVisits,
  markReady,
  shedOut,
  startSchedule,
  type ChecksheetsIncompleteDetail,
  type ShedOutBlockedDetail,
} from '../api/shedVisits'
import { ChecksheetBlockerNotice } from '../components/shed/ChecksheetBlockerNotice'
import { ShedOutBlockerNotice } from '../components/shed/ShedOutBlockerNotice'
import { ApiError, friendlyErrorMessage } from '../api/client'
import { orderShedVisitsByArrivalDesc } from '../lib/shedVisitOrder'
import { ScheduleActionDialog } from '../components/shed/ScheduleActionDialog'
import { useAuth } from '../auth/useAuth'
import { canManageLocoMovement } from '../auth/permissions'
import { ShedInForm } from '../components/shed/ShedInForm'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, LoadingState, RefreshIndicator } from '../components/ui/States'
import { formatDateTime, formatSchedule } from '../lib/format'
import { formatDuration } from '../lib/localDateTime'
import type { CurrentShedVisit, OperationalPhase, ShedVisitAction } from '../types'

/** The shed movement register: what is in the shed right now, plus Shed In
 * entry. Presented as a dense table because this is the register operators
 * scan and compare down a column; the card presentation of the same data
 * lives on the Overview page. */

const PHASE_CLASS: Record<OperationalPhase, string> = {
  SPARE: 'phase-chip-spare',
  SCHEDULE_IN_PROGRESS: 'phase-chip-in-progress',
  INSPECTION_COMPLETED: 'phase-chip-inspection-complete',
  READY: 'phase-chip-ready',
  SHED_OUT: 'phase-chip-shed-out',
}

const ACTION_LABEL: Record<ShedVisitAction, string> = {
  START_SCHEDULE: 'Start Schedule',
  COMPLETE_SCHEDULE: 'Complete Schedule',
  MARK_READY: 'Mark Ready',
  SHED_OUT: 'Shed Out',
}

/** Each phase has exactly one timing worth showing in a dense table row. */
function phaseTimingSummary(visit: CurrentShedVisit): string {
  const t = visit.timings
  switch (visit.operational_phase) {
    case 'SPARE':
      return `Waiting ${formatDuration(t.waiting_seconds)}`
    case 'SCHEDULE_IN_PROGRESS':
      return `Elapsed ${formatDuration(t.schedule_seconds)}`
    case 'INSPECTION_COMPLETED':
      return `Inspection ${formatDuration(t.schedule_seconds)}`
    case 'READY':
      return `Schedule ${formatDuration(t.schedule_seconds)}`
    default:
      return `Total ${formatDuration(t.total_seconds)}`
  }
}

/** Copy for the shared confirmation dialog, per action. */
const ACTION_DIALOG: Record<
  ShedVisitAction,
  { title: string; description: string; label: string; confirmLabel: string }
> = {
  START_SCHEDULE: {
    title: 'Start Schedule',
    description:
      'Records the authoritative start of schedule work for this locomotive. For a Minor schedule this is the start of the actual inspection, and Test Before must already be completed or skipped by an Admin.',
    label: 'Schedule Start Date/Time',
    confirmLabel: 'Start Schedule',
  },
  COMPLETE_SCHEDULE: {
    title: 'Complete Schedule',
    description:
      'Minor schedule: records that the actual inspection is complete - Test After comes next, and the locomotive is not Ready yet. Major schedule: records that the schedule is complete. Shed Out still checks bookings and checksheets separately.',
    label: 'Schedule Completion Date/Time',
    confirmLabel: 'Complete Schedule',
  },
  MARK_READY: {
    title: 'Mark Ready',
    description:
      'Records that the locomotive is Ready after Test After. Test After must already be completed - it is always required for a Minor schedule.',
    label: 'Ready Date/Time',
    confirmLabel: 'Mark Ready',
  },
  SHED_OUT: {
    title: 'Shed Out',
    description: 'Closes this visit and records the departure.',
    label: 'Departure Date/Time',
    confirmLabel: 'Shed Out',
  },
}

export function ShedMovementPage() {
  const { user } = useAuth()
  // EVERY mutation on this page is a locomotive MOVEMENT action - Shed In, Start Schedule,
  // Complete Schedule, Mark Ready, Shed Out - so one capability gates all of them. Read from the
  // server's resolved answer (capabilities.can_manage_loco_movement), never from a section code:
  // a planning account like PPIO is read-only here, and so is any Supervisor outside a movement
  // section. The routes behind these controls each return 403 independently
  // (app/core/authz.require_loco_movement), so this is about not offering an action that would
  // be refused - not about security.
  const mayMove = canManageLocoMovement(user)
  const [visits, setVisits] = useState<CurrentShedVisit[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; unauthorized: boolean } | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [pendingAction, setPendingAction] = useState<
    { visit: CurrentShedVisit; action: ShedVisitAction } | null
  >(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  // Ready refused because required checksheets are outstanding: kept as its own structured state
  // so the dialog can say which ones, instead of a generic "that conflicts with existing data".
  const [checksheetBlocker, setChecksheetBlocker] = useState<ChecksheetsIncompleteDetail | null>(null)
  // Shed Out refused by one of its gates: shown gate by gate, never as a bare "not eligible".
  const [shedOutBlocker, setShedOutBlocker] = useState<ShedOutBlockedDetail | null>(null)

  const runPendingAction = async (isoTimestamp: string) => {
    if (!pendingAction) return
    const { visit, action } = pendingAction
    setActionBusy(true)
    setActionError(null)
    setChecksheetBlocker(null)
    setShedOutBlocker(null)
    try {
      if (action === 'START_SCHEDULE') {
        await startSchedule(visit.id, isoTimestamp)
        setSuccessMessage(`Schedule started for ${visit.loco_number}.`)
      } else if (action === 'COMPLETE_SCHEDULE') {
        const result = await completeSchedule(visit.id, isoTimestamp)
        setSuccessMessage(
          result.operational_phase === 'INSPECTION_COMPLETED'
            ? `Inspection completed for ${visit.loco_number}. Test After is next.`
            : `Schedule completed for ${visit.loco_number}.`,
        )
      } else if (action === 'MARK_READY') {
        await markReady(visit.id, isoTimestamp)
        setSuccessMessage(`${visit.loco_number} is Ready.`)
      } else {
        await shedOut(visit.id, { departed_at: isoTimestamp })
        setSuccessMessage(`${visit.loco_number} has been shed out.`)
      }
      setPendingAction(null)
      // Refetch: phase, available actions and every timing are server-derived, so the server's
      // answer is the only correct post-action state.
      loadVisits()
    } catch (err) {
      const blocker = asChecksheetsIncompleteDetail(err)
      const shedOutBlocked = asShedOutBlockedDetail(err)
      setChecksheetBlocker(blocker)
      setShedOutBlocker(shedOutBlocked)
      // The specific message whenever the backend gave one; the generic wording is reserved for
      // failures that genuinely carry nothing more specific.
      setActionError(blocker?.message ?? shedOutBlocked?.message ?? friendlyErrorMessage(err))
    } finally {
      setActionBusy(false)
    }
  }


  // After the first successful load, a refetch (e.g. following a Shed In or schedule action) keeps
  // the register on screen with a small "Updating…" indicator instead of blanking to a loader.
  const [loaded, setLoaded] = useState(false)

  const loadVisits = useCallback(() => {
    setLoading(true)
    setError(null)
    listCurrentShedVisits()
      .then((result) => {
        setVisits(result)
        setLoaded(true)
      })
      .catch((err) =>
        setError({
          message: friendlyErrorMessage(err),
          unauthorized: err instanceof ApiError && err.status === 403,
        }),
      )
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    loadVisits()
  }, [loadVisits])

  const initialLoading = loading && !loaded
  const refreshing = loading && loaded

  // Client-side narrowing of the already-loaded register only; the request
  // takes no query parameters and is unchanged.
  //
  // ORDERED EXPLICITLY, newest arrival first, AFTER filtering - so the visible rows are in Shed-In
  // chronology whether or not a search is active. This page was already correct, but only because it
  // rendered the response in the order it arrived; stating the rule here means it cannot quietly
  // change if the endpoint's ordering ever does, and it is the same shared comparator the Overview
  // uses so the two pages cannot disagree about the same data.
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    const matching = term
      ? visits.filter((v) => v.loco_number.toLowerCase().includes(term))
      : visits
    return orderShedVisitsByArrivalDesc(matching)
  }, [visits, search])

  return (
    <div className="shed-movement-page">
      <PageHeader
        title="Shed Movement"
        description="Locomotives currently in the shed, and Shed In entry."
        actions={
          mayMove ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                setSuccessMessage(null)
                setShowForm((v) => !v)
              }}
            >
              {showForm ? 'Close' : 'Shed In'}
            </button>
          ) : undefined
        }
      />

      {successMessage && (
        <div className="form-success" role="status">
          {successMessage}
        </div>
      )}

      {mayMove && showForm && (
        <div className="panel">
          <ShedInForm
            onSuccess={(message) => {
              setSuccessMessage(message)
              loadVisits()
              setShowForm(false)
            }}
          />
        </div>
      )}

      <div className="panel">
        {/* Toolbar: heading, live count and the search control on one row,
            flush above the table rather than a control floating in its own
            box. Search narrows the already-loaded register client-side. */}
        <div className="table-toolbar">
          <div className="table-toolbar-heading">
            <h2 className="section-title">Currently In Shed</h2>
            {refreshing && <RefreshIndicator />}
            {!initialLoading && !error && visits.length > 0 && (
              <span className="table-toolbar-count">
                {filtered.length === visits.length
                  ? `${visits.length} locomotive${visits.length === 1 ? '' : 's'}`
                  : `${filtered.length} of ${visits.length} shown`}
              </span>
            )}
          </div>

          {!initialLoading && !error && visits.length > 0 && (
            <div className="table-toolbar-controls">
              <div className="field field-inline">
                <label className="field-label" htmlFor="shed-movement-search">
                  Find locomotive
                </label>
                <input
                  id="shed-movement-search"
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Loco number…"
                />
              </div>
            </div>
          )}
        </div>

        {initialLoading && <LoadingState layout="list" label="Loading current shed visits…" />}

        {error && (
          <ErrorState
            message={error.message}
            variant={error.unauthorized ? 'unauthorized' : 'error'}
            onRetry={loadVisits}
          />
        )}

        {!initialLoading && !error && visits.length === 0 && (
          <EmptyState
            title="No locomotives are currently in the shed."
            description={
              mayMove
                ? 'Use Shed In above to record an arrival.'
                : 'Locomotives appear here once they are shed in.'
            }
          />
        )}

        {!initialLoading && !error && visits.length > 0 && filtered.length === 0 && (
          <EmptyState title="No locomotive matches that number" description="Clear the search to see the full register." />
        )}

        {!initialLoading && !error && filtered.length > 0 && (
          <div className="table-scroll">
            <table className="data-table shed-visits-table">
              <thead>
                <tr>
                  <th scope="col">Locomotive</th>
                  <th scope="col">Schedule</th>
                  <th scope="col">Arrival Condition</th>
                  <th scope="col">Arrival</th>
                  <th scope="col">Phase</th>
                  <th scope="col">Timing</th>
                  <th scope="col">Bookings</th>
                  <th scope="col">Pending Bookings</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((v) => (
                  <tr key={v.id} className={v.pending_booking_count > 0 ? 'row-attention' : undefined}>
                    <td>
                      <span className="loco-number">{v.loco_number}</span>
                    </td>
                    <td>{formatSchedule(v.schedule_family, v.schedule_variant)}</td>
                    <td className="cell-muted">{v.arrival_condition ?? '—'}</td>
                    <td className="cell-muted">{formatDateTime(v.arrival_at)}</td>
                    <td>
                      {/* Phase and its label come from the server's single derivation
                          (app/services/shed_visit_phase.py) - never recomputed here. */}
                      <span className={`phase-chip ${PHASE_CLASS[v.operational_phase]}`}>
                        {v.display_label}
                      </span>
                    </td>
                    <td className="cell-muted">{phaseTimingSummary(v)}</td>
                    <td className="numeric">{v.booking_total}</td>
                    <td className="numeric">
                      <span className={v.pending_booking_count > 0 ? 'count-attention' : undefined}>
                        {v.pending_booking_count}
                      </span>
                    </td>
                    <td className="cell-actions">
                      {/* Straight from available_actions: an action invalid for this phase is
                          never rendered, so Complete Schedule cannot appear on a Spare loco.
                          And none of them is offered at all without movement capability - the
                          register stays fully readable, it simply has no actions. */}
                      {mayMove &&
                        v.available_actions.map((action) => (
                        <button
                          key={action}
                          type="button"
                          className={`btn btn-small ${action === 'SHED_OUT' ? 'btn-secondary' : 'btn-primary'}`}
                          disabled={actionBusy}
                          onClick={() => setPendingAction({ visit: v, action })}
                        >
                          {ACTION_LABEL[action]}
                        </button>
                      ))}
                      {/* Read-only navigation, deliberately NOT gated: the workflow page is
                          something PPIO should be able to open. */}
                      {v.schedule_family === 'MINOR' ? (
                        <Link className="btn btn-secondary btn-small" to={`/shed-visits/${v.id}/workflow`}>
                          Open Workflow
                        </Link>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-secondary btn-small"
                          disabled
                          title="Workflow tracking applies to MINOR schedules only — IOH/TOH visits have no workflow to open."
                        >
                          Open Workflow
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Belt and braces: the only way to set pendingAction is a button that `mayMove` already
          gates, so this can never be reached without it - but a confirm dialog for an action the
          account cannot perform is not a thing that should be constructible at all. */}
      {mayMove && pendingAction && (
        <ScheduleActionDialog
          title={`${ACTION_DIALOG[pendingAction.action].title} — ${pendingAction.visit.loco_number}`}
          description={ACTION_DIALOG[pendingAction.action].description}
          label={ACTION_DIALOG[pendingAction.action].label}
          confirmLabel={ACTION_DIALOG[pendingAction.action].confirmLabel}
          busy={actionBusy}
          error={actionError}
          errorDetail={
            shedOutBlocker ? (
              <ShedOutBlockerNotice detail={shedOutBlocker} />
            ) : checksheetBlocker ? (
              <ChecksheetBlockerNotice detail={checksheetBlocker} />
            ) : null
          }
          onConfirm={runPendingAction}
          onCancel={() => {
            setPendingAction(null)
            setActionError(null)
            setChecksheetBlocker(null)
            setShedOutBlocker(null)
          }}
        />
      )}
    </div>
  )
}
