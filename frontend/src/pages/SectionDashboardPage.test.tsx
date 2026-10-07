import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { server } from '../mocks/server'
import App from '../App'
import { SectionDashboardPage } from './SectionDashboardPage'

/** The section picker was removed with the Supervisor-only policy - a Supervisor's section is
 *  authoritative and auto-selected, so there is nothing to choose. Kept as a no-op so the tests
 *  below still read as "we are looking at M1-HR". */
async function selectSection(_user: ReturnType<typeof userEvent.setup>, _code = 'M1-HR') {
  return undefined
}

/** Opens every collapsed level, repeatedly, until nothing is collapsed any more.
 *
 * The page is now a locomotive -> equipment -> bookings hierarchy that starts fully collapsed,
 * so a test that wants to see a booking has to open its way down to it. Looping matters:
 * expanding a locomotive is what renders its equipment headers, which are themselves collapsed.
 */
function collapsedHeaders(): HTMLElement[] {
  // Scoped to the HIERARCHY headers only. An assignment card has its own aria-expanded
  // "Details" disclosure, and a blanket queryAllByRole('button', {expanded:false}) opened those
  // too - so a test that then clicked Details itself was closing it again.
  return Array.from(
    document.querySelectorAll<HTMLElement>('.collapsible-group-header[aria-expanded="false"]'),
  )
}

async function expandAll(user: ReturnType<typeof userEvent.setup>) {
  // Wait for the locomotive level to render before clicking anything: on arrival the page is
  // still loading and there is genuinely nothing collapsed yet.
  await waitFor(() => expect(document.querySelector('.collapsible-group-loco')).not.toBeNull())
  // Expanding a locomotive reveals equipment headers that were not in the DOM before, so this
  // repeats until nothing is left closed. Bounded, so a bug cannot hang the test.
  for (let pass = 0; pass < 6; pass += 1) {
    const closed = collapsedHeaders()
    if (closed.length === 0) return
    for (const header of closed) await user.click(header)
  }
}

/** The assignment card for a locomotive, with the hierarchy opened first.
 *
 * The loco number now also appears on the collapsed group header, so this deliberately picks
 * the occurrence inside an .assignment-card rather than the first match on the page.
 */
async function cardFor(locoNumber: string): Promise<HTMLElement> {
  const user = userEvent.setup()
  await expandAll(user)
  const matches = await screen.findAllByText(locoNumber)
  const card = matches.map((m) => m.closest('.assignment-card')).find(Boolean)
  return card as HTMLElement
}

