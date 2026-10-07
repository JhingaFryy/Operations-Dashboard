import { Fragment, useCallback, useEffect, useState } from 'react'
import { getDeletionItems, listDeletionHistory } from '../../api/adminDeletion'
import { friendlyErrorMessage } from '../../api/client'
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/States'
import { PageHeader } from '../../components/ui/PageHeader'
import type { DeletionEventSummary, DeletionItem } from '../../types'

/** The permanent record of every Admin deletion. ADMIN ONLY. Read-only, and there is no restore.
 *
 * WHY THIS PAGE CAN EXIST AT ALL. The deletion ledger holds no foreign key to operational data - not
 * to shed_visits, bookings, checksheet_header or even users. Every reference is a plain integer
 * carried alongside human-readable snapshots. So these rows remain complete and readable precisely
 * BECAUSE their subjects are gone, which is the one property that makes a destructive feature
 * auditable. A ledger that cascaded away with its own subject would be worthless.
 *
 * NO RESTORE BUTTON. The snapshots hold enough to reconstruct what was deleted, but putting rows back
 * is a different operation with its own hazards - re-creating a row under an id something else now
 * uses, or reviving half a visit. It is deliberately out of scope rather than half-built.
 */
export function DeletionHistoryPage() {
  const [events, setEvents] = useState<DeletionEventSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState('')
  const [locoFilter, setLocoFilter] = useState('')

  const [openId, setOpenId] = useState<number | null>(null)
  const [items, setItems] = useState<DeletionItem[] | null>(null)
  const [itemsTotal, setItemsTotal] = useState(0)
  const [itemsError, setItemsError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    listDeletionHistory({
      deletionType: typeFilter || undefined,
      locoNumber: locoFilter.trim() || undefined,
    })
      .then((rows) => {
        setEvents(rows)
        setError(null)
      })
      .catch((err) => {
        setEvents([])
        setError(friendlyErrorMessage(err))
      })
      .finally(() => setLoading(false))
  }, [typeFilter, locoFilter])

  useEffect(() => {
    load()
  }, [load])

  const openDetails = (event: DeletionEventSummary) => {
    if (openId === event.id) {
      setOpenId(null)
      return
    }
    setOpenId(event.id)
    setItems(null)
    setItemsError(null)
    // Paged: one visit deletion can carry a couple of thousand snapshots, and the largest visit in
    // production would produce roughly 950.
    getDeletionItems(event.id, { limit: 200 })
      .then((page) => {
        setItems(page.items)
        setItemsTotal(page.total)
      })
      .catch((err) => setItemsError(friendlyErrorMessage(err)))
  }

  return (
    <div className="deletion-history-page">
      <PageHeader
        title="Deletion History"
        description="Every booking and shed visit an administrator has permanently deleted, with who did it, why, and a snapshot of every destroyed record. This record is kept after the data itself is gone."
      />

      <div className="filter-bar">
        <div className="filter-bar-fields">
          <div className="field">
            <label className="field-label" htmlFor="deletion-type-filter">
              Type
            </label>
            <select
              id="deletion-type-filter"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All</option>
              <option value="SHED_VISIT">Shed visits</option>
              <option value="BOOKING">Bookings</option>
            </select>
          </div>
          <div className="field field-search">
            <label className="field-label" htmlFor="deletion-loco-filter">
              Locomotive
            </label>
            <input
              id="deletion-loco-filter"
              type="text"
              value={locoFilter}
              onChange={(e) => setLocoFilter(e.target.value)}
              placeholder="e.g. 32032"
            />
          </div>
        </div>
      </div>

      {loading && <LoadingState layout="list" label="Loading deletion history…" />}
      {error && <ErrorState message={error} onRetry={load} />}

      {!loading && !error && events.length === 0 && (
        <EmptyState
          title="Nothing has been deleted"
          description="When an administrator deletes a booking or a shed visit, a permanent record of it appears here."
        />
      )}

      {!loading && !error && events.length > 0 && (
        <table className="data-table deletion-history-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Administrator</th>
              <th>Type</th>
              <th>Locomotive</th>
              <th>Visit / Schedule</th>
              <th>Records</th>
              <th>Files</th>
              <th>Outcome</th>
              <th>Reason</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              // Fragment with a key, not <>: a bare fragment cannot carry one, so React would warn
              // and lose row identity across re-renders - which matters here because an expanded
              // details row must stay with its own event when the list re-filters.
              <Fragment key={event.id}>
                <tr className={event.status === 'FAILED' ? 'row-failed' : undefined}>
                  <td>{new Date(event.requested_at).toLocaleString()}</td>
                  <td>
                    {event.actor_name}
                    <span className="muted"> ({event.actor_employee_id})</span>
                  </td>
                  <td>{event.deletion_type === 'SHED_VISIT' ? 'Shed visit' : 'Booking'}</td>
                  <td>{event.loco_number}</td>
                  <td>
                    #{event.shed_visit_id}
                    {event.schedule_variant ? ` · ${event.schedule_variant}` : ''}
                  </td>
                  <td>{event.total_rows}</td>
                  <td>
                    {event.files_planned === 0
                      ? '—'
                      : event.files_destroyed === null
                        ? /* The database work committed but the filesystem step has not run or did
                             not report. Shown distinctly from "0 destroyed", which is a different
                             fact. */
                          `${event.files_planned} pending`
                        : `${event.files_destroyed} of ${event.files_planned}`}
                  </td>
                  <td>
                    {event.status === 'COMPLETED' ? 'Completed' : event.status === 'FAILED' ? 'Failed' : 'In progress'}
                    {event.failure_reason ? <div className="muted">{event.failure_reason}</div> : null}
                  </td>
                  <td className="deletion-reason">{event.reason}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-small"
                      aria-expanded={openId === event.id}
                      onClick={() => openDetails(event)}
                    >
                      {openId === event.id ? 'Hide' : 'Details'}
                    </button>
                  </td>
                </tr>
                {openId === event.id && (
                  <tr className="deletion-detail-row">
                    <td colSpan={10}>
                      <div className="deletion-detail">
                        <dl>
                          <dt>Manifest hash (SHA-256)</dt>
                          <dd>
                            <code>{event.manifest_hash ?? '—'}</code>
                          </dd>
                          <dt>Records by table</dt>
                          <dd>
                            {Object.entries(event.record_counts)
                              .sort(([a], [b]) => a.localeCompare(b))
                              .map(([entity, count]) => `${count} ${entity.replace(/_/g, ' ')}`)
                              .join(', ') || '—'}
                          </dd>
                        </dl>

                        {itemsError && (
                          <div className="form-error" role="alert">
                            {itemsError}
                          </div>
                        )}
                        {items === null && !itemsError && <p role="status">Loading snapshots…</p>}
                        {items !== null && (
                          <>
                            <h4>
                              Destroyed records ({items.length}
                              {itemsTotal > items.length ? ` of ${itemsTotal}` : ''})
                            </h4>
                            <table className="data-table deletion-items-table">
                              <thead>
                                <tr>
                                  <th>Table</th>
                                  <th>Original id</th>
                                  <th>Snapshot</th>
                                </tr>
                              </thead>
                              <tbody>
                                {items.map((item) => (
                                  <tr key={item.id}>
                                    <td>{item.entity_type.replace(/_/g, ' ')}</td>
                                    <td>
                                      {item.original_id ?? JSON.stringify(item.original_key)}
                                    </td>
                                    <td>
                                      <code className="snapshot-json">
                                        {JSON.stringify(item.snapshot)}
                                      </code>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            {itemsTotal > items.length && (
                              <p className="form-hint">
                                Showing the first {items.length} of {itemsTotal} snapshots.
                              </p>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
