import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useI18n } from '../i18n/I18nProvider'
import { useAnnounce } from './A11yAnnouncer'
import SaveSearchButton from './SaveSearchButton'

function ChatLogo() {
  return <span className="chat-logo" role="img" aria-label="Chat logo">🏛️</span>
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
  const { t, locale } = useI18n()
  const announce = useAnnounce()
  const [question, setQuestion] = useState('')
  const [meetingBody, setMeetingBody] = useState('')
  const [dateAfter, setDateAfter] = useState('')
  const [dateBefore, setDateBefore] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  // Announce state changes to screen readers
  useEffect(() => {
    if (loading) announce('Searching meeting records...')
  }, [loading])
  useEffect(() => {
    if (error) announce(`Error: ${error}`, 'assertive')
  }, [error])
  useEffect(() => {
    if (result) {
      const srcCount = result.sources?.length || 0
      announce(`Answer loaded with ${srcCount} source${srcCount !== 1 ? 's' : ''}`)
    }
  }, [result])

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

      const langParam = locale && locale !== 'en' ? `?lang=${locale}` : ''
      const response = await fetch(`/api/ask${langParam}`, {
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
        <ChatLogo />
        <h2>{t('ask.title')}</h2>
        <p>{t('ask.description')}</p>
      </div>

      <div className="ask-coverage-note">
        <strong>{t('ask.coverageNote')}</strong> {t('ask.coverageText')}{' '}
        {t('ask.coverageCost')}{' '}
        <a href="https://github.com/paul-codes-1/fuzzy-potato/" target="_blank" rel="noopener noreferrer">
          {t('ask.coverageHelp')}
        </a>.
      </div>

      <form onSubmit={handleSubmit} className="ask-form" aria-label="Ask a question about meeting records">
        <div className="ask-input-row">
          <label htmlFor="ask-question-input" className="sr-only">Your question</label>
          <input
            id="ask-question-input"
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={t('ask.placeholder')}
            className="ask-input"
            disabled={loading}
          />
          <button type="submit" className="ask-button" disabled={loading || !question.trim()}>
            {loading ? t('ask.searching') : t('ask.askButton')}
          </button>
        </div>

        <div className="ask-filters">
          <label className="ask-filter">
            <span id="meeting-body-label">{t('filters.meetingBody')}</span>
            <select
              value={meetingBody}
              onChange={(e) => setMeetingBody(e.target.value)}
              aria-labelledby="meeting-body-label"
              disabled={loading}
            >
              <option value="">{t('filters.all')}</option>
              <option value="Council">{t('filters.council')}</option>
              <option value="Committee">{t('filters.committee')}</option>
              <option value="Commission">{t('filters.commission')}</option>
              <option value="Board">{t('filters.board')}</option>
            </select>
          </label>
          <label className="ask-filter">
            {t('filters.after')}
            <input
              type="date"
              value={dateAfter}
              onChange={(e) => setDateAfter(e.target.value)}
              disabled={loading}
            />
          </label>
          <label className="ask-filter">
            {t('filters.before')}
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
        <div className="ask-loading" role="status" aria-live="polite">
          <div className="ask-spinner" aria-hidden="true" />
          <p>{t('ask.searchingMeetings')}</p>
        </div>
      )}

      {error && (
        <div className="ask-error" role="alert">
          <p>{t('ask.somethingWrong', { error })}</p>
        </div>
      )}

      {result && (
        <div className="ask-result">
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
            <SaveSearchButton
              searchType="rag_ask"
              queryText={question}
              label="Save this question"
              filters={{
                meeting_body: meetingBody || undefined,
                date_after: dateAfter || undefined,
                date_before: dateBefore || undefined,
              }}
            />
          </div>
          <div className="ask-answer">
            <div dangerouslySetInnerHTML={{ __html: simpleMarkdown(result.answer) }} />
          </div>

          {result.sources && result.sources.length > 0 && (
            <div className="ask-sources">
              <h3>{t('ask.sources', { count: result.sources.length })}</h3>
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
