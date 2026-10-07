import { useCallback, useEffect, useMemo, useState } from 'react'
import { listSections } from '../api/sections'
import {
  attendAssignment,
  listSectionAssignments,
  reopenAssignment,
  startAssignment,
} from '../api/sectionDashboard'
import { getSectionSummary } from '../api/bookings'
import { ApiError, friendlyErrorMessage } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { canAddBookingSections, canReopenAssignments, isAdmin } from '../auth/permissions'
import { AssignmentCard } from '../components/section/AssignmentCard'
import { CollapsibleGroup } from '../components/bookings/CollapsibleGroup'
import { StatusPills } from '../components/bookings/StatusPills'
import { MetricCard } from '../components/ui/MetricCard'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, LoadingState, RefreshIndicator } from '../components/ui/States'
import { countStatuses, groupAssignmentsByLocomotive } from '../lib/grouping'
import type { Section, SectionAssignment, SectionSummary } from '../types'

const STATUS_FILTERS = ['OPEN', 'IN_PROGRESS', 'ATTENDED', 'REOPENED'] as const

export function SectionDashboardPage() {
  const { user } = useAuth()
  const admin = isAdmin(user)
  const canAddSection = canAddBookingSections(user)
  const canReopen = canReopenAssignments(user)


  const [sections, setSections] = useState<Section[]>([])
  const [sectionsLoading, setSectionsLoading] = useState(true)
  // Always the caller's own section. A ?section= query parameter is deliberately ignored: the
  // backend scopes by the authenticated user's section_id regardless, so honouring it here would
  // only render a view that is guaranteed to 403.
  // Supervisor: always their own section - a ?section= parameter is ignored, because the backend
  // scopes by the authenticated user's section_id regardless. Admin: no section of their own, so
  // they choose one via the selector below (cross-section visibility is Admin-only).
  const [sectionCode, setSectionCode] = useState<string | null>(user?.section?.code ?? null)

  // `user` loads asynchronously (AuthContext fetches /api/auth/me after
  // mount), so the useState initializer above often runs before it's
  // available. Sync once it lands, for non-Admin users only — Admin's
  // sectionCode stays under their own manual control via the picker (or a
  // ?section= link, e.g. "View in Section Dashboard" from Test Before).
  useEffect(() => {
    // Never overwrite an Admin's manual choice with a section they don't have.
    if (!admin && user?.section?.code) {
      setSectionCode(user.section.code)
    }
  }, [admin, user])

  const [assignments, setAssignments] = useState<SectionAssignment[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ message: string; unauthorized: boolean } | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set())

  const [summary, setSummary] = useState<SectionSummary | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('')

  useEffect(() => {
    listSections()
      .then(setSections)
      .catch(() => setSections([]))
      .finally(() => setSectionsLoading(false))
  }, [])

  const currentSection = sections.find((s) => s.code === sectionCode) ?? null

  // Which section the on-screen assignments belong to. A refetch of the SAME section (after
  // Start / Attend / Reopen) keeps the cards visible with a small indicator; switching to a
  // different section still shows the loader, so another section's cards are never shown under
  // the wrong heading.
  const [loadedCode, setLoadedCode] = useState<string | null>(null)

  const loadAssignments = useCallback((code: string) => {
    setLoading(true)
    setError(null)
    listSectionAssignments(code)
      .then((result) => {
        setAssignments(result)
        setLoadedCode(code)
      })
      .catch((err) =>
        setError({
          message: friendlyErrorMessage(err),
          unauthorized: err instanceof ApiError && err.status === 403,
        }),
      )
      .finally(() => setLoading(false))
  }, [])

  const initialLoading = loading && loadedCode !== sectionCode
  const refreshing = loading && loadedCode === sectionCode

  const loadSummary = useCallback((sectionId: number | undefined) => {
    getSectionSummary(sectionId)
      .then(setSummary)
      .catch(() => setSummary(null))
  }, [])

  useEffect(() => {
    if (sectionCode) {
      loadAssignments(sectionCode)
    } else {
      setAssignments([])
    }
  }, [sectionCode, loadAssignments])

  useEffect(() => {
    if (!sectionCode) {
      setSummary(null)
      return
    }
    if (admin) {
      if (currentSection) loadSummary(currentSection.id)
    } else {
      loadSummary(undefined)
    }
  }, [sectionCode, admin, currentSection, loadSummary])

  function refreshAll() {
    if (sectionCode) loadAssignments(sectionCode)
    if (admin) {
      if (currentSection) loadSummary(currentSection.id)
    } else {
      loadSummary(undefined)
    }
  }

  function withBusy(id: number, action: () => Promise<SectionAssignment>) {
    setActionError(null)
    setBusyIds((prev) => new Set(prev).add(id))
    action()
      .then((updated) => {
        setAssignments((prev) => prev.map((a) => (a.id === id ? updated : a)))
        refreshAll()
      })
      .catch((err) => setActionError(friendlyErrorMessage(err)))
      .finally(() => {
        setBusyIds((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      })
  }

  // Client-side narrowing of the already-loaded assignment list only — the
  // request itself is unchanged, and Supervisor scoping stays entirely
  // server-side (GET /api/sections/{code}/assignments).
  const visibleAssignments = useMemo(
    () => (statusFilter ? assignments.filter((a) => a.status === statusFilter) : assignments),
    [assignments, statusFilter],
  )
  // Locomotive -> booking source -> equipment -> bookings, the same hierarchy the Admin Booking
  // Pool uses, through the same helpers. No section level: this page is already scoped to one
  // section, so grouping by it would be a click that reveals nothing new.
  //
  // SECTION SCOPING HAPPENS BEFORE THIS, and not here: the server returns only this section's
  // assignments. So every count at every level below is a tally of the visible, section-scoped,
  // status-filtered rows - there is no unfiltered total anywhere for them to disagree with.
  const locomotives = useMemo(
    () => groupAssignmentsByLocomotive(visibleAssignments),
    [visibleAssignments],
  )

  // Expansion keys are hierarchical and identity-derived, never array positions - re-filtering
  // reorders and removes rows, and an index-keyed set would hand one locomotive's open state to
  // whichever row slid into its place. Same three shapes as the Booking Pool:
  //
  //   loco:<locoNumber>
  //   source:<locoNumber>:<storedSource>
  //   equipment:<locoNumber>:<storedSource>:<equipmentNodeId>
  //
  // The equipment key carries BOTH ancestors, which matters because the same equipment node can
  // legitimately be booked from two different sources; with a "<loco>/<nodeId>" key those two
  // would share one entry and expanding either would expand both.
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

  return (
    <div className="section-dashboard-page">
      <PageHeader
        // Always names the section being managed, whichever way it was chosen.
        title={`Section Bookings${sectionCode ? ` — ${sectionCode}` : ''}`}
        description="Bookings routed to a section, grouped by where they were raised and then by the equipment they were raised against."
      />

      {/* ADMIN ONLY. A Supervisor is locked to their own section server-side, so offering them a
          selector could only ever produce a 403 - it is not merely hidden, it is not rendered. */}
      {admin && (
        <div className="field section-dashboard-picker">
          <label className="field-label" htmlFor="section-picker">
            Section
          </label>
          {sectionsLoading ? (
            <div className="field-loading" role="status">
              Loading sections…
            </div>
          ) : (
            <select
              id="section-picker"
              value={sectionCode ?? ''}
              onChange={(e) => setSectionCode(e.target.value || null)}
            >
              <option value="">Select a section…</option>
              {sections.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.code}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {!admin && !sectionsLoading && !user?.section && (
        <div className="form-error" role="alert">
          No section is assigned to your account. Contact an administrator to get section work
          functionality enabled.
        </div>
      )}

      {actionError && (
        <div className="form-error" role="alert">
          {actionError}
        </div>
      )}

      {admin && !sectionCode && !sectionsLoading && (
        <EmptyState
          title="Select a section"
          description="Choose a section above to see the bookings routed to it."
        />
      )}

      {sectionCode && summary && (
        <div className="metric-row">
          <MetricCard
            label="Open"
            value={summary.open}
            tone={summary.open > 0 ? 'attention' : 'neutral'}
            className="section-summary-card"
            valueClassName="section-summary-value"
          />
          <MetricCard
            label="In Progress"
            value={summary.in_progress}
            className="section-summary-card"
            valueClassName="section-summary-value"
          />
          <MetricCard
            label="Attended Today"
            value={summary.attended_today}
            className="section-summary-card"
            valueClassName="section-summary-value"
          />
          <MetricCard
            label="Reopened"
            value={summary.reopened}
            tone={summary.reopened > 0 ? 'attention' : 'neutral'}
            hint={summary.reopened > 0 ? 'Needs attention' : undefined}
            className="section-summary-card"
            valueClassName="section-summary-value"
          />
        </div>
      )}

      {sectionCode && !initialLoading && !error && assignments.length > 0 && (
        <div className="filter-bar filter-bar-inline section-dashboard-filter-bar">
          <span className="section-dashboard-filter-label">Status</span>
          <div className="segmented" role="group" aria-label="Filter by assignment status">
            <button
              type="button"
              className={`segmented-option${statusFilter === '' ? ' segmented-option-active' : ''}`}
              aria-pressed={statusFilter === ''}
              onClick={() => setStatusFilter('')}
            >
              All ({assignments.length})
            </button>
            {STATUS_FILTERS.map((s) => {
              const n = assignments.filter((a) => a.status === s).length
              return (
                <button
                  key={s}
                  type="button"
                  className={`segmented-option${statusFilter === s ? ' segmented-option-active' : ''}`}
                  aria-pressed={statusFilter === s}
                  onClick={() => setStatusFilter(statusFilter === s ? '' : s)}
                >
                  {s.charAt(0) + s.slice(1).toLowerCase().replace('_', ' ')} ({n})
                </button>
              )
            })}
          </div>
        </div>
      )}

      {sectionCode && initialLoading && <LoadingState layout="cards" label="Loading section bookings…" />}
      {sectionCode && refreshing && <RefreshIndicator label="Updating section bookings…" />}

      {sectionCode && error && (
        <ErrorState
          message={error.message}
          variant={error.unauthorized ? 'unauthorized' : 'error'}
          onRetry={() => loadAssignments(sectionCode)}
        />
      )}

      {sectionCode && !initialLoading && !error && assignments.length === 0 && (
        <EmptyState
          title="No bookings routed to this section"
          description="Bookings appear here once equipment raised against this section is booked during Shed In or a workflow stage."
        />
      )}

      {sectionCode && !initialLoading && !error && assignments.length > 0 && locomotives.length === 0 && (
        <EmptyState
          title="No bookings in this state"
          description="Clear the status filter to see the section's other bookings."
        />
      )}

      {sectionCode && !initialLoading && !error && locomotives.length > 0 && (
        <div className="section-dashboard-equipment-groups">
          {locomotives.map((loco) => (
            <CollapsibleGroup
              key={loco.key}
              id={`section-loco:${loco.key}`}
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
                      id={`section-${sourceKey}`}
                      level="source"
                      expanded={openSources.has(sourceKey)}
                      onToggle={() => toggle(setOpenSources, sourceKey)}
                      title={source.label}
                      // An unrecognised stored value is shown AS STORED and flagged, rather than
                      // hidden or folded into another group - identical handling to the pool.
                      subtitle={source.known ? undefined : 'Unrecognised source value'}
                      count={source.total}
                      countLabel="booking"
                      meta={<StatusPills counts={source.counts} label={source.label} />}
                    >
                      {() =>
                        source.equipment.map((group) => {
                          // Identity is the equipment NODE, scoped to this locomotive AND this
                          // source - two nodes sharing a display name are two groups, and the
                          // same node under two sources is two groups that expand independently.
                          const key = `equipment:${loco.key}:${source.key}:${group.key}`
                          return (
                            <CollapsibleGroup
                              key={key}
                              id={`section-${key}`}
                              level="equipment"
                              expanded={openEquipment.has(key)}
                              onToggle={() => toggle(setOpenEquipment, key)}
                              title={group.label}
                              subtitle={
                                group.path.length > 0
                                  ? group.path.map((p) => p.name).join(' / ')
                                  : undefined
                              }
                              count={group.items.length}
                              countLabel="booking"
                              meta={
                                <StatusPills
                                  counts={countStatuses(group.items.map((a) => a.status))}
                                  label={group.label}
                                />
                              }
                            >
                              {() => (
                                <div className="section-dashboard-equipment-group-cards">
                                  {group.items.map((a) => (
                                    <AssignmentCard
                                      key={a.id}
                                      assignment={a}
                                      busy={busyIds.has(a.id)}
                                      canReopen={canReopen}
                                      canAddSection={canAddSection}
                                      sections={sections}
                                      onStart={() => withBusy(a.id, () => startAssignment(a.id))}
                                      onAttend={(remarks) =>
                                        withBusy(a.id, () => attendAssignment(a.id, remarks))
                                      }
                                      onReopen={(reason) =>
                                        withBusy(a.id, () => reopenAssignment(a.id, reason))
                                      }
                                      onSectionAdded={refreshAll}
                                    />
                                  ))}
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
    </div>
  )
}
