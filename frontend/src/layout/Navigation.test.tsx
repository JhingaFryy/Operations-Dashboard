import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { AppShell } from './AppShell'
import { loginAsToken, renderWithProviders } from '../test/testUtils'

/**
 * Navigation reflects the server-issued capabilities.
 *
 * Hiding a link is a usability decision only - every endpoint behind it independently returns
 * 403, which is asserted in the backend's test_operations_authorization.py.
 */
describe('Capability-driven navigation', () => {
  it('a movement (SHIFT) Supervisor sees Shed Movement', async () => {
    loginAsToken('token-sup-shift')
    renderWithProviders(<AppShell><div /></AppShell>)

    expect(await screen.findByRole('link', { name: /shed movement/i })).toBeInTheDocument()
  })

  it('an ordinary section Supervisor does not see Shed Movement', async () => {
    loginAsToken('token-sup-m1hr')
    renderWithProviders(<AppShell><div /></AppShell>)

    // Their own section queue is present, so the nav has rendered before we assert an absence.
    await screen.findByRole('link', { name: /m1-hr bookings/i })
    expect(screen.queryByRole('link', { name: /shed movement/i })).not.toBeInTheDocument()
  })

  it('the section link names the Supervisor’s own section', async () => {
    loginAsToken('token-sup-m1hr')
    renderWithProviders(<AppShell><div /></AppShell>)

    expect(await screen.findByRole('link', { name: /m1-hr bookings/i })).toBeInTheDocument()
  })

  it('a maintenance or movement Supervisor does not see the global Booking Pool', async () => {
    // CHANGED 2026-10-06: a PLANNER does see it - routing is performed from that page, so hiding
    // it would hide their only write. Everyone else is unchanged, which is the half that matters.
    loginAsToken('token-sup-shift')
    renderWithProviders(<AppShell><div /></AppShell>)

    await screen.findByRole('link', { name: /shed movement/i })
    expect(screen.queryByRole('link', { name: /booking pool/i })).not.toBeInTheDocument()
  })

  it('movement privilege grants no administration navigation', async () => {
    loginAsToken('token-sup-shift')
    renderWithProviders(<AppShell><div /></AppShell>)

    await screen.findByRole('link', { name: /shed movement/i })
    expect(screen.queryByText(/^administration$/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /equipment responsibility mapping/i })).not.toBeInTheDocument()
  })
})

/**
 * Admin / Superadmin navigation - the authorization-policy correction.
 *
 * The previous phase excluded Admin from can_access_operations_dashboard, which hid the entire
 * shell from the genuine Superadmin. These assert the corrected hierarchy.
 */
describe('Admin navigation', () => {
  it('sees Shed Movement (movement is granted by role, not by section)', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<AppShell><div /></AppShell>)

    expect(await screen.findByRole('link', { name: /shed movement/i })).toBeInTheDocument()
  })

  it('sees the global Booking Pool, which no Supervisor does', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<AppShell><div /></AppShell>)

    expect(await screen.findByRole('link', { name: /booking pool/i })).toBeInTheDocument()
  })

  it('sees the Administration group', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<AppShell><div /></AppShell>)

    await screen.findByRole('link', { name: /booking pool/i })
    expect(screen.getByText(/^administration$/i)).toBeInTheDocument()
  })
})


/**
 * PPIO is the PLANNING section.
 *
 * THE PRODUCTION DEFECT THESE EXIST FOR. A real PPIOTEST account - role Supervisor, section PPIO,
 * no dashboard_access grant - was shown "PPIO Bookings" (a section work queue for a section
 * nothing is ever routed to) and was NOT shown the global Booking Pool (the one page it needs).
 * Both followed from the same cause: routing capability required a per-user grant, so the account
 * looked like an ordinary section Supervisor.
 *
 * The fixture deliberately carries NO routing grant, so these would fail again if the capability
 * ever went back to needing one.
 */
describe('PPIO planner navigation', () => {
  const PPIO_EXPECTED = [
    /operations overview|overview/i,
    /shed visits/i,
    /booking pool/i,
  ]

  it('sees the global Booking Pool with no routing grant', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<AppShell><div /></AppShell>)

    expect(await screen.findByRole('link', { name: /booking pool/i })).toBeInTheDocument()
  })

  it('does NOT see a "PPIO Bookings" section work queue', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<AppShell><div /></AppShell>)

    // Wait for the nav to render before asserting an absence.
    await screen.findByRole('link', { name: /booking pool/i })
    expect(screen.queryByRole('link', { name: /ppio bookings/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /section dashboard/i })).not.toBeInTheDocument()
  })

  it('sees Overview, Shed Visits and the Booking Pool', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<AppShell><div /></AppShell>)

    await screen.findByRole('link', { name: /booking pool/i })
    for (const expected of PPIO_EXPECTED) {
      expect(screen.getByRole('link', { name: expected })).toBeInTheDocument()
    }
  })

  it('does not see Shed Movement - PPIO moves no locomotives', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<AppShell><div /></AppShell>)

    await screen.findByRole('link', { name: /booking pool/i })
    expect(screen.queryByRole('link', { name: /shed movement/i })).not.toBeInTheDocument()
  })

  it('does not see equipment mapping or any other administration page', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<AppShell><div /></AppShell>)

    await screen.findByRole('link', { name: /booking pool/i })
    expect(screen.queryByRole('link', { name: /equipment responsibility|equipment mapping/i }))
      .not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /deletion history/i })).not.toBeInTheDocument()
  })
})

describe('an ordinary maintenance Supervisor is unaffected', () => {
  it('still sees its own section work queue', async () => {
    // The control. Removing the work queue for planning must not remove anyone else's.
    loginAsToken('token-sup-m1hr')
    renderWithProviders(<AppShell><div /></AppShell>)

    expect(await screen.findByRole('link', { name: /m1-hr bookings/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /booking pool/i })).not.toBeInTheDocument()
  })
})
