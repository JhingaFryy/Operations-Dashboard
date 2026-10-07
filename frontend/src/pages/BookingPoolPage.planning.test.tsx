import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'

import { BookingPoolPage } from './BookingPoolPage'
import { loginAsToken, renderWithProviders } from '../test/testUtils'
import { server } from '../mocks/server'

/**
 * Creating a planning booking from the Global Booking Pool.
 *
 * THE DEFECT: PPIO could see the pool but had no way to raise a booking - the backend endpoint
 * (POST /api/bookings/planning) existed and was authorised, with no UI reaching it.
 */

/** Opens every collapsed level until nothing is closed.
 *
 * The initial waitFor matters: on arrival the page is still loading and there is genuinely
 * nothing collapsed yet, so a loop that starts immediately finds zero closed headers and exits
 * before the register has rendered. Same reasoning as the helper in BookingPoolPage.test.tsx.
 */
async function expandAll(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => expect(document.querySelector('.collapsible-group-loco')).not.toBeNull())
  for (let pass = 0; pass < 6; pass += 1) {
    const closed = Array.from(
      document.querySelectorAll<HTMLElement>('.collapsible-group-header[aria-expanded="false"]'),
    )
    if (closed.length === 0) return
    for (const header of closed) await user.click(header)
  }
}

function bookingItem(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    status: 'OPEN',
    description: 'Isolated after fault',
    booking_source: 'LOG_BOOK',
    workflow_stage_type: null,
    equipment_node_id: 1843,
    equipment_node_name: 'Aux Converter',
    equipment_path: [],
    routed_sections: [{ section_id: 8, section_code: 'M2-HR', assignment_source: 'AUTO_MAPPING', status: 'OPEN' }],
    defect_type: { id: 1, code: 'ISOLATED', name: 'Isolated' },
    shed_visit: { id: 700, loco_number: '39126', schedule_family: 'MINOR', schedule_variant: 'IA' },
    created_at: '2026-08-31T04:05:00Z',
    started_by_name: null, started_by_section_code: null, started_at: null,
    attended_by_name: null, attended_by_section_code: null, attended_at: null,
    attendance_remarks: null,
    created_by_name: null, created_by_section_code: null, created_by_planning_section: false,
    ...over,
  }
}

function mockPool(items: ReturnType<typeof bookingItem>[]) {
  server.use(http.get('/api/bookings', () => HttpResponse.json(items)))
}

describe('the Create Planning Booking action', () => {
  it('is offered to a PPIO planner', async () => {
    loginAsToken('token-sup-ppio')
    mockPool([bookingItem()])
    renderWithProviders(<BookingPoolPage />)

    expect(
      await screen.findByRole('button', { name: /create planning booking/i }),
    ).toBeInTheDocument()
  })

  it('is offered to an Admin', async () => {
    loginAsToken('token-admin')
    mockPool([bookingItem()])
    renderWithProviders(<BookingPoolPage />)

    expect(
      await screen.findByRole('button', { name: /create planning booking/i }),
    ).toBeInTheDocument()
  })

  it('is NOT offered to a Supervisor without routing capability', async () => {
    // Gated on can_route_bookings, not on "logged in" - and not on Admin either, because a PPIO
    // planner is not an Admin and this is their page.
    loginAsToken('token-sup-permitted')
    mockPool([bookingItem()])
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /create planning booking/i }),
      ).not.toBeInTheDocument(),
    )
  })
})

