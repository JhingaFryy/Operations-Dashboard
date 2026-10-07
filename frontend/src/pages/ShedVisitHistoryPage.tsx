import { Fragment, useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { listSections } from '../api/sections'
import {
  HISTORY_PAGE_SIZE,
  getVisitHistory,
  historyErrorMessage,
  listHistoryLocoModels,
  searchVisitHistory,
  type VisitHistoryFilters,
} from '../api/shedVisitHistory'
import { VisitHistoryDetailPanel } from '../components/history/VisitHistoryDetailPanel'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, LoadingState, RefreshIndicator } from '../components/ui/States'
import { Tag } from '../components/ui/StatusBadge'
import { useAuth } from '../auth/useAuth'
import { canAccessAllSections } from '../auth/permissions'
import { formatDateTimeShort } from '../lib/format'
import { familyLabel, isAdminReset, visitStatusLabel } from '../lib/visitHistory'
import type { Section, VisitHistoryDetail, VisitHistoryPage, VisitHistoryRow } from '../types'

const VARIANTS = ['IA', 'IA0', 'IB', 'IC', 'IC0', 'IOH', 'TOH']
const EMPTY: VisitHistoryFilters = {}

type DetailState = { loading: boolean; error: string | null; data: VisitHistoryDetail | null }

/** Shed Visit History - every shed visit ever recorded (active, Ready, closed, administratively
 * reset; Minor and Major), searchable and paged on the server. Read-only.
 *
 * The server decides what each user sees: an Admin every section's bookings and checksheets, a
 * Supervisor their own section's. Nothing on this page widens or narrows that. */
