import { useEffect, useState } from 'react'
import { getResolvedSections } from '../../api/equipment'
import { friendlyErrorMessage } from '../../api/client'
import type { ResolvedSections } from '../../types'

/** Read-only "Routes to: M1-HR, M2-HR" preview for a selected equipment
 * node — lets the operator verify automatic routing before submitting.
 * Deliberately offers no way to edit the mapping; that stays an Admin
 * Equipment Mapping action. */
export function RoutingPreview({ nodeId }: { nodeId: number }) {
  const [resolved, setResolved] = useState<ResolvedSections | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getResolvedSections(nodeId)
      .then((result) => {
        if (!cancelled) setResolved(result)
      })
      .catch((err) => {
        if (!cancelled) setError(friendlyErrorMessage(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [nodeId])

  if (loading) {
    return (
      <div className="routing-preview" role="status">
        Checking routing…
      </div>
    )
  }

  if (error) {
    return (
      <div className="routing-preview form-error" role="alert">
        {error}
      </div>
    )
  }

  if (!resolved) return null

  if (resolved.resolution === 'NONE') {
    return (
      <div className="routing-preview routing-preview-unmapped">
        No section mapping exists for this equipment. This booking cannot be submitted until it's
        mapped in Admin → Equipment Mapping.
      </div>
    )
  }

  return (
    <div className="routing-preview">
      <span>Routes to: </span>
      <strong>{resolved.section_codes.join(', ')}</strong>
    </div>
  )
}
