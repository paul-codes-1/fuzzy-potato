import { useState, useEffect } from 'react'

/**
 * "Related meetings" lookup for a given clip ID.
 *
 * Backed by /api/related/{clip_id}, which uses the vector-store
 * embeddings the RAG ingest already paid for. Returns up to N
 * clips with similarity scores [0, 1].
 *
 * `enabled` gates the fetch. This call used to fire on EVERY detail-page
 * mount (94% of API traffic, 58% of it bots — telemetry, 2026-09), so
 * MeetingDetail now enables it only once the related section scrolls into
 * view (or the user asks for it), and never for automated clients.
 */
export function useRelatedClips(clipId, { limit = 5, enabled = true } = {}) {
  const [clips, setClips] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!clipId || !enabled) {
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
  }, [clipId, limit, enabled])

  return { clips, loading, error }
}
