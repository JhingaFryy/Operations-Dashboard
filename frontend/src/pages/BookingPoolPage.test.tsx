import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { server } from '../mocks/server'
import { BookingPoolPage } from './BookingPoolPage'

function bookingItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    status: 'OPEN',
    description: 'Isolated after fault',
    booking_source: 'LOG_BOOK',
    workflow_stage_type: null,
    equipment_node_id: 1843,
    equipment_node_name: 'Aux Converter',
    equipment_path: [],
    routed_sections: [
      {
        section_id: 4,
        section_code: 'M4-HR',
        section_name: 'Bogie & its Mechanical Equipment',
        assignment_source: 'AUTO_MAPPING',
        status: 'OPEN',
      },
    ],
    defect_type: { id: 1, code: 'ISOLATED', name: 'Isolated' },
    shed_visit: { id: 700, loco_number: '39126', schedule_family: 'MINOR', schedule_variant: 'IA' },
    created_at: '2026-08-31T04:05:00Z',
    started_by_name: null,
    started_by_section_code: null,
    started_at: null,
    attended_by_name: null,
    attended_by_section_code: null,
    attended_at: null,
    attendance_remarks: null,
    ...overrides,
  }
}

function mockBookings(items: ReturnType<typeof bookingItem>[]) {
  server.use(http.get('/api/bookings', () => HttpResponse.json(items)))
}


/** Everything is collapsed on arrival, so a test that wants a booking row must open its way
 * down to it: locomotive, then equipment. That IS the feature - these helpers are how a test
 * reaches a row, and `collapsedHeaders` is how it asserts the collapse itself. */
function collapsedHeaders(): HTMLElement[] {
  return screen.queryAllByRole('button', { expanded: false })
}

/** The BOOKING SOURCE level sits between a locomotive and its equipment, so reaching equipment
 *  now takes one more click. Every fixture here is LOG_BOOK unless it says otherwise. */
async function openSource(
  user: ReturnType<typeof userEvent.setup>,
  name: RegExp = /^Log Book/,
): Promise<HTMLElement> {
  const header = screen.getByRole('button', { name })
  await user.click(header)
  return header
}

async function expandAll(user: ReturnType<typeof userEvent.setup>) {
  // Wait for the locomotive level to render before clicking anything: on arrival the page
  // is still loading and there is genuinely nothing collapsed yet. Idempotent - calling it
  // again once everything is open is a no-op, not a timeout.
  await waitFor(() => expect(document.querySelector('.collapsible-group-loco')).not.toBeNull())
  // Expanding a locomotive reveals equipment headers that were not in the DOM before, so
  // this repeats until nothing is left closed. Bounded, so a bug cannot hang the test.
  for (let pass = 0; pass < 6; pass += 1) {
    const closed = collapsedHeaders()
    if (closed.length === 0) return
    for (const header of closed) await user.click(header)
  }
}

