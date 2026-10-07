import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchSignedChecksheet } from '../api/shedVisitHistory'
import { AppShell } from '../layout/AppShell'
import { HISTORY, historyChecksheet, historyDetail, historyRow } from '../mocks/visitHistory'
import { loginAsToken, renderWithProviders } from '../test/testUtils'
import type { VisitBookingSectionGroup } from '../types'
import { ShedVisitHistoryPage } from './ShedVisitHistoryPage'

/**
 * Shed Visit History, as the operator sees it. The server decides scope, filtering and paging
 * (backend tests/test_visit_history.py); these pin that the page asks for the right thing, shows
 * what it is given honestly ("Not recorded", never 0) and opens signed documents only through the
 * authenticated API.
 */

const RESET_ROW = historyRow({
  shed_visit_id: 104,
  status: 'CLOSED',
  operational_phase: 'SHED_OUT',
  display_label: 'Closed administratively (system reset)',
  arrival_at: '2026-09-01T08:00:00Z',
  departed_at: '2026-09-02T08:00:00Z',
  departure_source: 'SYSTEM',
  closure: {
    kind: 'ADMIN_RESET',
    label: 'Closed administratively (system reset)',
    at: '2026-09-02T08:00:00Z',
    source: 'SYSTEM',
    actor_name: 'System',
    reason: 'Production go-live reset',
  },
  booking_count: 0,
  checksheet_count: 0,
})

const SHED_OUT_ROW = historyRow({
  shed_visit_id: 103,
  loco_number: '22560',
  loco_model: 'WAP-4',
  schedule_variant: 'IB',
  status: 'CLOSED',
  operational_phase: 'SHED_OUT',
  display_label: 'Shed Out',
  arrival_at: '2026-08-01T08:00:00Z',
  departed_at: '2026-08-03T08:00:00Z',
  departure_source: 'DASHBOARD',
  closure: { kind: 'SHED_OUT', label: 'Shed Out', at: '2026-08-03T08:00:00Z', source: 'DASHBOARD', actor_name: null, reason: null },
  booking_count: 1,
  checksheet_count: 2,
})

const MAJOR_ROW = historyRow({
  shed_visit_id: 102,
  loco_number: '30476',
  loco_model: 'WAG9HC',
  schedule_family: 'MAJOR',
  schedule_variant: 'TOH',
  status: 'READY',
  operational_phase: 'READY',
  display_label: 'Ready',
  arrival_at: '2026-09-05T08:00:00Z',
  ready_at: '2026-09-12T08:00:00Z',
  booking_count: 0,
  checksheet_count: 94,
})

function seedRows() {
  HISTORY.rows = [historyRow(), MAJOR_ROW, RESET_ROW, SHED_OUT_ROW]
}

function lastQuery(): URLSearchParams {
  return HISTORY.listRequests[HISTORY.listRequests.length - 1]
}

async function rowFor(visitId: number) {
  const cell = await screen.findByText(`#${visitId}`)
  return cell.closest('tr') as HTMLTableRowElement
}

async function openDetails(user: ReturnType<typeof userEvent.setup>, visitId: number) {
  const row = await rowFor(visitId)
  await user.click(within(row).getByRole('button', { name: /details/i }))
  const detailRow = row.nextElementSibling as HTMLTableRowElement
  await within(detailRow).findByRole('heading', { name: 'Timings' })
  return detailRow
}

