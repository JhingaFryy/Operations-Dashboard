import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import {
  canManageEquipmentMapping,
  canManageLocoMovement,
  canReadBookingPool,
  hasSectionWorkQueue,
  isAdmin,
} from '../auth/permissions'
import { ThemeToggle } from '../components/common/ThemeToggle'

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? 'nav-link active' : 'nav-link'
}

/** Page context for the top bar, derived from the current route only.
 *
 * This mirrors the title each page already renders in its own PageHeader —
 * it is presentational context, not a second source of truth, and it adds
 * no navigation, no state and no route. Rendered as plain text rather than
 * a heading so each page keeps exactly one h1. */
function routeContext(pathname: string): { group: string; title: string } | null {
  if (pathname === '/') return { group: 'Operations', title: 'Operations Overview' }
  if (pathname.startsWith('/shed-movement')) return { group: 'Operations', title: 'Shed Movement' }
  if (pathname.startsWith('/section-dashboard')) return { group: 'Operations', title: 'Section Dashboard' }
  if (pathname.startsWith('/shed-visits/')) return { group: 'Operations', title: 'Shed Visit Workflow' }
  if (pathname.startsWith('/shed-visit-history')) return { group: 'Operations', title: 'Shed Visits' }
  if (pathname.startsWith('/booking-pool')) return { group: 'Administration', title: 'Booking Pool' }
  if (pathname.startsWith('/admin/equipment-mapping')) {
    return { group: 'Administration', title: 'Equipment Responsibility Mapping' }
  }
  return null
}

