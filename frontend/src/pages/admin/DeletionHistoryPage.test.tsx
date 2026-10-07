import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../../test/testUtils'
import { server } from '../../mocks/server'
import { DeletionHistoryPage } from './DeletionHistoryPage'

function event(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    deletion_type: 'SHED_VISIT',
    target_id: 700,
    shed_visit_id: 700,
    loco_number: '32032',
    schedule_family: 'MINOR',
    schedule_variant: 'IA',
    visit_status: 'IN_SHED',
    actor_employee_id: 'ADM1',
    actor_name: 'Admin One',
    reason: 'test visit created while learning the system',
    requested_at: '2026-10-01T06:00:00Z',
    completed_at: '2026-10-01T06:00:04Z',
    status: 'COMPLETED',
    failure_reason: null,
    record_counts: { shed_visits: 1, bookings: 2, checksheet_header: 4 },
    total_rows: 7,
    manifest_hash: 'a'.repeat(64),
    files_planned: 3,
    files_destroyed: 3,
    files_completed_at: '2026-10-01T06:00:05Z',
    ...overrides,
  }
}

function mockHistory(rows: ReturnType<typeof event>[]) {
  server.use(http.get('/api/admin/deletion-history', () => HttpResponse.json(rows)))
}

describe('DeletionHistoryPage', () => {
  it('lists each deletion with who did it, why, and how much was destroyed', async () => {
    mockHistory([event()])
    loginAsToken('token-admin')
    renderWithProviders(<DeletionHistoryPage />)

    const row = (await screen.findByText('32032')).closest('tr') as HTMLElement
    expect(within(row).getByText(/Admin One/)).toBeInTheDocument()
    expect(within(row).getByText(/ADM1/)).toBeInTheDocument()
    expect(within(row).getByText('test visit created while learning the system')).toBeInTheDocument()
    expect(within(row).getByText('7')).toBeInTheDocument()
    expect(within(row).getByText('Completed')).toBeInTheDocument()
    expect(within(row).getByText('3 of 3')).toBeInTheDocument()
  })

  it('distinguishes a pending filesystem step from nothing having been destroyed', async () => {
    // files_destroyed null means the step has not reported yet - the database work committed and the
    // files are still on disk. That is a different fact from "0 of 3" and must not read the same.
    mockHistory([event({ files_destroyed: null, files_completed_at: null })])
    loginAsToken('token-admin')
    renderWithProviders(<DeletionHistoryPage />)

    expect(await screen.findByText('3 pending')).toBeInTheDocument()
    expect(screen.queryByText('0 of 3')).toBeNull()
  })

  it('shows a failed deletion with its reason', async () => {
    mockHistory([event({ status: 'FAILED', failure_reason: 'database error during delete',
                         completed_at: '2026-10-01T06:00:02Z' })])
    loginAsToken('token-admin')
    renderWithProviders(<DeletionHistoryPage />)

    expect(await screen.findByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('database error during delete')).toBeInTheDocument()
  })

  it('offers no restore action anywhere', async () => {
    mockHistory([event()])
    loginAsToken('token-admin')
    renderWithProviders(<DeletionHistoryPage />)
    await screen.findByText('32032')

    for (const label of [/restore/i, /undo/i, /recover/i, /undelete/i]) {
      expect(screen.queryByRole('button', { name: label })).toBeNull()
    }
  })

  it('opens the manifest and the individual snapshots on demand', async () => {
    mockHistory([event()])
    server.use(
      http.get('/api/admin/deletion-history/1/items', () =>
        HttpResponse.json({
          items: [
            {
              id: 10,
              entity_type: 'bookings',
              original_id: 900,
              original_key: null,
              snapshot: { id: 900, description: 'Pantograph horn fault' },
              content_hash: 'b'.repeat(64),
            },
          ],
          total: 1,
          offset: 0,
          limit: 200,
        }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<DeletionHistoryPage />)

    await user.click(await screen.findByRole('button', { name: 'Details' }))

    expect(await screen.findByText('a'.repeat(64))).toBeInTheDocument()
    // The snapshot's substance, which is what makes the record meaningful after the row is gone.
    expect(await screen.findByText(/Pantograph horn fault/)).toBeInTheDocument()
  })

  it('says plainly when nothing has been deleted', async () => {
    mockHistory([])
    loginAsToken('token-admin')
    renderWithProviders(<DeletionHistoryPage />)
    expect(await screen.findByText(/nothing has been deleted/i)).toBeInTheDocument()
  })

  it('surfaces a load failure with a retry rather than an empty table', async () => {
    server.use(
      http.get('/api/admin/deletion-history', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    )
    loginAsToken('token-admin')
    renderWithProviders(<DeletionHistoryPage />)
    await waitFor(() => expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument())
  })

  it('filters by type and locomotive through the API, not client-side', async () => {
    const seen: string[] = []
    server.use(
      http.get('/api/admin/deletion-history', ({ request }) => {
        seen.push(new URL(request.url).search)
        return HttpResponse.json([event()])
      }),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWithProviders(<DeletionHistoryPage />)
    await screen.findByText('32032')

    await user.selectOptions(screen.getByLabelText('Type'), 'BOOKING')
    await waitFor(() => expect(seen.some((s) => s.includes('deletion_type=BOOKING'))).toBe(true))

    await user.type(screen.getByLabelText('Locomotive'), '32032')
    await waitFor(() => expect(seen.some((s) => s.includes('loco_number=32032'))).toBe(true))
  })
})