describe('SectionDashboardPage', () => {
  it('is a protected route', async () => {
    renderWithProviders(<App />, { route: '/section-dashboard' })
    await waitFor(() => expect(screen.getByLabelText(/employee id/i)).toBeInTheDocument())
  })

  // ---------------------------------------------------------------- admin --

  describe('Auto-scoped section (formerly the Admin picker)', () => {
    it("shows the Supervisor's own section without any picker", async () => {
      loginAsToken('token-sup-m1hr')

      renderWithProviders(<SectionDashboardPage />)

      await cardFor('39126')

      await cardFor('39127')
    })

    it('nests bookings under locomotive then equipment, not by status or section', async () => {
      // M1-HR fixture: assignment 1 (39126, IGBT, OPEN), assignment 3 (39128, IGBT, IN_PROGRESS),
      // assignment 2 (39127, Contactor, ATTENDED). Each locomotive is its own top-level card,
      // with its equipment nested beneath it - the same hierarchy the Admin Booking Pool uses.
      loginAsToken('token-sup-m1hr')
      const user = userEvent.setup()

      renderWithProviders(<SectionDashboardPage />)
      await expandAll(user)

      const loco39126 = screen
        .getAllByRole('button', { expanded: true })
        .find((b) => b.textContent?.includes('39126'))
      expect(loco39126).toBeTruthy()

      // The equipment level lives inside the locomotive it belongs to.
      const locoGroup = loco39126!.closest('.collapsible-group') as HTMLElement
      expect(within(locoGroup).getAllByText(/IGBT/i).length).toBeGreaterThan(0)
      // 39127's Contactor work is under its OWN locomotive, never mixed into this one.
      expect(within(locoGroup).queryByText(/Contactor/i)).not.toBeInTheDocument()
    })

    it('starts fully collapsed - no booking card is rendered until the user opens one', async () => {
      loginAsToken('token-sup-m1hr')

      renderWithProviders(<SectionDashboardPage />)

      // Wait for the data to land, then assert nothing below the top level is showing.
      await waitFor(() => expect(document.querySelector('.collapsible-group-loco')).not.toBeNull())
      for (const header of collapsedHeaders()) {
        expect(header).toHaveAttribute('aria-expanded', 'false')
      }
      expect(document.querySelectorAll('.assignment-card')).toHaveLength(0)
    })

    it('expanding a locomotive reveals equipment, still collapsed', async () => {
      loginAsToken('token-sup-m1hr')
      const user = userEvent.setup()

      renderWithProviders(<SectionDashboardPage />)
      await waitFor(() => expect(document.querySelector('.collapsible-group-loco')).not.toBeNull())

      await user.click(collapsedHeaders()[0])

      // Equipment headers appeared, and none of them opened by itself.
      expect(screen.getAllByRole('button', { expanded: false }).length).toBeGreaterThan(0)
      expect(document.querySelectorAll('.assignment-card')).toHaveLength(0)
    })

        it('starting an assignment moves it to In Progress', async () => {
      loginAsToken('token-sup-m1hr')
      const user = userEvent.setup()
      renderWithProviders(<SectionDashboardPage />)

      // 39126 is this section's OPEN assignment; 39127 belongs to work this Supervisor can no
      // longer see at all now that cross-section visibility is gone.
      const card = await cardFor('39126')
      await user.click(within(card).getByRole('button', { name: /start work/i }))

      await within(card).findByRole('button', { name: /mark attended/i })
      expect(within(card).queryByRole('button', { name: /start work/i })).not.toBeInTheDocument()
    })

    it('never offers Reopen to a Supervisor (it stays Admin-only)', async () => {
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)

      await cardFor('39126')
      expect(screen.queryByRole('button', { name: /^reopen$/i })).not.toBeInTheDocument()
    })
    it('offers no Reopen path to a Supervisor at all', async () => {
      // Reopen remains Admin-only, and an Admin cannot use this Supervisor-only application -
      // so the control is absent from the UI entirely rather than present-but-failing.
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)

      await cardFor('39126')
      expect(screen.queryByRole('button', { name: /^reopen$/i })).not.toBeInTheDocument()
    })
    it('shows + Add Section in booking details', async () => {
      loginAsToken('token-sup-m1hr-addperm')
      const user = userEvent.setup()
      renderWithProviders(<SectionDashboardPage />)

      const card = await cardFor('39126')
      await user.click(within(card).getByRole('button', { name: /details/i }))

      expect(await within(card).findByRole('button', { name: /\+ add section/i })).toBeInTheDocument()
    })

    it('shows no "no section assigned" warning for a sectioned Supervisor', async () => {
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)

      await cardFor('39126')
      expect(screen.queryByText(/no section is assigned/i)).not.toBeInTheDocument()
    })
  })

  // ----------------------------------------------------------- supervisor --

  describe('Supervisor', () => {
    it('hides the section selector and shows their own section in the header', async () => {
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)

      expect(await screen.findByRole('heading', { name: /section bookings — m1-hr/i })).toBeInTheDocument()
      expect(screen.queryByLabelText(/^section$/i)).not.toBeInTheDocument()
    })

    it('shows only their own section bookings', async () => {
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)

      const card = await cardFor('39126')
      expect(within(card).getByText('OPEN')).toBeInTheDocument()
    })

    it('hides Reopen entirely', async () => {
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)

      const card = await cardFor('39127')
      expect(within(card).queryByRole('button', { name: /^reopen$/i })).not.toBeInTheDocument()
    })

    it('attending in their own section moves the assignment to Attended', async () => {
      loginAsToken('token-sup-m1hr')
      const user = userEvent.setup()
      renderWithProviders(<SectionDashboardPage />)

      // booking 902 (fixture id 3, loco 39128) is already IN_PROGRESS for M1-HR.
      const card = await cardFor('39128')

      await user.click(within(card).getByRole('button', { name: /mark attended/i }))
      await user.type(within(card).getByLabelText(/attendance remarks/i), 'Cleared')
      await user.click(within(card).getByRole('button', { name: /confirm attend/i }))

      await within(card).findByText('Cleared')
      expect(within(card).queryByRole('button', { name: /mark attended/i })).not.toBeInTheDocument()
    })

    it('does not show Add Section without the permission', async () => {
      loginAsToken('token-sup-m1hr')
      const user = userEvent.setup()
      renderWithProviders(<SectionDashboardPage />)

      const card = await cardFor('39126')
      await user.click(within(card).getByRole('button', { name: /details/i }))

      await within(card).findByText('Section Assignments')
      expect(within(card).queryByRole('button', { name: /\+ add section/i })).not.toBeInTheDocument()
    })

    it('shows Add Section when they have the permission', async () => {
      loginAsToken('token-sup-m1hr-addperm')
      const user = userEvent.setup()
      renderWithProviders(<SectionDashboardPage />)

      const card = await cardFor('39126')
      await user.click(within(card).getByRole('button', { name: /details/i }))

      expect(await within(card).findByRole('button', { name: /\+ add section/i })).toBeInTheDocument()
    })

    it('cannot access another section directly via the API even if attempted', async () => {
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)
      await screen.findByRole('heading', { name: /section bookings — m1-hr/i })

      // The UI never offers a picker, so this asserts the backend boundary
      // by calling the API layer directly the way a tampered client would.
      const { listSectionAssignments } = await import('../api/sectionDashboard')
      await expect(listSectionAssignments('M2-HR')).rejects.toMatchObject({ status: 403 })
    })

    it('shows a message and no board when they have no section assigned', async () => {
      loginAsToken('token-sup-denied')
      renderWithProviders(<SectionDashboardPage />)

      await screen.findByText(/no section is assigned to your account/i)
      expect(screen.queryByRole('heading', { name: /^open/i })).not.toBeInTheDocument()
    })

    it('summary is automatically scoped to their own section', async () => {
      loginAsToken('token-sup-m1hr')
      renderWithProviders(<SectionDashboardPage />)

      await cardFor('39126')
      const summaryCards = document.querySelectorAll('.section-summary-card')
      expect(summaryCards.length).toBe(4)
      // M1-HR fixture: 1 OPEN, 1 IN_PROGRESS, 1 ATTENDED, 0 REOPENED.
      const values = Array.from(summaryCards).map(
        (c) => c.querySelector('.section-summary-value')?.textContent,
      )
      expect(values).toEqual(['1', '1', '0', '0'])
    })
  })

  // -------------------------------------------------------------- errors --

  it('shows a friendly error when a transition is rejected (e.g. stale state)', async () => {
    server.use(
      http.post('/api/section-assignments/:id/start', () =>
        HttpResponse.json({ detail: 'Cannot start an assignment in status IN_PROGRESS.' }, { status: 409 }),
      ),
    )
    loginAsToken('token-sup-m1hr')
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user, 'M1-HR')

    const card = await cardFor('39126')
    await user.click(within(card).getByRole('button', { name: /start work/i }))

    await screen.findByText('Cannot start an assignment in status IN_PROGRESS.')
  })

  it('shows a friendly 403 message from the backend when it disagrees with the UI state', async () => {
    server.use(
      http.post('/api/section-assignments/:id/start', () =>
        HttpResponse.json({ detail: 'You may only operate your own section\'s assignments.' }, { status: 403 }),
      ),
    )
    loginAsToken('token-sup-m1hr')
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)

    const card = await cardFor('39126')
    await user.click(within(card).getByRole('button', { name: /start work/i }))

    await screen.findByText(/don't have permission/i)
  })
})

