import { useCallback, useEffect, useMemo, useState } from 'react'
import { listBookings } from '../api/bookings'
import { useAuth } from '../auth/useAuth'
import { canOperateBookings, canRouteBookings, isAdmin } from '../auth/permissions'
import { ApiError, friendlyErrorMessage } from '../api/client'
import { BookingRow } from '../components/bookings/BookingRow'
import { DeleteBookingDialog } from '../components/admin/DeleteBookingDialog'
import { ManageSectionsDialog } from '../components/bookings/ManageSectionsDialog'
import { CreatePlanningBookingDialog } from '../components/bookings/CreatePlanningBookingDialog'

import { CollapsibleGroup } from '../components/bookings/CollapsibleGroup'
import { StatusPills } from '../components/bookings/StatusPills'
import { EmptyState, ErrorState, LoadingState, RefreshIndicator } from '../components/ui/States'
import { PageHeader } from '../components/ui/PageHeader'
import { countStatuses, groupBookingsByLocomotive } from '../lib/grouping'
import { BOOKING_SOURCE_ORDER } from '../lib/bookingSource'
import { bookingSourceLabel } from '../lib/format'
import { ASSIGNMENT_STATUS_META } from '../lib/status'
import type { BookingPoolItem } from '../types'

const STATUS_OPTIONS = ['OPEN', 'IN_PROGRESS', 'ATTENDED', 'REOPENED']
// The canonical vocabulary in operational order, shared with the grouping level so the dropdown
// and the group headings can never drift. This list previously held only five of the eight stored
// values, so a TRIP_INSPECTION, GENERAL_CHECKING or legacy SPECIAL_CHECKING booking could be seen
// in the pool but not filtered to.
const SOURCE_OPTIONS = BOOKING_SOURCE_ORDER

/** Business Rule Alignment: the global Booking Pool is Admin-only, read-only operational
 * visibility now (enforced server-side too - GET /api/bookings requires Admin, see
 * app/api/bookings.py). A Supervisor's operational surface is the Section Dashboard instead,
 * scoped to their own section, where start/attend/reopen actually happen (see
 * SectionDashboardPage.tsx). Bookings here are grouped by their individual equipment
 * (equipment_node_id), never by section - section-assignment detail is shown per-booking
 * (started_by_section_code/attended_by_section_code) since Admin's cross-section view benefits
 * from that context.
 *
 * All filtering is client-side over the already-loaded list; no new backend query parameter is
 * introduced by this page. */
