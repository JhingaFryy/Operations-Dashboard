import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../../test/testUtils'
import { server } from '../../mocks/server'
import { DeleteBookingDialog } from './DeleteBookingDialog'

const PREVIEW = {
  booking: {
    id: 900,
    description: 'Pantograph horn fault',
    status: 'OPEN',
    booking_source: 'LOG_BOOK',
    equipment_node_id: 1843,
  },
  visit: { id: 700, loco_number: '32032', schedule_family: 'MINOR', schedule_variant: 'IA',
           status: 'IN_SHED', arrival_at: null },
  counts: { bookings: 1, booking_section_assignments: 2, booking_events: 3 },
  total_rows: 6,
  unaffected: { shed_visit: true, other_bookings_on_this_visit: 4, checksheets: true },
  files: [],
  warnings: [],
}

function mockPreview(preview: typeof PREVIEW = PREVIEW) {
  server.use(
    http.get('/api/admin/bookings/900/deletion-preview', () => HttpResponse.json(preview)),
  )
}

const deleteButton = () => screen.getByRole('button', { name: /delete booking permanently/i })

function renderDialog(onDeleted = vi.fn(), onCancel = vi.fn()) {
  loginAsToken('token-admin')
  renderWithProviders(
    <DeleteBookingDialog bookingId={900} onDeleted={onDeleted} onCancel={onCancel} />,
  )
  return { onDeleted, onCancel }
}

describe('DeleteBookingDialog', () => {
  it('names the booking and says what survives', async () => {
    mockPreview()
    renderDialog()

    expect(await screen.findByText('Pantograph horn fault')).toBeInTheDocument()
    expect(screen.getByText(/This will permanently delete 6 record\(s\)/)).toBeInTheDocument()
    // The actual sibling count from the server, not a vague reassurance.
    expect(screen.getByText(/its 4 other booking\(s\) are not affected/)).toBeInTheDocument()
  })

  it('asks for no confirmation phrase, only a reason and the password', async () => {
    // A booking is one row with a visible description, so there is no ambiguity for a phrase to
    // resolve. Demanding one anyway would train people to type past it.
    mockPreview()
    const user = userEvent.setup()
    renderDialog()
    await screen.findByText('Pantograph horn fault')

    expect(screen.queryByLabelText(/to confirm, type/i)).toBeNull()
    expect(deleteButton()).toBeDisabled()

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'duplicate raised twice')
    expect(deleteButton()).toBeDisabled()
    await user.type(screen.getByLabelText(/your own account password/i), 'pw')
    expect(deleteButton()).toBeEnabled()
  })

  it('refuses to proceed when the preview could not be loaded', async () => {
    server.use(
      http.get('/api/admin/bookings/900/deletion-preview', () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
    )
    renderDialog()
    expect(await screen.findByText(/could not load what this deletion would destroy/i)).toBeInTheDocument()
    expect(deleteButton()).toBeDisabled()
  })

  it('sends the password in the body, with no identity', async () => {
    mockPreview()
    let seenUrl = ''
    let seenBody: Record<string, unknown> = {}
    server.use(
      http.delete('/api/admin/bookings/900', async ({ request }) => {
        seenUrl = request.url
        seenBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ id: 9, deletion_type: 'BOOKING', target_id: 900,
          shed_visit_id: 700, loco_number: '32032', actor_employee_id: 'ADM1',
          actor_name: 'Admin One', reason: 'duplicate raised twice', requested_at: 'x',
          status: 'COMPLETED', record_counts: {}, total_rows: 0, files_planned: 0,
          files_destroyed: 0, manifest: {}, file_plan: [], file_result: null, item_count: 0 })
      }),
    )
    const user = userEvent.setup()
    const { onDeleted } = renderDialog()
    await screen.findByText('Pantograph horn fault')

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'duplicate raised twice')
    await user.type(screen.getByLabelText(/your own account password/i), 'hunter2-secret')
    await user.click(deleteButton())

    await waitFor(() => expect(onDeleted).toHaveBeenCalled())
    expect(seenUrl).not.toContain('hunter2-secret')
    expect(seenBody.password).toBe('hunter2-secret')
    expect(seenBody).not.toHaveProperty('employee_id')
  })

  it('clears the password when the server refuses', async () => {
    mockPreview()
    server.use(
      http.delete('/api/admin/bookings/900', () =>
        HttpResponse.json({ detail: { code: 'REAUTH_FAILED', message: 'Password verification failed.' } },
          { status: 401 }),
      ),
    )
    const user = userEvent.setup()
    const { onDeleted } = renderDialog()
    await screen.findByText('Pantograph horn fault')

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'duplicate raised twice')
    const field = screen.getByLabelText(/your own account password/i) as HTMLInputElement
    await user.type(field, 'wrong')
    await user.click(deleteButton())

    await screen.findByText(/password verification failed/i)
    expect(onDeleted).not.toHaveBeenCalled()
    expect(field.value).toBe('')
  })
})
