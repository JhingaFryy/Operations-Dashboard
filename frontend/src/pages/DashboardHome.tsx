import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { listCurrentShedVisits } from '../api/shedVisits'
import { orderShedVisitsByArrivalDesc } from '../lib/shedVisitOrder'
import { ApiError, friendlyErrorMessage } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { LocoVisitCard } from '../components/shed/LocoVisitCard'
import { MetricCard } from '../components/ui/MetricCard'
import { PageHeader } from '../components/ui/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import type { CurrentShedVisit } from '../types'

/** Operational overview.
 *
 * Every figure on this page is either a field of GET /api/shed-visits/current
 * or a literal tally of the rows it returned — nothing is estimated,
 * back-filled or invented. In particular there is no "visits blocked for
 * Shed Out" metric: Shed Out readiness is only available per-visit and
 * Admin-only (GET /shed-visits/{id}/shed-out-eligibility), so it cannot be
 * summarised here without N extra requests, and it is not faked.
 */
export function DashboardHome() {
  const { user } = useAuth()

  const [visits, setVisits] = useState<CurrentShedVisit[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; unauthorized: boolean } | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    listCurrentShedVisits()
      .then(setVisits)
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

  const metrics = useMemo(() => {
    const pendingBookings = visits.reduce((sum, v) => sum + v.pending_booking_count, 0)
    return {
      inShed: visits.length,
      bookings: visits.reduce((sum, v) => sum + v.booking_total, 0),
      pendingBookings,
      locosWithPending: visits.filter((v) => v.pending_booking_count > 0).length,
    }
  }, [visits])

  /** NEWEST ARRIVAL FIRST - Shed-In chronology, the same order Shed Movement uses.
   *
   * This previously ordered by outstanding bookings and then by the OLDEST arrival, which put a
   * newly shed-in locomotive near the bottom and disagreed with Shed Movement for the same data.
   * Both pages now apply one shared comparator (lib/shedVisitOrder), so they cannot drift again.
   *
   * Ordering only - no row is hidden, and every booking count, duration, chip and action is
   * untouched. */
  const ordered = useMemo(() => orderShedVisitsByArrivalDesc(visits), [visits])

  return (
    <div className="overview-page">
      <PageHeader
        title="Operations Overview"
        description={
          user
            ? `${user.name} · ${user.role}${user.section ? ` · ${user.section.code}` : ''}`
            : 'Shed status at a glance.'
        }
      />

      {loading && <LoadingState layout="overview" label="Loading shed status…" />}

      {error && (
        <ErrorState
          message={error.message}
          variant={error.unauthorized ? 'unauthorized' : 'error'}
          onRetry={load}
        />
      )}

      {!loading && !error && (
        <>
          <div className="metric-row">
            <MetricCard
              label="Locomotives in shed"
              value={metrics.inShed}
              hint="Open shed visits right now"
            />
            <MetricCard
              label="Bookings raised"
              value={metrics.bookings}
              hint="Across all locomotives in shed"
            />
            <MetricCard
              label="Bookings pending"
              value={metrics.pendingBookings}
              tone={metrics.pendingBookings > 0 ? 'attention' : 'neutral'}
              hint="Not yet attended in every responsible section"
            />
            <MetricCard
              label="Locos awaiting work"
              value={metrics.locosWithPending}
              tone={metrics.locosWithPending > 0 ? 'attention' : 'neutral'}
              hint="Have at least one pending booking"
            />
          </div>

          {/* The major operational section of this page: every locomotive
              in the shed right now, attention-ordered, as full-width rows.
              The "Go to Section Dashboard / Shed Movement / Booking Pool"
              quick-link cards that used to sit below were removed — they
              duplicated the permanent sidebar navigation, and the space is
              better spent on this list. */}
          <section className="overview-section">
            <div className="panel-header">
              <h2 className="section-title">
                Active shed visits{' '}
                {ordered.length > 0 && (
                  <span className="table-toolbar-count">({ordered.length} in the shed now)</span>
                )}
              </h2>
              <Link className="overview-section-link" to="/shed-movement">
                Shed movement register →
              </Link>
            </div>

            {ordered.length === 0 ? (
              <EmptyState
                title="No locomotives are currently in the shed."
                description="Record an arrival from the Shed Movement page."
              />
            ) : (
              <div className="loco-visit-list">
                {ordered.map((v) => (
                  <LocoVisitCard key={v.id} visit={v} />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