describe('Shed Visit History - the list', () => {
  it('lists every visit with its type, schedule, status and counts', async () => {
    loginAsToken('token-admin')
    seedRows()
    renderWithProviders(<ShedVisitHistoryPage />)

    expect(await screen.findByText('4 visits recorded')).toBeInTheDocument()
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1)
    expect(rows.map((r) => (r as HTMLTableRowElement).cells[0].textContent)).toEqual(['#101', '#102', '#104', '#103'])

    const major = await rowFor(102)
    expect(major).toHaveTextContent('30476')
    expect(major).toHaveTextContent('WAG9HC')
    expect(major).toHaveTextContent('TOH')
    expect(major).toHaveTextContent('Major')
    expect(major).toHaveTextContent('Ready')
    expect(major.cells[8]).toHaveTextContent('94')
    expect(lastQuery().get('page')).toBe('1')
  })

  it('shows an administrative reset as such - never as a Shed Out', async () => {
    loginAsToken('token-admin')
    seedRows()
    renderWithProviders(<ShedVisitHistoryPage />)

    const reset = await rowFor(104)
    expect(reset).toHaveTextContent('Closed administratively (system reset)')
    expect(reset).toHaveTextContent('Not a Shed Out')
    const shedOut = await rowFor(103)
    expect(shedOut.cells[3]).toHaveTextContent(/^Shed Out$/)
  })

  it('sends the filters to the server and starts again from page 1', async () => {
    loginAsToken('token-admin')
    seedRows()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)
    await rowFor(101)

    await user.type(screen.getByLabelText('Loco number'), '22560')
    await user.selectOptions(screen.getByLabelText('Schedule family'), 'MINOR')
    await user.selectOptions(screen.getByLabelText('Loco type'), 'WAP-4')
    await user.selectOptions(screen.getByLabelText('Closed by'), 'SYSTEM')
    await user.type(screen.getByLabelText('Arrived from'), '2026-08-01')
    await user.type(screen.getByLabelText('Visit ID'), '103')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => expect(lastQuery().get('loco_number')).toBe('22560'))
    const q = lastQuery()
    expect(q.get('schedule_family')).toBe('MINOR')
    expect(q.get('loco_model')).toBe('WAP-4')
    expect(q.get('departure_source')).toBe('SYSTEM')
    expect(q.get('arrived_from')).toBe('2026-08-01')
    expect(q.get('visit_id')).toBe('103')
    expect(q.get('page')).toBe('1')
    expect(q.has('status')).toBe(false)          // an unset filter is not sent at all
    expect(await screen.findByText('1 visit matches the filters')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    await waitFor(() => expect(lastQuery().has('loco_number')).toBe(false))
    expect(screen.getByLabelText('Loco number')).toHaveValue('')
  })

  it('pages on the server', async () => {
    loginAsToken('token-admin')
    HISTORY.rows = Array.from({ length: 60 }, (_, i) => historyRow({ shed_visit_id: 1000 - i }))
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    expect(await screen.findByText('Page 1 of 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(26)

    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Page 2 of 3')).toBeInTheDocument()
    expect(lastQuery().get('page')).toBe('2')
    expect(lastQuery().get('page_size')).toBe('25')
    expect(await screen.findByText('#975')).toBeInTheDocument()
  })

  it('says when a search finds nothing', async () => {
    loginAsToken('token-admin')
    seedRows()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)
    await rowFor(101)

    await user.type(screen.getByLabelText('Loco number'), '99999')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText('No shed visits match these filters')).toBeInTheDocument()
  })

  it('marks unknown loco types and checksheet counts as unknown when BL-DCMS is down', async () => {
    loginAsToken('token-admin')
    HISTORY.rows = [historyRow({ loco_model: null, technology: null, checksheet_count: null })]
    HISTORY.locomotiveDetailsAvailable = false
    HISTORY.checksheetCountsAvailable = false
    HISTORY.models = null
    renderWithProviders(<ShedVisitHistoryPage />)

    const row = await rowFor(101)
    expect(row).toHaveTextContent('Type unknown')
    expect(row.cells[8]).toHaveTextContent('—')
    expect(row.cells[8]).not.toHaveTextContent('0')
    expect(screen.getByText(/BL-DCMS could not be reached/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('Loco type')).toBeDisabled())
  })

  it('shows a refusal as a refusal, not as an empty history', async () => {
    loginAsToken('token-admin')
    HISTORY.listError = { status: 403, detail: 'Dashboard access has not been granted for this account.' }
    renderWithProviders(<ShedVisitHistoryPage />)

    expect(await screen.findByText('Not available to your account')).toBeInTheDocument()
    expect(screen.queryByText(/no shed visits/i)).toBeNull()
  })

  it('offers the section filter to an Admin only', async () => {
    loginAsToken('token-admin')
    seedRows()
    const { unmount } = renderWithProviders(<ShedVisitHistoryPage />)
    expect(await screen.findByLabelText('Section involved')).toBeInTheDocument()
    unmount()

    loginAsToken('token-sup-m1hr')
    renderWithProviders(<ShedVisitHistoryPage />)
    await rowFor(101)
    expect(screen.queryByLabelText('Section involved')).toBeNull()
  })
})

