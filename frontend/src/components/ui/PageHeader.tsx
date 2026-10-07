import type { ReactNode } from 'react'

/** Consistent page title block: one h1 per page, an optional one-line
 * description, and a right-aligned slot for the page's primary action. */
export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  meta?: ReactNode
}) {
  return (
    <header className="page-header">
      <div className="page-header-main">
        <h1>{title}</h1>
        {description && <p className="page-intro">{description}</p>}
        {meta && <div className="page-header-meta">{meta}</div>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  )
}
