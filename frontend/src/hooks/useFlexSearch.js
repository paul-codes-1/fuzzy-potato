import { useState, useCallback } from 'react'
import FlexSearch from 'flexsearch'

const MANIFEST_URL = '/data/search_index_manifest.json'

/**
 * Check if a query is wrapped in quotes (exact word match mode)
 * Returns { isExact: boolean, term: string }
 */
function parseQuery(query) {
  const trimmed = query.trim()
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length > 2) {
    return { isExact: true, term: trimmed.slice(1, -1) }
  }
  return { isExact: false, term: trimmed }
}

/**
 * Create a regex for whole-word matching
 * Matches term surrounded by word boundaries (whitespace, punctuation, or start/end)
 */
function wholeWordRegex(term) {
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  // Word boundary: start of string, whitespace, or punctuation
  return new RegExp(`(?:^|[\\s.,;:!?'"()\\[\\]{}\\-])${escaped}(?:[\\s.,;:!?'"()\\[\\]{}\\-]|$)`, 'i')
}

/**
 * Find the index of a whole-word match in content
 */
function findWholeWordMatch(content, term) {
  const lowerContent = content.toLowerCase()
  const lowerTerm = term.toLowerCase()
  const regex = new RegExp(`(?:^|[\\s.,;:!?'"()\\[\\]{}\\-])(${lowerTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})(?:[\\s.,;:!?'"()\\[\\]{}\\-]|$)`, 'gi')

  const match = regex.exec(lowerContent)
  if (match) {
    // Return the index of the actual term (not the boundary char)
    const fullMatchStart = match.index
    const termInMatch = match[1]
    const termStart = lowerContent.indexOf(termInMatch.toLowerCase(), fullMatchStart)
    return termStart
  }
  return -1
}

/**
 * Extract a snippet from content around the first match of query.
 * Returns { text, matchStart, matchEnd } for highlighting.
 */
function extractSnippet(content, query, contextChars = 120, exactMatch = false) {
  if (!content || !query) return null

  const lowerContent = content.toLowerCase()
  const lowerQuery = query.toLowerCase()

  // Find the first occurrence of the query
  let matchIndex
  if (exactMatch) {
    matchIndex = findWholeWordMatch(content, query)
  } else {
    matchIndex = lowerContent.indexOf(lowerQuery)
  }

  if (matchIndex === -1) {
    if (exactMatch) return null // No partial matches for exact mode

    // Try to find partial match (first word of query)
    const firstWord = lowerQuery.split(/\s+/)[0]
    const partialIndex = lowerContent.indexOf(firstWord)
    if (partialIndex === -1) return null

    const start = Math.max(0, partialIndex - contextChars)
    const end = Math.min(content.length, partialIndex + firstWord.length + contextChars)

    // Find word boundaries
    let snippetStart = start
    if (start > 0) {
      const spaceIndex = content.indexOf(' ', start)
      if (spaceIndex !== -1 && spaceIndex < partialIndex) {
        snippetStart = spaceIndex + 1
      }
    }

    let snippetEnd = end
    const lastSpaceIndex = content.lastIndexOf(' ', end)
    if (lastSpaceIndex > partialIndex + firstWord.length) {
      snippetEnd = lastSpaceIndex
    }

    const snippet = content.slice(snippetStart, snippetEnd)
    const relativeMatchStart = partialIndex - snippetStart

    return {
      text: snippet,
      matchStart: relativeMatchStart,
      matchEnd: relativeMatchStart + firstWord.length,
      prefix: start > 0 ? '...' : '',
      suffix: end < content.length ? '...' : ''
    }
  }

  // Calculate snippet boundaries with context
  const start = Math.max(0, matchIndex - contextChars)
  const end = Math.min(content.length, matchIndex + query.length + contextChars)

  // Find word boundaries to avoid cutting words
  let snippetStart = start
  if (start > 0) {
    const spaceIndex = content.indexOf(' ', start)
    if (spaceIndex !== -1 && spaceIndex < matchIndex) {
      snippetStart = spaceIndex + 1
    }
  }

  let snippetEnd = end
  const lastSpaceIndex = content.lastIndexOf(' ', end)
  if (lastSpaceIndex > matchIndex + query.length) {
    snippetEnd = lastSpaceIndex
  }

  const snippet = content.slice(snippetStart, snippetEnd)
  const relativeMatchStart = matchIndex - snippetStart

  return {
    text: snippet,
    matchStart: relativeMatchStart,
    matchEnd: relativeMatchStart + query.length,
    prefix: start > 0 ? '...' : '',
    suffix: end < content.length ? '...' : ''
  }
}

// Module-level singleton so the index survives component unmount/remount
let _index = null
let _documents = {}
let _loaded = false
let _loading = false
let _loadPromise = null

/**
 * Hook for lazy-loading and searching the full-text FlexSearch index.
 *
 * The index is split into chunks and only loaded when loadIndex() is called,
 * keeping initial page load fast. Once loaded, searches are nearly instant (<10ms).
 * The index persists at module level so navigating away and back doesn't reload it.
 */
