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
  const [summary, setSummary] = useState(null)
  const [transcript, setTranscript] = useState(null)
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

        // Fetch summary HTML if available
        if (metadata.files?.summary_html) {
          try {
            const summaryResponse = await fetch(`/data/clips/${clipId}/${metadata.files.summary_html}`)
            if (summaryResponse.ok) {
              const html = await summaryResponse.text()
              // Extract body content from HTML
              const match = html.match(/<body>([\s\S]*)<\/body>/)
              setSummary(match ? match[1] : html)
            }
          } catch (e) {
            console.warn('Could not load summary:', e)
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

  return { meeting, summary, transcript, agenda, minutes, loading, error }
}