// ------------------------------------------------------------------------------ detail --

const BOOKING_GROUPS: VisitBookingSectionGroup[] = [
  {
    section_id: 9,
    section_code: 'M1-HR',
    section_name: 'M1-HR',
    booking_count: 1,
    bookings: [
      {
        booking_id: 701,
        booking_source: 'LOG_BOOK',
        booking_source_label: 'Log Book',
        stage_type: null,
        equipment: { node_id: 1843, name: 'Pantograph', path: ['Roof equipment'] },
        defect_type: 'Arcing',
        remarks: 'Pan arcing',
        status: 'REOPENED',
        created_at: '2026-09-10T13:00:00Z',
        created_by_name: 'Anil M1',
        origin_checksheet_id: 501,
        responsible_sections: ['M1-HR'],
        assignments: [
          {
            assignment_id: 801,
            section_id: 9,
            section_code: 'M1-HR',
            section_name: 'M1-HR',
            status: 'REOPENED',
            assignment_source: 'AUTO_MAPPING',
            assigned_at: '2026-09-10T13:00:00Z',
            assigned_by_name: 'System (automatic routing)',
            started_at: '2026-09-10T13:30:00Z',
            started_by_name: 'Anil M1',
            attended_at: '2026-09-10T14:00:00Z',
            attended_by_name: 'Anil M1',
            attendance_remarks: 'Carbon replaced',
            reopens: [{ at: '2026-09-10T15:00:00Z', by_name: 'Technical Admin', reason: 'Arcing again' }],
          },
        ],
        history: [
          {
            at: '2026-09-10T13:00:00Z', event_type: 'CREATED',
            sentence: 'Booking raised by Anil M1 from a BL-DCMS checksheet', actor_name: 'Anil M1',
            section_name: null, remarks: null, raw: { checksheet_id: 501 },
          },
          {
            at: '2026-09-10T15:00:00Z', event_type: 'REOPENED', sentence: 'Reopened for M1-HR by Technical Admin',
            actor_name: 'Technical Admin', section_name: 'M1-HR', remarks: 'Arcing again', raw: null,
          },
        ],
      },
    ],
  },
  {
    section_id: null,
    section_code: null,
    section_name: 'No responsible section',
    booking_count: 1,
    bookings: [
      {
        booking_id: 703,
        booking_source: 'LOG_BOOK',
        booking_source_label: 'Log Book',
        stage_type: null,
        equipment: { node_id: 9999, name: null, path: [] },
        defect_type: null,
        remarks: 'Unrouted',
        status: 'OPEN',
        created_at: '2026-09-10T13:00:00Z',
        created_by_name: 'Not recorded',
        origin_checksheet_id: null,
        responsible_sections: [],
        assignments: [],
        history: [],
      },
    ],
  },
]

const SIGNED_URL = '/api/shed-visit-history/101/checksheets/501/signed-document'

