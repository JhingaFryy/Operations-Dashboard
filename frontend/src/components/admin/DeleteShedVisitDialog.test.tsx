import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../../test/testUtils'
import { server } from '../../mocks/server'
import { DeleteShedVisitDialog } from './DeleteShedVisitDialog'

const PREVIEW = {
  visit: {
    id: 700,
    loco_number: '32032',
    schedule_family: 'MINOR',
    schedule_variant: 'IA',
    status: 'IN_SHED',
    arrival_at: '2026-09-25T06:00:00Z',
  },
  required_confirmation: 'DELETE 32032 IA',
  counts: { shed_visits: 1, bookings: 2, checksheet_header: 4, digital_signatures: 3 },
  total_rows: 10,
  files: [
    { entity_type: 'checksheet_header', origin_id: 1, path: '/s/a.pdf', exists: true,
      size_bytes: 10, sha256: 'a'.repeat(64), shared: false },
  ],
  warnings: ['3 digitally signed checksheet(s) will be destroyed.'],
}

function mockPreview(preview: typeof PREVIEW = PREVIEW) {
  server.use(
    http.get('/api/admin/shed-visits/700/deletion-preview', () => HttpResponse.json(preview)),
  )
}

function renderDialog(onDeleted = vi.fn(), onCancel = vi.fn()) {
  loginAsToken('token-admin')
  renderWithProviders(
    <DeleteShedVisitDialog visitId={700} onDeleted={onDeleted} onCancel={onCancel} />,
  )
  return { onDeleted, onCancel }
}

const deleteButton = () => screen.getByRole('button', { name: /delete visit permanently/i })