/** Locomotive ORDER, through the real page rather than the grouping helper.
 *
 * grouping.test.ts already pins the comparator. What only the page can prove is the WIRING: that
 * the list handed to the grouping function is the status-filtered one, so the order on screen is
 * driven by the same number each card prints. A page that filtered after grouping, or grouped the
 * unfiltered list, would pass every unit test and still show a wrong order here.
 */
describe('Locomotive ordering by booking count', () => {
  /** One assignment, shaped like GET /api/sections/{code}/assignments returns it. */
  function row(id: number, locoNumber: string, status: string, nodeId: number, equipment: string) {
    return {
      id,
      booking_id: id,
      section_id: 1,
      section_code: 'M1-HR',
      status,
      assigned_at: '2026-09-01T00:00:00Z',
      started_at: null,
      started_by_name: null,
      attended_at: null,
      attended_by_name: null,
      attendance_remarks: null,
      booking: {
        id,
        status,
        description: `Booking ${id}`,
        booking_source: 'LOG_BOOK',
        equipment_node_id: nodeId,
        equipment_node_name: equipment,
        defect_type: null,
        shed_visit: {
          id: 1,
          loco_number: locoNumber,
          schedule_family: 'MINOR',
          schedule_variant: 'IA',
          arrival_condition: 'WORKING',
        },
      },
    }
  }

  /** 22382: 1 OPEN + 4 ATTENDED = 5. 33586: 2 OPEN. 44373: 1 OPEN + 1 ATTENDED = 2.
   *  Chosen so the unfiltered and OPEN-filtered orders differ, which is the whole point. */
  const ROWS = [
    row(1, '22382', 'OPEN', 10, 'Pantograph'),
    row(2, '22382', 'ATTENDED', 10, 'Pantograph'),
    row(3, '22382', 'ATTENDED', 20, 'Contactor'),
    row(4, '22382', 'ATTENDED', 20, 'Contactor'),
    row(5, '22382', 'ATTENDED', 20, 'Contactor'),
    row(6, '33586', 'OPEN', 10, 'Pantograph'),
    row(7, '33586', 'OPEN', 20, 'Contactor'),
    row(8, '44373', 'OPEN', 10, 'Pantograph'),
    row(9, '44373', 'ATTENDED', 20, 'Contactor'),
  ]

  function useRows() {
    server.use(
      http.get('/api/sections/:code/assignments', () => HttpResponse.json(ROWS)),
    )
  }

  /** The locomotive-level headers, top to bottom as rendered. */
  function locoOrder(): string[] {
    return Array.from(document.querySelectorAll('.collapsible-group-loco .collapsible-group-header'))
      .map((h) => (h.textContent ?? '').match(/\d{4,}/)?.[0] ?? '')
      .filter(Boolean)
  }

  it('lists locomotives fewest bookings first', async () => {
    useRows()
    loginAsToken('token-sup-m1hr')
    renderWithProviders(<SectionDashboardPage />)

    await waitFor(() => expect(locoOrder()).toHaveLength(3))
    // 44373 = 2, 33586 = 2, 22382 = 5. The two twos tie, so the lower number leads.
    expect(locoOrder()).toEqual(['33586', '44373', '22382'])
  })

  it('re-orders by the visible count when a status filter is applied, and back again', async () => {
    useRows()
    loginAsToken('token-sup-m1hr')
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '44373', '22382']))

    // OPEN only: 22382 drops to 1, 44373 to 1, 33586 stays 2. 22382 must rise to the TOP, from
    // last - impossible if the sort were reading an unfiltered total.
    await user.click(screen.getByRole('button', { name: /^open \(/i }))
    await waitFor(() => expect(locoOrder()).toEqual(['22382', '44373', '33586']))

    // Each card now prints the count the order was built from.
    const header = document
      .querySelectorAll('.collapsible-group-loco .collapsible-group-header')[0]
    expect(header.textContent).toMatch(/22382/)
    expect(header.textContent).toMatch(/1 booking/)

    // Clearing restores the original counts and therefore the original order.
    await user.click(screen.getByRole('button', { name: /^open \(/i }))
    await waitFor(() => expect(locoOrder()).toEqual(['33586', '44373', '22382']))
  })

  it('keeps a locomotive expanded when filtering moves it to another position', async () => {
    useRows()
    loginAsToken('token-sup-m1hr')
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['33586', '44373', '22382']))

    // Expand 44373 where it currently sits, in the middle.
    const headers = () =>
      Array.from(
        document.querySelectorAll<HTMLElement>('.collapsible-group-loco .collapsible-group-header'),
      )
    const target = headers().find((h) => h.textContent?.includes('44373'))!
    await user.click(target)
    expect(target.getAttribute('aria-expanded')).toBe('true')

    // Filter to OPEN. 44373 moves from index 1 to index 1 in a DIFFERENT ordering, and 22382
    // takes the top - so whichever row slides under it must not inherit its open state.
    await user.click(screen.getByRole('button', { name: /^open \(/i }))
    await waitFor(() => expect(locoOrder()).toEqual(['22382', '44373', '33586']))

    const after = headers()
    const moved = after.find((h) => h.textContent?.includes('44373'))!
    expect(moved.getAttribute('aria-expanded')).toBe('true')
    // And expansion did not leak to its neighbours.
    expect(after.find((h) => h.textContent?.includes('22382'))!.getAttribute('aria-expanded')).toBe('false')
    expect(after.find((h) => h.textContent?.includes('33586'))!.getAttribute('aria-expanded')).toBe('false')
  })

  it('shows the existing empty state, not a zero-count row, when a filter hides every booking', async () => {
    // The pre-existing visibility rule, unchanged: a locomotive with no visible booking produces
    // no group at all. The sort must not invent a zero row at the top.
    server.use(
      http.get('/api/sections/:code/assignments', () =>
        HttpResponse.json([row(1, '22382', 'ATTENDED', 10, 'Pantograph')]),
      ),
    )
    loginAsToken('token-sup-m1hr')
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)

    await waitFor(() => expect(locoOrder()).toEqual(['22382']))

    await user.click(screen.getByRole('button', { name: /^open \(/i }))
    await screen.findByText(/no bookings in this state/i)
    expect(locoOrder()).toEqual([])
  })
})

