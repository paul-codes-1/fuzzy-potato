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
    async function fetchMeeting() {
      try {
        // Fetch metadata
        const metaResponse = await fetch(`/data/clips/${clipId}/metadata.json`)
        if (!metaResponse.ok) {
          throw new Error('Meeting not found')
        }
        const metadata = await metaResponse.json()
        setMeeting(metadata)

        // Fetch extracted facts if available
        if (metadata.files?.extracted_facts) {
          try {
            const factsResponse = await fetch(`/data/clips/${clipId}/${metadata.files.extracted_facts}`)
            if (factsResponse.ok) {
              setExtractedFacts(await factsResponse.json())
            }
          } catch (e) {
            console.warn('Could not load extracted facts:', e)
          }
        }

        // Fetch transcript if available
        if (metadata.files?.transcript) {
          try {
            const transcriptResponse = await fetch(`/data/clips/${clipId}/${metadata.files.transcript}`)
            if (transcriptResponse.ok) {
              setTranscript(await transcriptResponse.text())
            }
          } catch (e) {
            console.warn('Could not load transcript:', e)
          }
        }

        // Fetch transcript segments (timestamped) if available
        if (metadata.files?.transcript_segments) {
          try {
            const segmentsResponse = await fetch(`/data/clips/${clipId}/${metadata.files.transcript_segments}`)
            if (segmentsResponse.ok) {
              setTranscriptSegments(await segmentsResponse.json())
            }
          } catch (e) {
            console.warn('Could not load transcript segments:', e)
          }
        }

        // Fetch agenda text if available
        if (metadata.files?.agenda_txt) {
          try {
            const agendaResponse = await fetch(`/data/clips/${clipId}/${metadata.files.agenda_txt}`)
            if (agendaResponse.ok) {
              setAgenda(await agendaResponse.text())
            }
          } catch (e) {
            console.warn('Could not load agenda:', e)
          }
        }

        // Fetch minutes text if available
        if (metadata.files?.minutes_txt) {
          try {
            const minutesResponse = await fetch(`/data/clips/${clipId}/${metadata.files.minutes_txt}`)
            if (minutesResponse.ok) {
              setMinutes(await minutesResponse.text())
            }
          } catch (e) {
            console.warn('Could not load minutes:', e)
          }
        }
      } catch (err) {
        console.error('Error loading meeting:', err)
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    if (clipId) {
      fetchMeeting()
    }
  }, [clipId])

  return { meeting, extractedFacts, transcript, transcriptSegments, agenda, minutes, loading, error }
}
