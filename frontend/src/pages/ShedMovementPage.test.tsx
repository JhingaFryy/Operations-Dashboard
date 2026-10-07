import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { server } from '../mocks/server'
import App from '../App'
import { ShedMovementPage } from './ShedMovementPage'
import { selectEquipment } from '../test/equipmentSearch'

async function openForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /shed in/i }))
}

async function selectLocomotive(user: ReturnType<typeof userEvent.setup>, locoNumber: string) {
  const input = screen.getByLabelText(/^locomotive$/i)
  await user.type(input, locoNumber.slice(0, 3))
  const results = await screen.findByLabelText('Locomotive results')
  await user.click(within(results).getByText(locoNumber))
}

function bookingRow(index: number): HTMLElement {
  return screen.getByRole('heading', { name: `Booking #${index + 1}` }).closest('.log-book-booking-row') as HTMLElement
}

async function fillBooking(
  user: ReturnType<typeof userEvent.setup>,
  index: number,
  { nodeLabel, defectTypeLabel, remarks }: {
    nodeLabel: string
    defectTypeLabel: string
    remarks: string
  },
) {
  // No technology/family step: equipment search is already scoped to the locomotive
  // chosen in the header.
  const row = bookingRow(index)
  await selectEquipment(user, row, nodeLabel)
  await user.selectOptions(within(row).getByLabelText(/defect type/i), defectTypeLabel)
  await user.type(within(row).getByLabelText(/remarks/i), remarks)
}

async function fillHeader(user: ReturnType<typeof userEvent.setup>) {
  await selectLocomotive(user, '39126')
  await user.selectOptions(screen.getByLabelText(/^schedule$/i), 'IA')
  await user.selectOptions(screen.getByLabelText(/arrival condition/i), 'WORKING')
  await user.clear(screen.getByLabelText(/actual arrival date\/time/i))
  await user.type(screen.getByLabelText(/actual arrival date\/time/i), '2026-08-31T09:35')
}

