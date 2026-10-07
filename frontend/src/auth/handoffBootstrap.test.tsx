import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { server } from '../mocks/server'
import { renderWithProviders } from '../test/testUtils'
import { getToken } from '../api/client'
import { HomePlaceholder } from '../test/handoffFixtures'

/** The profile load that follows sign-in must succeed, or its 401 clears the token that was
 * just stored - the same coupling a password login has. */
const handedOverUser = {
  id: 20,
  employee_id: 'SHIFTSUP',
  name: 'Sam Shift',
  role: 'Supervisor',
  section: { id: 18, code: 'SHIFT', name: 'SHIFT' },
  dashboard_access: true,
  permissions: { can_add_booking_sections: false, can_manage_equipment_mapping: false },
  capabilities: {
    can_access_operations_dashboard: true,
    can_manage_loco_movement: true,
    can_manage_own_section_bookings: true,
    can_access_all_sections: false,
    can_admin: false,
  },
}

const acceptsTheHandedOverSession = http.get('/api/auth/me', () => HttpResponse.json(handedOverUser))

/**
 * Collecting a session left behind by a BL-DCMS handoff.
 *
 * A user arriving from BL-DCMS reaches this application's ROOT as a form POST. nginx routes
 * that to the backend, which redeems the one-time code, leaves an HttpOnly bootstrap cookie and
 * redirects to `/`. By the time this SPA loads there is no code and no token in the URL - only
 * a cookie the SPA cannot read. So on boot, with no stored token, it asks once whether there is
 * a session to collect.
 */
describe('handoff bootstrap on boot', () => {
  it('12. collects the token when a bootstrap exists, and stores it normally', async () => {
    server.use(
      http.post('/api/auth/handoff/finalize', () =>
        HttpResponse.json({ access_token: 'handed-over', token_type: 'bearer' }),
      ),
      acceptsTheHandedOverSession,
    )

    renderWithProviders(<HomePlaceholder />)

    await waitFor(() => expect(getToken()).toBe('handed-over'))
  })

  it('asks exactly once, and only when there is no stored token', async () => {
    let calls = 0
    server.use(
      http.post('/api/auth/handoff/finalize', () => {
        calls += 1
        return HttpResponse.json({ access_token: 'handed-over', token_type: 'bearer' })
      }),
      acceptsTheHandedOverSession,
    )

    renderWithProviders(<HomePlaceholder />)
    await waitFor(() => expect(getToken()).toBe('handed-over'))
    await new Promise((resolve) => setTimeout(resolve, 100))

    expect(calls).toBe(1)
  })

  it('an ordinary signed-out visit is unaffected by the attempt', async () => {
    // The default handler answers 401 - there is no bootstrap. The app must simply carry on.
    renderWithProviders(<HomePlaceholder />)

    await screen.findByText('rendered')
    expect(getToken()).toBeNull()
  })

  it('5/6/7. nothing is ever read from the URL - no code, token or password', async () => {
    let requestUrl = ''
    let body = ''
    server.use(
      http.post('/api/auth/handoff/finalize', async ({ request }) => {
        requestUrl = request.url
        body = await request.text()
        return HttpResponse.json({ access_token: 'handed-over', token_type: 'bearer' })
      }),
      acceptsTheHandedOverSession,
    )

    renderWithProviders(<HomePlaceholder />)

    await waitFor(() => expect(requestUrl).not.toBe(''))
    // The SPA sends nothing at all: the browser attaches the HttpOnly cookie by itself. The
    // endpoint's own path naturally contains the word "handoff"; what must never appear is a
    // code, a token or any value carried in a query string or a body.
    expect(requestUrl.endsWith('/api/auth/handoff/finalize')).toBe(true)
    expect(requestUrl.includes('?')).toBe(false)
    expect(requestUrl.includes('#')).toBe(false)
    expect(body).toBe('')
    expect(window.location.search).toBe('')
    expect(window.location.hash).toBe('')
  })
})

/**
 * The retired intermediate route.
 *
 * The browser must never visit, display or record `/sign-in-from-bldcms`. It is gone from the
 * router, the pages, the API layer and the nginx config; what replaced it is a method split on
 * the root, which is invisible to the address bar.
 */
describe('no intermediate sign-in route survives', () => {
  it('1/2. nothing in the frontend source references it', async () => {
    const modules = import.meta.glob('../**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true })

    const offenders = Object.entries(modules)
      .filter(([path]) => !path.includes('handoffBootstrap.test'))
      .filter(([, source]) => String(source).includes('sign-in-from-bldcms'))
      .map(([path]) => path)

    expect(offenders).toEqual([])
  })

  it('1. the router has no such route, and no page file remains', async () => {
    const modules = import.meta.glob('../**/*.tsx', { query: '?raw', import: 'default', eager: true })

    expect(Object.keys(modules).some((p) => p.includes('HandoffSignInPage'))).toBe(false)
    const app = String(modules['../App.tsx'] ?? '')
    expect(app).not.toContain('sign-in-from-bldcms')
    expect(app).not.toContain('HandoffSignInPage')
  })
})