/** Application shell: sidebar navigation, top bar, content area.
 *
 * Navigation is derived from the SAME permission helpers the routes
 * themselves use (auth/permissions.ts, mirroring the backend's own rules) —
 * this component introduces no authorization mechanism of its own, and a
 * nav item is never rendered for a page the current user is not authorized
 * for. The backend remains the real boundary and re-checks every request.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const [navOpen, setNavOpen] = useState(false)
  const { pathname } = useLocation()
  const context = routeContext(pathname)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  // Below 1000px the sidebar becomes a fixed drawer that covers the top-left corner of the
  // screen - INCLUDING the Menu button that opened it, which sits in the header beneath it.
  // That left the drawer with no exit: the only control that could close it was underneath
  // it. It now has three, and they are all real rather than decorative: a Close button
  // inside the drawer, the scrim behind it, and Escape.
  useEffect(() => {
    if (!navOpen) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setNavOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [navOpen])

  // Focus follows the drawer: into it on open, back to the button that opened it on close,
  // so a keyboard or screen-reader user is never left pointing at something off-screen.
  // Guarded on the ref being mounted, because the Close button only exists at drawer widths.
  const wasOpen = useRef(false)
  useEffect(() => {
    if (navOpen) closeRef.current?.focus()
    else if (wasOpen.current) toggleRef.current?.focus()
    wasOpen.current = navOpen
  }, [navOpen])

  // Tapping a nav link should navigate, not leave the drawer sitting open over the page it
  // just opened. Closed from the link's own onClick rather than from a route-change effect,
  // so the close is caused by the interaction instead of observed after the fact.
  const closeNav = () => setNavOpen(false)

  const showEquipmentMapping = canManageEquipmentMapping(user)
  // Business Rule Alignment: the Section Dashboard is the primary operational booking page again
  // (Supervisor's own section, real start/attend/reopen mutations) - primary navigation for
  // everyone. The global Booking Pool is Admin-only operational visibility now (GET /api/bookings
  // is Admin-only server-side too - see app/api/bookings.py), so it moves under Administration.
  // WIDENED to include an account holding the booking-routing capability (PPIO): routing is
  // performed from this page, so hiding it would hide the planner's only write. Still not
  // navigation for an ordinary Supervisor, whose surface is their own section's queue, and the
  // server applies the same rule (require_booking_pool_read).
  const showBookingPool = canReadBookingPool(user)
  // Admin-only, matching the server. Deliberately NOT placed near any operational action: the
  // deletion dialogs are reached from a visit's own page, and this is only the record of them.
  const showDeletionHistory = isAdmin(user)
  const showAdministration = showEquipmentMapping || showBookingPool || showDeletionHistory
  // Shed Movement is where locomotives are moved through the shed, so it is navigation for the
  // movement sections only. Everyone else still SEES active locomotives - on the Overview - they
  // simply have no movement actions. Hiding the page is a usability decision; every endpoint
  // behind it independently returns 403.
  const showShedMovement = canManageLocoMovement(user)
  // A planning section owns no section work queue - nothing is ever routed to it, so there is no
  // such thing as "PPIO Bookings". Read from the server's resolved capability, not inferred from
  // whether the account happens to hold some other one.
  const showSectionDashboard = hasSectionWorkQueue(user)

  return (
    <div className={`app-shell${navOpen ? ' app-shell-nav-open' : ''}`}>
      {/* Scrim: only rendered while the drawer is open, and only visible at drawer widths
          (it is display:none on desktop, where the sidebar is part of the layout). Sits
          between the header and the drawer so a tap anywhere outside closes it. */}
      {navOpen && (
        <div
          className="sidebar-scrim"
          role="presentation"
          onClick={() => setNavOpen(false)}
        />
      )}

      <aside className="sidebar" id="app-sidebar">
        <div className="sidebar-brand">
          <span className="sidebar-brand-mark" aria-hidden="true">
            ▣
          </span>
          <span className="sidebar-brand-text">Operations Dashboard</span>
          <button
            ref={closeRef}
            type="button"
            className="sidebar-close"
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <nav aria-label="Main navigation">
          <ul className="nav-list">
            <li className="nav-group">
              <div className="nav-section-label">Operations</div>
              <ul className="nav-sublist">
                <li>
                  <NavLink to="/" end className={navClass} onClick={closeNav}>
                    Overview
                  </NavLink>
                </li>
                {showShedMovement && (
                  <li>
                    <NavLink to="/shed-movement" className={navClass} onClick={closeNav}>
                      Shed Movement
                    </NavLink>
                  </li>
                )}
                {/* The Section Dashboard is a section's OWN work queue - start, attend, reopen.
                    A planning section owns no work there, so it is not navigation for them; the
                    page's own actions are refused server-side regardless. */}
                {user && showSectionDashboard && (
                  <li>
                    <NavLink to="/section-dashboard" className={navClass} onClick={closeNav}>
                      {user.section ? `${user.section.code} Bookings` : 'Section Dashboard'}
                    </NavLink>
                  </li>
                )}
                {user && (
                  <li>
                    <NavLink to="/shed-visit-history" className={navClass} onClick={closeNav}>
                      Shed Visits
                    </NavLink>
                  </li>
                )}
              </ul>
            </li>

            {showAdministration && (
              <li className="nav-group">
                <div className="nav-section-label">Administration</div>
                <ul className="nav-sublist">
                  {showEquipmentMapping && (
                    <li>
                      <NavLink to="/admin/equipment-mapping" className={navClass} onClick={closeNav}>
                        Equipment Responsibility Mapping
                      </NavLink>
                    </li>
                  )}
                  {showBookingPool && (
                    <li>
                      <NavLink to="/booking-pool" className={navClass} onClick={closeNav}>
                        Booking Pool (global)
                      </NavLink>
                    </li>
                  )}
                  {showDeletionHistory && (
                    <li>
                      <NavLink to="/admin/deletion-history" className={navClass} onClick={closeNav}>
                        Deletion History
                      </NavLink>
                    </li>
                  )}
                </ul>
              </li>
            )}
          </ul>
        </nav>

        {user && (
          <p className="sidebar-footnote">
            Workflow stages and checksheet progress open from a locomotive's shed visit.
          </p>
        )}
      </aside>

      <div className="app-main">
        <header className="app-header">
          <button
            ref={toggleRef}
            type="button"
            className="btn btn-secondary btn-small nav-toggle"
            aria-expanded={navOpen}
            aria-controls="app-sidebar"
            onClick={() => setNavOpen((v) => !v)}
          >
            Menu
          </button>

          {context && (
            <div className="app-header-context">
              <span className="app-header-eyebrow">{context.group}</span>
              <span className="app-header-title">{context.title}</span>
            </div>
          )}

          <div className="app-header-spacer" />
          <ThemeToggle />
          {user && (
            <div className="user-summary">
              <div className="user-summary-text">
                <span className="user-name">{user.name}</span>
                <span className="user-meta">
                  {user.role}
                  {user.section ? ` · ${user.section.code}` : ''}
                </span>
              </div>
              <button type="button" className="btn btn-secondary btn-small" onClick={logout}>
                Log out
              </button>
            </div>
          )}
        </header>

        <main className="app-content">{children}</main>
      </div>
    </div>
  )
}
