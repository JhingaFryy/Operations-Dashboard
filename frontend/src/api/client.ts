const TOKEN_KEY = 'od_token'

export class ApiError extends Error {
  status: number
  /** The parsed `detail` field of the error response, whatever shape it
   * took — a plain string for most endpoints, or a structured object like
   * {code, message, booking_index, ...} for Shed In validation failures. */
  detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

/** Fired whenever a request comes back 401 so AuthContext can react without
 * this module needing to know about routing/React state. */
export const UNAUTHORIZED_EVENT = 'od:unauthorized'

async function extractDetail(res: Response): Promise<unknown> {
  try {
    const body = await res.clone().json()
    return body?.detail
  } catch {
    return undefined
  }
}

function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
    return (detail as { message: string }).message
  }
  return fallback
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  try {
    res = await fetch(path, { ...options, headers })
  } catch {
    throw new ApiError(0, 'Could not reach the Operations Dashboard server.')
  }

  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    const detail = await extractDetail(res)
    throw new ApiError(401, messageFromDetail(detail, 'Your session has expired. Please log in again.'), detail)
  }

  if (!res.ok) {
    const detail = await extractDetail(res)
    throw new ApiError(res.status, messageFromDetail(detail, `Request failed (${res.status}).`), detail)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** A binary document (e.g. a signed checksheet PDF) fetched with the session's Authorization
 * header - never a token in the URL. Same 401 and error handling as apiFetch. */
export async function apiFetchBlob(path: string): Promise<Blob> {
  const token = getToken()
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  try {
    res = await fetch(path, { headers })
  } catch {
    throw new ApiError(0, 'Could not reach the Operations Dashboard server.')
  }

  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    const detail = await extractDetail(res)
    throw new ApiError(401, messageFromDetail(detail, 'Your session has expired. Please log in again.'), detail)
  }
  if (!res.ok) {
    const detail = await extractDetail(res)
    throw new ApiError(res.status, messageFromDetail(detail, `Request failed (${res.status}).`), detail)
  }
  return res.blob()
}

/** A human-readable message safe to show directly in the UI — never a raw
 * stack trace or backend-internal detail. */
export function friendlyErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return 'Could not reach the Operations Dashboard server.'
    if (err.status === 403) return "You don't have permission to do that."
    if (err.status === 404) return 'The requested item could not be found.'
    if (err.status === 409) return err.message || 'That conflicts with existing data.'
    if (err.status === 422) return err.message || 'That request was invalid.'
    if (err.status === 502 || err.status === 503) {
      return 'Equipment service is currently unavailable. Please try again shortly.'
    }
    return err.message || 'Something went wrong.'
  }
  return 'Something went wrong.'
}
