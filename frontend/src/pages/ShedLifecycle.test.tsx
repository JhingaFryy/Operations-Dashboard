import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ShedMovementPage } from './ShedMovementPage'
import { addShedVisit, EMPTY_TIMINGS } from '../mocks/fixtures'
import { loginAsToken, renderWithProviders } from '../test/testUtils'

/**
 * New shed workflow in the UI: Spare -> Schedule In Progress -> Ready -> Shed Out.
 *
 * Phase, its label and the offered actions are all server-derived; these tests assert the page
 * RENDERS what the server says rather than re-deriving anything, and that an action invalid for
 * the current phase is never offered.
 */

const ARRIVAL = '2026-09-01T08:32:00Z'

function seedVisit(overrides: Partial<Parameters<typeof addShedVisit>[0]> = {}) {
  return addShedVisit({
    loco_number: '39018',
    schedule_family: 'MINOR',
    schedule_variant: 'IA',
    arrival_condition: 'WORKING',
    arrival_at: ARRIVAL,
    status: 'IN_SHED',
    schedule_started_at: null,
    ready_at: null,
    departed_at: null,
    booking_total: 0,
    pending_booking_count: 0,
    timings: { ...EMPTY_TIMINGS, waiting_seconds: 2280, waiting_running: true },
    ...overrides,
  })
}

async function table() {
  return await screen.findByRole('table')
}

describe('Shed lifecycle phases', () => {
  it('a freshly shedded-in locomotive shows Spare <schedule> and only Start Schedule', async () => {
    seedVisit()
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    const t = await table()
    expect(within(t).getByText('Spare IA')).toBeInTheDocument()
    expect(within(t).getByRole('button', { name: 'Start Schedule' })).toBeInTheDocument()
    expect(within(t).queryByRole('button', { name: 'Complete Schedule' })).not.toBeInTheDocument()
    expect(within(t).queryByRole('button', { name: 'Shed Out' })).not.toBeInTheDocument()
  })

  it('shows Spare IOH for a Major visit - never "MAJOR IOH"', async () => {
    seedVisit({ loco_number: '39160', schedule_family: 'MAJOR', schedule_variant: 'IOH' })
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    const t = await table()
    expect(within(t).getByText('Spare IOH')).toBeInTheDocument()
    expect(within(t).queryByText(/MAJOR IOH/)).not.toBeInTheDocument()
  })

  it('an in-progress visit shows "<schedule> In Progress" and only Complete Schedule', async () => {
    seedVisit({ schedule_started_at: '2026-09-01T09:10:00Z' })
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    const t = await table()
    expect(within(t).getByText('IA In Progress')).toBeInTheDocument()
    expect(within(t).getByRole('button', { name: 'Complete Schedule' })).toBeInTheDocument()
    expect(within(t).queryByRole('button', { name: 'Start Schedule' })).not.toBeInTheDocument()
  })

  it('a completed visit shows Ready and only Shed Out', async () => {
    seedVisit({ schedule_started_at: '2026-09-01T09:10:00Z', ready_at: '2026-09-01T13:48:00Z', status: 'READY' })
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    const t = await table()
    expect(within(t).getByText('Ready')).toBeInTheDocument()
    expect(within(t).getByRole('button', { name: 'Shed Out' })).toBeInTheDocument()
    expect(within(t).queryByRole('button', { name: 'Complete Schedule' })).not.toBeInTheDocument()
  })

  it('Start Schedule opens a date/time dialog pre-filled with now, and advances the phase', async () => {
    seedVisit()
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const t = await table()
    await user.click(within(t).getByRole('button', { name: 'Start Schedule' }))

    const dialog = await screen.findByRole('dialog')
    const input = within(dialog).getByLabelText(/schedule start date\/time/i) as HTMLInputElement
    // Pre-filled with the current local time - the common case is one click - but editable.
    expect(input.value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)

    await user.click(within(dialog).getByRole('button', { name: 'Start Schedule' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const refreshed = await table()
    expect(within(refreshed).getByText('IA In Progress')).toBeInTheDocument()
  })

  it('the timestamp stays editable before submitting', async () => {
    seedVisit()
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    await user.click(within(await table()).getByRole('button', { name: 'Start Schedule' }))
    const dialog = await screen.findByRole('dialog')
    const input = within(dialog).getByLabelText(/schedule start date\/time/i)

    await user.clear(input)
    await user.type(input, '2026-09-01T09:10')
    expect(input).toHaveValue('2026-09-01T09:10')
  })

  it('Complete Schedule on a Minor visit completes the inspection, not Ready', async () => {
    seedVisit({ schedule_started_at: '2026-09-01T09:10:00Z' })
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    await user.click(within(await table()).getByRole('button', { name: 'Complete Schedule' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Test After comes next/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Complete Schedule' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const refreshed = await table()
    expect(within(refreshed).getByText('IA Inspection Complete')).toBeInTheDocument()
    expect(within(refreshed).queryByText('Ready')).not.toBeInTheDocument()
    expect(within(refreshed).getByRole('button', { name: 'Mark Ready' })).toBeInTheDocument()
    expect(within(refreshed).queryByRole('button', { name: 'Shed Out' })).not.toBeInTheDocument()
    expect(await screen.findByText(/Test After is next/)).toBeInTheDocument()
  })

  it('Mark Ready advances an inspected Minor visit to Ready', async () => {
    seedVisit({ schedule_started_at: '2026-09-01T09:10:00Z', inspection_completed_at: '2026-09-01T13:48:00Z' })
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const t = await table()
    expect(within(t).queryByRole('button', { name: 'Complete Schedule' })).not.toBeInTheDocument()
    await user.click(within(t).getByRole('button', { name: 'Mark Ready' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Test After must already be completed/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Mark Ready' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const refreshed = await table()
    expect(within(refreshed).getByText('Ready')).toBeInTheDocument()
    expect(within(refreshed).getByRole('button', { name: 'Shed Out' })).toBeInTheDocument()
  })

  it('Complete Schedule on a Major visit still goes straight to Ready', async () => {
    seedVisit({ loco_number: '39160', schedule_family: 'MAJOR', schedule_variant: 'IOH', schedule_started_at: '2026-09-01T09:10:00Z' })
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    await user.click(within(await table()).getByRole('button', { name: 'Complete Schedule' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Complete Schedule' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(within(await table()).getByText('Ready')).toBeInTheDocument()
  })

  it('surfaces a backend rejection instead of silently advancing the phase', async () => {
    // Already started; the mock backend rejects a second start with 409.
    const visit = seedVisit()
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    await user.click(within(await table()).getByRole('button', { name: 'Start Schedule' }))
    // Simulate another operator having started it in the meantime.
    visit.schedule_started_at = '2026-09-01T09:10:00Z'

    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Start Schedule' }))

    expect(await within(dialog).findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('cancelling the dialog changes nothing', async () => {
    seedVisit()
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    await user.click(within(await table()).getByRole('button', { name: 'Start Schedule' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(within(await table()).getByText('Spare IA')).toBeInTheDocument()
  })

  it('renders the phase-appropriate timing figure', async () => {
    seedVisit({
      schedule_started_at: '2026-09-01T09:10:00Z',
      timings: { ...EMPTY_TIMINGS, schedule_seconds: 8040, schedule_running: true },
    })
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    expect(within(await table()).getByText('Elapsed 2h 14m')).toBeInTheDocument()
  })
})
