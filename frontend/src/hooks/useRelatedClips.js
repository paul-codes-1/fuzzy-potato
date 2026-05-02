import { useState, useEffect } from 'react'

/**
 * "Related meetings" lookup for a given clip ID.
 *
 * Backed by /api/related/{clip_id}, which uses the ChromaDB
 * embeddings the RAG ingest already paid for. Returns up to N
 * clips with similarity scores [0, 1].
 */
export function useRelatedClips(clipId, { limit = 5 } = {}) {
  const [clips, setClips] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!clipId) {
      setClips([])
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setError(null)

    fetch(`/api/related/${clipId}?limit=${limit}`, { signal: controller.signal })
      .then(r => {
        if (!r.ok) throw new Error(`related failed: ${r.status}`)
        return r.json()
      })
      .then(data => setClips(data.results || []))
      .catch(err => {
        if (err.name !== 'AbortError') setError(err.message)
      })
      .finally(() => setLoading(false))

    return () => controller.abort()
  }, [clipId, limit])

  return { clips, loading, error }
}