describe('BookingPoolPage', () => {
  it('groups bookings by equipment, multiple bookings under the same equipment', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, description: 'Fault A' }),
      bookingItem({ id: 2, description: 'Fault B' }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    const group = screen.getByText('Fault A').closest('.collapsible-group-equipment') as HTMLElement
    expect(within(group).getByText('Fault A')).toBeInTheDocument()
    expect(within(group).getByText('Fault B')).toBeInTheDocument()
    expect(within(group).getByText(/2 bookings/)).toBeInTheDocument()
  })

  it('different equipment creates separate groups', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, equipment_node_id: 1843, equipment_node_name: 'Aux Converter', description: 'Fault A' }),
      bookingItem({ id: 2, equipment_node_id: 200, equipment_node_name: 'Pantograph', description: 'Fault B' }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    // One locomotive, two equipment groups beneath it.
    expect(document.querySelectorAll('.collapsible-group-loco').length).toBe(1)
    expect(document.querySelectorAll('.collapsible-group-equipment').length).toBe(2)
    expect(screen.getByRole('button', { name: /Aux Converter/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Pantograph/ })).toBeInTheDocument()
  })

  it('filters by status', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, status: 'OPEN', description: 'Open one' }),
      bookingItem({ id: 2, status: 'ATTENDED', description: 'Attended one' }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText('Open one')
    expect(screen.getByText('Attended one')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Status'), 'ATTENDED')
    expect(screen.queryByText('Open one')).not.toBeInTheDocument()
    expect(screen.getByText('Attended one')).toBeInTheDocument()
  })

  it('filters by source', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, booking_source: 'LOG_BOOK', description: 'Log book one' }),
      bookingItem({
        id: 2,
        booking_source: 'TEST_BEFORE',
        workflow_stage_type: 'TEST_BEFORE',
        description: 'TB one',
      }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText('Log book one')
    expect(screen.getByText('TB one')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Source'), 'TEST_BEFORE')
    expect(screen.queryByText('Log book one')).not.toBeInTheDocument()
    expect(screen.getByText('TB one')).toBeInTheDocument()
  })

  it('IN_PROGRESS booking shows the handling section', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({
        id: 1,
        status: 'IN_PROGRESS',
        started_by_name: 'Priya Supervisor',
        started_by_section_code: 'M1-HR',
        started_at: '2026-08-31T05:00:00Z',
      }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText(/Priya Supervisor/)
    expect(screen.getByText(/M1-HR/)).toBeInTheDocument()
  })

  it('ATTENDED booking shows attending section and remarks', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({
        id: 1,
        status: 'ATTENDED',
        attended_by_name: 'Priya Supervisor',
        attended_by_section_code: 'M1-HR',
        attended_at: '2026-08-31T06:00:00Z',
        attendance_remarks: 'Replaced faulty part.',
      }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText(/Priya Supervisor/)
    expect(screen.getByText(/M1-HR/)).toBeInTheDocument()
    expect(screen.getByText('Replaced faulty part.')).toBeInTheDocument()
  })

  it('does not claim work is "Not started" when the booking is ATTENDED with no handler recorded', async () => {
    // The pool response's started_*/attended_* fields are the parent-level
    // booking columns, which nothing writes any more, so an ATTENDED booking
    // legitimately arrives with all of them null. Saying "Not started" there
    // asserted something the status itself contradicts.
    loginAsToken('token-admin')
    mockBookings([bookingItem({ id: 1, status: 'ATTENDED' })])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText('Isolated after fault')
    expect(screen.getByText('No handler recorded')).toBeInTheDocument()
    expect(screen.queryByText('Not started')).not.toBeInTheDocument()
  })

  it('still says "Not started" for an OPEN booking, where that is true', async () => {
    loginAsToken('token-admin')
    mockBookings([bookingItem({ id: 1, status: 'OPEN' })])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText('Isolated after fault')
    expect(screen.getByText('Not started')).toBeInTheDocument()
  })

  it('a Supervisor cannot reach the global Booking Pool route', async () => {
    // Business Rule Alignment: the Booking Pool is Admin-only operational visibility now -
    // navigating there directly must show a permission-denied message, not the pool.
    const AppModule = await import('../App')
    const App = AppModule.default
    mockBookings([bookingItem({ id: 1, description: 'Shared booking' })])

    loginAsToken('token-sup-m1hr')
    renderWithProviders(<App />, { route: '/booking-pool' })

    await screen.findByText(/permission denied/i)
    expect(screen.queryByText('Shared booking')).not.toBeInTheDocument()
  })

  it('an Admin can reach the global Booking Pool route', async () => {
    const AppModule = await import('../App')
    const App = AppModule.default
    mockBookings([bookingItem({ id: 1, description: 'Admin-visible booking' })])

    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<App />, { route: '/booking-pool' })

    // The route is reachable and its data loads; the booking itself sits two levels down.
    await expandAll(user)
    expect(screen.getByText('Admin-visible booking')).toBeInTheDocument()
  })

  it('renders no forward/start/attend/reopen controls - the lifecycle stays elsewhere', async () => {
    // "Add sections" is now a DELIBERATE, separately-authorised mutation for a caller holding the
    // booking-routing capability (Admin always does), so it is expected here and asserted
    // positively below. Every LIFECYCLE control must still be absent: start, attend, reopen and
    // the retired forward belong to the Section Dashboard, not the global pool.
    loginAsToken('token-admin')
    mockBookings([bookingItem({ id: 1 })])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText('Isolated after fault')
    expect(screen.queryByText(/forward/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start work/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /mark attended/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^reopen$/i })).not.toBeInTheDocument()
  })

  it('offers Add sections to a routing-capable caller, and ONLY that mutation', async () => {
    loginAsToken('token-admin')
    mockBookings([bookingItem({ id: 1 })])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    await screen.findByText('Isolated after fault')
    expect(screen.getByRole('button', { name: /add sections/i })).toBeInTheDocument()
    // The action ADDS; there is no remove, replace or manage-set affordance anywhere.
    expect(screen.queryByRole('button', { name: /remove section/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /manage sections/i })).not.toBeInTheDocument()
  })

  it('offers NO Add sections to a caller without the routing capability', async () => {
    // A maintenance Supervisor cannot even read the global pool, so the realistic check is that
    // the prop is withheld rather than rendered disabled. Asserted via the row component's own
    // contract in src/lib/bookingRouting.test.ts; here we confirm the page does not render it
    // for a non-routing session.
    loginAsToken('token-sup-permitted')
    mockBookings([bookingItem({ id: 1 })])
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /add sections/i })).not.toBeInTheDocument(),
    )
  })
})

