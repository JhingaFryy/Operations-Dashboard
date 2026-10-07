import { useEffect, useId, useRef, useState } from 'react'
import { searchNodes, type EquipmentScope } from '../../api/equipment'
import { friendlyErrorMessage } from '../../api/client'
import type { EquipmentNode, EquipmentNodeSearchResult, PathItem } from '../../types'

export interface EquipmentSelection {
  node: EquipmentNode
  path: PathItem[]
}

const MIN_QUERY_LENGTH = 2
const DEBOUNCE_MS = 350

/** Search-only equipment selection: one input, one list, ONE selected value.
 *
 * This replaced a pair of controls - cascading "Equipment / Level 2 / …" dropdowns plus a
 * separate search box - that each wrote to a different piece of state for the same value.
 * That split caused three defects at once: a search result set a selection the Equipment
 * dropdown never showed (so the user believed nothing had been selected), touching the
 * dropdown afterwards silently replaced it, and clearing the dropdown back to "-- Select --"
 * left the previously chosen node id in place, so the form submitted equipment the user
 * could see they had cleared.
 *
 * The fix is structural rather than a patch: the SELECTION LIVES IN THE PARENT AND NOWHERE
 * ELSE. This component holds only what the user is typing. Every path that can change the
 * answer - picking a result, clearing it, editing the text after a selection - goes through
 * `onChange`, so there is no second copy to drift and no hidden id to go stale.
 *
 * Free text is never a selection: `onChange(null)` fires the moment the query is edited
 * after a choice, and the only way to produce a non-null selection is to pick a record the
 * server returned.
 *
 * Search stays family-restricted like every other equipment read - scoped to the locomotive
 * on a booking form, so equipment of the other technology cannot appear, let alone be picked. */
export function EquipmentSelector({
  id,
  scope,
  selection,
  onChange,
  disabled = false,
}: {
  id: string
  scope: EquipmentScope
  selection: EquipmentSelection | null
  onChange: (selection: EquipmentSelection | null) => void
  /** Rendered under "No equipment found" - the "+ Add Equipment" affordance, when the
   * viewer is allowed one. Nothing is rendered for anyone else. */
  onNoResults?: React.ReactNode
  disabled?: boolean
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<EquipmentNodeSearchResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeIndex, setActiveIndex] = useState(-1)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const requestIdRef = useRef(0)
  const listId = useId()

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    setActiveIndex(-1)

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
          // A failed search must not silently drop an equipment choice already made, and
          // must not take the rest of the Shed In form with it.
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

  function select(result: EquipmentNodeSearchResult) {
    const { path, ...node } = result
    onChange({ node, path })
    // The list has done its job; the selected item is now shown in its place.
    setQuery('')
    setResults(null)
    setActiveIndex(-1)
  }

  function clear() {
    onChange(null)
    setQuery('')
    setResults(null)
    setActiveIndex(-1)
  }

  function handleQueryChange(next: string) {
    setQuery(next)
    // Typing after a selection abandons it. Without this the input would say one thing
    // while a hidden id said another - exactly the defect this component replaced.
    if (selection) onChange(null)
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!results || results.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((i) => (i + 1) % results.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((i) => (i <= 0 ? results.length - 1 : i - 1))
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault()
      select(results[activeIndex])
    } else if (event.key === 'Escape') {
      setResults(null)
      setActiveIndex(-1)
    }
  }

  if (selection) {
    return (
      <div className="field equipment-selector">
        <span className="field-label" id={`${id}-label`}>
          Equipment
        </span>
        <div className="equipment-selected" aria-labelledby={`${id}-label`}>
          <span className="equipment-selected-name">{selection.node.name}</span>
          {selection.path.length > 1 && (
            <span className="equipment-selected-path">
              {selection.path.map((p) => p.name).join(' → ')}
            </span>
          )}
          <button
            type="button"
            className="equipment-selected-clear"
            aria-label={`Clear selected equipment ${selection.node.name}`}
            onClick={clear}
            disabled={disabled}
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
      </div>
    )
  }

  const showEmpty = !loading && !error && results !== null && results.length === 0

  return (
    <div className="field equipment-selector">
      <label className="field-label field-label-required" htmlFor={id}>
        Equipment
      </label>
      <input
        id={id}
        type="search"
        role="combobox"
        autoComplete="off"
        aria-expanded={Boolean(results && results.length > 0)}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        placeholder="Search equipment…"
        value={query}
        disabled={disabled}
        onChange={(e) => handleQueryChange(e.target.value)}
        onKeyDown={handleKeyDown}
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

      {showEmpty && (
        <div className="equipment-no-results">
          <p className="field-hint">No equipment found</p>
        </div>
      )}

      {!loading && !error && results && results.length > 0 && (
        <ul className="search-results" id={listId} role="listbox" aria-label="Equipment results">
          {results.map((result, i) => (
            <li key={result.id} role="presentation">
              <button
                type="button"
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === activeIndex}
                className={
                  i === activeIndex ? 'search-result-item search-result-item-active' : 'search-result-item'
                }
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => select(result)}
              >
                <span className="search-result-name">{result.name}</span>
                <span className="search-result-path">
                  {result.path.map((p) => p.name).join(' → ')}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