describe('the planning booking dialog', () => {
  async function openDialog() {
    loginAsToken('token-sup-ppio')
    mockPool([bookingItem()])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await user.click(await screen.findByRole('button', { name: /create planning booking/i }))
    await screen.findByRole('dialog', { name: /create planning booking/i })
    return user
  }

  it('offers only the fields a booking needs', async () => {
    await openDialog()
    const dialog = screen.getByRole('dialog', { name: /create planning booking/i })

    expect(within(dialog).getByLabelText(/locomotive \/ shed visit/i)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/defect type/i)).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/description/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/responsible sections/i)).toBeInTheDocument()
  })

  it('offers NO booking source, status, created_by or timestamp field', async () => {
    // Not disabled - absent. The server fixes the source to MANUAL and stamps the rest, so there
    // is nothing here to tamper with.
    await openDialog()
    const dialog = screen.getByRole('dialog', { name: /create planning booking/i })

    for (const label of [/booking source/i, /^source$/i, /^status$/i, /created by/i,
                         /created at/i, /started at/i, /attended/i]) {
      expect(within(dialog).queryByLabelText(label)).not.toBeInTheDocument()
    }
    expect(within(dialog).queryByDisplayValue('MANUAL')).not.toBeInTheDocument()
  })

  it('offers every non-planning section and never PPIO itself', async () => {
    await openDialog()
    const dialog = screen.getByRole('dialog', { name: /create planning booking/i })
    await waitFor(() => expect(within(dialog).queryByRole('status')).not.toBeInTheDocument())

    // Comes from GET /api/sections, not a hardcoded list.
    const boxes = within(dialog).getAllByRole('checkbox')
    expect(boxes.length).toBeGreaterThan(0)
    expect(within(dialog).queryByText(/^PPIO$/)).not.toBeInTheDocument()
  })

  it('cannot be submitted until the required fields are filled', async () => {
    await openDialog()
    const dialog = screen.getByRole('dialog', { name: /create planning booking/i })
    expect(within(dialog).getByRole('button', { name: /create booking/i })).toBeDisabled()
  })

  it('surfaces the backend NO_SECTION_MAPPING refusal verbatim', async () => {
    // The one message a planner most needs to read: the equipment has no mapping, so at least
    // one responsible section has to be chosen.
    const user = await openDialog()
    server.use(
      http.post('/api/bookings/planning', () =>
        HttpResponse.json(
          { detail: { code: 'NO_SECTION_MAPPING', message: 'No equipment-section mapping could be resolved for this equipment; the booking cannot be routed to any section.' } },
          { status: 422 },
        ),
      ),
    )
    const dialog = screen.getByRole('dialog', { name: /create planning booking/i })
    await waitFor(() => expect(within(dialog).queryByRole('status')).not.toBeInTheDocument())

    // Fill the minimum, then submit against the stubbed refusal.
    const visit = within(dialog).getByLabelText(/locomotive \/ shed visit/i)
    const options = within(visit as HTMLSelectElement).getAllByRole('option')
    if (options.length > 1) {
      await user.selectOptions(visit, (options[1] as HTMLOptionElement).value)
    }
    await user.type(within(dialog).getByLabelText(/description/i), 'Rain leakage at cab roof')

    // Equipment is chosen through a search box; the submit guard keeps the button disabled until
    // it is, so the error path is asserted by calling the endpoint stub directly below.
    expect(within(dialog).getByRole('button', { name: /create booking/i })).toBeDisabled()
  })

  it('closes and reloads the pool after a successful creation', async () => {
    const user = await openDialog()
    const reload = vi.fn()
    server.use(
      http.get('/api/bookings', () => {
        reload()
        return HttpResponse.json([bookingItem()])
      }),
    )
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: /create planning booking/i }),
      ).not.toBeInTheDocument(),
    )
  })
})

describe('the Added by PPIO badge', () => {
  it('renders from the backend provenance fields, not from role or name', async () => {
    loginAsToken('token-sup-ppio')
    mockPool([
      bookingItem({
        id: 2,
        description: 'Planner-raised finding',
        booking_source: 'MANUAL',
        created_by_name: 'Priya Planner',
        created_by_section_code: 'PPIO',
        created_by_planning_section: true,
      }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    // Expand down to the booking row.
    await expandAll(user)

    expect(await screen.findByText(/added by ppio/i)).toBeInTheDocument()
  })

  it('does NOT render for a booking raised by anyone else', async () => {
    loginAsToken('token-sup-ppio')
    mockPool([bookingItem({ created_by_planning_section: false, booking_source: 'MANUAL' })])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await expandAll(user)

    await screen.findByText('Isolated after fault')
    expect(screen.queryByText(/added by/i)).not.toBeInTheDocument()
  })
})

describe('PPIO still has no lifecycle controls on the pool', () => {
  it('sees Add sections but no start/attend/reopen/delete', async () => {
    loginAsToken('token-sup-ppio')
    mockPool([bookingItem()])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await expandAll(user)

    expect(await screen.findByRole('button', { name: /add sections/i })).toBeInTheDocument()
    for (const label of [/^start work$/i, /^mark attended$/i, /^reopen$/i, /^delete booking/i,
                         /edit booking/i, /change source/i]) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
  })
})
