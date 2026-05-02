import { useState, useEffect, useRef } from 'react'

/**
 * Server-backed full-text search hook.
 *
 * Replaces useFlexSearch. Instead of downloading a 40 MB chunked
 * FlexSearch index on every cold load, this calls /api/search with
 * a debounced query and returns the API's BM25-ranked results +
 * pre-highlighted HTML snippets.
 *
 * Filters that affect the *search* corpus (body, speaker, date
 * range) are passed to the server. Sorting + body-only browse
 * (no query) stays client-side via useSearch.
 *
 * The hook is fire-and-forget: every keystroke schedules a request,
 * the previous request is aborted on the way out, and only the most
 * recent response updates state.
 *
 * @param {string} query
 * @param {{meeting_body?: string|null, speaker?: string|null, date_after?: string|null, date_before?: string|null, limit?: number}} options
 */
export function useServerSearch(query, options = {}) {
  const { meeting_body, speaker, date_after, date_before, limit = 100 } = options

  const [results, setResults] = useState([])
  const [isSearching, setIsSearching] = useState(false)
  const [error, setError] = useState(null)
  const abortRef = useRef(null)
  const debounceRef = useRef(null)

  useEffect(() => {
    const trimmed = (query || '').trim()
    if (!trimmed) {
      setResults([])
      setIsSearching(false)
      setError(null)
      if (abortRef.current) abortRef.current.abort()
      return
    }

    if (debounceRef.current) clearTimeout(debounceRef.current)

    debounceRef.current = setTimeout(async () => {
      if (abortRef.current) abortRef.current.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setIsSearching(true)
      setError(null)

      try {
        const body = { q: trimmed, limit }
        if (meeting_body) body.meeting_body = meeting_body
        if (speaker) body.speaker = speaker
        if (date_after) body.date_after = date_after
        if (date_before) body.date_before = date_before

        const resp = await fetch('/api/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })

        if (!resp.ok) {
          throw new Error(`Search failed: ${resp.status}`)
        }

        const data = await resp.json()
        setResults(data.results || [])
      } catch (err) {
        if (err.name === 'AbortError') return
        setError(err.message || 'Search failed')
        setResults([])
      } finally {
        if (!controller.signal.aborted) setIsSearching(false)
      }
    }, 250)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, meeting_body, speaker, date_after, date_before, limit])

  return { results, isSearching, error }
}
