import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { friendlyErrorMessage } from '../api/client'
import { ButtonLabel } from '../components/ui/States'

export function LoginPage() {
  const { status, login } = useAuth()
  const location = useLocation()
  const [employeeId, setEmployeeId] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (status === 'authenticated') {
    const from = (location.state as { from?: Location })?.from
    return <Navigate to={from?.pathname ?? '/'} replace />
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)
    setSubmitting(true)
    try {
      await login(employeeId, password)
    } catch (err) {
      setError(friendlyErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-terminal">
        <div className="login-terminal-corner login-terminal-corner-tl" aria-hidden="true" />
        <div className="login-terminal-corner login-terminal-corner-tr" aria-hidden="true" />
        <div className="login-terminal-corner login-terminal-corner-bl" aria-hidden="true" />
        <div className="login-terminal-corner login-terminal-corner-br" aria-hidden="true" />
        <div className="login-scanline" aria-hidden="true" />

        <form className="login-card" onSubmit={handleSubmit}>
          <div className="login-brand">
            <span className="login-brand-mark" aria-hidden="true">
              <span className="login-terminal-dot" aria-hidden="true" />
              ▣
            </span>
            <div>
              <p className="login-eyebrow">Mission Control Access</p>
              <h1 className="login-title">Operations Dashboard</h1>
            </div>
          </div>

          <p className="login-subtitle">Sign in with your RDCMS credentials to reach the command deck.</p>

          <div className="field">
            <label className="field-label field-label-required" htmlFor="employee_id">
              Employee ID
            </label>
            <input
              id="employee_id"
              name="employee_id"
              type="text"
              autoComplete="username"
              value={employeeId}
              onChange={(e) => setEmployeeId(e.target.value)}
              aria-required="true"
              required
            />
          </div>

          <div className="field">
            <label className="field-label field-label-required" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-required="true"
              required
            />
          </div>

          {error && (
            <div className="form-error login-error" role="alert">
              <span aria-hidden="true">⚠</span> {error}
            </div>
          )}

          <button type="submit" className="btn btn-primary login-submit" disabled={submitting} aria-busy={submitting}>
            <ButtonLabel busy={submitting} label="Sign in" busyLabel="Signing in…" />
          </button>

          <p className="login-footnote">Authorized personnel only · all access is logged</p>
        </form>
      </div>
    </div>
  )
}
