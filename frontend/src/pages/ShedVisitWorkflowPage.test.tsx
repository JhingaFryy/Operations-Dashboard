import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { renderWithProviders, loginAsToken } from '../test/testUtils'
import { server } from '../mocks/server'
import App from '../App'

function renderWorkflow(visitId: number) {
  return renderWithProviders(<App />, { route: `/shed-visits/${visitId}/workflow` })
}

describe('ShedVisitWorkflowPage', () => {
  it('is a protected route', async () => {
    renderWorkflow(700)
    await waitFor(() => expect(screen.getByLabelText(/employee id/i)).toBeInTheDocument())
  })

  it('does not crash when the backend returns a legacy SPECIAL_CHECKING stage row', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/workflow', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          loco_number: '39126',
          arrival_at: '2026-08-31T04:00:00Z',
          schedule_family: 'MINOR',
          schedule_variant: 'IA',
          status: 'IN_SHED',
          stages: [
            { id: 1, stage_type: 'TEST_BEFORE', stage_order: 1, status: 'COMPLETED', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 2, stage_type: 'SCHEDULE_INSPECTION', stage_order: 2, status: 'COMPLETED', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 3, stage_type: 'TEST_AFTER', stage_order: 3, status: 'COMPLETED', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 4, stage_type: 'SPECIAL_CHECKING', stage_order: 4, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.getByText('Special Checking')).toBeInTheDocument()
  })

  it('renders exactly the 3 active workflow stages in order (Special Checking removed)', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    const list = document.querySelector('.stage-list') as HTMLElement
    const names = within(list).getAllByText(/Test Before|Schedule Inspection|Test After|Special Checking/)
    expect(names.map((n) => n.textContent)).toEqual(['Test Before', 'Schedule Inspection', 'Test After'])
  })

  it('Admin sees Start Test Before for a PENDING stage', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    expect(await screen.findByRole('button', { name: /start test before/i })).toBeInTheDocument()
  })

  it('Supervisor does not see Start Test Before', async () => {
    loginAsToken('token-sup-m1hr')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByRole('button', { name: /start test before/i })).not.toBeInTheDocument()
  })

  it('requires confirmation before starting, then moves TEST_BEFORE to IN_PROGRESS', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /start test before/i }))
    expect(screen.getByText(/start test before for this visit\?/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /confirm start/i }))

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      expect(within(list).getByText('In Progress')).toBeInTheDocument()
    })
    // Booking ownership: Test Before findings are raised on the Android checksheet, never here.
    expect(await screen.findByText(/raised by the technician on the Android Test Before checksheet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /\+ add finding/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save findings/i })).not.toBeInTheDocument()
  })

  it('allows completion with zero TB bookings', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /start test before/i }))
    await user.click(screen.getByRole('button', { name: /confirm start/i }))

    const completeButton = await screen.findByRole('button', { name: /complete test before/i })
    await user.click(completeButton)

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      expect(within(list).getByText('Completed')).toBeInTheDocument()
    })
  })

  it('shows a structured blocker when completion is rejected', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/stages/test-before/complete', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'STAGE_NOT_READY_FOR_COMPLETION',
              stage: 'TEST_BEFORE',
              reason: 'BOOKINGS_PENDING',
              checksheets_ready: true,
              bookings_ready: false,
            },
          },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /start test before/i }))
    await user.click(screen.getByRole('button', { name: /confirm start/i }))
    const completeButton = await screen.findByRole('button', { name: /complete test before/i })
    await user.click(completeButton)

    await screen.findByText(/not every booking for this stage has been attended yet/i)
  })

  it('shows a pending-evidence message when required checksheets are not yet APPROVED', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/stages/test-before/complete', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'STAGE_NOT_READY_FOR_COMPLETION',
              stage: 'TEST_BEFORE',
              reason: 'REQUIRED_CHECKSHEETS_PENDING',
              checksheets_ready: false,
              bookings_ready: true,
            },
          },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /start test before/i }))
    await user.click(screen.getByRole('button', { name: /confirm start/i }))
    const completeButton = await screen.findByRole('button', { name: /complete test before/i })
    await user.click(completeButton)

    await screen.findByText(/not every required checksheet has been submitted in bl-dcms yet/i)
  })

  it('later stages remain read-only/disabled', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByRole('button', { name: /start schedule inspection/i })).not.toBeInTheDocument()
  })

  it('handles a 409 non-Minor workflow response', async () => {
    loginAsToken('token-admin')
    renderWorkflow(701)

    await screen.findByText(/not on a minor schedule/i)
  })

})

async function completeTestBeforeFlow(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /start test before/i }))
  await user.click(screen.getByRole('button', { name: /confirm start/i }))
  await user.click(await screen.findByRole('button', { name: /complete test before/i }))
  await waitFor(() => {
    const list = document.querySelector('.stage-list') as HTMLElement
    const items = within(list).getAllByText(/Completed|Pending|In Progress/)
    // Test Before is the first stage row.
    expect(items[0].textContent).toBe('Completed')
  })
}