export function ShedVisitHistoryPage() {
  const { user } = useAuth()
  const allSections = canAccessAllSections(user)

  const [draft, setDraft] = useState<VisitHistoryFilters>(EMPTY)
  const [applied, setApplied] = useState<VisitHistoryFilters>(EMPTY)
  const [page, setPage] = useState(1)
  const [result, setResult] = useState<VisitHistoryPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; unauthorized: boolean } | null>(null)

  const [models, setModels] = useState<string[] | null>(null)
  const [sections, setSections] = useState<Section[]>([])
  const [expanded, setExpanded] = useState<Record<number, DetailState>>({})

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    searchVisitHistory(applied, page)
      .then(setResult)
      .catch((err) =>
        setError({
          message: historyErrorMessage(err),
          unauthorized: err instanceof ApiError && err.status === 403,
        }),
      )
      .finally(() => setLoading(false))
  }, [applied, page])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    listHistoryLocoModels()
      .then(setModels)
      .catch(() => setModels(null))
  }, [])

  useEffect(() => {
    if (!allSections) return
    listSections()
      .then(setSections)
      .catch(() => setSections([]))
  }, [allSections])

  function set<K extends keyof VisitHistoryFilters>(key: K, value: string) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    setExpanded({})
    setPage(1)
    setApplied({ ...draft })
  }

  function clear() {
    setDraft(EMPTY)
    setExpanded({})
    setPage(1)
    setApplied(EMPTY)
  }

  function goTo(next: number) {
    setExpanded({})
    setPage(next)
  }

  function toggle(visitId: number) {
    if (expanded[visitId]) {
      setExpanded(({ [visitId]: _closed, ...rest }) => rest)
      return
    }
    setExpanded((s) => ({ ...s, [visitId]: { loading: true, error: null, data: null } }))
    getVisitHistory(visitId)
      .then((data) => setExpanded((s) => (s[visitId] ? { ...s, [visitId]: { loading: false, error: null, data } } : s)))
      .catch((err) =>
        setExpanded((s) =>
          s[visitId] ? { ...s, [visitId]: { loading: false, error: historyErrorMessage(err), data: null } } : s,
        ),
      )
  }

  const totalPages = result ? Math.max(1, Math.ceil(result.total / result.page_size)) : 1
  const initialLoading = loading && !result
  const activeCount = Object.values(applied).filter((v) => typeof v === 'string' && v.trim()).length

  return (
    <div className="history-page">
      <PageHeader
        title="Shed Visits"
        description="Every shed visit — in shed, Ready, shed out or closed administratively — with its timings, bookings, checksheets and events. Read-only."
        meta={
          result && !error ? (
            <span className="page-header-count">
              {result.total === 1 ? '1 visit' : `${result.total} visits`}
              {activeCount > 0 ? (result.total === 1 ? ' matches the filters' : ' match the filters') : ' recorded'}
            </span>
          ) : undefined
        }
      />

      <form className="filter-bar history-filters" onSubmit={submit} aria-label="Search shed visits">
        <div className="filter-bar-fields">
          <div className="field">
            <label className="field-label" htmlFor="history-loco">Loco number</label>
            <input id="history-loco" type="text" inputMode="numeric" value={draft.loco_number ?? ''}
              onChange={(e) => set('loco_number', e.target.value)} placeholder="e.g. 39015" />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-model">Loco type</label>
            <select id="history-model" value={draft.loco_model ?? ''} disabled={models === null}
              onChange={(e) => set('loco_model', e.target.value)}>
              <option value="">{models === null ? 'Unavailable' : 'All'}</option>
              {(models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-family">Schedule family</label>
            <select id="history-family" value={draft.schedule_family ?? ''}
              onChange={(e) => set('schedule_family', e.target.value)}>
              <option value="">All</option>
              <option value="MINOR">Minor</option>
              <option value="MAJOR">Major</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-variant">Schedule</label>
            <select id="history-variant" value={draft.schedule_variant ?? ''}
              onChange={(e) => set('schedule_variant', e.target.value)}>
              <option value="">All</option>
              {VARIANTS.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-status">Status</label>
            <select id="history-status" value={draft.status ?? ''} onChange={(e) => set('status', e.target.value)}>
              <option value="">All</option>
              <option value="IN_SHED">In shed</option>
              <option value="READY">Ready</option>
              <option value="CLOSED">Closed</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-closure">Closed by</label>
            <select id="history-closure" value={draft.departure_source ?? ''}
              onChange={(e) => set('departure_source', e.target.value)}>
              <option value="">Any</option>
              <option value="DASHBOARD">Shed Out</option>
              <option value="SYSTEM">System reset</option>
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-visit-id">Visit ID</label>
            <input id="history-visit-id" type="number" min={1} value={draft.visit_id ?? ''}
              onChange={(e) => set('visit_id', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-arrived-from">Arrived from</label>
            <input id="history-arrived-from" type="date" value={draft.arrived_from ?? ''}
              onChange={(e) => set('arrived_from', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-arrived-to">Arrived to</label>
            <input id="history-arrived-to" type="date" value={draft.arrived_to ?? ''}
              onChange={(e) => set('arrived_to', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-departed-from">Departed from</label>
            <input id="history-departed-from" type="date" value={draft.departed_from ?? ''}
              onChange={(e) => set('departed_from', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-departed-to">Departed to</label>
            <input id="history-departed-to" type="date" value={draft.departed_to ?? ''}
              onChange={(e) => set('departed_to', e.target.value)} />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="history-booking-status">Has a booking that is</label>
            <select id="history-booking-status" value={draft.booking_status ?? ''}
              onChange={(e) => set('booking_status', e.target.value)}>
              <option value="">Any</option>
              <option value="OPEN">Open</option>
              <option value="IN_PROGRESS">In progress</option>
              <option value="ATTENDED">Attended</option>
              <option value="REOPENED">Reopened</option>
            </select>
          </div>
          {allSections && (
            <div className="field">
              <label className="field-label" htmlFor="history-section">Section involved</label>
              <select id="history-section" value={draft.section_id ?? ''}
                onChange={(e) => set('section_id', e.target.value)}>
                <option value="">Any</option>
                {sections.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
          )}
        </div>
        <div className="history-filter-actions">
          <button type="submit" className="btn btn-primary btn-small">Search</button>
          <button type="button" className="btn btn-secondary btn-small" onClick={clear}>Clear</button>
        </div>
      </form>

      {initialLoading && <LoadingState layout="list" label="Loading shed visit history…" />}
      {loading && result && <RefreshIndicator label="Updating…" />}
      {error && (
        <ErrorState message={error.message} variant={error.unauthorized ? 'unauthorized' : 'error'} onRetry={load} />
      )}

      {result && !error && (
        <>
          {(!result.locomotive_details_available || !result.checksheet_counts_available) && (
            <p className="history-warning" role="status">
              BL-DCMS could not be reached, so loco types and checksheet counts are not shown on this page.
            </p>
          )}

          {result.items.length === 0 ? (
            <EmptyState
              title={activeCount > 0 ? 'No shed visits match these filters' : 'No shed visits recorded yet'}
              description={activeCount > 0 ? 'Change or clear the filters to widen the search.' : undefined}
            />
          ) : (
            <div className="panel history-results">
              <div className="table-scroll">
                <table className="data-table history-table">
                  <caption className="visually-hidden">Shed visits, newest first</caption>
                  <thead>
                    <tr>
                      <th scope="col">Visit</th>
                      <th scope="col">Locomotive</th>
                      <th scope="col">Schedule</th>
                      <th scope="col">Status</th>
                      <th scope="col">Arrived</th>
                      <th scope="col">Ready</th>
                      <th scope="col">Departed</th>
                      <th scope="col" className="numeric">Bookings</th>
                      <th scope="col" className="numeric">Checksheets</th>
                      <th scope="col"><span className="visually-hidden">Details</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.items.map((row) => (
                      <HistoryRow key={row.shed_visit_id} row={row} state={expanded[row.shed_visit_id]}
                        onToggle={() => toggle(row.shed_visit_id)} />
                    ))}
                  </tbody>
                </table>
              </div>
              <nav className="history-pagination" aria-label="Pages">
                <button type="button" className="btn btn-secondary btn-small" disabled={page <= 1 || loading}
                  onClick={() => goTo(page - 1)}>
                  Previous
                </button>
                <span>Page {result.page} of {totalPages}</span>
                <button type="button" className="btn btn-secondary btn-small"
                  disabled={page >= totalPages || loading} onClick={() => goTo(page + 1)}>
                  Next
                </button>
                <span className="history-muted">{HISTORY_PAGE_SIZE} per page</span>
              </nav>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function HistoryRow({ row, state, onToggle }: { row: VisitHistoryRow; state?: DetailState; onToggle: () => void }) {
  const open = Boolean(state)
  const detailId = `history-detail-${row.shed_visit_id}`
  return (
    <Fragment>
      <tr className={isAdminReset(row) ? 'history-row-reset' : undefined}>
        <td>#{row.shed_visit_id}</td>
        <td>
          <div className="history-loco">{row.loco_number}</div>
          <div className="history-muted">{row.loco_model ?? 'Type unknown'}</div>
        </td>
        <td>
          {row.schedule_variant ?? '—'} <Tag>{familyLabel(row.schedule_family)}</Tag>
        </td>
        <td>
          <div>{visitStatusLabel(row)}</div>
          {row.status !== 'CLOSED' && <div className="history-muted">{row.display_label}</div>}
          {isAdminReset(row) && <Tag tone="warn">Not a Shed Out</Tag>}
        </td>
        <td>{formatDateTimeShort(row.arrival_at)}</td>
        <td>{row.ready_at ? formatDateTimeShort(row.ready_at) : '—'}</td>
        <td>{row.departed_at ? formatDateTimeShort(row.departed_at) : row.status === 'CLOSED' ? '—' : 'In shed'}</td>
        <td className="numeric">{row.booking_count}</td>
        <td className="numeric">
          {row.checksheet_count == null ? <span title="BL-DCMS unavailable">—</span> : row.checksheet_count}
        </td>
        <td className="cell-actions">
          <button type="button" className="btn btn-secondary btn-small" aria-expanded={open}
            aria-controls={open ? detailId : undefined} onClick={onToggle}>
            {open ? 'Hide' : 'Details'}
            <span className="visually-hidden"> for visit #{row.shed_visit_id} ({row.loco_number})</span>
          </button>
        </td>
      </tr>
      {state && (
        <tr className="history-detail-row">
          <td colSpan={10} id={detailId}>
            {state.loading && <LoadingState label="Loading visit details…" />}
            {state.error && <ErrorState message={state.error} />}
            {state.data && <VisitHistoryDetailPanel detail={state.data} />}
          </td>
        </tr>
      )}
    </Fragment>
  )
}
