import { useState, useRef, useCallback } from 'react'
import FlexSearch from 'flexsearch'

const MANIFEST_URL = '/data/search_index_manifest.json'

/**
 * Extract a snippet from content around the first match of query.
 * Returns { text, matchStart, matchEnd } for highlighting.
 */
function extractSnippet(content, query, contextChars = 120) {
  if (!content || !query) return null

  const lowerContent = content.toLowerCase()
  const lowerQuery = query.toLowerCase()

  // Find the first occurrence of the query
  const matchIndex = lowerContent.indexOf(lowerQuery)
  if (matchIndex === -1) {
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

/**
 * Hook for lazy-loading and searching the full-text FlexSearch index.
 *
 * The index is split into chunks and only loaded when loadIndex() is called,
 * keeping initial page load fast. Once loaded, searches are nearly instant (<10ms).
 */
export function useFlexSearch() {
  const [isLoading, setIsLoading] = useState(false)
  const [isLoaded, setIsLoaded] = useState(false)
  const [loadProgress, setLoadProgress] = useState({ loaded: 0, total: 0 })
  const [error, setError] = useState(null)

  // Store index and documents in refs to avoid re-renders
  const indexRef = useRef(null)
  const documentsRef = useRef({})

  const loadIndex = useCallback(async () => {
    if (isLoaded || isLoading) return

    setIsLoading(true)
    setError(null)

    try {
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
      const chunkPromises = chunks.map(async (chunk, i) => {
        const response = await fetch(`/data/${chunk.filename}`)
        if (!response.ok) {
          throw new Error(`Failed to load chunk ${chunk.filename}: ${response.status}`)
        }
        const data = await response.json()

        // Update progress
        setLoadProgress(prev => ({ ...prev, loaded: prev.loaded + 1 }))

        return data.documents
      })

      const chunkResults = await Promise.all(chunkPromises)

      // Index all documents from all chunks
      for (const documents of chunkResults) {
        for (const doc of documents) {
          index.add(doc.id, doc.content)
          documentsRef.current[doc.id] = doc
        }
      }

      indexRef.current = index
      setIsLoaded(true)
    } catch (err) {
      setError(err.message)
      console.error('Failed to load FlexSearch index:', err)
    } finally {
      setIsLoading(false)
    }
  }, [isLoaded, isLoading])

  const search = useCallback((query, limit = 100) => {
    if (!indexRef.current || !query.trim()) {
      return []
    }

    // FlexSearch returns array of matching IDs
    const resultIds = indexRef.current.search(query, limit)

    // Map IDs back to document metadata with snippets
    return resultIds.map(id => {
      const doc = documentsRef.current[id]
      if (!doc) return null

      const snippet = extractSnippet(doc.content, query)

      return {
        clip_id: id,
        title: doc.title,
        date: doc.date,
        meeting_body: doc.meeting_body,
        topics: doc.topics || [],
        snippet
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