describe('ShedVisitWorkflowPage — Schedule Inspection', () => {
  it('explains a grandfathered legacy Test Before and releases Schedule Inspection without it', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/workflow', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          loco_number: '39126',
          arrival_at: '2026-08-31T04:00:00Z',
          schedule_family: 'MINOR',
          schedule_variant: 'IA',
          status: 'IN_SHED',
          test_before_legacy_waived: true,
          stages: [
            { id: 1, stage_type: 'TEST_BEFORE', stage_order: 1, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 2, stage_type: 'SCHEDULE_INSPECTION', stage_order: 2, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 3, stage_type: 'TEST_AFTER', stage_order: 3, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.getByRole('note')).toHaveTextContent(/started before the Test Before gate/)
    expect(await screen.findByRole('button', { name: /start schedule inspection/i })).toBeInTheDocument()
  })

  it('treats an Admin-skipped Test Before as Skipped - never Completed - and releases Schedule Inspection', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/workflow', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          loco_number: '39126',
          arrival_at: '2026-08-31T04:00:00Z',
          schedule_family: 'MINOR',
          schedule_variant: 'IA',
          status: 'IN_SHED',
          stages: [
            { id: 1, stage_type: 'TEST_BEFORE', stage_order: 1, status: 'SKIPPED', started_at: null, started_by: null, completed_at: null, completed_by: null, skipped_at: '2026-08-31T05:00:00Z', skipped_by: 99, skip_reason: 'Traffic requirement' },
            { id: 2, stage_type: 'SCHEDULE_INSPECTION', stage_order: 2, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 3, stage_type: 'TEST_AFTER', stage_order: 3, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    const list = document.querySelector('.stage-list') as HTMLElement
    expect(within(list).getAllByText('Skipped').length).toBeGreaterThan(0)
    expect(screen.getByRole('note')).toHaveTextContent(/skipped by an Admin.*Traffic requirement.*Test After is still required/)
    expect(screen.queryByRole('button', { name: /start test before/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/becomes available once test before is completed/i)).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /start schedule inspection/i })).toBeInTheDocument()
  })

  it('is not actionable until Test Before is completed', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByRole('button', { name: /start schedule inspection/i })).not.toBeInTheDocument()
    expect(screen.getByText(/becomes available once test before is completed/i)).toBeInTheDocument()
  })

  it('becomes actionable for Admin once Test Before is completed', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await completeTestBeforeFlow(user)

    expect(await screen.findByRole('button', { name: /start schedule inspection/i })).toBeInTheDocument()
  })

  it('Supervisor never sees stage-control buttons', async () => {
    loginAsToken('token-sup-m1hr')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByRole('button', { name: /start schedule inspection/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /complete schedule inspection/i })).not.toBeInTheDocument()
  })

  it('requires confirmation before starting, then moves to IN_PROGRESS', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestBeforeFlow(user)

    await user.click(await screen.findByRole('button', { name: /start schedule inspection/i }))
    expect(screen.getByText(/start schedule inspection for this visit\?/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /confirm start/i }))

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      const items = within(list).getAllByText(/Completed|Pending|In Progress/)
      expect(items[1].textContent).toBe('In Progress') // Schedule Inspection is the second row
    })
    // Minor Inspection checksheets never raise bookings - there is no way to add one here.
    expect(await screen.findByText(/Minor Inspection checksheets do not raise bookings/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /\+ add finding/i })).not.toBeInTheDocument()
  })

  it('allows zero-finding completion', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestBeforeFlow(user)
    await user.click(await screen.findByRole('button', { name: /start schedule inspection/i }))
    await user.click(screen.getByRole('button', { name: /confirm start/i }))

    await user.click(await screen.findByRole('button', { name: /complete schedule inspection/i }))

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      const items = within(list).getAllByText(/Completed|Pending|In Progress/)
      expect(items[1].textContent).toBe('Completed')
    })
  })

  it('shows a structured blocker when completion is rejected', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/stages/schedule-inspection/complete', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'STAGE_NOT_READY_FOR_COMPLETION',
              stage: 'SCHEDULE_INSPECTION',
              reason: 'BOOKINGS_PENDING',
              checksheets_ready: true,
              bookings_ready: false,
            },
          },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestBeforeFlow(user)
    await user.click(await screen.findByRole('button', { name: /start schedule inspection/i }))
    await user.click(screen.getByRole('button', { name: /confirm start/i }))

    await user.click(await screen.findByRole('button', { name: /complete schedule inspection/i }))

    await screen.findByText(/not every booking for this stage has been attended yet/i)
  })

  it('later stage (Test After) remains read-only, and Special Checking is not offered at all', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestBeforeFlow(user)

    await screen.findByRole('button', { name: /start schedule inspection/i })
    expect(screen.queryByRole('button', { name: /start test after/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start special checking/i })).not.toBeInTheDocument()
    expect(screen.queryByText('Special Checking')).not.toBeInTheDocument()
  })

  it('handles a 403 when a Supervisor attempts the API directly', async () => {
    loginAsToken('token-sup-m1hr')
    await expect((await import('../api/workflow')).startScheduleInspection(700)).rejects.toMatchObject({
      status: 403,
    })
  })

  async function startSi(user: ReturnType<typeof userEvent.setup>) {
    await completeTestBeforeFlow(user)
    await user.click(await screen.findByRole('button', { name: /start schedule inspection/i }))
    await user.click(screen.getByRole('button', { name: /confirm start/i }))
    await screen.findByText(/Minor Inspection checksheets do not raise bookings/i)
  }

  it('after a successful completion, TEST_AFTER remains PENDING', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await startSi(user)

    await user.click(await screen.findByRole('button', { name: /complete schedule inspection/i }))

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      const items = within(list).getAllByText(/Completed|Pending|In Progress/)
      expect(items[1].textContent).toBe('Completed') // Schedule Inspection
      expect(items[2].textContent).toBe('Pending') // Test After
    })
  })
})

async function completeScheduleInspectionFlow(user: ReturnType<typeof userEvent.setup>) {
  await completeTestBeforeFlow(user)
  await user.click(await screen.findByRole('button', { name: /start schedule inspection/i }))
  await user.click(screen.getByRole('button', { name: /confirm start/i }))
  await user.click(await screen.findByRole('button', { name: /complete schedule inspection/i }))
  await waitFor(() => {
    const list = document.querySelector('.stage-list') as HTMLElement
    const items = within(list).getAllByText(/Completed|Pending|In Progress/)
    expect(items[1].textContent).toBe('Completed') // Schedule Inspection
  })
}

