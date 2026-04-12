import { useState, useRef, useEffect } from 'react'
import { useChat } from '../hooks/useChat'
import { useI18n } from '../i18n/I18nProvider'
import { useAnnounce } from './A11yAnnouncer'
import ChatMessage from './ChatMessage'
import ModelSelector from './ModelSelector'

export default function ChatMeetings() {
  const { t, locale } = useI18n()
  const announce = useAnnounce()
  const { messages, filters, modelProvider, loading, error,
          sendMessage, setModelProvider, setFilters, clearChat, retry } = useChat()
  const [input, setInput] = useState('')
  const [showFilters, setShowFilters] = useState(false)

  // Announce loading/error/new response to screen readers
  useEffect(() => {
    if (loading) announce('Thinking...')
  }, [loading])
  useEffect(() => {
    if (error) announce(`Error: ${error}`, 'assertive')
  }, [error])
  useEffect(() => {
    if (messages.length > 0) {
      const last = messages[messages.length - 1]
      if (last.role === 'assistant') {
        announce('New response received')
      }
    }
  }, [messages.length])

  const SUGGESTED_QUESTIONS = [
    t('suggestedQuestions.budget'),
    t('suggestedQuestions.zoning'),
    t('suggestedQuestions.rentals'),
    t('suggestedQuestions.infrastructure'),
  ]
  const messagesEndRef = useRef(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, loading])

  function handleSend() {
    if (!input.trim() || loading) return
    sendMessage(input.trim())
    setInput('')
  }

  function handleKeyDown(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSend()
    }
  }

  function handleSuggestedClick(q) {
    sendMessage(q)
  }

  return (
    <div className="chat-container">
      <div className="chat-header">
        <h2>{t('chatMeetings.title')}</h2>
        <button className="chat-new-btn" onClick={clearChat}>{t('chatMeetings.newChat')}</button>
      </div>

      {messages.length === 0 && !loading ? (
        <div className="chat-empty-state">
          <div className="chat-logo" aria-hidden="true">🏛️</div>
          <h3>{t('chatMeetings.emptyTitle')}</h3>
          <p>{t('chatMeetings.emptyDescription')}</p>
          <div className="chat-suggested-questions">
            {SUGGESTED_QUESTIONS.map((q) => (
              <button
                key={q}
                className="chat-suggested-btn"
                onClick={() => handleSuggestedClick(q)}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="chat-messages" role="log" aria-label="Chat conversation" aria-live="polite">
          {messages.map((msg, i) => (
            <ChatMessage key={i} message={msg} />
          ))}
          {loading && (
            <div className="chat-typing" role="status" aria-label="Generating response">
              <div className="chat-typing-dot" aria-hidden="true" />
              <div className="chat-typing-dot" aria-hidden="true" />
              <div className="chat-typing-dot" aria-hidden="true" />
            </div>
          )}
          {error && (
            <div className="chat-error" role="alert">
              <p>{error}</p>
              <button className="chat-retry-btn" onClick={retry}>{t('chatMeetings.retry')}</button>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      )}

      <div className="chat-input-area">
        <ModelSelector value={modelProvider} onChange={setModelProvider} />
        <div className="chat-input-row">
          <label htmlFor="chat-message-input" className="sr-only">Chat message</label>
          <textarea
            id="chat-message-input"
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('chatMeetings.placeholder')}
            rows={1}
            disabled={loading}
            aria-label="Type your question about meeting records"
          />
          <button
            className="chat-send-btn"
            onClick={handleSend}
            disabled={!input.trim() || loading}
          >
            {t('chatMeetings.send')}
          </button>
        </div>
        <button
          className="chat-filters-toggle"
          onClick={() => setShowFilters(!showFilters)}
          aria-expanded={showFilters}
          aria-controls="chat-filters-panel"
        >
          {showFilters ? t('filters.hideFilters') : t('filters.filters')}
        </button>
        {showFilters && (
          <div className="chat-filters-panel" id="chat-filters-panel">
            <label>
              {t('filters.meetingBody')}
              <select
                value={filters.meeting_body || ''}
                onChange={(e) => setFilters({ ...filters, meeting_body: e.target.value || undefined })}
              >
                <option value="">{t('filters.all')}</option>
                <option value="Council">{t('filters.council')}</option>
                <option value="Committee">{t('filters.committee')}</option>
                <option value="Commission">{t('filters.commission')}</option>
                <option value="Board">{t('filters.board')}</option>
              </select>
            </label>
            <label>
              {t('filters.after')}
              <input
                type="date"
                value={filters.date_after || ''}
                onChange={(e) => setFilters({ ...filters, date_after: e.target.value || undefined })}
              />
            </label>
            <label>
              {t('filters.before')}
              <input
                type="date"
                value={filters.date_before || ''}
                onChange={(e) => setFilters({ ...filters, date_before: e.target.value || undefined })}
              />
            </label>
          </div>
        )}
      </div>
    </div>
  )
}
