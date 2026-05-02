import { useState, useEffect, useRef } from 'react'

/**
 * Autocomplete suggestions from /api/suggest.
 *
 * Tighter debounce than search (100ms) so the dropdown feels
 * instant. Returns [] when the prefix is too short — picks up at 2
 * chars to avoid spamming the server with single-letter queries.
 */
export function useSuggestions(prefix, { limit = 8, minChars = 2 } = {}) {
  const [suggestions, setSuggestions] = useState([])
  const abortRef = useRef(null)
  const debounceRef = useRef(null)

  useEffect(() => {
    const p = (prefix || '').trim()
    if (p.length < minChars) {
      setSuggestions([])
      if (abortRef.current) abortRef.current.abort()
      return
    }

    if (debounceRef.current) clearTimeout(debounceRef.current)

    debounceRef.current = setTimeout(async () => {
      if (abortRef.current) abortRef.current.abort()
      const controller = new AbortController()
      abortRef.current = controller

      try {
        const url = `/api/suggest?q=${encodeURIComponent(p)}&limit=${limit}`
        const resp = await fetch(url, { signal: controller.signal })
        if (!resp.ok) return
        const data = await resp.json()
        setSuggestions(data.results || [])
      } catch (err) {
        if (err.name !== 'AbortError') {
          setSuggestions([])
        }
      }
    }, 100)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [prefix, limit, minChars])

  return suggestions
}
