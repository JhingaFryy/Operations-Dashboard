import { useEffect, useRef, useState } from 'react'
import { searchLocomotives } from '../../api/locomotives'
import { friendlyErrorMessage } from '../../api/client'
import type { Locomotive } from '../../types'

const MIN_QUERY_LENGTH = 2
const DEBOUNCE_MS = 300

/** Searchable locomotive dropdown — never lets the operator type an
 * arbitrary, unvalidated locomotive number; a selection can only come from
 * Loco Master's own search results. */
export function LocomotiveSelect({
  value,
  onChange,
}: {
  value: Locomotive | null
  onChange: (locomotive: Locomotive | null) => void
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Locomotive[] | null>(null)
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
      searchLocomotives(query.trim(), 20)
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
  }, [query])

  if (value) {
    return (
      <div className="field">
        <label className="field-label">Locomotive</label>
        <div className="locomotive-selected">
          <strong>{value.loco_number}</strong> ({value.loco_type})
          <button
            type="button"
            className="btn btn-secondary btn-small"
            onClick={() => {
              onChange(null)
              setQuery('')
              setResults(null)
            }}
          >
            Change
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="field locomotive-select">
      <label className="field-label" htmlFor="locomotive-search">
        Locomotive
      </label>
      <input
        id="locomotive-search"
        type="search"
        placeholder="Search locomotive number…"
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
        <ul className="search-results" aria-label="Locomotive results">
          {results.length === 0 ? (
            <li className="search-results-empty">No matching locomotives found.</li>
          ) : (
            results.map((loco) => (
              <li key={loco.loco_number}>
                <button
                  type="button"
                  className="search-result-item"
                  onClick={() => onChange(loco)}
                >
                  <span className="search-result-name">{loco.loco_number}</span>
                  <span className="search-result-path">{loco.loco_type}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}