async function startTa(user: ReturnType<typeof userEvent.setup>) {
  await completeScheduleInspectionFlow(user)
  await user.click(await screen.findByRole('button', { name: /start test after/i }))
  await user.click(screen.getByRole('button', { name: /confirm start/i }))
  await screen.findByText('Test Before Bookings — Recheck Reference')
}

describe('ShedVisitWorkflowPage — Test After', () => {
  it('is not actionable until Schedule Inspection is completed', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestBeforeFlow(user)

    expect(screen.queryByRole('button', { name: /start test after/i })).not.toBeInTheDocument()
    expect(screen.getByText(/becomes available once schedule inspection is completed/i)).toBeInTheDocument()
  })

  it('becomes actionable for Admin once Schedule Inspection is completed', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeScheduleInspectionFlow(user)

    expect(await screen.findByRole('button', { name: /start test after/i })).toBeInTheDocument()
  })

  it('Supervisor never sees stage-control buttons', async () => {
    loginAsToken('token-sup-m1hr')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByRole('button', { name: /start test after/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /complete test after/i })).not.toBeInTheDocument()
  })

  it('requires confirmation before starting, then moves to IN_PROGRESS', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeScheduleInspectionFlow(user)

    await user.click(await screen.findByRole('button', { name: /start test after/i }))
    expect(screen.getByText(/start test after for this visit\?/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /confirm start/i }))

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      const items = within(list).getAllByText(/Completed|Pending|In Progress/)
      expect(items[2].textContent).toBe('In Progress') // Test After is the third row
    })
  })

  it('offers no way to create Test After findings on the Dashboard', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await startTa(user)

    expect(await screen.findByText(/raised by the technician on the Android Test After checksheet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /\+ add finding/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save findings/i })).not.toBeInTheDocument()
  })

  it('shows the Test Before reference bookings read-only, with no mutation controls', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await startTa(user)

    expect(screen.getByText(/recorded during test before for this shed visit/i)).toBeInTheDocument()
    // Fixture visit 700 already has seeded Test Before bookings (used by
    // other describe blocks' fixtures); whatever shows up must be TB-only.
    const referenceSection = screen.getByText('Test Before Bookings — Recheck Reference').closest('section') as HTMLElement
    expect(within(referenceSection).queryByRole('button', { name: /reopen/i })).not.toBeInTheDocument()
    expect(within(referenceSection).queryByRole('button', { name: /attend/i })).not.toBeInTheDocument()
    expect(within(referenceSection).queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('allows zero-finding completion as the final active stage', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await startTa(user)

    await user.click(await screen.findByRole('button', { name: /complete test after/i }))

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      const items = within(list).getAllByText(/Completed|Pending|In Progress/)
      expect(items).toHaveLength(3)
      expect(items[2].textContent).toBe('Completed') // Test After — last active stage
    })
    expect(screen.queryByText('Special Checking')).not.toBeInTheDocument()
  })

  it('shows a structured blocker when completion is rejected', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/stages/test-after/complete', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'STAGE_NOT_READY_FOR_COMPLETION',
              stage: 'TEST_AFTER',
              reason: 'BOOKINGS_PENDING',
              checksheets_ready: true,
              bookings_ready: false,
            },
          },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await startTa(user)

    await user.click(await screen.findByRole('button', { name: /complete test after/i }))

    await screen.findByText(/not every booking for this stage has been attended yet/i)
  })

  it('handles a 403 when a Supervisor attempts the start API directly', async () => {
    loginAsToken('token-sup-m1hr')
    await expect((await import('../api/workflow')).startTestAfter(700)).rejects.toMatchObject({
      status: 403,
    })
  })

  it('handles a 409 when starting before prerequisites are completed, via API directly', async () => {
    loginAsToken('token-admin')
    await expect((await import('../api/workflow')).startTestAfter(700)).rejects.toMatchObject({
      status: 409,
    })
  })

})

async function completeTestAfterFlow(user: ReturnType<typeof userEvent.setup>) {
  await startTa(user)
  await user.click(await screen.findByRole('button', { name: /complete test after/i }))
  await waitFor(() => {
    const list = document.querySelector('.stage-list') as HTMLElement
    const items = within(list).getAllByText(/Completed|Pending|In Progress/)
    expect(items[2].textContent).toBe('Completed') // Test After
  })
  await screen.findByText('Shed Out Readiness')
}