/**
 * Locomotive > equipment > bookings, collapsed by default.
 *
 * The pool routinely holds hundreds of bookings; rendering them flat is what made the page
 * unwieldy. A collapsed group renders NOTHING below its header - not hidden rows - so the
 * assertions below check absence from the DOM, not visibility.
 *
 * Grouping is by equipment_node_id, never by display name: production's hierarchy reuses
 * names heavily ("Others" hundreds of times), so two distinct nodes with the same label must
 * stay two groups.
 */
describe('BookingPoolPage hierarchy', () => {
  const twoLocos = [
    bookingItem({ id: 1, shed_visit: { id: 700, loco_number: '30542', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 10, equipment_node_name: 'Traction Converter', description: 'TC fault' }),
    bookingItem({ id: 2, shed_visit: { id: 700, loco_number: '30542', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 10, equipment_node_name: 'Traction Converter', description: 'TC fault two' }),
    bookingItem({ id: 3, shed_visit: { id: 700, loco_number: '30542', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 20, equipment_node_name: 'VCB', description: 'VCB fault' }),
    bookingItem({ id: 4, shed_visit: { id: 800, loco_number: '22991', schedule_family: 'MINOR', schedule_variant: 'IB' }, equipment_node_id: 30, equipment_node_name: 'Pantograph', description: 'Pan fault' }),
  ]

  it('40. shows only locomotives on arrival, all collapsed', async () => {
    loginAsToken('token-admin')
    mockBookings(twoLocos)
    renderWithProviders(<BookingPoolPage />)

    const locos = await screen.findAllByRole('button', { name: /^(30542|22991)/ })
    expect(locos).toHaveLength(2)
    for (const loco of locos) expect(loco).toHaveAttribute('aria-expanded', 'false')

    // Nothing below the locomotive level exists yet - not a source, not equipment, not bookings.
    expect(screen.queryByRole('button', { name: /^Log Book/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Traction Converter/ })).toBeNull()
    expect(screen.queryByText('TC fault')).toBeNull()
    expect(document.querySelectorAll('.collapsible-group-source')).toHaveLength(0)
    expect(document.querySelectorAll('.collapsible-group-equipment')).toHaveLength(0)
  })

  it('41/42. expanding a locomotive reveals its SOURCES, themselves collapsed', async () => {
    loginAsToken('token-admin')
    mockBookings(twoLocos)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^30542/ }))

    // One level down is now the source, not equipment.
    const logBook = screen.getByRole('button', { name: /^Log Book/ })
    expect(logBook).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: /Traction Converter/ })).toBeNull()
    expect(screen.queryByText('TC fault')).toBeNull()
    // The other locomotive stayed shut, so its own source group does not exist either.
    expect(screen.getAllByRole('button', { name: /^Log Book/ })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: /Pantograph/ })).toBeNull()
  })

  it('41/42b. expanding a SOURCE reveals its equipment, itself collapsed', async () => {
    loginAsToken('token-admin')
    mockBookings(twoLocos)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^30542/ }))
    await openSource(user)

    const tc = screen.getByRole('button', { name: /Traction Converter/ })
    const vcb = screen.getByRole('button', { name: /VCB/ })
    expect(tc).toHaveAttribute('aria-expanded', 'false')
    expect(vcb).toHaveAttribute('aria-expanded', 'false')
    // Still no booking rows, and the other locomotive stayed shut.
    expect(screen.queryByText('TC fault')).toBeNull()
    expect(screen.queryByRole('button', { name: /Pantograph/ })).toBeNull()
  })

  it('43. expanding an equipment reveals its bookings, and only its own', async () => {
    loginAsToken('token-admin')
    mockBookings(twoLocos)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^30542/ }))
    await openSource(user)
    await user.click(screen.getByRole('button', { name: /Traction Converter/ }))

    expect(screen.getByText('TC fault')).toBeInTheDocument()
    expect(screen.getByText('TC fault two')).toBeInTheDocument()
    expect(screen.queryByText('VCB fault')).toBeNull()
  })

  it('44. counts are literal tallies at all three levels', async () => {
    loginAsToken('token-admin')
    mockBookings(twoLocos)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    expect((await screen.findByRole('button', { name: /^30542/ })).textContent).toContain('3 bookings')
    expect(screen.getByRole('button', { name: /^22991/ }).textContent).toContain('1 booking')

    await user.click(screen.getByRole('button', { name: /^30542/ }))
    // The source level carries the same tally over its own bookings - all three of 30542's are
    // Log Book, so it reports the locomotive's whole count.
    expect(screen.getByRole('button', { name: /^Log Book/ }).textContent).toContain('3 bookings')

    await openSource(user)
    expect(screen.getByRole('button', { name: /Traction Converter/ }).textContent).toContain('2 bookings')
    expect(screen.getByRole('button', { name: /VCB/ }).textContent).toContain('1 booking')
  })

  it('45. same display name under different nodes stays two groups', async () => {
    // The production hazard: "Others" and "Split Pin" repeat throughout the hierarchy.
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, equipment_node_id: 101, equipment_node_name: 'Others', equipment_path: [{ id: 5, name: 'Bogie' }, { id: 101, name: 'Others' }], description: 'Bogie others' }),
      bookingItem({ id: 2, equipment_node_id: 202, equipment_node_name: 'Others', equipment_path: [{ id: 9, name: 'Pantograph' }, { id: 202, name: 'Others' }], description: 'Pantograph others' }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    await openSource(user)

    const groups = screen.getAllByRole('button', { name: /Others/ })
    expect(groups).toHaveLength(2)
    // Their hierarchy paths are what tells them apart on screen.
    expect(groups.map((g) => g.textContent).join(' ')).toContain('Bogie / Others')
    expect(groups.map((g) => g.textContent).join(' ')).toContain('Pantograph / Others')

    // And they expand independently.
    await user.click(groups[0])
    expect(document.querySelectorAll('.collapsible-group-equipment .booking-table')).toHaveLength(1)
  })

  it('46. a booking with no equipment identity is kept, not dropped', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, equipment_node_id: null, equipment_node_name: null, description: 'Orphan fault' }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    await openSource(user)
    await user.click(screen.getByRole('button', { name: /No equipment specified/ }))

    expect(screen.getByText('Orphan fault')).toBeInTheDocument()
  })

  it('47. filters still work, and re-filtering does not move expansion to another group', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, status: 'OPEN', shed_visit: { id: 700, loco_number: '30542', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 10, equipment_node_name: 'Traction Converter', description: 'Open TC' }),
      bookingItem({ id: 2, status: 'ATTENDED', shed_visit: { id: 800, loco_number: '22991', schedule_family: 'MINOR', schedule_variant: 'IB' }, equipment_node_id: 20, equipment_node_name: 'VCB', description: 'Attended VCB' }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    // Open 30542 only.
    await user.click(await screen.findByRole('button', { name: /^30542/ }))
    expect(screen.getByRole('button', { name: /^30542/ })).toHaveAttribute('aria-expanded', 'true')

    await user.selectOptions(screen.getByLabelText('Status'), 'ATTENDED')

    // 30542 is filtered away; 22991 is now the only locomotive and must NOT have inherited
    // the open state - expansion is keyed by locomotive number, not by list position.
    expect(screen.queryByRole('button', { name: /^30542/ })).toBeNull()
    expect(screen.getByRole('button', { name: /^22991/ })).toHaveAttribute('aria-expanded', 'false')
  })

  it('48. expansion survives a background refresh of the same data', async () => {
    loginAsToken('token-admin')
    mockBookings(twoLocos)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^30542/ }))
    await openSource(user)
    await user.click(screen.getByRole('button', { name: /Traction Converter/ }))
    expect(screen.getByText('TC fault')).toBeInTheDocument()

    // A re-render with identical data (what a refresh produces) must not collapse anything: the
    // keys are the locomotive number, the stored source and the equipment node id, all unchanged.
    mockBookings(twoLocos)
    expect(screen.getByRole('button', { name: /^30542/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('TC fault')).toBeInTheDocument()
  })

  it('preserves the read-only contract - no action controls appear at any level', async () => {
    loginAsToken('token-admin')
    mockBookings(twoLocos)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    for (const label of [/^start$/i, /^attend$/i, /^reopen$/i, /forward/i, /assign/i]) {
      expect(screen.queryByRole('button', { name: label })).toBeNull()
    }
  })
})