const CHECKSHEETS = [
  historyChecksheet({
    checksheet_id: 501, workflow_stage_type: 'TEST_BEFORE', workflow_group: 'TEST_BEFORE',
    equipment_label: null, template_name: 'Test Before Performa', status: 'APPROVED',
    approved_at: '2026-09-10T11:00:00Z', approved_by_name: 'Anil M1',
    signed: true, signed_at: '2026-09-10T11:05:00Z', signed_by_name: 'Anil M1', signature_verification: 'VALID',
    signed_document_available: true, signed_document_url: SIGNED_URL,
  }),
  historyChecksheet({ checksheet_id: 502, section_name: 'M2-HR', equipment_label: 'Compressor',
    requirement: { is_required: false, is_active: false } }),
  historyChecksheet({ checksheet_id: 503, schedule_family: 'TI', workflow_stage_type: null, workflow_group: 'TI',
    equipment_label: 'Trip item' }),
  historyChecksheet({ checksheet_id: 504, workflow_stage_type: 'TEST_AFTER', workflow_group: 'TEST_AFTER',
    equipment_label: null, template_name: 'Test After Performa', status: 'APPROVED',
    signed: true, signed_at: '2026-09-11T11:05:00Z', signed_by_name: 'Anil M1',
    signed_document_available: false, signed_document_url: null }),
  historyChecksheet({ checksheet_id: 505, section_name: 'M1-HR', equipment_label: 'Axle box', status: 'REJECTED',
    rejected_at: '2026-09-10T16:00:00Z', rejected_by_name: 'Anil M1', rejection_reason: 'Readings missing' }),
]

function seedDetail() {
  HISTORY.rows = [historyRow()]
  HISTORY.details[101] = historyDetail({ bookings: BOOKING_GROUPS, checksheets: { available: true, message: null, items: CHECKSHEETS } })
}