describe('ShedVisitWorkflowPage — Shed Out', () => {
  it('does not show the readiness panel before Test After is completed', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByText('Shed Out Readiness')).not.toBeInTheDocument()
  })

  it('shows READY FOR SHED OUT and an enabled Shed Out button when eligible', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestAfterFlow(user)

    await screen.findByText(/ready for shed out/i)
    const shedOutButton = await screen.findByRole('button', { name: /^shed out$/i })
    expect(shedOutButton).not.toBeDisabled()
  })

  it('shows a blocked stage in the readiness panel and disables Shed Out', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/shed-out-eligibility', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          eligible: false,
          stage_blockers: [{ stage_type: 'TEST_AFTER', status: 'IN_PROGRESS' }],
          booking_blockers: [],
        }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await startTa(user)
    // Complete TA so the readiness panel appears, but the eligibility
    // override above still reports it blocked (simulating a race).
    await user.click(await screen.findByRole('button', { name: /complete test after/i }))
    await screen.findByText('Shed Out Readiness')

    await screen.findByText(/not yet eligible/i)
    expect(screen.getByText(/TEST AFTER — IN_PROGRESS/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^shed out$/i })).toBeDisabled()
  })

  it('shows a blocked booking in the readiness panel', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/shed-out-eligibility', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          eligible: false,
          stage_blockers: [],
          // Exactly backend BookingBlockerOut - the booking's own derived status.
          booking_blockers: [{ booking_id: 905, booking_source: 'TEST_BEFORE', status: 'IN_PROGRESS' }],
        }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestAfterFlow(user)

    expect(screen.getByText(/Booking #905 \(TEST_BEFORE\)/)).toBeInTheDocument()
    expect(screen.getByText('In Progress')).toBeInTheDocument()
    expect(screen.getByText('1 booking still In Progress.')).toBeInTheDocument()
  })

  it('Supervisor never sees the Shed Out readiness panel', async () => {
    loginAsToken('token-sup-m1hr')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByText('Shed Out Readiness')).not.toBeInTheDocument()
  })

  it('requires confirmation, collects departure date/time and remarks, and completes Shed Out', async () => {
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestAfterFlow(user)

    await user.click(await screen.findByRole('button', { name: /^shed out$/i }))
    expect(screen.getByText(/confirm shed out for locomotive 39126/i)).toBeInTheDocument()

    await user.type(screen.getByLabelText(/departure date\/time/i), '2026-09-01T12:00')
    await user.type(screen.getByLabelText(/remarks/i), 'Loco released after IA schedule')
    await user.click(screen.getByRole('button', { name: /confirm shed out/i }))

    await screen.findByText(/shed out successfully/i)
  })

  it('shows a structured 409 blocker if Shed Out is rejected on submit', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/out', () =>
        HttpResponse.json(
          {
            detail: {
              code: 'SHED_OUT_BLOCKED',
              message: 'Shed Out blocked: 1 booking still has work outstanding.',
              blocked_by: ['BOOKINGS'],
              stage_blockers: [],
              booking_blockers: [{ booking_id: 911, booking_source: 'LOG_BOOK', status: 'REOPENED' }],
              checksheet_blockers: [],
              checksheets: null,
            },
          },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)
    await completeTestAfterFlow(user)

    await user.click(await screen.findByRole('button', { name: /^shed out$/i }))
    await user.type(screen.getByLabelText(/departure date\/time/i), '2026-09-01T12:00')
    await user.click(screen.getByRole('button', { name: /confirm shed out/i }))

    await screen.findByText('Shed Out blocked: 1 booking needs attention.')
    expect(screen.getByText(/Booking #911 \(LOG BOOK\)/)).toBeInTheDocument()
    expect(screen.getByText(/Reopened/)).toBeInTheDocument()
    expect(screen.queryByText(/not eligible for shed out/i)).toBeNull()
  })

  it('rejects Shed Out on an already-closed visit', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/out', () =>
        HttpResponse.json(
          { detail: { code: 'VISIT_ALREADY_CLOSED', message: 'This shed visit has already been Shed Out.' } },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    await expect(
      (await import('../api/shedVisits')).shedOut(700, { departed_at: '2026-09-01T12:00:00Z' }),
    ).rejects.toMatchObject({ status: 409 })
  })

  it('handles a 403 when a Supervisor calls the eligibility API directly', async () => {
    loginAsToken('token-sup-m1hr')
    await expect((await import('../api/shedVisits')).getShedOutEligibility(700)).rejects.toMatchObject({
      status: 403,
    })
  })

  it('handles a 404 for a nonexistent visit', async () => {
    loginAsToken('token-admin')
    await expect((await import('../api/shedVisits')).getShedOutEligibility(999999)).rejects.toMatchObject({
      status: 404,
    })
  })

  it('handles a 422 when departure predates arrival', async () => {
    loginAsToken('token-admin')
    await expect(
      (await import('../api/shedVisits')).shedOut(700, { departed_at: '2000-01-01T00:00:00Z' }),
    ).rejects.toMatchObject({ status: 422 })
  })
})

