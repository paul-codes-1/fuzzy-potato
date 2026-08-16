import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useFacets } from '../hooks/useFacets'
import { getSiteConfig } from '../config/site'
import { SUGGESTED_QUESTIONS } from './ChatLFUCG'

// Hard-coded fallback for when /api/facets hasn't loaded (or failed)
const FALLBACK_MEETING_BODIES = ['Council', 'Committee', 'Commission', 'Board']

function ChatLFUCGLogo() {
  return <span className="chatlfucg-logo" role="img" aria-label="ChatLFUCG logo">🐴</span>
}

function formatTimestamp(seconds) {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function SourceCard({ source }) {
  return (
    <div className="ask-source-card">
      <div className="ask-source-header">
        <Link to={`/meeting/${source.clip_id}`} className="ask-source-title">
          {source.title}
        </Link>
        <span className="ask-source-date">{source.date}</span>
        <span className="ask-source-body">{source.meeting_body}</span>
      </div>
      {source.timestamp != null && (
        <a
          href={source.granicus_url}
          target="_blank"
          rel="noopener noreferrer"
          className="ask-timestamp-badge"
        >
          {formatTimestamp(source.timestamp)}
        </a>
      )}
      {source.excerpt && (
        <p className="ask-source-excerpt">{source.excerpt}</p>
      )}
    </div>
  )
}

export default function AskQuestion() {
  const site = getSiteConfig()
  const chat = site.chat || {}
  useEffect(() => {
    document.title = `Ask a question | ${site.archive_name}`
  }, [site.archive_name])
  const [question, setQuestion] = useState('')
  const [meetingBody, setMeetingBody] = useState('')
  const [dateAfter, setDateAfter] = useState('')
  const [dateBefore, setDateBefore] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [showUncited, setShowUncited] = useState(false)
  const abortRef = useRef(null)

  const { facets } = useFacets()
  const meetingBodies = facets?.bodies?.length ? facets.bodies : FALLBACK_MEETING_BODIES

  // Abort any in-flight ask on unmount so a late response can't land
  // after the component is gone.
  useEffect(() => () => { abortRef.current?.abort() }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    ask(question)
  }

  // A suggested-question chip fills the input and submits in one click.
  // `ask` takes the text explicitly because setQuestion is async — reading
  // `question` state right after clicking a chip would race the update.
  function handleSuggestedClick(q) {
    setQuestion(q)
    ask(q)
  }

  async function ask(q) {
    if (!q.trim()) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)
    setResult(null)
    setShowUncited(false)

    try {
      const body = { question: q.trim() }
      if (meetingBody) body.meeting_body = meetingBody
      if (dateAfter) body.date_after = dateAfter
      if (dateBefore) body.date_before = dateBefore

      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`)
      }

      const data = await response.json()
      setResult(data)
    } catch (err) {
      if (err.name === 'AbortError') return
      setError(err.message || 'Something went wrong. Please try again.')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }

  // Split sources on the backend's `cited` flag. If no source carries the
  // flag (older backend), fall back to the original render-everything
  // behavior.
  const sources = result?.sources || []
  const hasCitedFlags = sources.some(s => s.cited !== undefined)
  const citedSources = hasCitedFlags ? sources.filter(s => s.cited) : sources
  const uncitedSources = hasCitedFlags ? sources.filter(s => !s.cited) : []
  const citedMeetingCount = new Set(citedSources.map(s => s.clip_id)).size

  return (
    <div className="container ask-container">
      <div className="ask-header">
        <ChatLFUCGLogo />
        <h2>{chat.title}</h2>
        <p>{chat.description} Answers are AI-generated with citations.</p>
      </div>

      {site.show_coverage_note && (
        <div className="ask-coverage-note">
          <strong>Coverage:</strong> The archive spans August 2007 to present — 2,800+ meetings, nearly
          all with full transcripts, AI summaries, and structured vote records. New meetings are
          ingested automatically, usually the same day they're posted. The pipeline is{' '}
          <a href="https://github.com/paul-codes-1/fuzzy-potato/" target="_blank" rel="noopener noreferrer">
            open source
          </a>.
        </div>
      )}

      <form onSubmit={handleSubmit} className="ask-form">
        <div className="ask-input-row">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={chat.placeholder}
            aria-label="Ask a question about the meeting archive"
            className="ask-input"
            disabled={loading}
          />
          <button type="submit" className="ask-button" disabled={loading || !question.trim()}>
            {loading ? 'Searching...' : 'Ask'}
          </button>
        </div>

        <div className="ask-filters">
          <label className="ask-filter">
            <span id="meeting-body-label">Meeting Body</span>
            <select
              value={meetingBody}
              onChange={(e) => setMeetingBody(e.target.value)}
              aria-labelledby="meeting-body-label"
              disabled={loading}
            >
              <option value="">All</option>
              {meetingBodies.map(body => (
                <option key={body} value={body}>{body}</option>
              ))}
            </select>
          </label>
          <label className="ask-filter">
            After
            <input
              type="date"
              value={dateAfter}
              onChange={(e) => setDateAfter(e.target.value)}
              disabled={loading}
            />
          </label>
          <label className="ask-filter">
            Before
            <input
              type="date"
              value={dateBefore}
              onChange={(e) => setDateBefore(e.target.value)}
              disabled={loading}
            />
          </label>
        </div>
      </form>

      {!result && !loading && !error && (
        <div className="chat-suggested-questions ask-suggested-questions">
          {(chat.suggested_questions || SUGGESTED_QUESTIONS).map((q) => (
            <button
              key={q}
              type="button"
              className="chat-suggested-btn"
              onClick={() => handleSuggestedClick(q)}
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div className="ask-loading">
          <div className="ask-spinner" />
          <p>Searching meetings and generating answer...</p>
        </div>
      )}

      {error && (
        <div className="ask-error">
          <p>Something went wrong: {error}</p>
        </div>
      )}

      {result && (
        <div className="ask-result">
          <div className="ask-answer">
            <div dangerouslySetInnerHTML={{ __html: simpleMarkdown(result.answer) }} />
          </div>

          {sources.length > 0 && !hasCitedFlags && (
            // Backward compat: old backend without `cited` flags — render
            // every retrieved source prominently, as before.
            <div className="ask-sources">
              <h3>Sources ({sources.length})</h3>
              {sources.map((source, i) => (
                <SourceCard key={`${source.clip_id}-${i}`} source={source} />
              ))}
            </div>
          )}

          {sources.length > 0 && hasCitedFlags && (
            <div className="ask-sources">
              {citedSources.length > 0 && (
                <>
                  <p className="ask-sources-synthesis-note">
                    Answer synthesized from {citedSources.length} excerpt{citedSources.length !== 1 ? 's' : ''}{' '}
                    across {citedMeetingCount} meeting{citedMeetingCount !== 1 ? 's' : ''}.
                  </p>
                  <h3>Cited in this answer ({citedSources.length})</h3>
                  {citedSources.map((source, i) => (
                    <SourceCard key={`${source.clip_id}-${i}`} source={source} />
                  ))}
                </>
              )}
              {uncitedSources.length > 0 && (
                <div className="ask-sources-other">
                  <button
                    type="button"
                    className="ask-sources-toggle"
                    aria-expanded={showUncited}
                    onClick={() => setShowUncited(!showUncited)}
                  >
                    {showUncited ? '▾' : '▸'} Other retrieved excerpts ({uncitedSources.length})
                  </button>
                  {showUncited && uncitedSources.map((source, i) => (
                    <SourceCard key={`${source.clip_id}-${i}`} source={source} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function simpleMarkdown(text) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^/, '<p>')
    .replace(/$/, '</p>')
}
