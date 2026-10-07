import { apiFetch } from './client'
import type { CurrentUser } from '../types'

interface LoginResponse {
  access_token: string
  token_type: string
}

export function login(employeeId: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ employee_id: employeeId, password }),
  })
}

/** Collect a session left behind by a BL-DCMS handoff, if there is one.
 *
 * A user arriving from BL-DCMS reaches this application as a form POST to its ROOT, which
 * nginx routes to the backend. That request redeems the one-time code, authorizes the user
 * under this application's own rules, and leaves an HttpOnly bootstrap cookie before
 * redirecting to `/`. This call is what the freshly-loaded SPA uses to exchange that cookie
 * for its normal token.
 *
 * The cookie is HttpOnly, so no script can read it and nothing here needs to: the browser
 * attaches it to this same-origin request automatically. The token comes back in the response
 * body and is stored exactly as a password login's is. Nothing travels in a URL at any point. */
export function finalizeHandoff(): Promise<LoginResponse> {
  return apiFetch<LoginResponse>('/api/auth/handoff/finalize', { method: 'POST' })
}

export function getMe(): Promise<CurrentUser> {
  return apiFetch<CurrentUser>('/api/auth/me')
}