describe('ShedVisitWorkflowPage — Checksheet Progress (BL-DCMS integration, Phase 4)', () => {
  it('renders the progress panel with all three stages at 0/0 by default', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Checksheet Progress')
    const counts = await screen.findAllByText('0 / 0 approved')
    expect(counts).toHaveLength(3)
    const panel = document.querySelector('.checksheet-progress') as HTMLElement
    expect(within(panel).getByText('Test Before')).toBeInTheDocument()
    expect(within(panel).getByText('Schedule Inspection')).toBeInTheDocument()
    expect(within(panel).getByText('Test After')).toBeInTheDocument()
  })

  it('shows the empty-state message when no checksheets are linked, not an error', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('No BL-DCMS checksheets linked to this visit yet.')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders real stage counts when checksheets exist', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/checksheet-summary', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          source: 'BLDCMS',
          available: true,
          stages: [
            { workflow_stage_type: 'TEST_BEFORE', total_checksheets: 1, approved_checksheets: 1, pending_checksheets: 0 },
            { workflow_stage_type: 'SCHEDULE_INSPECTION', total_checksheets: 12, approved_checksheets: 8, pending_checksheets: 4 },
            { workflow_stage_type: 'TEST_AFTER', total_checksheets: 1, approved_checksheets: 0, pending_checksheets: 1 },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('1 / 1 approved')
    expect(screen.getByText('8 / 12 approved')).toBeInTheDocument()
    expect(screen.getByText('0 / 1 approved')).toBeInTheDocument()
    expect(screen.queryByText('No BL-DCMS checksheets linked to this visit yet.')).not.toBeInTheDocument()
  })

  it('shows the unavailable-state message when BL-DCMS cannot be reached, not a crash', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/checksheet-summary', () =>
        HttpResponse.json({ shed_visit_id: 700, source: 'BLDCMS', available: false, stages: [] }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('BL-DCMS checksheet status is temporarily unavailable.')
    // The rest of the page must still render normally alongside it.
    await screen.findByRole('button', { name: /start test before/i })
  })

  it('does not render a "Stage Complete" claim from checksheet counts alone', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/checksheet-summary', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          source: 'BLDCMS',
          available: true,
          stages: [
            { workflow_stage_type: 'TEST_BEFORE', total_checksheets: 1, approved_checksheets: 1, pending_checksheets: 0 },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('1 / 1 approved')
    expect(screen.queryByText(/stage complete/i)).not.toBeInTheDocument()
    // The actual workflow StageList must still show TEST_BEFORE as PENDING - nothing here
    // auto-completed it.
    const list = document.querySelector('.stage-list') as HTMLElement
    const items = within(list).getAllByText(/Completed|Pending|In Progress/)
    expect(items[0].textContent).toBe('Pending')
  })

  it('expands to show the checksheet list with read-only columns and no edit/sign controls', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/checksheets', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          source: 'BLDCMS',
          available: true,
          items: [
            {
              id: 42,
              template_id: 5,
              template_name: 'Aux Converter Checksheet',
              section: 'M4-HR',
              equipment: 'Aux Converter',
              loco: '39126',
              schedule_family: 'MINOR',
              schedule_variant: 'IA',
              workflow_stage_type: 'TEST_BEFORE',
              status: 'APPROVED',
              approved: true,
              signed: true,
              signing_timestamp: '2026-06-01T10:00:00Z',
              verification_status: 'VALID',
            },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await screen.findByText('Checksheet Progress')
    await user.click(await screen.findByRole('button', { name: /view checksheet list/i }))

    await screen.findByText('Aux Converter Checksheet')
    expect(screen.getByText('M4-HR')).toBeInTheDocument()
    expect(screen.getByText('Aux Converter')).toBeInTheDocument()
    expect(screen.getByText('APPROVED')).toBeInTheDocument()
    expect(screen.getAllByText('Yes').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^sign$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /review/i })).not.toBeInTheDocument()
  })

  it('checksheet list itself reflects the unavailable state when expanded', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/checksheets', () =>
        HttpResponse.json({ shed_visit_id: 700, source: 'BLDCMS', available: false, items: [] }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await screen.findByText('Checksheet Progress')
    await user.click(await screen.findByRole('button', { name: /view checksheet list/i }))

    const messages = await screen.findAllByText('BL-DCMS checksheet status is temporarily unavailable.')
    expect(messages.length).toBeGreaterThan(0)
  })
})

describe('ShedVisitWorkflowPage — Required Checksheets (materialized work package, Phase 5B.1)', () => {
  it('Admin sees a Generate button when no work package exists yet', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Required Checksheets')
    await screen.findByRole('button', { name: /generate checksheet work package/i })
  })

  it('Supervisor sees "not generated yet" text instead of a Generate button', async () => {
    loginAsToken('token-sup-m1hr')
    renderWorkflow(700)

    await screen.findByText('Required Checksheets')
    await screen.findByText('Work package not generated yet.')
    expect(
      screen.queryByRole('button', { name: /generate checksheet work package/i }),
    ).not.toBeInTheDocument()
  })

  it('renders requirements grouped by stage with required/optional labels', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/checksheet-work-package', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          generated: true,
          generated_by: 1,
          generated_at: '2026-06-01T10:00:00Z',
          stages: [
            {
              workflow_stage_type: 'TEST_BEFORE',
              requirements: [
                {
                  applicability_id: 1,
                  template_id: 5,
                  template_name: 'Aux Converter Checksheet',
                  technology: '3_PHASE',
                  section_id: 4,
                  section_name: 'M4-HR',
                  equipment_id: 9,
                  equipment_name: 'Aux Converter',
                  maintenance_type: 'MINOR',
                  is_required: true,
                },
                {
                  applicability_id: 2,
                  template_id: 6,
                  template_name: 'Optional Bogie Checksheet',
                  technology: '3_PHASE',
                  section_id: null,
                  section_name: null,
                  equipment_id: null,
                  equipment_name: null,
                  maintenance_type: 'MINOR',
                  is_required: false,
                },
              ],
            },
            {
              workflow_stage_type: 'SCHEDULE_INSPECTION',
              requirements: [],
            },
            {
              workflow_stage_type: 'TEST_AFTER',
              requirements: [],
            },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Required Checksheets')
    await screen.findByText('Aux Converter Checksheet')
    const list = document.querySelector('.required-checksheets-stage-list') as HTMLElement
    expect(within(list).getByText('Test Before')).toBeInTheDocument()
    expect(within(list).getByText('Schedule Inspection')).toBeInTheDocument()
    expect(within(list).getByText('Test After')).toBeInTheDocument()

    expect(within(list).getByText('M4-HR')).toBeInTheDocument()
    expect(within(list).getByText('Aux Converter')).toBeInTheDocument()
    expect(within(list).getByText('Optional Bogie Checksheet')).toBeInTheDocument()
    const badges = Array.from(list.querySelectorAll('.badge'))
    expect(badges.map((b) => b.textContent)).toEqual(['Required', 'Optional'])

    expect(
      screen.queryByRole('button', { name: /generate checksheet work package/i }),
    ).not.toBeInTheDocument()
  })

  it('shows a friendly error and stays usable when generation fails with a configuration error', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/checksheet-work-package', () =>
        HttpResponse.json(
          { detail: 'No checksheet applicability configured for this visit schedule/technology.' },
          { status: 409 },
        ),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /generate checksheet work package/i }))
    await user.click(await screen.findByRole('button', { name: /confirm generate/i }))

    await screen.findByText('No checksheet applicability configured for this visit schedule/technology.')
    // The confirm control must still be usable afterward - no crash, no permanent lockout.
    await screen.findByRole('button', { name: /confirm generate/i })
    await screen.findByRole('button', { name: /cancel/i })
  })

  it('does not disturb the existing workflow controls (Test Before start button, Checksheet Progress)', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Required Checksheets')
    await screen.findByRole('button', { name: /start test before/i })
    await screen.findByText('Checksheet Progress')
  })
})

