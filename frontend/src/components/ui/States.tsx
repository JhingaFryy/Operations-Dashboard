import type { ReactNode } from 'react'

/** The three page states are deliberately distinct components with distinct
 * copy and ARIA roles, so "the server failed" is never presented as "there
 * is nothing here":
 *
 *   LoadingState  — request in flight        (role="status")
 *   EmptyState    — request succeeded, zero rows
 *   ErrorState    — request failed, or the caller is not permitted
 *                                            (role="alert")
 */

/** Skeleton shapes for a FIRST load, matched to the layout that will
 * replace them. `inline` (default) is the compact spinner row used inside
 * panels and for small secondary fetches. */
export type LoadingLayout = 'inline' | 'overview' | 'list' | 'cards' | 'workflow'

function SkeletonLine({ width, large = false }: { width: string; large?: boolean }) {
  return <span className={`skeleton skeleton-line${large ? ' skeleton-line-lg' : ''}`} style={{ width }} />
}

function SkeletonRows({ count }: { count: number }) {
  return (
    <div className="skeleton-list">
      {Array.from({ length: count }, (_, i) => (
        <div className="skeleton-row" key={i}>
          <SkeletonLine width="70%" large />
          <SkeletonLine width="80%" />
          <SkeletonLine width="60%" />
          <SkeletonLine width="75%" />
          <span className="skeleton" style={{ height: '2rem', borderRadius: 'var(--radius-sm)' }} />
        </div>
      ))}
    </div>
  )
}

function SkeletonCards({ count }: { count: number }) {
  return (
    <div className="skeleton-card-grid">
      {Array.from({ length: count }, (_, i) => (
        <div className="skeleton-card" key={i}>
          <SkeletonLine width="45%" large />
          <SkeletonLine width="85%" />
          <SkeletonLine width="65%" />
          <SkeletonLine width="35%" />
        </div>
      ))}
    </div>
  )
}

export function LoadingState({ label = 'Loading…', layout = 'inline' }: { label?: string; layout?: LoadingLayout }) {
  if (layout === 'inline') {
    return (
      <div className="state-block state-loading" role="status">
        <span className="state-spinner" aria-hidden="true" />
        <span>{label}</span>
      </div>
    )
  }
  return (
    <div className="state-loading-skeleton">
      <div className="state-loading-label" role="status">
        <span className="state-spinner" aria-hidden="true" />
        <span>{label}</span>
      </div>
      <div aria-hidden="true">
        {layout === 'overview' && (
          <>
            <div className="skeleton-metric-grid">
              {Array.from({ length: 4 }, (_, i) => (
                <div className="skeleton-metric" key={i}>
                  <SkeletonLine width="38%" large />
                  <SkeletonLine width="62%" />
                  <SkeletonLine width="80%" />
                </div>
              ))}
            </div>
            <SkeletonRows count={4} />
          </>
        )}
        {layout === 'list' && <SkeletonRows count={5} />}
        {layout === 'cards' && <SkeletonCards count={6} />}
        {layout === 'workflow' && (
          <>
            <div className="skeleton-card" style={{ minHeight: 88, marginBottom: 'var(--space-5)' }}>
              <SkeletonLine width="24%" large />
              <SkeletonLine width="48%" />
            </div>
            <SkeletonCards count={3} />
          </>
        )}
      </div>
    </div>
  )
}

/** Shown while data already on screen is being refreshed (e.g. after a
 * mutation) - the content stays visible instead of blanking to a loader. */
export function RefreshIndicator({ label = 'Updating…' }: { label?: string }) {
  return (
    <span className="refresh-indicator" role="status">
      <span className="state-spinner" aria-hidden="true" />
      {label}
    </span>
  )
}

/** Button content that keeps the button's width stable while busy: the idle
 * label stays in the layout (CSS makes it transparent under the spinner via
 * the button's aria-busy), and the busy wording is announced to assistive
 * technology only. */
export function ButtonLabel({ busy, label, busyLabel }: { busy: boolean; label: string; busyLabel: string }) {
  return (
    <>
      {label}
      {busy && <span className="visually-hidden"> {busyLabel}</span>}
    </>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="state-block state-empty">
      <span className="state-glyph" aria-hidden="true">
        ○
      </span>
      <div className="state-body">
        <p className="state-title">{title}</p>
        {description && <p className="state-description">{description}</p>}
        {action && <div className="state-action">{action}</div>}
      </div>
    </div>
  )
}

/** `variant` separates a genuine request failure from an authorization
 * refusal — they need different copy and a different next step, and neither
 * should ever read as "no records". */
export function ErrorState({
  message,
  variant = 'error',
  onRetry,
}: {
  message: string
  variant?: 'error' | 'unauthorized'
  onRetry?: () => void
}) {
  const unauthorized = variant === 'unauthorized'
  return (
    <div className={`state-block state-error${unauthorized ? ' state-unauthorized' : ''}`} role="alert">
      <span className="state-glyph" aria-hidden="true">
        {unauthorized ? '⛔' : '!'}
      </span>
      <div className="state-body">
        <p className="state-title">{unauthorized ? 'Not available to your account' : 'Could not load this'}</p>
        <p className="state-description">{message}</p>
        {onRetry && !unauthorized && (
          <div className="state-action">
            <button type="button" className="btn btn-secondary btn-small" onClick={onRetry}>
              Try again
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
