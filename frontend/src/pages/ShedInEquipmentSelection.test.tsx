import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { bookingRow, selectEquipment, selectedEquipmentName } from '../test/equipmentSearch'
import { ShedMovementPage } from './ShedMovementPage'

/**
 * The Log Book booking form's equipment field.
 *
 * THE MODEL UNDER TEST: one search box, one selected value, and no dropdown. The cascading
 * "Equipment / Level 2 / …" selects that used to sit beside the search box are gone from
 * booking forms, because they were a SECOND source of truth for the same value — a search
 * result set a selection the dropdown never showed, the dropdown silently overwrote a
 * searched choice, and clearing the dropdown left the chosen node id behind so the form
 * submitted equipment the user could see they had cleared. Several tests below pin each of
 * those three failures shut.
 *
 * Equipment Responsibility Mapping still browses the hierarchy and keeps HierarchyBrowser —
 * it administers the tree rather than booking against a locomotive.
 *
 * Requests are asserted against the wire, because "the field looked right" is not the same
 * as "we asked the server for the right family".
 */

function requestedUrls(): string[] {
  return captured
}

let captured: string[] = []
server.events.on('request:start', ({ request }) => {
  captured.push(request.url)
})

async function openForm(user: ReturnType<typeof userEvent.setup>) {
  captured = []
  await user.click(await screen.findByRole('button', { name: /shed in/i }))
}

async function selectLocomotive(user: ReturnType<typeof userEvent.setup>, locoNumber: string) {
  const change = screen.queryByRole('button', { name: /^change$/i })
  if (change) await user.click(change) // a locomotive is already chosen; swap it
  const input = screen.getByLabelText(/^locomotive$/i)
  await user.clear(input)
  await user.type(input, locoNumber.slice(0, 3))
  const results = await screen.findByLabelText('Locomotive results')
  await user.click(within(results).getByText(locoNumber))
}

async function addBooking(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /\+ add booking/i }))
}

async function setUp(
  user: ReturnType<typeof userEvent.setup>,
  { locoNumber = '39126', token = 'token-admin' } = {},
) {
  loginAsToken(token)
  renderWithProviders(<ShedMovementPage />)
  await openForm(user)
  await selectLocomotive(user, locoNumber)
  await addBooking(user)
  return bookingRow(0)
}

async function fillRestOfHeader(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText(/^schedule$/i), 'IA')
  await user.selectOptions(screen.getByLabelText(/arrival condition/i), 'WORKING')
}

function captureShedIn() {
  const box: { body: { log_book_bookings: { equipment_node_id: number }[] } | null } = {
    body: null,
  }
  server.events.on('request:start', async ({ request }) => {
    if (request.method === 'POST' && request.url.endsWith('/api/shed-visits/in')) {
      box.body = await request.clone().json()
    }
  })
  return box
}

// ------------------------------------------------------------- the selector --

