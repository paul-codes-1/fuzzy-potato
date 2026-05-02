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
  const isSearchMode = !!query.trim()
  // In search mode the implicit default is BM25 relevance; in browse
  // mode it's clip-id desc. 'relevance' is meaningless without a query,
  // so we coerce a stale ?sort=relevance back to the browse default.
  const defaultSort = isSearchMode ? 'relevance' : 'clip-desc'
  let sortBy = searchParams.get('sort') || defaultSort
  if (!isSearchMode && sortBy === 'relevance') sortBy = 'clip-desc'

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
  const setSortBy = useCallback((v) => updateParam('sort', v, defaultSort), [updateParam, defaultSort])

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
    // 'relevance' preserves whatever order the caller passed in (BM25
    // from the server in search mode); the no-op clone keeps the
    // identity-different return contract used by the other branches.
    if (sortBy === 'relevance') return [...meetingList]
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
      // local meeting objects (preserving snippet HTML), then re-sort
      // if the user picked something other than relevance.
      const ranked = []
      for (const r of serverResults) {
        const meeting = meetingsById.get(r.clip_id)
        if (meeting) {
          ranked.push(meeting)
          if (r.snippet) snippets.set(r.clip_id, r.snippet)
        }
      }
      return { filteredMeetings: sortMeetings(ranked), searchSnippets: snippets }
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
    isSearchMode,
    meetingBodies,
    filteredMeetings,
    searchSnippets,
    isSearching,
    searchError,
  }
}
