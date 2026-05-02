import { useState, useEffect } from 'react'

/**
 * Filter dropdown data: meeting bodies, top-N speakers, date range.
 *
 * Cached at the module level — the facets only change when the
 * pipeline rebuilds the FTS index, so a single fetch on app mount
 * is plenty.
 */
let _cache = null
let _inflight = null

export function useFacets() {
  const [facets, setFacets] = useState(_cache)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (_cache) {
      setFacets(_cache)
      return
    }

    const inflight = _inflight || (
      _inflight = fetch('/api/facets')
        .then(r => {
          if (!r.ok) throw new Error(`facets failed: ${r.status}`)
          return r.json()
        })
        .then(data => {
          _cache = data
          return data
        })
        .finally(() => { _inflight = null })
    )

    let cancelled = false
    inflight
      .then(data => { if (!cancelled) setFacets(data) })
      .catch(err => { if (!cancelled) setError(err.message) })

    return () => { cancelled = true }
  }, [])

  return { facets, error }
}
