import { useState, useMemo, useEffect } from 'react'
import Fuse from 'fuse.js'
import { useFlexSearch } from './useFlexSearch'

const fuseOptions = {
  keys: [
    { name: 'title', weight: 0.3 },
    { name: 'topics', weight: 0.3 },
    { name: 'meeting_body', weight: 0.2 },
    { name: 'transcript_preview', weight: 0.2 }
  ],
  threshold: 0.4,
  ignoreLocation: true,
  includeScore: true
}

export function useSearch(meetings) {
  const [query, setQuery] = useState('')
  const [selectedBody, setSelectedBody] = useState(null)
  const [selectedTopic, setSelectedTopic] = useState(null)
  const [sortBy, setSortBy] = useState('date-desc') // 'date-desc', 'date-asc', 'title'
  const [searchMode, setSearchMode] = useState('quick') // 'quick' or 'full'

  // FlexSearch for full-text search
  const {
    loadIndex: loadFlexSearchIndex,
    search: flexSearch,
    isLoading: flexSearchLoading,
    isLoaded: flexSearchLoaded,
    loadProgress: flexSearchProgress,
    error: flexSearchError
  } = useFlexSearch()

  // Load FlexSearch index when mode switches to 'full'
  useEffect(() => {
    if (searchMode === 'full' && !flexSearchLoaded && !flexSearchLoading) {
      loadFlexSearchIndex()
    }
  }, [searchMode, flexSearchLoaded, flexSearchLoading, loadFlexSearchIndex])

  // Create Fuse instance
  const fuse = useMemo(() => {
    return new Fuse(meetings, fuseOptions)
  }, [meetings])

  // Get unique meeting bodies and topics
  const meetingBodies = useMemo(() => {
    const bodies = new Set()
    meetings.forEach(m => {
      if (m.meeting_body) bodies.add(m.meeting_body)
    })
    return Array.from(bodies).sort()
  }, [meetings])

  // Get ALL topics with counts (not limited to 20)
  const allTopics = useMemo(() => {
    const topics = new Map()
    meetings.forEach(m => {
      (m.topics || []).forEach(t => {
        topics.set(t, (topics.get(t) || 0) + 1)
      })
    })
    return Array.from(topics.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([topic, count]) => ({ topic, count }))
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

  // Filter and search meetings, track snippets for full-text results
  const { filteredMeetings, searchSnippets } = useMemo(() => {
    let results = meetings
    let snippets = new Map()

    // Apply search query
    if (query.trim()) {
      if (searchMode === 'full' && flexSearchLoaded) {
        // Full-text search using FlexSearch
        const flexResults = flexSearch(query)
        // Map FlexSearch results back to full meeting objects, preserving snippets
        results = flexResults
          .map(r => {
            const meeting = meetingsById.get(r.clip_id)
            if (meeting && r.snippet) {
              snippets.set(r.clip_id, r.snippet)
            }
            return meeting
          })
          .filter(Boolean)
      } else if (searchMode === 'quick') {
        // Quick search using Fuse.js
        const searchResults = fuse.search(query)
        results = searchResults.map(r => r.item)
      }
      // If searchMode is 'full' but not loaded yet, show all meetings (loading state)
    }

    // Apply meeting body filter
    if (selectedBody) {
      results = results.filter(m => m.meeting_body === selectedBody)
    }

    // Apply topic filter
    if (selectedTopic) {
      results = results.filter(m => (m.topics || []).includes(selectedTopic))
    }

    // Apply sorting (only if not searching - search results preserve relevance order)
    if (!query.trim()) {
      results = sortMeetings(results)
    }

    return { filteredMeetings: results, searchSnippets: snippets }
  }, [meetings, query, selectedBody, selectedTopic, sortBy, fuse, searchMode, flexSearchLoaded, flexSearch, meetingsById])

  return {
    query,
    setQuery,
    selectedBody,
    setSelectedBody,
    selectedTopic,
    setSelectedTopic,
    sortBy,
    setSortBy,
    searchMode,
    setSearchMode,
    meetingBodies,
    allTopics,
    filteredMeetings,
    searchSnippets,
    flexSearchLoading,
    flexSearchLoaded,
    flexSearchProgress,
    flexSearchError
  }
}