describe('Shed Visit History - one visit opened', () => {
  it('shows every Minor milestone and says "Not recorded" rather than inventing a time or a zero', async () => {
    loginAsToken('token-admin')
    seedDetail()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 101)
    const timings = within(detail).getByRole('heading', { name: 'Timings' }).closest('section') as HTMLElement
    const terms = within(timings).getAllByRole('term').map((t) => t.textContent)
    expect(terms).toEqual(expect.arrayContaining([
      'Test Before started', 'Test Before completed', 'Minor Inspection completed',
      'Test After started', 'Test After completed', 'Ready', 'Shed Out',
    ]))
    const testAfter = within(timings).getByText('Test After duration').closest('div') as HTMLElement
    expect(testAfter).toHaveTextContent('Not recorded')
    expect(testAfter).not.toHaveTextContent('0m')
    expect(within(timings).getByText('Test Before duration').closest('div')).toHaveTextContent('1h 0m')
    expect(within(timings).getByText('Total shed dwell').closest('div')).toHaveTextContent('still running')
  })

  it('names an administrative reset in the detail too', async () => {
    loginAsToken('token-admin')
    HISTORY.rows = [RESET_ROW]
    HISTORY.details[104] = historyDetail({ visit: RESET_ROW })
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 104)
    expect(within(detail).getByRole('note')).toHaveTextContent('closed administratively by a system reset')
    expect(within(detail).getByRole('note')).toHaveTextContent('it was not a Shed Out')
    expect(within(detail).getByRole('note')).toHaveTextContent('Production go-live reset')
  })

  it('groups bookings by section and opens each booking to its full history', async () => {
    loginAsToken('token-admin')
    seedDetail()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 101)
    const bookings = within(detail).getByRole('heading', { name: 'Bookings by section' }).closest('section') as HTMLElement
    const groups = bookings.querySelectorAll(':scope > details')
    expect(Array.from(groups).map((g) => g.querySelector('summary')?.textContent)).toEqual([
      'M1-HR1 booking',
      'No responsible section1 booking',
    ])

    await user.click(within(bookings).getByText('M1-HR', { selector: '.history-group-title' }))
    await user.click(within(bookings).getByText(/Booking #701/))
    const booking = within(bookings).getByText(/Booking #701/).closest('details') as HTMLElement
    expect(booking).toHaveTextContent('Roof equipment → Pantograph')
    expect(booking).toHaveTextContent('System (automatic routing)')
    expect(booking).toHaveTextContent('Carbon replaced')
    expect(booking).toHaveTextContent('Technical Admin — “Arcing again”')
    expect(booking).toHaveTextContent('Booking raised by Anil M1 from a BL-DCMS checksheet')
    expect(booking).toHaveTextContent('Reopened for M1-HR by Technical Admin')
    expect(booking).toHaveTextContent('#501')

    const unrouted = within(bookings).getByText(/Booking #703/).closest('details') as HTMLElement
    expect(unrouted).toHaveTextContent('Equipment #9999 (name unavailable)')
    expect(unrouted).toHaveTextContent('None assigned')
  })

  it('shows raw event data only when the server sent it (Admin)', async () => {
    loginAsToken('token-admin')
    seedDetail()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 101)
    expect(within(detail).getAllByText('Raw data (Admin)')).toHaveLength(1)
  })

  it('tells a Supervisor the bookings are their own section only', async () => {
    loginAsToken('token-sup-m1hr')
    HISTORY.rows = [historyRow()]
    HISTORY.details[101] = historyDetail({ booking_scope: 'OWN_SECTION', bookings: [BOOKING_GROUPS[0]] })
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 101)
    expect(within(detail).getByText("Showing your own section's bookings only.")).toBeInTheDocument()
  })

  it('groups checksheets by stage in workflow order and keeps unknown families', async () => {
    loginAsToken('token-admin')
    seedDetail()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 101)
    const section = within(detail).getByRole('heading', { name: 'Checksheets' }).closest('section') as HTMLElement
    const titles = Array.from(section.querySelectorAll('.history-group-title')).map((t) => t.textContent)
    expect(titles).toEqual(['Test Before', 'Minor Inspection', 'Test After', 'TI'])

    const inspection = within(section).getByText('Minor Inspection').closest('details') as HTMLElement
    await user.click(within(inspection).getByText('Minor Inspection'))
    const subsections = Array.from(inspection.querySelectorAll('h4')).map((h) => h.textContent)
    expect(subsections).toEqual(['M1-HR', 'M2-HR'])
    expect(inspection).toHaveTextContent('Optional')
    expect(inspection).toHaveTextContent('Requirement deactivated')
    expect(inspection).toHaveTextContent('Readings missing')
    expect(inspection).toHaveTextContent('Not signed')
  })

  it('shows a Major visit as one Major group with maintenance type', async () => {
    loginAsToken('token-admin')
    HISTORY.rows = [MAJOR_ROW]
    HISTORY.details[102] = historyDetail({
      visit: MAJOR_ROW,
      checksheets: {
        available: true, message: null,
        items: [historyChecksheet({ checksheet_id: 601, schedule_family: 'MAJOR', workflow_stage_type: null,
          workflow_group: 'MAJOR', equipment_label: 'Traction Motor 1', maintenance_type: 'OVERHAUL' })],
      },
    })
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 102)
    const group = within(detail).getByText('Major schedule').closest('details') as HTMLElement
    expect(group).toHaveTextContent('Traction Motor 1')
    expect(group).toHaveTextContent('OVERHAUL')
  })

  it('says checksheets are unavailable instead of showing none', async () => {
    loginAsToken('token-admin')
    HISTORY.rows = [historyRow()]
    HISTORY.details[101] = historyDetail({
      checksheets: { available: false, message: 'Checksheets could not be read from BL-DCMS right now.', items: [] },
    })
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 101)
    expect(within(detail).getByText('Checksheets could not be read from BL-DCMS right now.')).toBeInTheDocument()
    expect(within(detail).queryByText(/No checksheets are recorded/)).toBeNull()
  })

  it('reports a detail that cannot be loaded', async () => {
    loginAsToken('token-admin')
    HISTORY.rows = [historyRow()]
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const row = await rowFor(101)
    await user.click(within(row).getByRole('button', { name: /details/i }))
    const detailRow = row.nextElementSibling as HTMLTableRowElement
    expect(await within(detailRow).findByRole('alert')).toHaveTextContent('Shed visit not found')
  })
})

// ------------------------------------------------------------------- signed documents --