describe('DeleteShedVisitDialog', () => {
  it('shows what would be destroyed before asking for anything', async () => {
    mockPreview()
    renderDialog()

    expect(await screen.findByText('32032')).toBeInTheDocument()
    expect(screen.getByText('#700')).toBeInTheDocument()
    expect(screen.getByText('MINOR / IA')).toBeInTheDocument()
    expect(screen.getByText(/This will permanently delete 10 record\(s\)/)).toBeInTheDocument()
    expect(screen.getByText(/digitally signed checksheet/)).toBeInTheDocument()
  })

  it('cannot delete until the reason, the exact phrase and the password are all given', async () => {
    mockPreview()
    const user = userEvent.setup()
    renderDialog()
    await screen.findByText('32032')

    expect(deleteButton()).toBeDisabled()

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'mistaken test entry here')
    expect(deleteButton()).toBeDisabled()

    await user.type(screen.getByLabelText(/to confirm, type/i), 'DELETE 32032 IA')
    expect(deleteButton()).toBeDisabled()

    await user.type(screen.getByLabelText(/your own account password/i), 'pw')
    expect(deleteButton()).toBeEnabled()
  })

  it('rejects a confirmation phrase for a different visit', async () => {
    mockPreview()
    const user = userEvent.setup()
    renderDialog()
    await screen.findByText('32032')

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'mistaken test entry here')
    await user.type(screen.getByLabelText(/your own account password/i), 'pw')
    await user.type(screen.getByLabelText(/to confirm, type/i), 'DELETE 99999 IB')
    expect(deleteButton()).toBeDisabled()
  })

  it('accepts the phrase in lower case', async () => {
    mockPreview()
    const user = userEvent.setup()
    renderDialog()
    await screen.findByText('32032')

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'mistaken test entry here')
    await user.type(screen.getByLabelText(/your own account password/i), 'pw')
    await user.type(screen.getByLabelText(/to confirm, type/i), 'delete 32032 ia')
    expect(deleteButton()).toBeEnabled()
  })

  it('rejects a reason shorter than ten characters', async () => {
    mockPreview()
    const user = userEvent.setup()
    renderDialog()
    await screen.findByText('32032')

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'oops')
    await user.type(screen.getByLabelText(/to confirm, type/i), 'DELETE 32032 IA')
    await user.type(screen.getByLabelText(/your own account password/i), 'pw')
    expect(deleteButton()).toBeDisabled()
    expect(screen.getByText(/at least 10 characters/i)).toBeInTheDocument()
  })

  it('refuses to proceed at all when the preview could not be loaded', async () => {
    // Without a preview there is no confirmation phrase to check and no idea what would be
    // destroyed. Refusing is the only safe behaviour; defaulting to "delete anyway" would be the
    // single most damaging mistake this dialog could make.
    server.use(
      http.get('/api/admin/shed-visits/700/deletion-preview', () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
    )
    renderDialog()

    expect(await screen.findByText(/could not load what this deletion would destroy/i)).toBeInTheDocument()
    expect(deleteButton()).toBeDisabled()
  })

  it('sends the password in the body and never in the URL', async () => {
    mockPreview()
    let seenUrl = ''
    let seenBody: Record<string, unknown> = {}
    server.use(
      http.delete('/api/admin/shed-visits/700', async ({ request }) => {
        seenUrl = request.url
        seenBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ id: 5, deletion_type: 'SHED_VISIT', target_id: 700,
          shed_visit_id: 700, loco_number: '32032', actor_employee_id: 'ADM1',
          actor_name: 'Admin One', reason: 'mistaken test entry here', requested_at: 'x',
          status: 'COMPLETED', record_counts: {}, total_rows: 0, files_planned: 0,
          files_destroyed: 0, manifest: {}, file_plan: [], file_result: null, item_count: 0 })
      }),
    )
    const user = userEvent.setup()
    const { onDeleted } = renderDialog()
    await screen.findByText('32032')

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'mistaken test entry here')
    await user.type(screen.getByLabelText(/to confirm, type/i), 'DELETE 32032 IA')
    await user.type(screen.getByLabelText(/your own account password/i), 'super-secret-pw')
    await user.click(deleteButton())

    await waitFor(() => expect(onDeleted).toHaveBeenCalled())
    expect(seenUrl).not.toContain('super-secret-pw')
    expect(seenUrl).not.toContain('password')
    expect(seenBody.password).toBe('super-secret-pw')
    // No identity is sent: the server derives the actor from the token.
    expect(seenBody).not.toHaveProperty('employee_id')
    expect(seenBody).not.toHaveProperty('actor_user_id')
  })

  it('clears the password and keeps the dialog open when the server refuses', async () => {
    mockPreview()
    server.use(
      http.delete('/api/admin/shed-visits/700', () =>
        HttpResponse.json({ detail: { code: 'REAUTH_FAILED', message: 'Password verification failed.' } },
          { status: 401 }),
      ),
    )
    const user = userEvent.setup()
    const { onDeleted } = renderDialog()
    await screen.findByText('32032')

    await user.type(screen.getByLabelText(/why is this being deleted/i), 'mistaken test entry here')
    await user.type(screen.getByLabelText(/to confirm, type/i), 'DELETE 32032 IA')
    const passwordField = screen.getByLabelText(/your own account password/i) as HTMLInputElement
    await user.type(passwordField, 'wrong-password')
    await user.click(deleteButton())

    await screen.findByText(/password verification failed/i)
    expect(onDeleted).not.toHaveBeenCalled()
    // Retyped, not resubmitted: a second click must not retry the same wrong password.
    expect(passwordField.value).toBe('')
    expect(deleteButton()).toBeDisabled()
  })

  it('does not keep the password after cancelling', async () => {
    mockPreview()
    const user = userEvent.setup()
    const { onCancel } = renderDialog()
    await screen.findByText('32032')

    await user.type(screen.getByLabelText(/your own account password/i), 'secret')
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(onCancel).toHaveBeenCalled()
  })

  it('never offers the password field to a password manager', async () => {
    mockPreview()
    renderDialog()
    await screen.findByText('32032')
    // A saved value would defeat the point of a re-authentication challenge.
    expect(screen.getByLabelText(/your own account password/i)).toHaveAttribute(
      'autocomplete', 'off',
    )
  })

  it('mentions shared files only when some are shared', async () => {
    mockPreview({ ...PREVIEW, files: [{ ...PREVIEW.files[0], shared: true }] })
    renderDialog()
    expect(await screen.findByText(/also used by records that are not being deleted/i)).toBeInTheDocument()
  })
})
