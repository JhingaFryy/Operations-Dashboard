import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { addShedVisit } from '../mocks/fixtures'
import { setShedOutBlocker } from '../mocks/handlers'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { ShedMovementPage } from './ShedMovementPage'

/**
 * Shed Out refused, as the Shed Movement register shows it.
 *
 * This register is the ONLY place a MAJOR visit can be shed out (the workflow page is Minor-only),
 * and it used to show "This visit is not eligible for Shed Out." for every refusal - including a
 * READY MAJOR visit with 94 checksheets outstanding. The gates themselves are the backend's
 * (backend tests/test_major_shed_out.py); these tests pin what the operator is told.
 */

function outstanding(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    requirement_id: 100 + i,
    section: 'M35-Aux',
    label: `Equipment ${i + 1}`,
    template_name: `Checksheet ${i + 1}`,
    stage: null,
    maintenance_type: i === 2 ? 'OVERHAUL' : null,
    status: i === 1 ? 'DRAFT' : null,
    reason: i === 1 ? 'DRAFT' : 'MISSING',
  }))
}

const CHECKSHEET_BLOCKED = {
  code: 'SHED_OUT_BLOCKED',
  message: 'Shed Out blocked: 94 required checksheets are still outstanding.',
  blocked_by: ['CHECKSHEETS'],
  stage_blockers: [],
  booking_blockers: [],
  checksheet_blockers: [],
  checksheets: {
    code: 'CHECKSHEETS_INCOMPLETE',
    message: 'Shed Out blocked: 94 required checksheets are still outstanding.',
    work_package_generated: true,
    checksheets_readable: true,
    total_required: 94,
    satisfied: 0,
    outstanding: 94,
    optional: 2,
    deactivated: 0,
    outstanding_entries: outstanding(25),
    outstanding_entries_truncated: true,
  },
}

function seedReadyMajor() {
  return addShedVisit({
    loco_number: '39066',
    schedule_family: 'MAJOR',
    schedule_variant: 'TOH',
    status: 'READY',
    arrival_condition: 'WORKING',
    arrival_at: '2026-08-28T01:00:00Z',
    schedule_started_at: '2026-09-01T08:00:00Z',
    ready_at: '2026-09-16T11:19:00Z',
    departed_at: null,
    booking_total: 0,
    pending_booking_count: 0,
  })
}

async function attemptShedOut(user: ReturnType<typeof userEvent.setup>) {
  const table = await screen.findByRole('table')
  await user.click(within(table).getByRole('button', { name: /shed out/i }))
  const dialog = await screen.findByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: /shed out/i }))
  return dialog
}

describe('Shed Out refused - what the operator is told', () => {
  it('names the outstanding checksheet count instead of "not eligible"', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    setShedOutBlocker({ status: 409, detail: CHECKSHEET_BLOCKED })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await attemptShedOut(user)

    expect(
      await within(dialog).findByText('Shed Out blocked: 94 required checksheets are still outstanding.'),
    ).toBeInTheDocument()
    expect(within(dialog).getByText('0 of 94 required checksheets satisfied.')).toBeInTheDocument()
    expect(within(dialog).queryByText(/not eligible for shed out/i)).toBeNull()
  })

  it('lists only the first few outstanding checksheets and says how many more there are', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    setShedOutBlocker({ status: 409, detail: CHECKSHEET_BLOCKED })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await attemptShedOut(user)
    const items = within(await within(dialog).findByRole('list')).getAllByRole('listitem')

    expect(items).toHaveLength(5)
    expect(items[0].textContent).toContain('M35-Aux · Equipment 1')
    expect(items[0].textContent).toContain('Not started')
    expect(items[1].textContent).toContain('Draft')
    expect(items[2].textContent).toContain('OVERHAUL')
    expect(within(dialog).getByText(/and 89 more/i)).toBeInTheDocument()
  })

  it('offers no way around the checksheet gate and leaves the locomotive Ready', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    setShedOutBlocker({ status: 409, detail: CHECKSHEET_BLOCKED })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await attemptShedOut(user)
    await within(dialog).findByText(/94 required checksheets are still outstanding/)

    const labels = within(dialog).getAllByRole('button').map((b) => b.textContent ?? '')
    expect(labels.some((l) => /override|force|anyway|bypass|skip/i.test(l))).toBe(false)
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }))
    const table = await screen.findByRole('table')
    expect(within(table).getByText('Ready')).toBeInTheDocument()
  })

  it('keeps a booking blocker readable, by booking and status', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    setShedOutBlocker({
      status: 409,
      detail: {
        code: 'SHED_OUT_BLOCKED',
        message: 'Shed Out blocked: 1 booking still has work outstanding. Shed Out blocked: 1 booking has no responsible section assigned.',
        blocked_by: ['BOOKINGS'],
        stage_blockers: [],
        booking_blockers: [
          { booking_id: 80, booking_source: 'LOG_BOOK', status: 'OPEN' },
          { booking_id: 82, booking_source: 'LOG_BOOK', status: 'NO_ASSIGNMENTS' },
        ],
        checksheet_blockers: [],
        checksheets: null,
      },
    })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await attemptShedOut(user)

    expect(await within(dialog).findByText('Shed Out blocked: 2 bookings need attention.')).toBeInTheDocument()
    const items = within(within(dialog).getByRole('list')).getAllByRole('listitem')
    expect(items.map((i) => i.textContent)).toEqual([
      'Booking #80 (LOG BOOK) — Open',
      'Booking #82 (LOG BOOK) — No responsible section assigned',
    ])
  })

  it('shows every refusing gate together', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    setShedOutBlocker({
      status: 409,
      detail: {
        ...CHECKSHEET_BLOCKED,
        blocked_by: ['BOOKINGS', 'CHECKSHEETS'],
        booking_blockers: [{ booking_id: 80, booking_source: 'LOG_BOOK', status: 'IN_PROGRESS' }],
      },
    })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await attemptShedOut(user)

    expect(await within(dialog).findByText('Shed Out blocked: 1 booking needs attention.')).toBeInTheDocument()
    expect(within(dialog).getByText(/94 required checksheets are still outstanding/)).toBeInTheDocument()
  })

  it('shows a not-Ready refusal in its own words', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    setShedOutBlocker({
      status: 409,
      detail: {
        code: 'VISIT_NOT_READY',
        message: 'Complete the schedule (which makes the locomotive Ready) before Shed Out.',
      },
    })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await attemptShedOut(user)

    expect(
      await within(dialog).findByText('Complete the schedule (which makes the locomotive Ready) before Shed Out.'),
    ).toBeInTheDocument()
  })

  it('keeps the generic wording only for failures that carry nothing specific', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    setShedOutBlocker({ status: 500, detail: 'Internal Server Error' })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await attemptShedOut(user)

    const alert = await within(dialog).findByRole('alert')
    expect(alert.textContent?.length).toBeGreaterThan(0)
    expect(alert.textContent).not.toMatch(/checksheets are still outstanding/)
    expect(within(dialog).queryByRole('list')).toBeNull()
  })

  it('sheds a complete MAJOR visit out normally', async () => {
    loginAsToken('token-admin')
    seedReadyMajor()
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    await attemptShedOut(user)

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(await screen.findByText('39066 has been shed out.')).toBeInTheDocument()
  })
})
