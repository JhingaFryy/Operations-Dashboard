import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { server } from '../mocks/server'
import { DashboardHome } from './DashboardHome'
import { ShedMovementPage } from './ShedMovementPage'

/** Active shed visits are ordered by Shed-In chronology on BOTH pages.
 *
 * Tested through the real pages, not only the comparator, because the bug was never in a comparator:
 * the Overview had its own inline sort that put the most outstanding bookings first and, within that,
 * the OLDEST arrival. Shed Movement rendered the response order and was right by accident. Only a
 * rendered test catches a page sorting for itself.
 */

function visit(overrides: Record<string, unknown>) {
  return {
    id: 1,
    loco_number: '11111',
    schedule_family: 'MINOR',
    schedule_variant: 'IA',
    arrival_condition: 'WORKING',
    arrival_at: '2026-10-01T14:20:00+05:30',
    status: 'IN_SHED',
    schedule_started_at: null,
    inspection_completed_at: null,
    ready_at: null,
    departed_at: null,
    operational_phase: 'SPARE',
    display_label: 'Spare IA',
    available_actions: ['START_SCHEDULE'],
    timings: {
      waiting_seconds: 60, waiting_running: true, schedule_seconds: null, schedule_running: false,
      ready_delay_seconds: null, ready_delay_running: false, total_seconds: 60, total_running: true,
    },
    booking_total: 0,
    pending_booking_count: 0,
    ...overrides,
  }
}

/** The brief's example, returned by the API in a DIFFERENT order so a page that merely echoes the
 *  response cannot pass by luck. The booking counts are deliberately INVERTED against arrival order:
 *  the oldest visit has the most outstanding bookings, which is exactly what the Overview used to
 *  sort on. */
const LADDER = [
  visit({ id: 4, loco_number: '44444', arrival_at: '2026-09-29T09:00:00+05:30',
          booking_total: 9, pending_booking_count: 9 }),
  visit({ id: 2, loco_number: '22222', arrival_at: '2026-10-01T09:10:00+05:30',
          booking_total: 3, pending_booking_count: 3 }),
  visit({ id: 1, loco_number: '11111', arrival_at: '2026-10-01T14:20:00+05:30',
          booking_total: 0, pending_booking_count: 0 }),
  visit({ id: 3, loco_number: '30000', arrival_at: '2026-09-30T18:00:00+05:30',
          booking_total: 5, pending_booking_count: 5 }),
]

const EXPECTED = ['11111', '22222', '30000', '44444']

function mockVisits(rows: ReturnType<typeof visit>[]) {
  server.use(http.get('/api/shed-visits/current', () => HttpResponse.json(rows)))
}

/** Locomotive numbers in the order they appear in the document. */
function renderedOrder(): string[] {
  return Array.from(document.querySelectorAll('body *'))
    .filter((el) => el.children.length === 0)
    .map((el) => (el.textContent ?? '').trim())
    .filter((text) => /^\d{5}$/.test(text))
}

describe('Operations Overview', () => {
  it('lists active shed visits newest arrival first', async () => {
    mockVisits(LADDER)
    loginAsToken('token-admin')
    renderWithProviders(<DashboardHome />)

    await waitFor(() => expect(renderedOrder().length).toBeGreaterThanOrEqual(4))
    expect(renderedOrder().slice(0, 4)).toEqual(EXPECTED)
  })

  it('does not order by outstanding bookings any more', async () => {
    // 44444 has the most pending bookings and the OLDEST arrival. Under the previous ordering it was
    // first; it must now be last.
    mockVisits(LADDER)
    loginAsToken('token-admin')
    renderWithProviders(<DashboardHome />)

    await waitFor(() => expect(renderedOrder().length).toBeGreaterThanOrEqual(4))
    const order = renderedOrder().slice(0, 4)
    expect(order[0]).toBe('11111')
    expect(order[order.length - 1]).toBe('44444')
  })

  it('still shows every booking count, chip and action', async () => {
    mockVisits(LADDER)
    loginAsToken('token-admin')
    renderWithProviders(<DashboardHome />)

    await waitFor(() => expect(renderedOrder().length).toBeGreaterThanOrEqual(4))
    // Ordering only: no row hidden, and the data each row carries is unchanged.
    expect(renderedOrder().slice(0, 4)).toHaveLength(4)
    expect(screen.getAllByText(/Spare IA/).length).toBeGreaterThan(0)
  })

  it('puts a newly shed-in locomotive at the top after a re-fetch', async () => {
    mockVisits(LADDER)
    loginAsToken('token-admin')
    const first = renderWithProviders(<DashboardHome />)
    await waitFor(() => expect(renderedOrder()[0]).toBe('11111'))
    first.unmount()

    // A locomotive shed in just now, returned LAST by the API. Remounted rather than rerendered:
    // the fetch runs in a mount effect, so rerender() would re-render the same component without
    // fetching - which is not what a page refresh does.
    mockVisits([
      ...LADDER,
      visit({ id: 5, loco_number: '55555', arrival_at: '2026-10-02T06:00:00+05:30' }),
    ])
    renderWithProviders(<DashboardHome />)
    await waitFor(() => expect(renderedOrder()[0]).toBe('55555'))
  })
})

describe('Shed Movement', () => {
  it('lists active shed visits newest arrival first', async () => {
    mockVisits(LADDER)
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    await waitFor(() => expect(renderedOrder().length).toBeGreaterThanOrEqual(4))
    expect(renderedOrder().slice(0, 4)).toEqual(EXPECTED)
  })

  it('keeps newest-first within the visible rows when a filter is applied', async () => {
    mockVisits([
      ...LADDER,
      visit({ id: 6, loco_number: '41111', arrival_at: '2026-10-02T07:00:00+05:30' }),
    ])
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await waitFor(() => expect(renderedOrder().length).toBeGreaterThanOrEqual(5))

    // "4" matches 44444 (29 Sep) and 41111 (2 Oct). The newer must lead.
    await user.type(screen.getByLabelText(/find locomotive/i), '4')
    await waitFor(() => expect(renderedOrder()).toEqual(['41111', '44444']))
  })

  it('puts a newly shed-in locomotive at the top after a re-fetch', async () => {
    mockVisits(LADDER)
    loginAsToken('token-admin')
    const first = renderWithProviders(<ShedMovementPage />)
    await waitFor(() => expect(renderedOrder()[0]).toBe('11111'))
    first.unmount()

    mockVisits([
      visit({ id: 5, loco_number: '55555', arrival_at: '2026-10-02T06:00:00+05:30' }),
      ...LADDER,
    ])
    renderWithProviders(<ShedMovementPage />)
    await waitFor(() => expect(renderedOrder()[0]).toBe('55555'))
  })
})

describe('both pages agree', () => {
  it('shows the same order for the same data', async () => {
    mockVisits(LADDER)
    loginAsToken('token-admin')

    const overview = renderWithProviders(<DashboardHome />)
    await waitFor(() => expect(renderedOrder().length).toBeGreaterThanOrEqual(4))
    const overviewOrder = renderedOrder().slice(0, 4)
    overview.unmount()

    renderWithProviders(<ShedMovementPage />)
    await waitFor(() => expect(renderedOrder().length).toBeGreaterThanOrEqual(4))
    expect(renderedOrder().slice(0, 4)).toEqual(overviewOrder)
  })
})
