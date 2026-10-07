import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'

import { BookingPoolPage } from './BookingPoolPage'
import { loginAsToken, renderWithProviders } from '../test/testUtils'
import { server } from '../mocks/server'

/**
 * Per-section status chips in the Global Booking Pool.
 *
 * THE GAP: the backend has always sent routed_sections[].status, and the row discarded it -
 * every section chip rendered as the same neutral pill showing only the code. A planner looking
 * at a multi-section booking could not tell which section had finished and which had not
 * started, which is precisely what the pool exists to tell them.
 */

async function expandAll(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => expect(document.querySelector('.collapsible-group-loco')).not.toBeNull())
  for (let pass = 0; pass < 6; pass += 1) {
    const closed = Array.from(
      document.querySelectorAll<HTMLElement>('.collapsible-group-header[aria-expanded="false"]'),
    )
    if (closed.length === 0) return
    for (const header of closed) await user.click(header)
  }
}

function bookingItem(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    status: 'OPEN',
    description: 'CBC Operating Handle',
    booking_source: 'LOG_BOOK',
    workflow_stage_type: null,
    equipment_node_id: 1843,
    equipment_node_name: 'CBC',
    equipment_path: [],
    routed_sections: [
      { section_id: 8, section_code: 'M4-HR', assignment_source: 'AUTO_MAPPING', status: 'OPEN' },
    ],
    defect_type: { id: 1, code: 'ISOLATED', name: 'Isolated' },
    shed_visit: { id: 700, loco_number: '39126', schedule_family: 'MINOR', schedule_variant: 'IA' },
    created_at: '2026-08-31T04:05:00Z',
    started_by_name: null, started_by_section_code: null, started_at: null,
    attended_by_name: null, attended_by_section_code: null, attended_at: null,
    attendance_remarks: null,
    created_by_name: null, created_by_section_code: null, created_by_planning_section: false,
    ...over,
  }
}

function mockPool(items: ReturnType<typeof bookingItem>[]) {
  server.use(http.get('/api/bookings', () => HttpResponse.json(items)))
}

/** The chip's own element, located by the combined title the helper builds. */
function chip(sectionCode: string, statusWord: string) {
  return screen.getByTitle(`${sectionCode} — ${statusWord}`)
}

async function renderPool(items: ReturnType<typeof bookingItem>[]) {
  const user = userEvent.setup()
  loginAsToken('token-admin')
  mockPool(items)
  renderWithProviders(<BookingPoolPage />)
  await expandAll(user)
  return user
}

describe('per-section status chips', () => {
  it('renders a chip carrying each section code and its own status', async () => {
    await renderPool([
      bookingItem({
        routed_sections: [
          { section_id: 8, section_code: 'M4-HR', assignment_source: 'AUTO_MAPPING', status: 'ATTENDED' },
          { section_id: 9, section_code: 'MACHINE SHOP', assignment_source: 'MANUAL', status: 'OPEN' },
        ],
      }),
    ])

    // The audited scenario exactly: one section done, the other not started. Both must be
    // distinguishable, which is the whole point of the change.
    expect(chip('M4-HR', 'ATTENDED')).toBeInTheDocument()
    expect(chip('MACHINE SHOP', 'OPEN')).toBeInTheDocument()
  })

  it('gives OPEN the neutral tone and the OPEN label', async () => {
    await renderPool([bookingItem()])
    const el = chip('M4-HR', 'OPEN')
    expect(el.closest('.tag')).toHaveClass('tag-neutral')
    expect(el).toHaveTextContent('OPEN')
  })

  it('gives IN_PROGRESS the info tone', async () => {
    await renderPool([
      bookingItem({
        routed_sections: [
          { section_id: 8, section_code: 'M4-HR', assignment_source: 'AUTO_MAPPING', status: 'IN_PROGRESS' },
        ],
      }),
    ])
    const el = chip('M4-HR', 'IN PROGRESS')
    expect(el.closest('.tag')).toHaveClass('tag-info')
  })

  it('gives ATTENDED the success tone', async () => {
    await renderPool([
      bookingItem({
        status: 'ATTENDED',
        routed_sections: [
          { section_id: 8, section_code: 'M4-HR', assignment_source: 'AUTO_MAPPING', status: 'ATTENDED' },
        ],
      }),
    ])
    expect(chip('M4-HR', 'ATTENDED').closest('.tag')).toHaveClass('tag-success')
  })

  it('gives REOPENED the warn tone', async () => {
    await renderPool([
      bookingItem({
        status: 'REOPENED',
        routed_sections: [
          { section_id: 8, section_code: 'M4-HR', assignment_source: 'AUTO_MAPPING', status: 'REOPENED' },
        ],
      }),
    ])
    expect(chip('M4-HR', 'REOPENED').closest('.tag')).toHaveClass('tag-warn')
  })

  it('never conveys status by colour alone', async () => {
    // The glyph is decorative and hidden from assistive tech; the status word travels with it as
    // real text. Greyscale and screen-reader users must get the same information.
    await renderPool([bookingItem()])
    const el = chip('M4-HR', 'OPEN')
    expect(el.querySelector('[aria-hidden="true"]')).not.toBeNull()
    expect(el).toHaveTextContent(/OPEN/)
  })

  it('shows a section count only when the booking really is multi-section', async () => {
    await renderPool([
      bookingItem({
        routed_sections: [
          { section_id: 8, section_code: 'M4-HR', assignment_source: 'AUTO_MAPPING', status: 'ATTENDED' },
          { section_id: 9, section_code: 'MACHINE SHOP', assignment_source: 'MANUAL', status: 'OPEN' },
        ],
      }),
    ])
    expect(screen.getByText('2 sections')).toBeInTheDocument()
  })

  it('shows no count for a single-section booking', async () => {
    await renderPool([bookingItem()])
    await waitFor(() => expect(screen.queryByText(/^1 sections?$/)).toBeNull())
    expect(screen.queryByText(/\d+ sections/)).toBeNull()
  })

  it('still says Not routed when a booking has no assignments', async () => {
    await renderPool([bookingItem({ routed_sections: [] })])
    expect(screen.getByText('Not routed')).toBeInTheDocument()
    expect(screen.queryByText(/\d+ sections/)).toBeNull()
  })

  it('keeps the Added by PPIO provenance badge unchanged', async () => {
    await renderPool([
      bookingItem({
        created_by_name: 'Planner',
        created_by_section_code: 'PPIO',
        created_by_planning_section: true,
      }),
    ])
    expect(screen.getByText('Added by PPIO')).toBeInTheDocument()
  })
})