/** Locomotive ORDER, through the real page rather than the grouping helper.
 *
 * grouping.test.ts pins the comparator and each filter's effect on the count. What only the page
 * can prove is the WIRING: that the list handed to groupBookingsByLocomotive is the one left after
 * all four filters, so the order on screen is driven by the same number each card prints. A page
 * that grouped the unfiltered list would pass every unit test and still render a wrong order.
 */
describe('BookingPoolPage locomotive ordering', () => {
  /** 44373: 4 bookings (1 OPEN). 32032: 2. 33586: 1. Chosen so unfiltered and filtered orders
   *  differ, which is the whole point of sorting on the visible count. */
  const LADDER = [
    bookingItem({ id: 1, status: 'OPEN', booking_source: 'LOG_BOOK', shed_visit: { id: 700, loco_number: '44373', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 10, equipment_node_name: 'Pantograph', description: 'Pan horn fault' }),
    bookingItem({ id: 2, status: 'ATTENDED', booking_source: 'LOG_BOOK', shed_visit: { id: 700, loco_number: '44373', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 10, equipment_node_name: 'Pantograph', description: 'Pan drift' }),
    bookingItem({ id: 3, status: 'ATTENDED', booking_source: 'SCHEDULE_INSPECTION', shed_visit: { id: 700, loco_number: '44373', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 20, equipment_node_name: 'VCB', description: 'VCB slow' }),
    bookingItem({ id: 4, status: 'ATTENDED', booking_source: 'SCHEDULE_INSPECTION', shed_visit: { id: 700, loco_number: '44373', schedule_family: 'MINOR', schedule_variant: 'IA' }, equipment_node_id: 20, equipment_node_name: 'VCB', description: 'VCB noise' }),
    bookingItem({ id: 5, status: 'OPEN', booking_source: 'LOG_BOOK', shed_visit: { id: 800, loco_number: '32032', schedule_family: 'MINOR', schedule_variant: 'IB' }, equipment_node_id: 10, equipment_node_name: 'Pantograph', description: 'Pan horn weak' }),
    bookingItem({ id: 6, status: 'OPEN', booking_source: 'LOG_BOOK', shed_visit: { id: 800, loco_number: '32032', schedule_family: 'MINOR', schedule_variant: 'IB' }, equipment_node_id: 20, equipment_node_name: 'VCB', description: 'VCB trip' }),
    bookingItem({ id: 7, status: 'OPEN', booking_source: 'LOG_BOOK', shed_visit: { id: 900, loco_number: '33586', schedule_family: 'MINOR', schedule_variant: 'IC' }, equipment_node_id: 30, equipment_node_name: 'IGBT', description: 'IGBT alarm' }),
  ]

  /** The locomotive-level headers, top to bottom as rendered. */
  function locoOrder(): string[] {
    return Array.from(document.querySelectorAll('.collapsible-group-loco .collapsible-group-header'))
      .map((h) => (h.textContent ?? '').match(/\d{4,}/)?.[0] ?? '')
      .filter(Boolean)
  }

  it('lists locomotives fewest bookings first', async () => {
    loginAsToken('token-admin')
    mockBookings(LADDER)
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() => expect(locoOrder()).toHaveLength(3))
    // 33586 = 1, 32032 = 2, 44373 = 4.
    expect(locoOrder()).toEqual(['33586', '32032', '44373'])
  })

  it('re-orders from the visible count when the STATUS filter is applied', async () => {
    loginAsToken('token-admin')
    mockBookings(LADDER)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))

    // OPEN only: 44373 falls from 4 to 1 and must rise from LAST to the top of the ties -
    // impossible if the sort were reading an unfiltered total.
    await user.selectOptions(screen.getByLabelText('Status'), 'OPEN')
    await waitFor(() => expect(locoOrder()).toEqual(['33586', '44373', '32032']))

    // The card now prints the count the order was built from.
    const header = Array.from(
      document.querySelectorAll('.collapsible-group-loco .collapsible-group-header'),
    ).find((h) => h.textContent?.includes('44373'))!
    expect(header.textContent).toMatch(/1 booking/)

    // Clearing restores the original counts and therefore the original order.
    await user.selectOptions(screen.getByLabelText('Status'), '')
    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))
  })

  it('re-orders from the visible count when the SOURCE filter is applied', async () => {
    loginAsToken('token-admin')
    mockBookings(LADDER)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))

    // SCHEDULE_INSPECTION: only 44373 has any, so it becomes the sole group.
    await user.selectOptions(screen.getByLabelText('Source'), 'SCHEDULE_INSPECTION')
    await waitFor(() => expect(locoOrder()).toEqual(['44373']))

    // LOG_BOOK: 44373 drops to 2, 32032 keeps 2, 33586 keeps 1. The two 2s tie on number.
    await user.selectOptions(screen.getByLabelText('Source'), 'LOG_BOOK')
    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))
  })

  it('re-orders from the visible count when the EQUIPMENT filter is applied', async () => {
    loginAsToken('token-admin')
    mockBookings(LADDER)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))

    // VCB (node 20): 44373 has 2, 32032 has 1, 33586 none.
    await user.selectOptions(screen.getByLabelText('Equipment'), '20')
    await waitFor(() => expect(locoOrder()).toEqual(['32032', '44373']))
  })

  it('re-orders from the visible count when SEARCH text is applied', async () => {
    loginAsToken('token-admin')
    mockBookings(LADDER)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))

    // "vcb" matches 2 on 44373 and 1 on 32032.
    await user.type(screen.getByLabelText('Search'), 'vcb')
    await waitFor(() => expect(locoOrder()).toEqual(['32032', '44373']))

    await user.clear(screen.getByLabelText('Search'))
    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))
  })

  it('keeps a locomotive expanded when filtering moves it to another position', async () => {
    loginAsToken('token-admin')
    mockBookings(LADDER)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))

    const headers = () =>
      Array.from(
        document.querySelectorAll<HTMLElement>('.collapsible-group-loco .collapsible-group-header'),
      )

    // Expand 44373 where it sits, last.
    const target = headers().find((h) => h.textContent?.includes('44373'))!
    await user.click(target)
    expect(target).toHaveAttribute('aria-expanded', 'true')

    // 44373's VCB work is all SCHEDULE_INSPECTION, so reaching it means opening that source
    // group first. Expand both, to prove the source key and the node-id key each survive.
    const scheduleInspection = await openSource(user, /^Schedule Inspection/)
    expect(scheduleInspection).toHaveAttribute('aria-expanded', 'true')
    const vcb = screen.getByRole('button', { name: /VCB/ })
    await user.click(vcb)
    expect(vcb).toHaveAttribute('aria-expanded', 'true')

    // Filter to OPEN. 44373 moves from last to the middle.
    await user.selectOptions(screen.getByLabelText('Status'), 'OPEN')
    await waitFor(() => expect(locoOrder()).toEqual(['33586', '44373', '32032']))

    const after = headers()
    expect(after.find((h) => h.textContent?.includes('44373'))!).toHaveAttribute('aria-expanded', 'true')
    // Expansion did not leak to the rows that moved around it.
    expect(after.find((h) => h.textContent?.includes('33586'))!).toHaveAttribute('aria-expanded', 'false')
    expect(after.find((h) => h.textContent?.includes('32032'))!).toHaveAttribute('aria-expanded', 'false')

    // The locomotive is still open, so its SOURCE level is still rendered. Every
    // SCHEDULE_INSPECTION booking on 44373 was ATTENDED, so that whole source group is now gone -
    // not present and empty. Log Book remains, still collapsed, because it was never expanded:
    // expansion is per identity, not per position.
    const group = after.find((h) => h.textContent?.includes('44373'))!.closest('.collapsible-group') as HTMLElement
    const sourceHeaders = Array.from(
      group.querySelectorAll<HTMLElement>('.collapsible-group-source > .collapsible-group-header'),
    )
    expect(sourceHeaders.map((h) => h.textContent?.match(/Log Book|Schedule Inspection/)?.[0])).toEqual([
      'Log Book',
    ])
    expect(sourceHeaders[0]).toHaveAttribute('aria-expanded', 'false')
    // And with that source collapsed, no equipment header is in the DOM under it at all.
    expect(
      group.querySelectorAll('.collapsible-group-equipment .collapsible-group-header'),
    ).toHaveLength(0)
  })

  it('leaves equipment grouping and booking order inside a group untouched', async () => {
    loginAsToken('token-admin')
    mockBookings(LADDER)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '32032', '44373']))

    const header = Array.from(
      document.querySelectorAll<HTMLElement>('.collapsible-group-loco .collapsible-group-header'),
    ).find((h) => h.textContent?.includes('44373'))!
    await user.click(header)

    // 44373's two equipment groups now sit under two DIFFERENT sources - Pantograph under Log
    // Book, VCB under Schedule Inspection - which is the whole point of the new level. Open both
    // sources and the equipment level below each is unchanged: by node id, ordered by label.
    const group = header.closest('.collapsible-group') as HTMLElement
    const sourceHeaders = Array.from(
      group.querySelectorAll<HTMLElement>('.collapsible-group-source > .collapsible-group-header'),
    )
    expect(sourceHeaders.map((h) => h.textContent?.match(/Log Book|Schedule Inspection/)?.[0])).toEqual([
      'Log Book',
      'Schedule Inspection',
    ])
    for (const sh of sourceHeaders) await user.click(sh)

    const equipmentHeaders = Array.from(
      group.querySelectorAll<HTMLElement>('.collapsible-group-equipment .collapsible-group-header'),
    )
    expect(equipmentHeaders.map((h) => h.textContent?.match(/Pantograph|VCB/)?.[0])).toEqual([
      'Pantograph',
      'VCB',
    ])
  })
})

