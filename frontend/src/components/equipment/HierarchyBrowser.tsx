import { useEffect, useState } from 'react'
import { listNodes, type EquipmentScope } from '../../api/equipment'
import { friendlyErrorMessage } from '../../api/client'
import type { EquipmentNode } from '../../types'

interface Props {
  /** `{ locoNumber }` on booking forms - the family, and therefore the whole cascade, is decided
   * by the locomotive's technology on the server. `{ family }` on the equipment mapping screen. */
  scope: EquipmentScope
  onSelectNode: (node: EquipmentNode, path: EquipmentNode[]) => void
  /** Override when more than one HierarchyBrowser can be on the page at
   * once (e.g. one per Shed In booking row) — ids must stay unique. */
  idPrefix?: string
}

/** Cascading Equipment -> Type/Version -> ... dropdowns. A new level is
 * only fetched/shown once its parent level's selection reports
 * has_children — no assumption is made about how many levels exist or
 * that every branch has all of them. Changing a level clears every level
 * below it. Picking any node — leaf or not — immediately becomes the
 * active selection; the user is never forced deeper. */
export function HierarchyBrowser({ scope, onSelectNode, idPrefix = 'hierarchy-level' }: Props) {
  // A plain string, so the reset effect below re-runs when the locomotive (or family) actually
  // changes and not on every re-render that rebuilds the object.
  const scopeKey = scope.locoNumber ? `loco:${scope.locoNumber}` : `family:${scope.family}`
  const [levelOptions, setLevelOptions] = useState<EquipmentNode[][]>([])
  const [chain, setChain] = useState<EquipmentNode[]>([])
  const [loadingLevel, setLoadingLevel] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setChain([])
    setLevelOptions([])
    setError(null)
    setLoadingLevel(0)
    listNodes(scope)
      .then((roots) => {
        if (cancelled) return
        setLevelOptions([roots])
      })
      .catch((err) => {
        if (cancelled) return
        setError(friendlyErrorMessage(err))
      })
      .finally(() => {
        if (!cancelled) setLoadingLevel(null)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey])

  async function handleSelect(levelIndex: number, nodeId: string) {
    setError(null)

    if (!nodeId) {
      // Cleared this level: keep this level's own options, drop everything below.
      const newChain = chain.slice(0, levelIndex)
      setChain(newChain)
      setLevelOptions((prev) => prev.slice(0, levelIndex + 1))
      return
    }

    const node = levelOptions[levelIndex].find((n) => n.id === Number(nodeId))
    if (!node) return

    const newChain = [...chain.slice(0, levelIndex), node]
    setChain(newChain)
    setLevelOptions((prev) => prev.slice(0, levelIndex + 1))
    onSelectNode(node, newChain)

    if (node.has_children) {
      setLoadingLevel(levelIndex + 1)
      try {
        const children = await listNodes(scope, node.id)
        setLevelOptions((prev) => [...prev.slice(0, levelIndex + 1), children])
      } catch (err) {
        setError(friendlyErrorMessage(err))
      } finally {
        setLoadingLevel(null)
      }
    }
  }

  if (error) {
    return (
      <div className="form-error" role="alert">
        {error}
      </div>
    )
  }

  if (levelOptions.length === 0) {
    return loadingLevel === 0 ? <div role="status">Loading equipment…</div> : null
  }

  return (
    <div className="hierarchy-browser">
      {levelOptions.map((options, i) => (
        <div className="field" key={i}>
          <label className="field-label" htmlFor={`${idPrefix}-${i}`}>
            {i === 0 ? 'Equipment' : `Level ${i + 1}`}
          </label>
          {options.length === 0 ? (
            <div className="field-empty">No items</div>
          ) : (
            <select
              id={`${idPrefix}-${i}`}
              value={chain[i]?.id ?? ''}
              onChange={(e) => handleSelect(i, e.target.value)}
            >
              <option value="">-- Select --</option>
              {options.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                </option>
              ))}
            </select>
          )}
        </div>
      ))}
      {loadingLevel !== null && loadingLevel > 0 && <div role="status">Loading…</div>}
    </div>
  )
}
