import { useEffect, useRef, useState } from 'react'
import { searchNodes, type EquipmentScope } from '../../api/equipment'
import { friendlyErrorMessage } from '../../api/client'
import type { EquipmentNodeSearchResult } from '../../types'

const MIN_QUERY_LENGTH = 2
const DEBOUNCE_MS = 350

export function EquipmentSearchBox({
  scope,
  onSelectResult,
  id = 'equipment-search',
}: {
  /** Search is family-restricted like every other equipment read: on a booking form it is scoped
   * to the locomotive, so a result from the other technology cannot appear, let alone be picked. */
  scope: EquipmentScope
  onSelectResult: (result: EquipmentNodeSearchResult) => void
  /** Override when more than one EquipmentSearchBox can be on the page at
   * once (e.g. one per Shed In booking row) — ids must stay unique. */
  id?: string
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<EquipmentNodeSearchResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const requestIdRef = useRef(0)

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)

    if (query.trim().length < MIN_QUERY_LENGTH) {
      setResults(null)
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    debounceRef.current = setTimeout(() => {
      const requestId = ++requestIdRef.current
      searchNodes(query.trim(), scope)
        .then((items) => {
          if (requestId !== requestIdRef.current) return
          setResults(items)
          setError(null)
        })
        .catch((err) => {
          if (requestId !== requestIdRef.current) return
          setError(friendlyErrorMessage(err))
          setResults(null)
        })
        .finally(() => {
          if (requestId === requestIdRef.current) setLoading(false)
        })
    }, DEBOUNCE_MS)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, scope.locoNumber, scope.family])

  return (
    <div className="equipment-search">
      <label className="field-label" htmlFor={id}>
        Equipment Search
      </label>
      <input
        id={id}
        type="search"
        placeholder="Search equipment by name…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {query.trim().length > 0 && query.trim().length < MIN_QUERY_LENGTH && (
        <div className="field-hint">Type at least {MIN_QUERY_LENGTH} characters to search.</div>
      )}

      {loading && (
        <div className="field-loading" role="status">
          Searching…
        </div>
      )}

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}

      {!loading && !error && results && (
        <ul className="search-results" aria-label="Search results">
          {results.length === 0 ? (
            <li className="search-results-empty">No matching equipment found.</li>
          ) : (
            results.map((result) => (
              <li key={result.id}>
                <button
                  type="button"
                  className="search-result-item"
                  onClick={() => onSelectResult(result)}
                >
                  <span className="search-result-name">{result.name}</span>
                  <span className="search-result-path">
                    {result.path.map((p) => p.name).join(' → ')}
                  </span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