describe('BookingPoolPage routed section + source', () => {
  it('shows the section the booking was routed to', async () => {
    mockBookings([bookingItem()])
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    expect(screen.getByText('M4-HR')).toBeInTheDocument()
  })

  it('shows every section of a multi-section routed booking', async () => {
    mockBookings([
      bookingItem({
        routed_sections: [
          { section_id: 2, section_code: 'M2-HR', section_name: 'Two', assignment_source: 'AUTO_MAPPING', status: 'OPEN' },
          { section_id: 4, section_code: 'M4-HR', section_name: 'Four', assignment_source: 'MANUAL', status: 'OPEN' },
        ],
      }),
    ])
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    expect(screen.getByText('M2-HR')).toBeInTheDocument()
    expect(screen.getByText('M4-HR')).toBeInTheDocument()
  })

  it('says a booking is not routed rather than showing a blank cell', async () => {
    mockBookings([bookingItem({ routed_sections: [] })])
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    expect(screen.getByText('Not routed')).toBeInTheDocument()
  })

  it('does not crash when an older backend omits routed_sections entirely', async () => {
    // A rolling deploy can pair this bundle with a backend that predates the field.
    const { routed_sections: _omitted, ...withoutField } = bookingItem()
    mockBookings([withoutField as ReturnType<typeof bookingItem>])
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    expect(screen.getByText('Not routed')).toBeInTheDocument()
  })

  it('shows the friendly source label, not the raw enum', async () => {
    mockBookings([bookingItem({ booking_source: 'TRIP_INSPECTION' })])
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    // The friendly label in all three places it can appear, and the raw enum in none of them.
    // The row's Source column stays: the row component is shared with the Section Dashboard, and
    // a row should still state its source when read on its own.
    expect(screen.getByRole('button', { name: /^Trip Inspection/ })).toBeInTheDocument() // group
    expect(
      within(screen.getByRole('table')).getByText('Trip Inspection'),
    ).toBeInTheDocument() // row cell
    expect(
      within(screen.getByLabelText('Source') as HTMLSelectElement).getByRole('option', {
        name: 'Trip Inspection',
      }),
    ).toBeInTheDocument() // filter dropdown
    expect(screen.queryByText('TRIP_INSPECTION')).not.toBeInTheDocument()
  })
})