export function BookingPoolPage() {
  const [bookings, setBookings] = useState<BookingPoolItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; unauthorized: boolean } | null>(null)

  const [statusFilter, setStatusFilter] = useState<string>('')
  const [sourceFilter, setSourceFilter] = useState<string>('')
  const [equipmentFilter, setEquipmentFilter] = useState<string>('')
  const [search, setSearch] = useState('')

  // A refetch after the first load keeps the grouped bookings visible (see RefreshIndicator).
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    listBookings()
      .then((result) => {
        setBookings(result)
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
    load()
  }, [load])

  const initialLoading = loading && !loaded
  const refreshing = loading && loaded

  const equipmentOptions = useMemo(() => {
    const seen = new Map<number, string>()
    for (const b of bookings) {
      if (b.equipment_node_id != null) {
        seen.set(b.equipment_node_id, b.equipment_node_name ?? 'Unnamed equipment')
      }
    }
    return Array.from(seen.entries()).sort((a, b) => a[1].localeCompare(b[1]))
  }, [bookings])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return bookings.filter((b) => {
      if (statusFilter && b.status !== statusFilter) return false
      if (sourceFilter && b.booking_source !== sourceFilter) return false
      if (equipmentFilter && String(b.equipment_node_id) !== equipmentFilter) return false
      if (term) {
        const haystack = `${b.description} ${b.shed_visit.loco_number} ${b.equipment_node_name ?? ''}`.toLowerCase()
        if (!haystack.includes(term)) return false
      }
      return true
    })
  }, [bookings, statusFilter, sourceFilter, equipmentFilter, search])

  const locomotives = useMemo(() => groupBookingsByLocomotive(filtered), [filtered])

  // Expansion state, keyed by IDENTITY and never by array position, so re-filtering or
  // re-sorting cannot hand one group's open state to a different group:
  //
  //   loco:<locoNumber>
  //   source:<locoNumber>:<storedSource>
  //   equipment:<locoNumber>:<storedSource>:<equipmentNodeId>
  //
  // Each level's key CONTAINS its ancestors, which is what keeps the same equipment node open
  // independently under Log Book and under Test Before - with the old "<loco>/<nodeId>" key those
  // two would have been one entry and expanding either would have expanded both. The prefixes
  // also keep the three sets from ever colliding with one another.
  //
  // Everything starts collapsed: the pool holds hundreds of bookings and rendering them all at
  // once is what made this page unusable.
  // The route already gates this page to Admin (see App.tsx BookingPoolRoute), and the server
  // refuses /api/bookings to anyone else. Re-checked here so the delete affordance is tied to the
  // role rather than to an assumption about how the component is mounted.
  const { user } = useAuth()
  const admin = isAdmin(user)
  // A planner holds routing and nothing else: every other control must be ABSENT rather than
  // disabled, which is why these are two separate questions rather than one "canEdit".
  const mayRoute = canRouteBookings(user)
  const mayOperate = canOperateBookings(user)

  /** The booking an Admin has asked to delete, if any. Opening the dialog performs nothing; it
   *  fetches a preview and demands a reason and the Admin's own password. */
  const [deleting, setDeleting] = useState<BookingPoolItem | null>(null)

  /** The booking whose section routing is being managed, if any. Opening the dialog performs
   *  nothing; it loads the section list and waits for an explicit Save. */
  const [routingTarget, setRoutingTarget] = useState<BookingPoolItem | null>(null)

  /** Whether the planning-booking dialog is open. Gated on the same capability as routing:
   *  deciding where work goes and raising the work are one authority (can_route_bookings). */
  const [creatingBooking, setCreatingBooking] = useState(false)

  const [openLocos, setOpenLocos] = useState<Set<string>>(new Set())
  const [openSources, setOpenSources] = useState<Set<string>>(new Set())
  const [openEquipment, setOpenEquipment] = useState<Set<string>>(new Set())

  const toggle = useCallback((setter: typeof setOpenLocos, key: string) => {
    setter((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const activeFilters: { key: string; label: string; clear: () => void }[] = []
  if (equipmentFilter) {
    const name = equipmentOptions.find(([id]) => String(id) === equipmentFilter)?.[1] ?? 'Equipment'
    activeFilters.push({ key: 'equipment', label: `Equipment: ${name}`, clear: () => setEquipmentFilter('') })
  }
  if (statusFilter) {
    activeFilters.push({
      key: 'status',
      label: `Status: ${ASSIGNMENT_STATUS_META[statusFilter]?.label ?? statusFilter}`,
      clear: () => setStatusFilter(''),
    })
  }
  if (sourceFilter) {
    activeFilters.push({
      key: 'source',
      label: `Source: ${bookingSourceLabel(sourceFilter)}`,
      clear: () => setSourceFilter(''),
    })
  }
  if (search.trim()) {
    activeFilters.push({ key: 'search', label: `Search: “${search.trim()}”`, clear: () => setSearch('') })
  }

  return (
    <div className="booking-pool-page">
      <PageHeader
        title="Booking Pool"
        description="Every booking across the shed, grouped by equipment — Admin-only global visibility. Read-only: to start, attend, or reopen a booking, use the Section Dashboard for the section it's routed to."
        meta={
          !initialLoading && !error ? (
            <span className="page-header-count">
              {filtered.length} of {bookings.length} bookings shown
            </span>
          ) : undefined
        }
        /* The planner's second write. Offered on exactly the capability the server gates the
           endpoint with - NOT on Admin, because a PPIO planner is not an Admin and this is their
           page. Absent entirely for anyone else, so there is no disabled control to wonder about. */
        actions={
          mayRoute ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setCreatingBooking(true)}
            >
              + Create Planning Booking
            </button>
          ) : undefined
        }
      />

      <div className="filter-bar">
        <div className="filter-bar-fields">
          <div className="field">
            <label className="field-label" htmlFor="booking-pool-equipment-filter">
              Equipment
            </label>
            <select
              id="booking-pool-equipment-filter"
              value={equipmentFilter}
              onChange={(e) => setEquipmentFilter(e.target.value)}
            >
              <option value="">All</option>
              {equipmentOptions.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="booking-pool-status-filter">
              Status
            </label>
            <select
              id="booking-pool-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">All</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s.replace('_', ' ')}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="booking-pool-source-filter">
              Source
            </label>
            <select
              id="booking-pool-source-filter"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
            >
              <option value="">All</option>
              {SOURCE_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {bookingSourceLabel(s)}
                </option>
              ))}
            </select>
          </div>

          <div className="field field-search">
            <label className="field-label" htmlFor="booking-pool-search">
              Search
            </label>
            <input
              id="booking-pool-search"
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Loco number, description, equipment…"
            />
          </div>
        </div>

        {activeFilters.length > 0 && (
          <div className="active-filters">
            <span className="active-filters-label">Active filters</span>
            {activeFilters.map((f) => (
              <button key={f.key} type="button" className="filter-chip" onClick={f.clear}>
                <span>{f.label}</span>
                <span className="filter-chip-clear" aria-hidden="true">
                  ×
                </span>
                <span className="visually-hidden">— clear this filter</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {initialLoading && <LoadingState layout="list" label="Loading bookings…" />}
      {refreshing && <RefreshIndicator label="Updating bookings…" />}

      {error && (
        <ErrorState
          message={error.message}
          variant={error.unauthorized ? 'unauthorized' : 'error'}
          onRetry={load}
        />
      )}

      {!initialLoading && !error && locomotives.length === 0 && bookings.length === 0 && (
        <EmptyState
          title="No bookings recorded"
          description="Bookings appear here as locomotives are shed in and as Test Before, Schedule Inspection and Test After findings are raised."
        />
      )}

      {!initialLoading && !error && locomotives.length === 0 && bookings.length > 0 && (
        <EmptyState
          title="No bookings match the current filters"
          description="Clear one or more filters above to widen the view."
        />
      )}

      {!initialLoading && !error && locomotives.length > 0 && (
        <div className="booking-pool-groups">
          {locomotives.map((loco) => (
            <CollapsibleGroup
              key={loco.key}
              id={`loco-${loco.key}`}
              level="loco"
              expanded={openLocos.has(loco.key)}
              onToggle={() => toggle(setOpenLocos, loco.key)}
              title={loco.locoNumber}
              subtitle={loco.scheduleVariant}
              count={loco.total}
              countLabel="booking"
              meta={<StatusPills counts={loco.counts} label={loco.locoNumber} />}
            >
              {() =>
                loco.sources.map((source) => {
                  // Identity is the STORED source value, scoped to this locomotive.
                  const sourceKey = `source:${loco.key}:${source.key}`
                  return (
                    <CollapsibleGroup
                      key={sourceKey}
                      id={sourceKey}
                      level="source"
                      expanded={openSources.has(sourceKey)}
                      onToggle={() => toggle(setOpenSources, sourceKey)}
                      title={source.label}
                      // An unrecognised stored value is shown AS STORED and flagged, rather than
                      // hidden or folded into another group - an operator who sees this can
                      // report the exact value.
                      subtitle={source.known ? undefined : 'Unrecognised source value'}
                      count={source.total}
                      countLabel="booking"
                      meta={<StatusPills counts={source.counts} label={source.label} />}
                    >
                      {() =>
                        source.equipment.map((group) => {
                          // Identity is the equipment NODE, scoped to this locomotive AND this
                          // source - the same node under two sources is two groups that expand
                          // independently.
                          const key = `equipment:${loco.key}:${source.key}:${group.key}`
                          return (
                            <CollapsibleGroup
                              key={key}
                              id={key}
                              level="equipment"
                              expanded={openEquipment.has(key)}
                              onToggle={() => toggle(setOpenEquipment, key)}
                              title={group.label}
                              subtitle={
                                group.path.length > 0
                                  ? group.path.map((pp) => pp.name).join(' / ')
                                  : undefined
                              }
                              count={group.items.length}
                              countLabel="booking"
                              meta={
                                <StatusPills
                                  counts={countStatuses(group.items.map((i) => i.status))}
                                  label={group.label}
                                />
                              }
                            >
                              {() => (
                                <div className="table-scroll">
                                  <table className="data-table booking-table">
                                    {/* Read-only by design: no action column exists here, on the
                                        row component, or anywhere on this page. Start, attend and
                                        reopen happen on the Section Dashboard. The Source column
                                        stays: the source group heading says which source you are
                                        inside, the column still states it per row, and the row
                                        component is shared with the Section Dashboard. */}
                                    <thead>
                                      <tr>
                                        <th scope="col">Locomotive</th>
                                        <th scope="col">Defect</th>
                                        <th scope="col">Source</th>
                                        <th scope="col">Section</th>
                                        <th scope="col">Status</th>
                                        <th scope="col">Raised</th>
                                        <th scope="col">Handling</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {group.items.map((b) => (
                                        <BookingRow
                                          key={b.id}
                                          booking={b}
                                          // Admin only, and explicitly NOT a planner: routing is
                                          // a planner's only write. For anyone else the prop is
                                          // absent, so no control is rendered at all - not a
                                          // disabled one.
                                          onDelete={
                                            admin && mayOperate
                                              ? (booking) => setDeleting(booking)
                                              : undefined
                                          }
                                          // EVERY source, now. Planning responsibility covers
                                          // the whole pool, and the action can only ADD a
                                          // section - so which source a finding came from is not
                                          // what makes it safe. Absent entirely for anyone
                                          // without the routing capability.
                                          onAddSections={
                                            mayRoute
                                              ? (booking) => setRoutingTarget(booking)
                                              : undefined
                                          }
                                        />
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              )}
                            </CollapsibleGroup>
                          )
                        })
                      }
                    </CollapsibleGroup>
                  )
                })
              }
            </CollapsibleGroup>
          ))}
        </div>
      )}

      {creatingBooking && (
        <CreatePlanningBookingDialog
          onClose={() => setCreatingBooking(false)}
          onCreated={() => {
            setCreatingBooking(false)
            // Reload rather than splice the new booking in locally: the server decides its
            // final section set (auto-mapped plus chosen) and its provenance, and the
            // "Added by PPIO" badge is read from that response, never derived here.
            load()
          }}
        />
      )}

      {routingTarget && (
        <ManageSectionsDialog
          booking={routingTarget}
          onClose={() => setRoutingTarget(null)}
          onSaved={() => {
            setRoutingTarget(null)
            // Reload rather than patch the row locally: the server decides what the resulting
            // assignment set is, including the source of rows it left untouched.
            load()
          }}
        />
      )}

      {deleting && (
        <DeleteBookingDialog
          bookingId={deleting.id}
          onDeleted={() => {
            setDeleting(null)
            // Reload rather than splice the row out locally: the deletion may have changed more than
            // this one booking's presence, and the server is the authority on what is left.
            load()
          }}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  )
}


