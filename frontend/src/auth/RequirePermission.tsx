import type { ReactNode } from 'react'

/** Frontend-side gate for a page whose real enforcement lives on the
 * backend (the backend rejects the underlying requests with 403
 * regardless of what this renders). Its job is just to avoid showing a
 * confusing, guaranteed-to-fail form to someone who manually navigates to
 * a URL they don't have the nav link for. */
export function RequirePermission({
  allowed,
  children,
}: {
  allowed: boolean
  children: ReactNode
}) {
  if (!allowed) {
    return (
      <div className="panel permission-denied" role="alert">
        <h2>Permission denied</h2>
        <p>You don't have access to this page.</p>
      </div>
    )
  }

  return <>{children}</>
}