describe('Log Book booking: equipment selection', () => {
  it('1. offers no equipment dropdown at all', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)

    // The field labelled Equipment is a search input, not a <select>, and none of the
    // cascading level selects exist any more.
    const field = await within(row).findByLabelText(/^equipment$/i)
    expect(field.tagName).toBe('INPUT')
    expect(field).toHaveAttribute('type', 'search')
    expect(within(row).queryByRole('combobox', { name: /level 2/i })).toBeNull()
    expect(within(row).queryByLabelText('Level 2')).toBeNull()
    expect(within(row).queryByLabelText('Level 3')).toBeNull()
    expect(row.querySelectorAll('select')).toHaveLength(1) // defect type only
  })

  it('2. typing shows matching equipment', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)

    await user.type(within(row).getByLabelText(/^equipment$/i), 'converter')

    const results = await within(row).findByRole('listbox', { name: /equipment results/i })
    const names = within(results)
      .getAllByRole('option')
      .map((o) => o.querySelector('.search-result-name')?.textContent)
    expect(names).toContain('Auxiliary Converter')
    expect(names).toContain('Traction Converter')
  })

  it('3. clicking a result selects the real equipment id, and submits it', async () => {
    const box = captureShedIn()
    const user = userEvent.setup()
    const row = await setUp(user)

    await selectEquipment(user, row, 'Auxiliary Converter')
    await fillRestOfHeader(user)
    await user.selectOptions(within(row).getByLabelText(/defect type/i), 'Defective')
    await user.type(within(row).getByLabelText(/remarks/i), 'noisy')
    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await waitFor(() => expect(box.body).not.toBeNull())
    expect(box.body!.log_book_bookings[0].equipment_node_id).toBe(100) // Auxiliary Converter
  })

  it('4. the selected equipment is visible in the field itself', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)

    await selectEquipment(user, row, 'Auxiliary Converter')

    // The old failure: the selection existed but the field labelled Equipment stayed
    // empty, so the user believed nothing had been selected.
    expect(selectedEquipmentName(row)).toBe('Auxiliary Converter')
    expect(within(row).queryByRole('listbox')).toBeNull()
  })

  it('5. clearing the selection clears the id, and the form then refuses to submit', async () => {
    const box = captureShedIn()
    const user = userEvent.setup()
    const row = await setUp(user)
    await selectEquipment(user, row, 'Auxiliary Converter')

    await user.click(within(row).getByRole('button', { name: /clear selected equipment/i }))

    expect(selectedEquipmentName(row)).toBeNull()
    await fillRestOfHeader(user)
    await user.selectOptions(within(row).getByLabelText(/defect type/i), 'Defective')
    await user.type(within(row).getByLabelText(/remarks/i), 'noisy')
    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    expect(await screen.findByText('Booking #1: select equipment.')).toBeInTheDocument()
    expect(box.body).toBeNull() // nothing was sent with a stale id
  })

  it('6. editing the text after choosing abandons the selection - no stale hidden id', async () => {
    const box = captureShedIn()
    const user = userEvent.setup()
    const row = await setUp(user)
    await selectEquipment(user, row, 'Auxiliary Converter')

    // Clear, then type something that matches nothing, and try to submit anyway.
    await user.click(within(row).getByRole('button', { name: /clear selected equipment/i }))
    await user.type(within(row).getByLabelText(/^equipment$/i), 'Auxiliary Conv')
    await fillRestOfHeader(user)
    await user.selectOptions(within(row).getByLabelText(/defect type/i), 'Defective')
    await user.type(within(row).getByLabelText(/remarks/i), 'noisy')
    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    // Free text is never a selection.
    expect(await screen.findByText('Booking #1: select equipment.')).toBeInTheDocument()
    expect(box.body).toBeNull()
  })

  it('7. keyboard: arrow down then Enter selects, and Escape closes the list', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)
    const input = within(row).getByLabelText(/^equipment$/i)

    await user.type(input, 'auxiliary converter')
    await within(row).findByRole('listbox', { name: /equipment results/i })
    await user.keyboard('{Escape}')
    expect(within(row).queryByRole('listbox')).toBeNull()

    await user.clear(input)
    await user.type(input, 'auxiliary converter')
    await within(row).findByRole('listbox', { name: /equipment results/i })
    await user.keyboard('{ArrowDown}{Enter}')

    expect(selectedEquipmentName(row)).toBe('Auxiliary Converter')
  })

  it('8. each booking row keeps its own equipment, with no bleed between rows', async () => {
    const box = captureShedIn()
    const user = userEvent.setup()
    await setUp(user)
    await addBooking(user)

    await selectEquipment(user, bookingRow(0), 'Auxiliary Converter')
    await selectEquipment(user, bookingRow(1), 'Traction Converter')

    expect(selectedEquipmentName(bookingRow(0))).toBe('Auxiliary Converter')
    expect(selectedEquipmentName(bookingRow(1))).toBe('Traction Converter')

    await fillRestOfHeader(user)
    for (const i of [0, 1]) {
      const row = bookingRow(i)
      await user.selectOptions(within(row).getByLabelText(/defect type/i), 'Defective')
      await user.type(within(row).getByLabelText(/remarks/i), `row ${i}`)
    }
    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await waitFor(() => expect(box.body).not.toBeNull())
    const ids = box.body!.log_book_bookings.map((b) => b.equipment_node_id)
    expect(ids).toEqual([100, 300]) // Auxiliary Converter, Traction Converter
    expect(new Set(ids).size).toBe(2)
  })

  it('9. says so plainly when nothing matches', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)

    await user.type(within(row).getByLabelText(/^equipment$/i), 'zzzz')

    expect(await within(row).findByText('No equipment found')).toBeInTheDocument()
    expect(within(row).queryByRole('listbox')).toBeNull()
  })

  it('10. search stays inside the locomotive’s own family', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)

    await user.type(within(row).getByLabelText(/^equipment$/i), 'auxiliary converter')
    const results = await within(row).findByRole('listbox', { name: /equipment results/i })

    const names = within(results)
      .getAllByRole('option')
      .map((o) => o.querySelector('.search-result-name')?.textContent)
    expect(names).toEqual(['Auxiliary Converter']) // the conventional namesake is not offered
    const searches = requestedUrls().filter((u) => u.includes('/nodes/search'))
    expect(searches.length).toBeGreaterThan(0)
    expect(searches.every((u) => u.includes('loco_number=39126') && !u.includes('family='))).toBe(
      true,
    )
  })

  it('11. the equipment field waits for a locomotive to be chosen', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)
    await openForm(user)
    await addBooking(user)

    const row = bookingRow(0)
    expect(within(row).getByText(/select the locomotive first/i)).toBeInTheDocument()
    expect(within(row).queryByLabelText(/^equipment$/i)).toBeNull()
    expect(requestedUrls().some((u) => u.includes('/api/equipment/nodes'))).toBe(false)
  })

  it('12. changing the locomotive discards equipment chosen for the previous one', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)
    await selectEquipment(user, row, 'Auxiliary Converter')

    await selectLocomotive(user, '22345')

    await waitFor(() => expect(selectedEquipmentName(bookingRow(0))).toBeNull())
  })
})