describe('Section selector (Admin only)', () => {
  it('offers a section selector to an Admin', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<SectionDashboardPage />)

    expect(await screen.findByLabelText(/^section$/i)).toBeInTheDocument()
  })

  it('never offers a section selector to a Supervisor', async () => {
    loginAsToken('token-sup-m1hr')
    renderWithProviders(<SectionDashboardPage />)

    // Wait for the page to settle before asserting an absence.
    await cardFor('39126')
    expect(screen.queryByLabelText(/^section$/i)).not.toBeInTheDocument()
  })

  it('never offers one to a movement (SHIFT) Supervisor either', async () => {
    loginAsToken('token-sup-shift')
    renderWithProviders(<SectionDashboardPage />)

    await screen.findByRole('heading', { name: /section bookings/i })
    expect(screen.queryByLabelText(/^section$/i)).not.toBeInTheDocument()
  })
})

/** The BOOKING SOURCE level, through the real page.
 *
 * grouping.test.ts pins the order, the labels and the agreement with the Booking Pool. What only
 * the page can prove is the WIRING: that the level is rendered between the locomotive and its
 * equipment, that its keys do not collide, and that Start Work and the assignment detail still
 * work at the new depth.
 */
describe('Section Dashboard booking source level', () => {
  function row(
    id: number,
    locoNumber: string,
    status: string,
    nodeId: number,
    equipment: string,
    bookingSource = 'LOG_BOOK',
  ) {
    return {
      id,
      booking_id: id,
      section_id: 1,
      section_code: 'M1-HR',
      status,
      assigned_at: '2026-09-01T00:00:00Z',
      started_at: null,
      started_by_name: null,
      attended_at: null,
      attended_by_name: null,
      attendance_remarks: null,
      booking: {
        id,
        status,
        description: `Booking ${id}`,
        booking_source: bookingSource,
        equipment_node_id: nodeId,
        equipment_node_name: equipment,
        defect_type: null,
        shed_visit: {
          id: 1,
          loco_number: locoNumber,
          schedule_family: 'MINOR',
          schedule_variant: 'IC',
          arrival_condition: 'WORKING',
        },
      },
    }
  }

  const MIXED = [
    row(1, '42818', 'OPEN', 10, 'JB to ACD/TPWS/DPWCS', 'LOG_BOOK'),
    row(2, '42818', 'ATTENDED', 20, 'Pantograph', 'LOG_BOOK'),
    row(3, '42818', 'OPEN', 30, 'Bogie', 'TEST_BEFORE'),
    row(4, '42818', 'OPEN', 10, 'JB to ACD/TPWS/DPWCS', 'TEST_AFTER'),
  ]

  function useRows(rows: unknown[] = MIXED) {
    server.use(http.get('/api/sections/:code/assignments', () => HttpResponse.json(rows)))
  }

  function sourceHeadings(): (string | undefined)[] {
    return Array.from(
      document.querySelectorAll<HTMLElement>('.collapsible-group-source > .collapsible-group-header'),
    ).map((h) => h.querySelector('.collapsible-group-title')?.textContent ?? undefined)
  }

  async function openLoco(user: ReturnType<typeof userEvent.setup>, loco = '42818') {
    const header = await screen.findByRole('button', { name: new RegExp(`^${loco}`) })
    await user.click(header)
    return header
  }

  it('renders a SOURCE level between the locomotive and its equipment', async () => {
    loginAsToken('token-sup-m1hr')
    useRows()
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)

    await openLoco(user)
    // One level down is the source, not equipment - and in the canonical operational order, with
    // the same friendly labels the Booking Pool uses.
    expect(sourceHeadings()).toEqual(['Log Book', 'Test Before', 'Test After'])
    expect(screen.queryByRole('button', { name: /Bogie/ })).toBeNull()
  })

  it('separates LOG_BOOK and TEST_BEFORE work on the same locomotive', async () => {
    loginAsToken('token-sup-m1hr')
    useRows()
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)

    await openLoco(user)
    await user.click(screen.getByRole('button', { name: /^Test Before/ }))

    // Only Test Before's equipment is revealed; the Log Book group stays shut.
    expect(screen.getByRole('button', { name: /Bogie/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Pantograph/ })).toBeNull()
  })

  it('renders the SAME equipment node under two sources independently', async () => {
    loginAsToken('token-sup-m1hr')
    useRows()
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)

    await openLoco(user)
    await user.click(screen.getByRole('button', { name: /^Log Book/ }))
    await user.click(screen.getByRole('button', { name: /^Test After/ }))

    // Node 10 is booked from both Log Book and Test After.
    const jbs = screen.getAllByRole('button', { name: /JB to ACD/ })
    expect(jbs).toHaveLength(2)

    await user.click(jbs[0])
    expect(jbs[0]).toHaveAttribute('aria-expanded', 'true')
    // The expansion did NOT leak to the identically-named node under the other source.
    expect(jbs[1]).toHaveAttribute('aria-expanded', 'false')
    expect(document.querySelectorAll('.assignment-card')).toHaveLength(1)
  })

  it('counts: locomotive unchanged, source per-source, equipment per-equipment', async () => {
    loginAsToken('token-sup-m1hr')
    useRows()
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)

    const loco = await openLoco(user)
    expect(loco.textContent).toContain('4 bookings')
    expect(screen.getByRole('button', { name: /^Log Book/ }).textContent).toContain('2 bookings')
    expect(screen.getByRole('button', { name: /^Test Before/ }).textContent).toContain('1 booking')
    expect(screen.getByRole('button', { name: /^Test After/ }).textContent).toContain('1 booking')

    await user.click(screen.getByRole('button', { name: /^Log Book/ }))
    expect(screen.getByRole('button', { name: /JB to ACD/ }).textContent).toContain('1 booking')
    expect(screen.getByRole('button', { name: /Pantograph/ }).textContent).toContain('1 booking')
  })

  it('hides a source group entirely once its bookings are filtered out', async () => {
    loginAsToken('token-sup-m1hr')
    useRows()
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)

    await openLoco(user)
    expect(sourceHeadings()).toEqual(['Log Book', 'Test Before', 'Test After'])

    // Only the Log Book Pantograph row is ATTENDED. The status filter is the segmented group.
    const filters = screen.getByRole('group', { name: /filter by assignment status/i })
    await user.click(within(filters).getByRole('button', { name: /^Attended/ }))
    await waitFor(() => expect(sourceHeadings()).toEqual(['Log Book']))
    expect(screen.getByRole('button', { name: /^42818/ }).textContent).toContain('1 booking')
  })

  it('shows an unrecognised source last, with its raw stored value', async () => {
    loginAsToken('token-sup-m1hr')
    useRows([
      row(1, '42818', 'OPEN', 10, 'Air Dryer', 'FUTURE_SOURCE'),
      row(2, '42818', 'OPEN', 20, 'Bogie', 'LOG_BOOK'),
    ])
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)

    await openLoco(user)
    expect(sourceHeadings()).toEqual(['Log Book', 'FUTURE_SOURCE'])
    // Still reachable, not dropped.
    await user.click(screen.getByRole('button', { name: /^FUTURE_SOURCE/ }))
    await user.click(screen.getByRole('button', { name: /Air Dryer/ }))
    expect(document.querySelectorAll('.assignment-card')).toHaveLength(1)
  })

  it('shows a blank source as "Source not recorded" rather than hiding the work', async () => {
    loginAsToken('token-sup-m1hr')
    useRows([row(1, '42818', 'OPEN', 10, 'Air Dryer', '')])
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)

    await openLoco(user)
    expect(sourceHeadings()).toEqual(['Source not recorded'])
  })

  it('still states the source inside the booking card, not only on the group heading', async () => {
    loginAsToken('token-sup-m1hr')
    useRows([row(1, '42818', 'OPEN', 30, 'Bogie', 'TEST_BEFORE')])
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)
    await expandAll(user)

    const card = document.querySelector('.assignment-card') as HTMLElement
    expect(within(card).getByText('Source')).toBeInTheDocument()
    expect(within(card).getByText('Test Before')).toBeInTheDocument()
    // The friendly label, never the raw enum - same helper as the heading.
    expect(within(card).queryByText('TEST_BEFORE')).toBeNull()
  })

  it('Start Work still works at the new depth, and the card still shows its detail', async () => {
    loginAsToken('token-sup-m1hr')
    useRows([row(1, '42818', 'OPEN', 30, 'Bogie', 'TEST_BEFORE')])
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)
    await expandAll(user)

    const card = document.querySelector('.assignment-card') as HTMLElement
    expect(within(card).getByText('Booking 1')).toBeInTheDocument()
    expect(within(card).getByText('Assigned')).toBeInTheDocument()

    // The action reaches the API from inside the deeper hierarchy. The full OPEN -> IN_PROGRESS
    // transition is already covered above against the default stateful handlers; what is new here
    // is only that the button is reachable and wired at this depth, so this asserts the request.
    let startedId: string | undefined
    server.use(
      http.post('/api/section-assignments/:id/start', ({ params }) => {
        startedId = String(params.id)
        return HttpResponse.json({
          ...row(1, '42818', 'IN_PROGRESS', 30, 'Bogie', 'TEST_BEFORE'),
          started_at: '2026-09-02T00:00:00Z',
          started_by_name: 'M1 Supervisor',
        })
      }),
    )

    await user.click(within(card).getByRole('button', { name: /start work/i }))
    await waitFor(() => expect(startedId).toBe('1'))
  })

  it("still shows only the Supervisor's own section's bookings", async () => {
    // Scoping is the SERVER's: the page groups whatever the section endpoint returned. This pins
    // that the new level did not introduce a client-side source fetch that could widen it.
    loginAsToken('token-sup-m1hr')
    let requestedPath = ''
    server.use(
      http.get('/api/sections/:code/assignments', ({ params, request }) => {
        requestedPath = new URL(request.url).pathname
        if (params.code !== 'M1-HR') {
          return HttpResponse.json({ detail: 'wrong section' }, { status: 403 })
        }
        return HttpResponse.json(MIXED)
      }),
    )
    const user = userEvent.setup()
    renderWithProviders(<SectionDashboardPage />)
    await selectSection(user)
    await openLoco(user)

    expect(requestedPath).toBe('/api/sections/M1-HR/assignments')
    // Every card on screen is M1-HR's own assignment, and no other section was requested.
    await expandAll(user)
    const cards = Array.from(document.querySelectorAll('.assignment-card'))
    expect(cards).toHaveLength(MIXED.length)
  })
})

