import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { finalizeHandoff, getMe, login as apiLogin } from '../api/auth'
import { clearToken, setToken, getToken, UNAUTHORIZED_EVENT } from '../api/client'
import type { CurrentUser } from '../types'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

interface AuthContextValue {
  user: CurrentUser | null
  status: AuthStatus
  login: (employeeId: string, password: string) => Promise<void>
  logout: () => void
}

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')

  const loadCurrentUser = useCallback(async () => {
    if (!getToken()) {
      setUser(null)
      setStatus('unauthenticated')
      return
    }
    try {
      const me = await getMe()
      setUser(me)
      setStatus('authenticated')
    } catch {
      setUser(null)
      setStatus('unauthenticated')
    }
  }, [])

  // A user arriving from BL-DCMS lands here with no stored token but an HttpOnly bootstrap
  // cookie left by the POST that redirected them. The cookie cannot be read by script - that
  // is the point - so the only way to know whether one exists is to ask, exactly once, before
  // concluding this is an ordinary signed-out visit. A visitor with no cookie pays one fast
  // 401 and carries on to the sign-in page.
  const restoreSession = useCallback(async () => {
    if (!getToken()) {
      try {
        const { access_token: token } = await finalizeHandoff()
        setToken(token)
      } catch {
        // No bootstrap, or one already spent/expired. An ordinary signed-out visit.
      }
    }
    await loadCurrentUser()
  }, [loadCurrentUser])

  useEffect(() => {
    restoreSession()
  }, [restoreSession])

  useEffect(() => {
    const onUnauthorized = () => {
      setUser(null)
      setStatus('unauthenticated')
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [])

  const login = useCallback(async (employeeId: string, password: string) => {
    const { access_token: token } = await apiLogin(employeeId, password)
    setToken(token)
    await loadCurrentUser()
  }, [loadCurrentUser])

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
    setStatus('unauthenticated')
  }, [])

  const value = useMemo(() => ({ user, status, login, logout }), [user, status, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