describe('ShedVisitWorkflowPage — Required Checksheet Progress (BL-DCMS correlation, Phase 5B.2)', () => {
  function progressFixture(overrides: Record<string, unknown> = {}) {
    return {
      shed_visit_id: 700,
      work_package_generated: true,
      bldcms_available: true,
      stages: [
        {
          workflow_stage_type: 'TEST_BEFORE',
          required_total: 3,
          required_satisfied: 1,
          required_remaining: 2,
          optional_total: 1,
          optional_satisfied: 0,
          ready_for_completion: false,
          requirements: [
            {
              requirement_id: 1,
              applicability_id: 1,
              template_id: 1,
              template_name_snapshot: 'Traction Motor Inspection',
              section: 'M4-HR',
              equipment: 'Traction Motor',
              workflow_stage_type: 'TEST_BEFORE',
              is_required: true,
              progress_state: 'SATISFIED',
              satisfied: true,
              matching_checksheets: [
                { checksheet_id: 1, status: 'APPROVED', signed: true, signing_timestamp: null, verification_status: 'VALID' },
              ],
            },
            {
              requirement_id: 2,
              applicability_id: 2,
              template_id: 2,
              template_name_snapshot: 'Auxiliary Converter Inspection',
              section: 'M4-HR',
              equipment: 'Aux Converter',
              workflow_stage_type: 'TEST_BEFORE',
              is_required: true,
              progress_state: 'IN_PROGRESS',
              satisfied: false,
              matching_checksheets: [
                { checksheet_id: 2, status: 'SUBMITTED', signed: false, signing_timestamp: null, verification_status: null },
              ],
            },
            {
              requirement_id: 3,
              applicability_id: 3,
              template_id: 3,
              template_name_snapshot: 'VCU Inspection',
              section: 'M4-HR',
              equipment: 'VCU',
              workflow_stage_type: 'TEST_BEFORE',
              is_required: true,
              progress_state: 'NOT_STARTED',
              satisfied: false,
              matching_checksheets: [],
            },
            {
              requirement_id: 4,
              applicability_id: 4,
              template_id: 4,
              template_name_snapshot: 'Optional Bogie Inspection',
              section: null,
              equipment: null,
              workflow_stage_type: 'TEST_BEFORE',
              is_required: false,
              progress_state: 'NOT_STARTED',
              satisfied: false,
              matching_checksheets: [],
            },
          ],
        },
        { workflow_stage_type: 'SCHEDULE_INSPECTION', required_total: 0, required_satisfied: 0, required_remaining: 0, optional_total: 0, optional_satisfied: 0, ready_for_completion: false, requirements: [] },
        { workflow_stage_type: 'TEST_AFTER', required_total: 0, required_satisfied: 0, required_remaining: 0, optional_total: 0, optional_satisfied: 0, ready_for_completion: false, requirements: [] },
      ],
      ...overrides,
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function mockProgress(body: any) {
    server.use(
      http.get('/api/shed-visits/:visitId/checksheet-requirement-progress', () => HttpResponse.json(body)),
    )
  }

  it('renders satisfied, in-progress, and not-started rows', async () => {
    mockProgress(progressFixture())
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Traction Motor Inspection')
    const panel = document.querySelector('.checksheet-requirement-progress') as HTMLElement

    expect(within(panel).getByText('Traction Motor Inspection')).toBeInTheDocument()
    expect(within(panel).getByText('SUBMITTED / SATISFIED')).toBeInTheDocument()

    expect(within(panel).getByText('Auxiliary Converter Inspection')).toBeInTheDocument()
    expect(within(panel).getByText('IN PROGRESS')).toBeInTheDocument()

    expect(within(panel).getByText('VCU Inspection')).toBeInTheDocument()
    expect(within(panel).getAllByText('NOT STARTED').length).toBeGreaterThan(0)
  })

  it('SUBMITTED, UNDER_REVIEW, and APPROVED all render as satisfied; REJECTED is flagged distinctly', async () => {
    function requirementRow(id: number, name: string, checksheetStatus: string, progressState: string, satisfied: boolean) {
      return {
        requirement_id: id,
        applicability_id: id,
        template_id: id,
        template_name_snapshot: name,
        section: 'M4-HR',
        equipment: 'Equipment',
        workflow_stage_type: 'TEST_BEFORE',
        is_required: true,
        progress_state: progressState,
        satisfied,
        matching_checksheets: [
          { checksheet_id: id, status: checksheetStatus, signed: checksheetStatus === 'APPROVED', signing_timestamp: null, verification_status: null },
        ],
      }
    }

    mockProgress({
      shed_visit_id: 700,
      work_package_generated: true,
      bldcms_available: true,
      stages: [
        {
          workflow_stage_type: 'TEST_BEFORE',
          required_total: 4,
          required_satisfied: 3,
          required_remaining: 1,
          optional_total: 0,
          optional_satisfied: 0,
          ready_for_completion: false,
          requirements: [
            requirementRow(1, 'Submitted Checksheet', 'SUBMITTED', 'SATISFIED', true),
            requirementRow(2, 'Under Review Checksheet', 'UNDER_REVIEW', 'SATISFIED', true),
            requirementRow(3, 'Approved Checksheet', 'APPROVED', 'SATISFIED', true),
            requirementRow(4, 'Rejected Checksheet', 'REJECTED', 'REJECTED', false),
          ],
        },
        { workflow_stage_type: 'SCHEDULE_INSPECTION', required_total: 0, required_satisfied: 0, required_remaining: 0, optional_total: 0, optional_satisfied: 0, ready_for_completion: false, requirements: [] },
        { workflow_stage_type: 'TEST_AFTER', required_total: 0, required_satisfied: 0, required_remaining: 0, optional_total: 0, optional_satisfied: 0, ready_for_completion: false, requirements: [] },
      ],
    })
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Submitted Checksheet')
    const panel = document.querySelector('.checksheet-requirement-progress') as HTMLElement

    const submittedRow = within(panel).getByText('Submitted Checksheet').closest('.checksheet-progress-row') as HTMLElement
    expect(within(submittedRow).getByText('SUBMITTED / SATISFIED')).toBeInTheDocument()

    const underReviewRow = within(panel).getByText('Under Review Checksheet').closest('.checksheet-progress-row') as HTMLElement
    expect(within(underReviewRow).getByText('SUBMITTED / SATISFIED')).toBeInTheDocument()

    const approvedRow = within(panel).getByText('Approved Checksheet').closest('.checksheet-progress-row') as HTMLElement
    expect(within(approvedRow).getByText('SUBMITTED / SATISFIED')).toBeInTheDocument()
    // The checksheet's own actual BL-DCMS status (APPROVED, signed) is still shown as evidence -
    // digital-signature status is never hidden, it just doesn't gate "satisfied" anymore.
    expect(within(approvedRow).getByText(/APPROVED/)).toBeInTheDocument()
    expect(within(approvedRow).getByText(/\(signed\)/)).toBeInTheDocument()

    const rejectedRow = within(panel).getByText('Rejected Checksheet').closest('.checksheet-progress-row') as HTMLElement
    expect(within(rejectedRow).getByText(/REJECTED.*needs resubmission/)).toBeInTheDocument()
  })

  it('shows required progress counts and remaining, not "ready" until all required are satisfied', async () => {
    mockProgress(progressFixture())
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText(/Required: 1 \/ 3 submitted/)
    expect(screen.getByText('2 remaining')).toBeInTheDocument()
    expect(screen.queryByText('Ready for completion')).not.toBeInTheDocument()
  })

  it('shows "Ready for completion" wording (never "Stage Completed") once all required rows are satisfied', async () => {
    const fixture = progressFixture()
    fixture.stages[0].required_satisfied = 3
    fixture.stages[0].required_remaining = 0
    fixture.stages[0].ready_for_completion = true
    mockProgress(fixture)
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Ready for completion')
    expect(screen.queryByText(/stage completed/i)).not.toBeInTheDocument()
  })

  it('optional rows are shown but do not block ready_for_completion, and have their own count', async () => {
    mockProgress(progressFixture())
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Optional Bogie Inspection')
    expect(screen.getByText(/Optional: 0 \/ 1 submitted/)).toBeInTheDocument()
  })

  it('zero required rows are never shown as ready', async () => {
    const fixture = progressFixture()
    fixture.stages[1] = {
      workflow_stage_type: 'SCHEDULE_INSPECTION',
      required_total: 0,
      required_satisfied: 0,
      required_remaining: 0,
      optional_total: 0,
      optional_satisfied: 0,
      ready_for_completion: false,
      requirements: [],
    }
    mockProgress(fixture)
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Traction Motor Inspection')
    const progressPanel = document.querySelector('.checksheet-requirement-progress') as HTMLElement
    const siItem = within(progressPanel).getByText('Schedule Inspection').closest('li') as HTMLElement
    expect(within(siItem).getByText(/Required: 0 \/ 0 submitted/)).toBeInTheDocument()
    // Zero-required stage must show "remaining" (0), never "Ready for completion".
    expect(within(siItem).queryByText('Ready for completion')).not.toBeInTheDocument()
    expect(within(siItem).getByText('0 remaining')).toBeInTheDocument()
  })

  it('shows a controlled BL-DCMS-unavailable state, distinct from genuine zero progress', async () => {
    mockProgress({ shed_visit_id: 700, work_package_generated: true, bldcms_available: false, stages: [] })
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Required Checksheet Progress')
    await screen.findByText(/cannot currently be checked/i)
    expect(screen.queryByText(/Required: /)).not.toBeInTheDocument()
  })

  it('shows a no-work-package state', async () => {
    mockProgress({ shed_visit_id: 700, work_package_generated: false, bldcms_available: false, stages: [] })
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Required Checksheet Progress')
    await screen.findByText(/no checksheet work package has been generated/i)
  })

  it('the actual Dashboard stage status remains independent of ready_for_completion', async () => {
    const fixture = progressFixture()
    fixture.stages[0].required_satisfied = 3
    fixture.stages[0].required_remaining = 0
    fixture.stages[0].ready_for_completion = true
    mockProgress(fixture)
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByText('Ready for completion')
    // The real stage list still shows Test Before as Pending - nothing here auto-completed it.
    const list = document.querySelector('.stage-list') as HTMLElement
    const items = within(list).getAllByText(/Completed|Pending|In Progress/)
    expect(items[0].textContent).toBe('Pending')
  })
})

// Operations Dashboard Phase 5B.3: Authoritative Checksheet Stage Reconciliation. "Reconcile
// Workflow" is the sole mutation path that may transition a stage to COMPLETED - it asks the
// system to evaluate evidence, it is never presented as a manual "complete this stage" action.
describe('Reconcile Workflow (Phase 5B.3)', () => {
  it('Admin sees the Reconcile Workflow button', async () => {
    loginAsToken('token-admin')
    renderWorkflow(700)

    expect(await screen.findByRole('button', { name: /reconcile workflow/i })).toBeInTheDocument()
  })

  it('Supervisor cannot see or trigger the Reconcile Workflow button', async () => {
    loginAsToken('token-sup-m1hr')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByRole('button', { name: /reconcile workflow/i })).not.toBeInTheDocument()
  })

  it('shows a pending-evidence reason without mutating the stage when checksheets are not yet approved', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/reconcile-checksheet-stages', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          changed: false,
          stages: [
            {
              workflow_stage_type: 'TEST_BEFORE',
              previous_status: 'PENDING',
              current_status: 'PENDING',
              checksheets_ready: false,
              bookings_ready: true,
              reason: 'REQUIRED_CHECKSHEETS_PENDING',
            },
            {
              workflow_stage_type: 'SCHEDULE_INSPECTION',
              previous_status: 'PENDING',
              current_status: 'PENDING',
              checksheets_ready: null,
              bookings_ready: null,
              reason: 'PREVIOUS_STAGE_NOT_COMPLETED',
            },
            {
              workflow_stage_type: 'TEST_AFTER',
              previous_status: 'PENDING',
              current_status: 'PENDING',
              checksheets_ready: null,
              bookings_ready: null,
              reason: 'PREVIOUS_STAGE_NOT_COMPLETED',
            },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /reconcile workflow/i }))

    await screen.findByText(/not every required checksheet has been submitted in bl-dcms yet/i)
    const list = document.querySelector('.stage-list') as HTMLElement
    expect(within(list).getAllByText('Pending').length).toBeGreaterThan(0)
  })

  it('shows a bookings-pending reason', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/reconcile-checksheet-stages', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          changed: false,
          stages: [
            {
              workflow_stage_type: 'TEST_BEFORE',
              previous_status: 'IN_PROGRESS',
              current_status: 'IN_PROGRESS',
              checksheets_ready: true,
              bookings_ready: false,
              reason: 'BOOKINGS_PENDING',
            },
            {
              workflow_stage_type: 'SCHEDULE_INSPECTION',
              previous_status: 'PENDING',
              current_status: 'PENDING',
              checksheets_ready: null,
              bookings_ready: null,
              reason: 'PREVIOUS_STAGE_NOT_COMPLETED',
            },
            {
              workflow_stage_type: 'TEST_AFTER',
              previous_status: 'PENDING',
              current_status: 'PENDING',
              checksheets_ready: null,
              bookings_ready: null,
              reason: 'PREVIOUS_STAGE_NOT_COMPLETED',
            },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /reconcile workflow/i }))

    await screen.findByText(/not every booking for this stage has been attended yet/i)
  })

  it('a successful reconciliation refreshes the stage list to COMPLETED', async () => {
    server.use(
      http.post('/api/shed-visits/:visitId/reconcile-checksheet-stages', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          changed: true,
          stages: [
            {
              workflow_stage_type: 'TEST_BEFORE',
              previous_status: 'IN_PROGRESS',
              current_status: 'COMPLETED',
              checksheets_ready: true,
              bookings_ready: true,
              reason: 'COMPLETED',
            },
            {
              workflow_stage_type: 'SCHEDULE_INSPECTION',
              previous_status: 'PENDING',
              current_status: 'PENDING',
              checksheets_ready: null,
              bookings_ready: null,
              reason: 'PREVIOUS_STAGE_NOT_COMPLETED',
            },
            {
              workflow_stage_type: 'TEST_AFTER',
              previous_status: 'PENDING',
              current_status: 'PENDING',
              checksheets_ready: null,
              bookings_ready: null,
              reason: 'PREVIOUS_STAGE_NOT_COMPLETED',
            },
          ],
        }),
      ),
      http.get('/api/shed-visits/:visitId/workflow', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          loco_number: '39126',
          arrival_at: '2026-08-31T04:00:00Z',
          schedule_family: 'MINOR',
          schedule_variant: 'IA',
          status: 'IN_SHED',
          stages: [
            { id: 1, stage_type: 'TEST_BEFORE', stage_order: 1, status: 'COMPLETED', started_at: '2026-08-31T05:00:00Z', started_by: null, completed_at: '2026-08-31T06:00:00Z', completed_by: null },
            { id: 2, stage_type: 'SCHEDULE_INSPECTION', stage_order: 2, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 3, stage_type: 'TEST_AFTER', stage_order: 3, status: 'PENDING', started_at: null, started_by: null, completed_at: null, completed_by: null },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    const user = userEvent.setup()
    renderWorkflow(700)

    await user.click(await screen.findByRole('button', { name: /reconcile workflow/i }))

    await waitFor(() => {
      const list = document.querySelector('.stage-list') as HTMLElement
      const items = within(list).getAllByText(/Completed|Pending|In Progress/)
      expect(items[0].textContent).toBe('Completed')
    })
  })

  it('is not offered once the visit is CLOSED', async () => {
    server.use(
      http.get('/api/shed-visits/:visitId/workflow', () =>
        HttpResponse.json({
          shed_visit_id: 700,
          loco_number: '39126',
          arrival_at: '2026-08-31T04:00:00Z',
          schedule_family: 'MINOR',
          schedule_variant: 'IA',
          status: 'CLOSED',
          stages: [
            { id: 1, stage_type: 'TEST_BEFORE', stage_order: 1, status: 'COMPLETED', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 2, stage_type: 'SCHEDULE_INSPECTION', stage_order: 2, status: 'COMPLETED', started_at: null, started_by: null, completed_at: null, completed_by: null },
            { id: 3, stage_type: 'TEST_AFTER', stage_order: 3, status: 'COMPLETED', started_at: null, started_by: null, completed_at: null, completed_by: null },
          ],
        }),
      ),
    )
    loginAsToken('token-admin')
    renderWorkflow(700)

    await screen.findByRole('heading', { name: /loco 39126/i })
    expect(screen.queryByRole('button', { name: /reconcile workflow/i })).not.toBeInTheDocument()
  })
})
