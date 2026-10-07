import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { addShedVisit } from '../mocks/fixtures'
import { setReadyBlocker } from '../mocks/handlers'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { ShedMovementPage } from './ShedMovementPage'

/**
 * Ready blocked by outstanding checksheets, as the shed sees it.
 *
 * The rule itself is the backend's (see backend tests/test_ready_checksheet_gate.py); what is
 * asserted here is that the operator is told what is outstanding instead of "that conflicts with
 * existing data", and that the page offers no way around it.
 */

const BLOCKER = {
  code: 'CHECKSHEETS_INCOMPLETE',
  message: 'Ready blocked: 4 required checksheets are still outstanding.',
  work_package_generated: true,
  checksheets_readable: true,
  total_required: 12,
  satisfied: 8,
  outstanding: 4,
  optional: 2,
  deactivated: 1,
  outstanding_entries: [
    { requirement_id: 1, section: 'M1-HR', label: 'Pantograph PT-1', template_name: 'PT-1 Performa',
      stage: 'SCHEDULE_INSPECTION', maintenance_type: null, status: null, reason: 'MISSING' },
    { requirement_id: 2, section: 'M4-HR', label: 'Compressor', template_name: 'CP Performa',
      stage: 'SCHEDULE_INSPECTION', maintenance_type: null, status: 'DRAFT', reason: 'DRAFT' },
    { requirement_id: 3, section: 'M35-TM', label: 'Traction Motor 3', template_name: 'TM Overhaul',
      stage: null, maintenance_type: 'OVERHAUL', status: 'REJECTED', reason: 'REJECTED' },
    { requirement_id: 4, section: 'M2-HR', label: null, template_name: 'Test After Performa',
      stage: 'TEST_AFTER', maintenance_type: null, status: null, reason: 'MISSING' },
  ],
  outstanding_entries_truncated: false,
}

function seedMinorAwaitingReady() {
  return addShedVisit({
    loco_number: '39126',
    schedule_family: 'MINOR',
    schedule_variant: 'IA',
    status: 'IN_SHED',
    arrival_condition: 'WORKING',
    arrival_at: '2026-09-01T08:00:00Z',
    schedule_started_at: '2026-09-01T09:00:00Z',
    inspection_completed_at: '2026-09-01T13:00:00Z',
    ready_at: null,
    departed_at: null,
    booking_total: 0,
    pending_booking_count: 0,
  })
}

function seedMajorInProgress() {
  return addShedVisit({
    loco_number: '39160',
    schedule_family: 'MAJOR',
    schedule_variant: 'IOH',
    status: 'IN_SHED',
    arrival_condition: 'WORKING',
    arrival_at: '2026-09-01T08:00:00Z',
    schedule_started_at: '2026-09-01T09:00:00Z',
    ready_at: null,
    departed_at: null,
    booking_total: 0,
    pending_booking_count: 0,
  })
}

async function confirmAction(user: ReturnType<typeof userEvent.setup>, action: RegExp) {
  const table = await screen.findByRole('table')
  await user.click(within(table).getByRole('button', { name: action }))
  const dialog = await screen.findByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: action }))
  return dialog
}

describe('Ready blocked by outstanding checksheets', () => {
  it('explains the blocker with a count instead of a generic failure', async () => {
    loginAsToken('token-admin')
    seedMinorAwaitingReady()
    setReadyBlocker(BLOCKER)
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await confirmAction(user, /mark ready/i)

    expect(
      await within(dialog).findByText('Ready blocked: 4 required checksheets are still outstanding.'),
    ).toBeInTheDocument()
    expect(within(dialog).getByText(/8 of 12 required checksheets satisfied/i)).toBeInTheDocument()
    expect(within(dialog).queryByText(/conflicts with existing data/i)).toBeNull()
  })

  it('lists the outstanding checksheets with section, stage and reason', async () => {
    loginAsToken('token-admin')
    seedMinorAwaitingReady()
    setReadyBlocker(BLOCKER)
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await confirmAction(user, /mark ready/i)
    const items = within(await within(dialog).findByRole('list')).getAllByRole('listitem')

    expect(items).toHaveLength(4)
    expect(items[0].textContent).toContain('M1-HR · Pantograph PT-1')
    expect(items[0].textContent).toContain('Inspection')
    expect(items[0].textContent).toContain('Not started')
    expect(items[1].textContent).toContain('Draft')
    expect(items[2].textContent).toContain('OVERHAUL')       // Major Pattern C is legible
    expect(items[2].textContent).toContain('Rejected')
    expect(items[3].textContent).toContain('Test After')
  })

  it('offers no override, force or continue-anyway control', async () => {
    loginAsToken('token-admin')
    seedMinorAwaitingReady()
    setReadyBlocker(BLOCKER)
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await confirmAction(user, /mark ready/i)
    await within(dialog).findByText(/4 required checksheets/i)

    const labels = within(dialog).getAllByRole('button').map((b) => b.textContent ?? '')
    expect(labels.some((l) => /override|force|anyway|bypass|skip/i.test(l))).toBe(false)
    // Only Cancel and the ordinary retry remain.
    expect(labels).toEqual(['Cancel', 'Mark Ready'])
  })

  it('leaves the locomotive not Ready after a blocked attempt', async () => {
    loginAsToken('token-admin')
    seedMinorAwaitingReady()
    setReadyBlocker(BLOCKER)
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await confirmAction(user, /mark ready/i)
    await within(dialog).findByText(/4 required checksheets/i)
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }))

    const table = await screen.findByRole('table')
    expect(within(table).getByText('IA Inspection Complete')).toBeInTheDocument()
    expect(within(table).queryByText('Ready')).toBeNull()
  })

  it('blocks a MAJOR Complete Schedule the same way, since that is its Ready transition', async () => {
    loginAsToken('token-admin')
    seedMajorInProgress()
    setReadyBlocker({ ...BLOCKER, message: 'Ready blocked: 1 required checksheet is still outstanding.' })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await confirmAction(user, /complete schedule/i)

    expect(
      await within(dialog).findByText('Ready blocked: 1 required checksheet is still outstanding.'),
    ).toBeInTheDocument()
    const table = await screen.findByRole('table')
    expect(within(table).queryByText('Ready')).toBeNull()
  })

  it('says the work package is missing rather than counting zero outstanding', async () => {
    loginAsToken('token-admin')
    seedMinorAwaitingReady()
    setReadyBlocker({
      ...BLOCKER,
      message:
        'This visit has no checksheet work package, so its required checksheets are unknown. ' +
        'Generate the work package first; the locomotive cannot be marked Ready until every ' +
        'required checksheet is accounted for.',
      work_package_generated: false,
      total_required: 0,
      satisfied: 0,
      outstanding: 0,
      outstanding_entries: [],
    })
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    const dialog = await confirmAction(user, /mark ready/i)

    expect(await within(dialog).findByText(/no checksheet work package/i)).toBeInTheDocument()
    expect(within(dialog).queryByText(/required checksheets satisfied/i)).toBeNull()
    expect(within(dialog).queryByRole('list')).toBeNull()
  })

  it('still marks Ready when nothing is outstanding', async () => {
    loginAsToken('token-admin')
    seedMinorAwaitingReady()
    const user = userEvent.setup()
    renderWithProviders(<ShedMovementPage />)

    await confirmAction(user, /mark ready/i)

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const table = await screen.findByRole('table')
    expect(within(table).getByText('Ready')).toBeInTheDocument()
  })
})