describe('Signed checksheet documents', () => {
  let opened: { opener: unknown; location: { href: string }; close: ReturnType<typeof vi.fn> }
  let openSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    opened = { opener: {}, location: { href: '' }, close: vi.fn() }
    openSpy = vi.fn(() => opened)
    vi.stubGlobal('open', openSpy)
    URL.createObjectURL = vi.fn(() => 'blob:signed-document')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  async function openSignedGroup(user: ReturnType<typeof userEvent.setup>) {
    const detail = await openDetails(user, 101)
    const group = within(detail).getByText('Test Before').closest('details') as HTMLElement
    await user.click(within(group).getByText('Test Before'))
    return { detail, group }
  }

  it('fetches the PDF with the session header, never a token in the URL, and opens it privately', async () => {
    loginAsToken('token-admin')
    seedDetail()
    HISTORY.documents[SIGNED_URL] = 'pdf'
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const { group } = await openSignedGroup(user)
    await user.click(within(group).getByRole('button', { name: /view signed checksheet/i }))

    await waitFor(() => expect(opened.location.href).toBe('blob:signed-document'))
    expect(openSpy).toHaveBeenCalledWith('', '_blank')
    expect(opened.opener).toBeNull()
    expect(HISTORY.documentRequests).toEqual([{ url: SIGNED_URL, authorization: 'Bearer token-admin' }])
    const blob = (URL.createObjectURL as ReturnType<typeof vi.fn>).mock.calls[0][0] as Blob
    expect(blob.type).toBe('application/pdf')
  })

  it('shows the refusal and closes the empty window when the server says no', async () => {
    loginAsToken('token-sup-m1hr')
    seedDetail()
    HISTORY.documents[SIGNED_URL] = {
      status: 403,
      detail: 'You may only view checksheets of your own section.',
    }
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const { group } = await openSignedGroup(user)
    await user.click(within(group).getByRole('button', { name: /view signed checksheet/i }))

    expect(await within(group).findByRole('alert')).toHaveTextContent(
      'You may only view checksheets of your own section.',
    )
    expect(opened.close).toHaveBeenCalled()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('reports a missing document clearly', async () => {
    loginAsToken('token-admin')
    seedDetail()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const { group } = await openSignedGroup(user)
    await user.click(within(group).getByRole('button', { name: /view signed checksheet/i }))

    expect(await within(group).findByRole('alert')).toHaveTextContent('This checksheet has no digitally signed document.')
  })

  it('offers no document link for a signed checksheet whose file is unavailable, nor for an unsigned one', async () => {
    loginAsToken('token-admin')
    seedDetail()
    const user = userEvent.setup()
    renderWithProviders(<ShedVisitHistoryPage />)

    const detail = await openDetails(user, 101)
    const testAfter = within(detail).getByText('Test After').closest('details') as HTMLElement
    await user.click(within(testAfter).getByText('Test After'))
    expect(within(testAfter).getByText('Signed document not available')).toBeInTheDocument()
    expect(within(testAfter).queryByRole('button', { name: /view signed checksheet/i })).toBeNull()
    expect(within(detail).getAllByRole('button', { name: /view signed checksheet/i })).toHaveLength(1)
  })

  it('refuses to fetch anything but this app’s own signed-document route', async () => {
    await expect(fetchSignedChecksheet('https://evil.example/doc.pdf')).rejects.toThrow()
    await expect(fetchSignedChecksheet('/storage/pdfs/checksheet_1.pdf')).rejects.toThrow()
    await expect(fetchSignedChecksheet(`${SIGNED_URL}?token=abc`)).rejects.toThrow()
    expect(HISTORY.documentRequests).toEqual([])
  })
})

describe('Navigation', () => {
  it.each(['token-admin', 'token-sup-m1hr', 'token-sup-shift'])('%s sees the Shed Visits link, which still points at the unchanged route', async (token) => {
    loginAsToken(token)
    renderWithProviders(
      <AppShell>
        <div />
      </AppShell>,
    )
    // User-facing label renamed to "Shed Visits"; the ROUTE deliberately did not move, so
    // bookmarks, the API path and anything holding the old URL keep working.
    expect(await screen.findByRole('link', { name: 'Shed Visits' })).toHaveAttribute('href', '/shed-visit-history')
  })
})