export function useFlexSearch() {
  const [isLoading, setIsLoading] = useState(_loading)
  const [isLoaded, setIsLoaded] = useState(_loaded)
  const [loadProgress, setLoadProgress] = useState({ loaded: 0, total: 0 })
  const [error, setError] = useState(null)

  const loadIndex = useCallback(async () => {
    if (_loaded) {
      setIsLoaded(true)
      return
    }
    if (_loading) {
      // Another mount already started loading; wait for it
      setIsLoading(true)
      try {
        await _loadPromise
        setIsLoaded(true)
      } catch (err) {
        setError(err.message)
      } finally {
        setIsLoading(false)
      }
      return
    }

    _loading = true
    setIsLoading(true)
    setError(null)

    _loadPromise = (async () => {
      // First, load the manifest to know how many chunks there are
      const manifestResponse = await fetch(MANIFEST_URL)
      if (!manifestResponse.ok) {
        throw new Error(`Failed to load search index manifest: ${manifestResponse.status}`)
      }

      const manifest = await manifestResponse.json()
      const { chunks } = manifest

      setLoadProgress({ loaded: 0, total: chunks.length })

      // Create FlexSearch index with good defaults for full-text search
      const index = new FlexSearch.Index({
        tokenize: 'forward',
        resolution: 9,
        cache: 100,
        context: {
          depth: 2,
          bidirectional: true
        }
      })

      // Load all chunks in parallel
      let loadedCount = 0
      const chunkPromises = chunks.map(async (chunk) => {
        const response = await fetch(`/data/${chunk.filename}`)
        if (!response.ok) {
          throw new Error(`Failed to load chunk ${chunk.filename}: ${response.status}`)
        }
        const data = await response.json()

        loadedCount++
        setLoadProgress({ loaded: loadedCount, total: chunks.length })

        return data.documents
      })

      const chunkResults = await Promise.all(chunkPromises)

      // Index all documents from all chunks
      const docs = {}
      for (const documents of chunkResults) {
        for (const doc of documents) {
          index.add(doc.id, doc.content)
          docs[doc.id] = doc
        }
      }

      _index = index
      _documents = docs
      _loaded = true

      return index
    })()

    try {
      await _loadPromise
      setIsLoaded(true)
    } catch (err) {
      _loading = false
      _loadPromise = null
      setError(err.message)
      console.error('Failed to load FlexSearch index:', err)
    } finally {
      _loading = false
      setIsLoading(false)
    }
  }, [])

  const search = useCallback((query, limit = 100) => {
    if (!_index || !query.trim()) {
      return []
    }

    // Check for exact match mode (quoted query)
    const { isExact, term } = parseQuery(query)

    if (isExact) {
      // Exact mode: search the full phrase, then verify whole-word match
      const resultIds = _index.search(term, limit * 3)

      let results = resultIds.map(id => {
        const doc = _documents[id]
        if (!doc) return null

        const hasWholeWord = wholeWordRegex(term).test(doc.content)
        if (!hasWholeWord) return null

        const snippet = extractSnippet(doc.content, term, 120, true)

        return {
          clip_id: id,
          title: doc.title,
          date: doc.date,
          meeting_body: doc.meeting_body,
          snippet,
          isExactMatch: true
        }
      }).filter(Boolean)

      if (results.length > limit) {
        results = results.slice(0, limit)
      }

      return results
    }

    // Non-exact mode: search each word individually and combine results.
    // This handles cases like "price rd" matching "price road" because
    // "price" will match even if "rd" doesn't match "road".
    const words = term.split(/\s+/).filter(w => w.length > 0)

    if (words.length <= 1) {
      // Single word: simple search
      const resultIds = _index.search(term, limit)
      return resultIds.map(id => {
        const doc = _documents[id]
        if (!doc) return null
        const snippet = extractSnippet(doc.content, term, 120, false)
        return {
          clip_id: id,
          title: doc.title,
          date: doc.date,
          meeting_body: doc.meeting_body,
          snippet,
          isExactMatch: false
        }
      }).filter(Boolean)
    }

    // Multi-word: search each word, score by how many words match
    const idScores = new Map() // id -> { matchCount, firstMatchPos }

    for (const word of words) {
      const wordResults = _index.search(word, limit * 2)
      for (let rank = 0; rank < wordResults.length; rank++) {
        const id = wordResults[rank]
        if (!idScores.has(id)) {
          idScores.set(id, { matchCount: 0, bestRank: rank })
        }
        const entry = idScores.get(id)
        entry.matchCount++
        entry.bestRank = Math.min(entry.bestRank, rank)
      }
    }

    // Sort: most matched words first, then by FlexSearch rank
    const sortedIds = Array.from(idScores.entries())
      .sort((a, b) => {
        // More word matches = better
        if (b[1].matchCount !== a[1].matchCount) return b[1].matchCount - a[1].matchCount
        // Same match count: better rank wins
        return a[1].bestRank - b[1].bestRank
      })
      .slice(0, limit)
      .map(([id]) => id)

    // Use the first word for snippet extraction (most likely the important one)
    const snippetTerm = words[0]

    return sortedIds.map(id => {
      const doc = _documents[id]
      if (!doc) return null
      const snippet = extractSnippet(doc.content, snippetTerm, 120, false)
      return {
        clip_id: id,
        title: doc.title,
        date: doc.date,
        meeting_body: doc.meeting_body,
        snippet,
        isExactMatch: false
      }
    }).filter(Boolean)
  }, [])

  return {
    loadIndex,
    search,
    isLoading,
    isLoaded,
    loadProgress,
    error
  }
}
