import { Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { RequirePermission } from './auth/RequirePermission'
import { AppShell } from './layout/AppShell'
import { LoginPage } from './pages/LoginPage'
import { DashboardHome } from './pages/DashboardHome'
import { EquipmentMappingPage } from './pages/admin/EquipmentMappingPage'
import { DeletionHistoryPage } from './pages/admin/DeletionHistoryPage'
import { ShedMovementPage } from './pages/ShedMovementPage'
import { BookingPoolPage } from './pages/BookingPoolPage'
import { SectionDashboardPage } from './pages/SectionDashboardPage'
import { ShedVisitWorkflowPage } from './pages/ShedVisitWorkflowPage'
import { ShedVisitHistoryPage } from './pages/ShedVisitHistoryPage'
import { useAuth } from './auth/useAuth'
import {
  canManageEquipmentMapping,
  canManageLocoMovement,
  canReadBookingPool,
  hasSectionWorkQueue,
  isAdmin,
} from './auth/permissions'

function EquipmentMappingRoute() {
  const { user } = useAuth()
  return (
    <RequirePermission allowed={canManageEquipmentMapping(user)}>
      <EquipmentMappingPage />
    </RequirePermission>
  )
}

// Business Rule Alignment: the global Booking Pool is Admin-only operational visibility now -
// GET /api/bookings itself is Admin-only server-side (see app/api/bookings.py); this is the
// matching frontend gate so a Supervisor who navigates here directly sees a clear message
// instead of a page that would just fail every request with 403.
/** The global Booking Pool. Admin as before, plus an account holding the booking-routing
 *  capability - a planner cannot decide where a finding should go without seeing the pool. The
 *  server enforces the same rule (require_booking_pool_read); this only stops the page being
 *  reachable for someone who would be refused by it. */
/** A section's own work queue. A PLANNING section has none - nothing is ever routed to it, so
 *  this page would be a dashboard for a section that can never have work. The backend refuses
 *  these routes independently (section_dashboard_service._assert_can_operate_section); this stops
 *  a planner who types the URL from reaching the page at all, rather than letting them load it
 *  and watch every request fail. */
/** The shed movement register. Its entire purpose is movement mutation - Shed In, Start/Complete
 *  Schedule, Mark Ready, Shed Out - so an account without movement capability has nothing to do
 *  here; the same locomotives are readable on the Overview. The sidebar already hides the link;
 *  this stops a direct URL reaching a page with every action stripped out, which reads as a bug
 *  rather than as a permission boundary. Each endpoint behind it also returns 403 independently. */
function ShedMovementRoute() {
  const { user } = useAuth()
  return (
    <RequirePermission allowed={canManageLocoMovement(user)}>
      <ShedMovementPage />
    </RequirePermission>
  )
}

function SectionDashboardRoute() {
  const { user } = useAuth()
  return (
    <RequirePermission allowed={hasSectionWorkQueue(user)}>
      <SectionDashboardPage />
    </RequirePermission>
  )
}

function BookingPoolRoute() {
  const { user } = useAuth()
  return (
    <RequirePermission allowed={canReadBookingPool(user)}>
      <BookingPoolPage />
    </RequirePermission>
  )
}

/** The permanent record of Admin deletions. Admin-only, matching the server: every
 * /api/admin/deletion-history route is Depends(require_admin), so a Supervisor reaching here
 * directly sees a clear message rather than a page whose every request 403s. */
function DeletionHistoryRoute() {
  const { user } = useAuth()
  return (
    <RequirePermission allowed={isAdmin(user)}>
      <DeletionHistoryPage />
    </RequirePermission>
  )
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <AppShell>
                <DashboardHome />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/shed-movement"
          element={
            <ProtectedRoute>
              <AppShell>
                <ShedMovementRoute />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/booking-pool"
          element={
            <ProtectedRoute>
              <AppShell>
                <BookingPoolRoute />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/section-dashboard"
          element={
            <ProtectedRoute>
              <AppShell>
                <SectionDashboardRoute />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/shed-visits/:visitId/workflow"
          element={
            <ProtectedRoute>
              <AppShell>
                <ShedVisitWorkflowPage />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/shed-visit-history"
          element={
            <ProtectedRoute>
              <AppShell>
                <ShedVisitHistoryPage />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/deletion-history"
          element={
            <ProtectedRoute>
              <AppShell>
                <DeletionHistoryRoute />
              </AppShell>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/equipment-mapping"
          element={
            <ProtectedRoute>
              <AppShell>
                <EquipmentMappingRoute />
              </AppShell>
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  )
}

export default App
