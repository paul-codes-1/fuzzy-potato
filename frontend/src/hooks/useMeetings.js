import { useState, useEffect } from 'react'

const INDEX_URL = '/data/index.json'

export function useMeetings() {
  const [meetings, setMeetings] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    async function fetchMeetings() {
      try {
        const response = await fetch(INDEX_URL)
        if (!response.ok) {
          throw new Error('Failed to fetch meeting index')
        }
        const data = await response.json()
        setMeetings(data.clips || [])
      } catch (err) {
        console.error('Error loading meetings:', err)
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    fetchMeetings()
  }, [])

  return { meetings, loading, error }
}

export function useMeeting(clipId) {
  const [meeting, setMeeting] = useState(null)
  const [extractedFacts, setExtractedFacts] = useState(null)
  const [transcript, setTranscript] = useState(null)
  const [transcriptSegments, setTranscriptSegments] = useState(null)
  const [agenda, setAgenda] = useState(null)
  const [minutes, setMinutes] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    // Reset everything on clip change so the previous clip's data never
    // renders for the new one (e.g. a clip without minutes showing the
    // previous clip's minutes forever).
    setMeeting(null)
    setExtractedFacts(null)
    setTranscript(null)
    setTranscriptSegments(null)
    setAgenda(null)
    setMinutes(null)
    setError(null)
    setLoading(true)

    // Fetch one optional per-clip file; failures are non-fatal.
    async function fetchFile(filename, parse, setter, label) {
      if (!filename) return
      try {
        const response = await fetch(`/data/clips/${clipId}/${filename}`)
        if (response.ok) {
          const value = await parse(response)
          if (!cancelled) setter(value)
        }
      } catch (e) {
        console.warn(`Could not load ${label}:`, e)
      }
    }

    async function fetchMeeting() {
      try {
        // Metadata first — it tells us which other files exist
        const metaResponse = await fetch(`/data/clips/${clipId}/metadata.json`)
        if (!metaResponse.ok) {
          throw new Error('Meeting not found')
        }
        const metadata = await metaResponse.json()
        if (cancelled) return
        setMeeting(metadata)

        // The rest are independent — fetch concurrently
        await Promise.all([
          fetchFile(metadata.files?.extracted_facts, r => r.json(), setExtractedFacts, 'extracted facts'),
          fetchFile(metadata.files?.transcript, r => r.text(), setTranscript, 'transcript'),
          fetchFile(metadata.files?.transcript_segments, r => r.json(), setTranscriptSegments, 'transcript segments'),
          fetchFile(metadata.files?.agenda_txt, r => r.text(), setAgenda, 'agenda'),
          fetchFile(metadata.files?.minutes_txt, r => r.text(), setMinutes, 'minutes'),
        ])
      } catch (err) {
        if (cancelled) return
        console.error('Error loading meeting:', err)
        setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    if (clipId) {
      fetchMeeting()
    }

    return () => { cancelled = true }
  }, [clipId])

  return { meeting, extractedFacts, transcript, transcriptSegments, agenda, minutes, loading, error }
}
