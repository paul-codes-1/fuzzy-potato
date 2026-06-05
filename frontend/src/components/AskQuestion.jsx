import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { getSiteConfig } from '../config/site'

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

  async function handleSubmit(e) {
    e.preventDefault()
    if (!question.trim()) return

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const body = { question: question.trim() }
      if (meetingBody) body.meeting_body = meetingBody
      if (dateAfter) body.date_after = dateAfter
      if (dateBefore) body.date_before = dateBefore

      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`)
      }

      const data = await response.json()
      setResult(data)
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="container ask-container">
      <div className="ask-header">
        <ChatLFUCGLogo />
        <h2>{chat.title}</h2>
        <p>{chat.description} Answers are AI-generated with citations.</p>
      </div>

      {site.show_coverage_note && (
        <div className="ask-coverage-note">
          <strong>Coverage note:</strong> The archive spans August 2007 to present with no monthly gaps,
          but only about 10% of meetings have full transcripts so far. Results are strongest for late 2007,
          late 2019, and August 2025 onward. We're working to transcribe the rest — if you'd like to help
          cover the cost of AI transcription for the remaining ~2,000 meetings,{' '}
          <a href="https://github.com/paul-codes-1/fuzzy-potato/" target="_blank" rel="noopener noreferrer">
            get in touch on GitHub
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
              <option value="Council">Council</option>
              <option value="Committee">Committee</option>
              <option value="Commission">Commission</option>
              <option value="Board">Board</option>
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

          {result.sources && result.sources.length > 0 && (
            <div className="ask-sources">
              <h3>Sources ({result.sources.length})</h3>
              {result.sources.map((source, i) => (
                <SourceCard key={`${source.clip_id}-${i}`} source={source} />
              ))}
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