// ------------------------------- equipment creation is gone from this form --
//
// "+ Add Equipment" used to appear here on a dead-end search, for Admin and SHIFT. It is
// withdrawn: creating equipment is master-data administration, it is Admin-only, and it lives
// on the Equipment Responsibility Mapping page. The removal is not cosmetic - POST
// /api/equipment/nodes was deleted server-side, so there is no endpoint left for this form to
// call. Search and selection are untouched, which the block above covers.

describe('Log Book booking: equipment creation is not available here', () => {
  it('33. offers no Add Equipment when a search finds nothing - for anyone', async () => {
    for (const token of ['token-admin', 'token-sup-shift']) {
      const user = userEvent.setup()
      const row = await setUp(user, { token })

      await user.type(within(row).getByLabelText(/^equipment$/i), 'zzzz')

      expect(await within(row).findByText('No equipment found')).toBeInTheDocument()
      expect(within(row).queryByRole('button', { name: /add equipment/i })).toBeNull()
      cleanup()
    }
  })

  it('33b. the whole form offers no creation control at any point', async () => {
    const user = userEvent.setup()
    const row = await setUp(user, { token: 'token-sup-shift' })

    // Empty, matching, and matching-nothing - none of them produces a create affordance.
    expect(within(row).queryByRole('button', { name: /add equipment/i })).toBeNull()
    await user.type(within(row).getByLabelText(/^equipment$/i), 'converter')
    await within(row).findByRole('listbox', { name: /equipment results/i })
    expect(within(row).queryByRole('button', { name: /add equipment/i })).toBeNull()
  })

  it('34. search and selection still work for a SHIFT Supervisor', async () => {
    const box = captureShedIn()
    const user = userEvent.setup()
    const row = await setUp(user, { token: 'token-sup-shift' })

    await selectEquipment(user, row, 'Auxiliary Converter')
    expect(selectedEquipmentName(row)).toBe('Auxiliary Converter')

    await fillRestOfHeader(user)
    await user.selectOptions(within(row).getByLabelText(/defect type/i), 'Defective')
    await user.type(within(row).getByLabelText(/remarks/i), 'noisy')
    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await waitFor(() => expect(box.body).not.toBeNull())
    expect(box.body!.log_book_bookings[0].equipment_node_id).toBe(100)
  })

  it('21. a failed search does not take the Shed In form with it', async () => {
    server.use(
      http.get('/api/equipment/nodes/search', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )
    const user = userEvent.setup()
    const row = await setUp(user)
    await user.type(within(row).getByLabelText(/remarks/i), 'still here')

    await user.type(within(row).getByLabelText(/^equipment$/i), 'converter')

    expect(await within(row).findByRole('alert')).toBeInTheDocument()
    expect(within(row).getByLabelText(/remarks/i)).toHaveValue('still here')
    expect(screen.getByRole('button', { name: 'Shed In' })).toBeInTheDocument()
  })

  it('22. a defect type is chosen from the server list, never typed', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)

    const defect = await within(row).findByLabelText(/defect type/i)
    expect(defect.tagName).toBe('SELECT')
    expect(Array.from((defect as HTMLSelectElement).options).map((o) => o.textContent)).toEqual([
      '-- Select defect type --',
      'Defective',
      'Broken',
      'Isolated',
    ])
  })

  it('23. equipment, defect type and non-blank remarks are all still required', async () => {
    const user = userEvent.setup()
    const row = await setUp(user)
    await fillRestOfHeader(user)
    await user.type(within(row).getByLabelText(/remarks/i), '    ')

    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    expect(await screen.findByText('Booking #1: select equipment.')).toBeInTheDocument()
    expect(screen.getByText('Booking #1: select a defect type.')).toBeInTheDocument()
    expect(screen.getByText('Booking #1: remarks cannot be blank.')).toBeInTheDocument()
  })

  it('24. the submitted payload still names no technology and no family', async () => {
    const box = captureShedIn()
    const user = userEvent.setup()
    const row = await setUp(user)
    await selectEquipment(user, row, 'Auxiliary Converter')
    await fillRestOfHeader(user)
    await user.selectOptions(within(row).getByLabelText(/defect type/i), 'Defective')
    await user.type(within(row).getByLabelText(/remarks/i), 'noisy')

    await user.click(screen.getByRole('button', { name: 'Shed In' }))

    await waitFor(() => expect(box.body).not.toBeNull())
    expect(JSON.stringify(box.body)).not.toContain('technology')
    expect(JSON.stringify(box.body!.log_book_bookings)).not.toContain('family')
  })
})
