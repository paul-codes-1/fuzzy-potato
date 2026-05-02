import { useMemo, useCallback } from 'react'
import { useServerSearch } from './useServerSearch'

/**
 * Search + filter + sort state for the meeting list.
 *
 * Two modes:
 * - **Browse mode** (no `q`): metadata-only filter/sort happens
 *   client-side over the already-loaded index.json.
 * - **Search mode** (`q` set): query goes to /api/search; the server
 *   returns BM25-ranked clips with pre-marked HTML snippets.
 *
 * Filters (body, speaker, date range) apply in both modes — they're
 * client-side filters on the metadata in browse mode, and passed to
 * the server in search mode so BM25 ranking is filtered too.
 */
export function useSearch(meetings, searchParams, setSearchParams) {
  const query = searchParams.get('q') || ''
  const selectedBody = searchParams.get('body') || null
  const selectedSpeaker = searchParams.get('speaker') || null
  const sortBy = searchParams.get('sort') || 'clip-desc'

  const updateParam = useCallback((key, value, defaultValue) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (!value || value === defaultValue) {
        next.delete(key)
      } else {
        next.set(key, value)
      }
      if (key !== 'page') {
        next.delete('page')
      }
      return next
    }, { replace: true })
  }, [setSearchParams])

  const setQuery = useCallback((v) => updateParam('q', v, ''), [updateParam])
  const setSelectedBody = useCallback((v) => updateParam('body', v, null), [updateParam])
  const setSelectedSpeaker = useCallback((v) => updateParam('speaker', v, null), [updateParam])
  const setSortBy = useCallback((v) => updateParam('sort', v, 'clip-desc'), [updateParam])

  const { results: serverResults, isSearching, error: searchError } = useServerSearch(
    query,
    {
      meeting_body: selectedBody,
      speaker: selectedSpeaker,
      limit: 200,
    },
  )

  const meetingBodies = useMemo(() => {
    const bodies = new Set()
    meetings.forEach(m => { if (m.meeting_body) bodies.add(m.meeting_body) })
    return Array.from(bodies).sort()
  }, [meetings])

  const meetingsById = useMemo(() => {
    const map = new Map()
    meetings.forEach(m => map.set(m.clip_id, m))
    return map
  }, [meetings])

  const sortMeetings = useCallback((meetingList) => {
    const sorted = [...meetingList]
    switch (sortBy) {
      case 'clip-desc':
        return sorted.sort((a, b) => (b.clip_id || 0) - (a.clip_id || 0))
      case 'date-desc':
        return sorted.sort((a, b) => (b.date || '').localeCompare(a.date || ''))
      case 'date-asc':
        return sorted.sort((a, b) => (a.date || '').localeCompare(b.date || ''))
      case 'title':
        return sorted.sort((a, b) => (a.title || '').localeCompare(b.title || ''))
      default:
        return sorted
    }
  }, [sortBy])

  const { filteredMeetings, searchSnippets } = useMemo(() => {
    const snippets = new Map()

    if (query.trim()) {
      // Search mode — server already ranked + filtered. Map back to
      // the local meeting objects (preserving snippet HTML).
      const ranked = []
      for (const r of serverResults) {
        const meeting = meetingsById.get(r.clip_id)
        if (meeting) {
          ranked.push(meeting)
          if (r.snippet) snippets.set(r.clip_id, r.snippet)
        }
      }
      return { filteredMeetings: ranked, searchSnippets: snippets }
    }

    // Browse mode — no query, just filter + sort client-side.
    let results = meetings
    if (selectedBody) {
      results = results.filter(m => m.meeting_body === selectedBody)
    }
    if (selectedSpeaker) {
      results = results.filter(m => Array.isArray(m.speakers) && m.speakers.includes(selectedSpeaker))
    }
    results = sortMeetings(results)
    return { filteredMeetings: results, searchSnippets: snippets }
  }, [meetings, query, serverResults, selectedBody, selectedSpeaker, sortMeetings, meetingsById])

  return {
    query,
    setQuery,
    selectedBody,
    setSelectedBody,
    selectedSpeaker,
    setSelectedSpeaker,
    sortBy,
    setSortBy,
    meetingBodies,
    filteredMeetings,
    searchSnippets,
    isSearching,
    searchError,
  }
}