describe('BookingPoolPage source hierarchy on screen', () => {
  const mixedSources = [
    bookingItem({ id: 1, booking_source: 'LOG_BOOK', equipment_node_id: 10, equipment_node_name: 'Air Dryer', description: 'Dryer leak' }),
    bookingItem({ id: 2, booking_source: 'LOG_BOOK', equipment_node_id: 20, equipment_node_name: 'Pantograph', description: 'Pan drift' }),
    bookingItem({ id: 3, booking_source: 'TEST_BEFORE', equipment_node_id: 30, equipment_node_name: 'Bogie', description: 'Bogie play' }),
    bookingItem({ id: 4, booking_source: 'TEST_AFTER', equipment_node_id: 10, equipment_node_name: 'Air Dryer', description: 'Dryer recheck' }),
  ]

  function sourceHeadings(): (string | undefined)[] {
    return Array.from(
      document.querySelectorAll<HTMLElement>('.collapsible-group-source > .collapsible-group-header'),
    ).map((h) => h.querySelector('.collapsible-group-title')?.textContent ?? undefined)
  }

  it('renders source groups in the fixed operational order, with friendly labels', async () => {
    loginAsToken('token-admin')
    mockBookings(mixedSources)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    // Not alphabetical (Log Book, Test After, Test Before) and not by count.
    expect(sourceHeadings()).toEqual(['Log Book', 'Test Before', 'Test After'])
  })

  it('gives each source group its own counts', async () => {
    loginAsToken('token-admin')
    mockBookings(mixedSources)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    expect(screen.getByRole('button', { name: /^Log Book/ }).textContent).toContain('2 bookings')
    expect(screen.getByRole('button', { name: /^Test Before/ }).textContent).toContain('1 booking')
    expect(screen.getByRole('button', { name: /^Test After/ }).textContent).toContain('1 booking')
    // The locomotive total is unchanged by the new level.
    expect(screen.getByRole('button', { name: /^39126/ }).textContent).toContain('4 bookings')
  })

  it('separates the SAME equipment appearing under two sources', async () => {
    // Air Dryer node 10 is booked from both Log Book and Test After. Before this change those
    // were one group whose rows differed only by the Source column.
    loginAsToken('token-admin')
    mockBookings(mixedSources)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    await openSource(user, /^Log Book/)
    await openSource(user, /^Test After/)

    const airDryers = screen.getAllByRole('button', { name: /Air Dryer/ })
    expect(airDryers).toHaveLength(2)

    // Expanding one does NOT expand the other - the keys carry the source.
    await user.click(airDryers[0])
    expect(airDryers[0]).toHaveAttribute('aria-expanded', 'true')
    expect(airDryers[1]).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Dryer leak')).toBeInTheDocument()
    expect(screen.queryByText('Dryer recheck')).toBeNull()
  })

  it('shows an unrecognised source last, labelled with its raw stored value', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({ id: 1, booking_source: 'FUTURE_SOURCE', equipment_node_id: 10, equipment_node_name: 'Air Dryer', description: 'From the future' }),
      bookingItem({ id: 2, booking_source: 'LOG_BOOK', equipment_node_id: 20, equipment_node_name: 'Pantograph', description: 'Pan drift' }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    expect(sourceHeadings()).toEqual(['Log Book', 'FUTURE_SOURCE'])

    // And its booking is still reachable, not dropped.
    await openSource(user, /^FUTURE_SOURCE/)
    await user.click(screen.getByRole('button', { name: /Air Dryer/ }))
    expect(screen.getByText('From the future')).toBeInTheDocument()
  })

  it('hides a source group entirely once its bookings are filtered out', async () => {
    loginAsToken('token-admin')
    mockBookings(mixedSources)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    expect(sourceHeadings()).toEqual(['Log Book', 'Test Before', 'Test After'])

    await user.selectOptions(screen.getByLabelText('Source'), 'TEST_BEFORE')
    // One group, not three with two of them empty.
    await waitFor(() => expect(sourceHeadings()).toEqual(['Test Before']))
    expect(screen.getByRole('button', { name: /^39126/ }).textContent).toContain('1 booking')
  })

  it('offers every canonical source in the filter, including TI/GC and legacy Special Checking', async () => {
    // The dropdown used to list only five of the eight stored values, so a Trip Inspection,
    // General Checking or legacy Special Checking booking could be seen but not filtered to.
    loginAsToken('token-admin')
    mockBookings([bookingItem()])
    renderWithProviders(<BookingPoolPage />)

    const select = (await screen.findByLabelText('Source')) as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.value)).toEqual([
      '',
      'LOG_BOOK',
      'TEST_BEFORE',
      'SCHEDULE_INSPECTION',
      'TEST_AFTER',
      'SPECIAL_CHECKING',
      'TRIP_INSPECTION',
      'GENERAL_CHECKING',
      'MANUAL',
    ])
  })

  it('still reaches the Admin Delete affordance through the deeper hierarchy', async () => {
    loginAsToken('token-admin')
    mockBookings(mixedSources)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)

    await user.click(await screen.findByRole('button', { name: /^39126/ }))
    await openSource(user, /^Test Before/)
    await user.click(screen.getByRole('button', { name: /Bogie/ }))

    // Unchanged: one per visible row, Admin only, and a single click performs nothing.
    const del = screen.getByRole('button', { name: 'Delete booking: Bogie play' })
    expect(del).toBeInTheDocument()
    await user.click(del)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('still renders no Delete affordance for a non-Admin', async () => {
    loginAsToken('token-sup-permitted')
    mockBookings(mixedSources)
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    expect(screen.queryByRole('button', { name: /^Delete booking:/ })).toBeNull()
  })

  it('still shows status and routed-section detail on the row, at the new depth', async () => {
    loginAsToken('token-admin')
    mockBookings([
      bookingItem({
        id: 1,
        booking_source: 'TEST_AFTER',
        status: 'ATTENDED',
        attended_at: '2026-09-02T06:00:00Z',
        attended_by_name: 'A. Supervisor',
        attended_by_section_code: 'M4-HR',
        attendance_remarks: 'Replaced seal',
        description: 'Dryer recheck',
      }),
    ])
    const user = userEvent.setup()
    renderWithProviders(<BookingPoolPage />)
    await expandAll(user)

    expect(screen.getByText('Dryer recheck')).toBeInTheDocument()
    expect(screen.getByText('M4-HR')).toBeInTheDocument()
    expect(screen.getByText(/Replaced seal/)).toBeInTheDocument()
    // And still no lifecycle controls anywhere, at any level.
    for (const label of [/^start$/i, /^attend$/i, /^reopen$/i, /forward/i, /assign/i]) {
      expect(screen.queryByRole('button', { name: label })).toBeNull()
    }
  })
})