describe('ShedMovementPage', () => {
  it('is a protected route', async () => {
    renderWithProviders(<App />, { route: '/shed-movement' })
    await waitFor(() => expect(screen.getByLabelText(/employee id/i)).toBeInTheDocument())
  })

  it('shows an empty current-shed message when nothing is in shed', async () => {
    loginAsToken('token-admin')
    renderWithProviders(<ShedMovementPage />)

    await screen.findByText(/no locomotives are currently in the shed/i)
  })

  it('locomotive search returns matching results only', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    await user.type(screen.getByLabelText(/^locomotive$/i), '391')
    const results = await screen.findByLabelText('Locomotive results')
    expect(within(results).getByText('39126')).toBeInTheDocument()
    expect(within(results).getByText('39127')).toBeInTheDocument()
  })

  it('selecting a locomotive shows it and allows changing it', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    await selectLocomotive(user, '39126')
    expect(screen.getByText('39126')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /change/i }))
    expect(screen.getByLabelText(/^locomotive$/i)).toBeInTheDocument()
  })

  it('supports schedule and arrival condition selection, and arrival datetime entry', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    await user.selectOptions(screen.getByLabelText(/^schedule$/i), 'IB')
    await user.selectOptions(screen.getByLabelText(/arrival condition/i), 'DEAD')
    await user.clear(screen.getByLabelText(/actual arrival date\/time/i))
  await user.type(screen.getByLabelText(/actual arrival date\/time/i), '2026-08-31T10:00')

    expect(screen.getByLabelText(/^schedule$/i)).toHaveValue('IB')
    expect(screen.getByLabelText(/arrival condition/i)).toHaveValue('DEAD')
    expect(screen.getByLabelText(/actual arrival date\/time/i)).toHaveValue('2026-08-31T10:00')
  })

  it('adds and removes Log Book booking rows', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    expect(screen.getByText(/no log book bookings added yet/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))
    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))
    expect(screen.getByRole('heading', { name: 'Booking #1' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Booking #2' })).toBeInTheDocument()

    const firstRow = bookingRow(0)
    await user.click(within(firstRow).getByRole('button', { name: /remove/i }))

    expect(screen.queryByRole('heading', { name: 'Booking #2' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Booking #1' })).toBeInTheDocument()
  })

  it('loads defect types into each booking row', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))

    const row = bookingRow(0)
    const select = await within(row).findByLabelText(/defect type/i)
    expect(within(select).getByText('Defective')).toBeInTheDocument()
    expect(within(select).getByText('Broken')).toBeInTheDocument()
    expect(within(select).getByText('Isolated')).toBeInTheDocument()
  })

  it('selecting mapped equipment shows a routing preview', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await selectLocomotive(user, '39126')
    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))

    const row = bookingRow(0)
    await selectEquipment(user, row, 'Contactor')

    await within(row).findByText(/routes to:/i)
    expect(within(row).getByText('M1-HR, M2-HR')).toBeInTheDocument()
  })

  it('selecting unmapped equipment shows an unmapped routing warning', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await selectLocomotive(user, '39126')
    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))

    const row = bookingRow(0)
    await selectEquipment(user, row, 'Standalone Part')

    await within(row).findByText(/no section mapping exists/i)
  })

  it('shows a validation summary when required fields are missing', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    // Arrival now defaults to the current local time, so it must be explicitly cleared for the
    // "missing arrival" branch to be exercised at all.
    await user.clear(screen.getByLabelText(/actual arrival date\/time/i))
    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    expect(await screen.findByText('Select a locomotive.')).toBeInTheDocument()
    expect(screen.getByText('Select a schedule (IA, IA0, IB, IC, IC0, IOH, or TOH).')).toBeInTheDocument()
    expect(screen.getByText('Select the arrival condition.')).toBeInTheDocument()
    expect(screen.getByText('Enter the actual arrival date/time.')).toBeInTheDocument()
  })

  it('Common Booking Pool reform: unmapped equipment no longer rejects booking creation', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await fillHeader(user)
    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))
    await fillBooking(user, 0, {
      nodeLabel: 'Standalone Part',
      defectTypeLabel: 'Defective',
      remarks: 'unmapped part',
    })

    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await waitFor(() => expect(screen.getByText(/1 booking\(s\)/i)).toBeInTheDocument())
    expect(screen.queryByText(/no responsible section mapping exists/i)).not.toBeInTheDocument()
  })

  it('submits successfully with multiple bookings and refreshes the current list', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await fillHeader(user)

    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))
    await fillBooking(user, 0, {
      nodeLabel: 'Contactor',
      defectTypeLabel: 'Defective',
      remarks: 'first booking',
    })

    await user.click(screen.getByRole('button', { name: /\+ add booking/i }))
    await fillBooking(user, 1, {
      nodeLabel: 'Auxiliary Converter',
      defectTypeLabel: 'Broken',
      remarks: 'second booking',
    })

    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await screen.findByText(/shed in recorded for 39126/i)

    // Form panel closes and the current-shed table refreshes with the new visit.
    await waitFor(() => expect(screen.queryByLabelText(/^locomotive$/i)).not.toBeInTheDocument())
    const table = await screen.findByRole('table')
    expect(within(table).getByText('39126')).toBeInTheDocument()
    // The table now shows the OPERATIONAL PHASE rather than the raw physical status: a
    // freshly-shedded-in locomotive is Spare until its schedule is started, and the only action
    // offered is Start Schedule.
    expect(within(table).getByText('Spare IA')).toBeInTheDocument()
    expect(within(table).getByRole('button', { name: 'Start Schedule' })).toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: 'Complete Schedule' })).not.toBeInTheDocument()
  })

  it('offers IA/IA0/IB/IC/IC0/IOH/TOH as plain schedule labels, with no MINOR/MAJOR prefix', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    const select = screen.getByLabelText(/^schedule$/i) as HTMLSelectElement
    const optionLabels = Array.from(select.options).map((o) => o.textContent)
    expect(optionLabels).toEqual(['-- Select schedule --', 'IA', 'IA0', 'IB', 'IC', 'IC0', 'IOH', 'TOH'])
  })

  it.each(['IA0', 'IC0'])(
    'selecting %s sends schedule_family MINOR with that exact variant (never normalized to IA/IC)',
    async (variant) => {
      let capturedBody: { schedule_family: string; schedule_variant: string } | null = null
      server.events.on('request:start', async ({ request }) => {
        if (request.method === 'POST' && request.url.endsWith('/api/shed-visits/in')) {
          capturedBody = (await request.clone().json()) as { schedule_family: string; schedule_variant: string }
        }
      })

      loginAsToken('token-admin')
      const user = userEvent.setup()
      renderWithProviders(<ShedMovementPage />)
      await openForm(user)

      await selectLocomotive(user, '39126')
      await user.selectOptions(screen.getByLabelText(/^schedule$/i), variant)
      await user.selectOptions(screen.getByLabelText(/arrival condition/i), 'WORKING')
      await user.clear(screen.getByLabelText(/actual arrival date\/time/i))
      await user.type(screen.getByLabelText(/actual arrival date\/time/i), '2026-08-31T09:35')

      await user.click(screen.getByRole('button', { name: 'Shed In' }))

      await screen.findByText(/shed in recorded for 39126/i)
      expect(capturedBody).toEqual(expect.objectContaining({ schedule_family: 'MINOR', schedule_variant: variant }))

      const table = await screen.findByRole('table')
      expect(within(table).getAllByText(variant, { exact: false }).length).toBeGreaterThan(0)
      expect(within(table).queryByText(/MINOR/)).not.toBeInTheDocument()
    },
  )

  it('selecting IOH sends schedule_family MAJOR / schedule_variant IOH, and displays just "IOH"', async () => {
    // Capture the real request body the mock backend receives (via the
    // default /api/shed-visits/in handler, which echoes schedule_family /
    // schedule_variant straight through and only creates stages for
    // MINOR) so the fixture store stays consistent for the table refresh
    // that follows.
    let capturedBody: { schedule_family: string; schedule_variant: string } | null = null
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST' && request.url.endsWith('/api/shed-visits/in')) {
        capturedBody = (await request.clone().json()) as { schedule_family: string; schedule_variant: string }
      }
    })

    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    await selectLocomotive(user, '39126')
    await user.selectOptions(screen.getByLabelText(/^schedule$/i), 'IOH')
    await user.selectOptions(screen.getByLabelText(/arrival condition/i), 'WORKING')
    await user.clear(screen.getByLabelText(/actual arrival date\/time/i))
  await user.type(screen.getByLabelText(/actual arrival date\/time/i), '2026-08-31T09:35')

    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    // stages_created is 0 for MAJOR (the mock only creates 4 for MINOR),
    // which corroborates the family sent was MAJOR, not MINOR.
    await screen.findByText(/shed in recorded for 39126: 0 booking\(s\), 0 stage\(s\) created\./i)
    expect(capturedBody).toMatchObject({ schedule_family: 'MAJOR', schedule_variant: 'IOH' })

    const table = await screen.findByRole('table')
    expect(within(table).getByText('IOH')).toBeInTheDocument()
    expect(within(table).queryByText(/MAJOR/i)).not.toBeInTheDocument()
  })

  it('selecting TOH sends schedule_family MAJOR / schedule_variant TOH', async () => {
    let capturedBody: { schedule_family: string; schedule_variant: string } | null = null
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST' && request.url.endsWith('/api/shed-visits/in')) {
        capturedBody = (await request.clone().json()) as { schedule_family: string; schedule_variant: string }
      }
    })

    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)

    await selectLocomotive(user, '39126')
    await user.selectOptions(screen.getByLabelText(/^schedule$/i), 'TOH')
    await user.selectOptions(screen.getByLabelText(/arrival condition/i), 'WORKING')
    await user.clear(screen.getByLabelText(/actual arrival date\/time/i))
  await user.type(screen.getByLabelText(/actual arrival date\/time/i), '2026-08-31T09:35')

    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await screen.findByText(/shed in recorded for 39126: 0 booking\(s\), 0 stage\(s\) created\./i)
    expect(capturedBody).toMatchObject({ schedule_family: 'MAJOR', schedule_variant: 'TOH' })

    const table = await screen.findByRole('table')
    expect(within(table).getByText('TOH')).toBeInTheDocument()
    expect(within(table).queryByText(/MAJOR/i)).not.toBeInTheDocument()
  })

  it('a MINOR/IA visit (the existing, unchanged workflow) still renders with just "IA", no MINOR prefix', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await fillHeader(user) // selects 'IA' via the header helper above

    await user.click(screen.getByRole('button', { name: 'Shed In' }))
    await screen.findByText(/shed in recorded for 39126/i)

    const table = await screen.findByRole('table')
    const row = within(table).getByText('39126').closest('tr') as HTMLElement
    expect(within(row).getByText('IA')).toBeInTheDocument()
    expect(within(row).queryByText(/MINOR/i)).not.toBeInTheDocument()
  })

  it('shows a friendly message when the backend rejects with a duplicate open visit', async () => {
    server.use(
      http.post('/api/shed-visits/in', () =>
        HttpResponse.json(
          { detail: { code: 'LOCO_ALREADY_IN_SHED', message: '39126 already has an open shed visit.' } },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await fillHeader(user)

    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await screen.findByText('39126 already has an open shed visit.')
  })
})
