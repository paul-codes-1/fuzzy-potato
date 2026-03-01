import { useMemo, useCallback } from 'react'
import { useSearchIndex } from '../contexts/SearchContext'

export function useSearch(meetings, searchParams, setSearchParams) {
  // Read state from URL params (single source of truth)
  const query = searchParams.get('q') || ''
  const selectedBody = searchParams.get('body') || null
  const sortBy = searchParams.get('sort') || 'date-desc'

  // Setters that update URL params
  const updateParam = useCallback((key, value, defaultValue) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (!value || value === defaultValue) {
        next.delete(key)
      } else {
        next.set(key, value)
      }
      // Reset page when filters change
      if (key !== 'page') {
        next.delete('page')
      }
      return next
    }, { replace: true })
  }, [setSearchParams])

  const setQuery = useCallback((v) => updateParam('q', v, ''), [updateParam])
  const setSelectedBody = useCallback((v) => updateParam('body', v, null), [updateParam])
  const setSortBy = useCallback((v) => updateParam('sort', v, 'date-desc'), [updateParam])

  // FlexSearch from context (already loading on app mount)
  const {
    search: flexSearch,
    isLoading: flexSearchLoading,
    isLoaded: flexSearchLoaded,
    loadProgress: flexSearchProgress,
    error: flexSearchError
  } = useSearchIndex()

  // Get unique meeting bodies
  const meetingBodies = useMemo(() => {
    const bodies = new Set()
    meetings.forEach(m => {
      if (m.meeting_body) bodies.add(m.meeting_body)
    })
    return Array.from(bodies).sort()
  }, [meetings])

  // Create a lookup map for meetings by clip_id
  const meetingsById = useMemo(() => {
    const map = new Map()
    meetings.forEach(m => map.set(m.clip_id, m))
    return map
  }, [meetings])

  // Sort function
  const sortMeetings = (meetingList) => {
    const sorted = [...meetingList]
    switch (sortBy) {
      case 'date-desc':
        return sorted.sort((a, b) => (b.date || '').localeCompare(a.date || ''))
      case 'date-asc':
        return sorted.sort((a, b) => (a.date || '').localeCompare(b.date || ''))
      case 'title':
        return sorted.sort((a, b) => (a.title || '').localeCompare(b.title || ''))
      default:
        return sorted
    }
  }

  // Filter and search meetings
  const { filteredMeetings, searchSnippets } = useMemo(() => {
    let results = meetings
    let snippets = new Map()

    if (query.trim()) {
      if (flexSearchLoaded) {
        const flexResults = flexSearch(query)
        results = flexResults
          .map(r => {
            const meeting = meetingsById.get(r.clip_id)
            if (meeting && r.snippet) {
              snippets.set(r.clip_id, r.snippet)
            }
            return meeting
          })
          .filter(Boolean)
      } else {
        // Index still loading — show all meetings while we wait
        results = meetings
      }
    }

    // Apply meeting body filter
    if (selectedBody) {
      results = results.filter(m => m.meeting_body === selectedBody)
    }

    // Apply sorting (only if not searching - search results preserve relevance order)
    if (!query.trim()) {
      results = sortMeetings(results)
    }

    return { filteredMeetings: results, searchSnippets: snippets }
  }, [meetings, query, selectedBody, sortBy, flexSearchLoaded, flexSearch, meetingsById])

  return {
    query,
    setQuery,
    selectedBody,
    setSelectedBody,
    sortBy,
    setSortBy,
    meetingBodies,
    filteredMeetings,
    searchSnippets,
    flexSearchLoading,
    flexSearchLoaded,
    flexSearchProgress,
    flexSearchError
  }
}
