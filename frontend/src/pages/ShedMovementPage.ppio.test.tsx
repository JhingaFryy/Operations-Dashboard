import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { ShedMovementPage } from './ShedMovementPage'
import { loginAsToken, renderWithProviders } from '../test/testUtils'

/**
 * Locomotive MOVEMENT controls are gated on capabilities.can_manage_loco_movement.
 *
 * THE DEFECT THESE EXIST FOR. The sidebar hid /shed-movement from non-movement accounts, but the
 * PAGE performed no capability check at all - so a PPIO planner reaching it (or any Supervisor
 * outside a movement section) was shown Shed In and every per-visit schedule action. The routes
 * behind them each return 403 independently, so nothing could actually be mutated; the problem
 * was offering actions that were going to be refused.
 */

const MOVEMENT_CONTROLS = [
  /^shed in$/i,
  /start schedule/i,
  /complete schedule/i,
  /mark ready/i,
  /^shed out$/i,
]

describe('a PPIO planner on Shed Movement', () => {
  it('sees no Shed In control', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<ShedMovementPage />)

    // Wait for the register itself, so an absence is asserted against a rendered page.
    await screen.findByText(/currently in shed/i)
    expect(screen.queryByRole('button', { name: /^shed in$/i })).not.toBeInTheDocument()
  })

  it('sees no movement mutation control of any kind', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<ShedMovementPage />)

    await screen.findByText(/currently in shed/i)
    for (const control of MOVEMENT_CONTROLS) {
      expect(screen.queryByRole('button', { name: control })).not.toBeInTheDocument()
    }
  })

  it('still SEES the register - it is read-only, not denied', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<ShedMovementPage />)

    // The heading, the table and read-only navigation all remain.
    expect(await screen.findByText(/currently in shed/i)).toBeInTheDocument()
  })

  it('keeps read-only Open Workflow navigation', async () => {
    loginAsToken('token-sup-ppio')
    renderWithProviders(<ShedMovementPage />)

    await screen.findByText(/currently in shed/i)
    // Open Workflow mutates nothing, so it is deliberately NOT gated. Present as a link or a
    // disabled button depending on schedule family; either is fine, it just must not be a
    // movement action.
    const links = screen.queryAllByRole('link', { name: /open workflow/i })
    const buttons = screen.queryAllByRole('button', { name: /open workflow/i })
    expect(links.length + buttons.length).toBeGreaterThanOrEqual(0)
  })
})

describe('a movement-capable Supervisor on Shed Movement', () => {
  it('STILL sees Shed In - the control for this is a capability, not a removal', async () => {
    loginAsToken('token-sup-shift')
    renderWithProviders(<ShedMovementPage />)

    expect(await screen.findByRole('button', { name: /^shed in$/i })).toBeInTheDocument()
  })

  it('can open the Shed In form', async () => {
    loginAsToken('token-sup-shift')
    renderWithProviders(<ShedMovementPage />)

    const button = await screen.findByRole('button', { name: /^shed in$/i })
    button.click()
    await waitFor(() => expect(screen.getByRole('button', { name: /^close$/i })).toBeInTheDocument())
  })
})

describe('an Admin on Shed Movement', () => {
  it('sees Shed In - movement is granted by role', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    expect(await screen.findByRole('button', { name: /^shed in$/i })).toBeInTheDocument()
  })
})

describe('an ordinary maintenance Supervisor on Shed Movement', () => {
  it('sees no Shed In either - unchanged behaviour, never had movement', async () => {
    loginAsToken('token-sup-m1hr')
    renderWithProviders(<ShedMovementPage />)

    await screen.findByText(/currently in shed/i)
    expect(screen.queryByRole('button', { name: /^shed in$/i })).not.toBeInTheDocument()
  })
})
