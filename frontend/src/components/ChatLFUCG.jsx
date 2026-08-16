import { useState, useRef, useEffect } from 'react'
import { useChat } from '../hooks/useChat'
import ChatMessage from './ChatMessage'
import ModelSelector from './ModelSelector'
import { getSiteConfig } from '../config/site'

// Fallback when site.json doesn't provide chat.suggested_questions — mirrors
// the curated LFUCG list in config.build_site_config (the baked-in defaults
// here are always the LFUCG strings; see config/site.js header note).
export const SUGGESTED_QUESTIONS = [
  "How did each council member vote on the Government Center lease-to-own ordinance in December 2025?",
  "How much has Lexington put into the Affordable Housing Fund, and what has it paid for?",
  "What has the council done about short-term rental regulations?",
  "How has the council spent the ARPA pandemic relief money?",
  "What has the council discussed about expanding the urban service boundary?",
  "What has the council discussed about Lexington's tree canopy?",
]

export default function ChatLFUCG() {
  const chat = getSiteConfig().chat || {}
  const { messages, filters, modelProvider, loading, error,
          sendMessage, setModelProvider, setFilters, clearChat, retry } = useChat()
  const [input, setInput] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const messagesEndRef = useRef(null)

  useEffect(() => {
    document.title = `Chat | ${getSiteConfig().archive_name}`
  }, [])

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
        <h2>{chat.title}</h2>
        <button className="chat-new-btn" onClick={clearChat}>New Chat</button>
      </div>

      {messages.length === 0 && !loading ? (
        <div className="chat-empty-state">
          <div className="chatlfucg-logo">🐴</div>
          <h3>{chat.title}</h3>
          <p>{chat.description}</p>
          <div className="chat-suggested-questions">
            {(chat.suggested_questions || SUGGESTED_QUESTIONS).map((q) => (
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
        <div className="chat-messages">
          {messages.map((msg, i) => (
            <ChatMessage key={i} message={msg} />
          ))}
          {loading && (
            <div className="chat-typing">
              <div className="chat-typing-dot" />
              <div className="chat-typing-dot" />
              <div className="chat-typing-dot" />
            </div>
          )}
          {error && (
            <div className="chat-error">
              <p>{error}</p>
              <button className="chat-retry-btn" onClick={retry}>Retry</button>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      )}

      <div className="chat-input-area">
        <ModelSelector value={modelProvider} onChange={setModelProvider} />
        <div className="chat-input-row">
          <textarea
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={chat.placeholder}
            rows={1}
            disabled={loading}
          />
          <button
            className="chat-send-btn"
            onClick={handleSend}
            disabled={!input.trim() || loading}
          >
            Send
          </button>
        </div>
        <button
          className="chat-filters-toggle"
          onClick={() => setShowFilters(!showFilters)}
        >
          {showFilters ? 'Hide Filters' : 'Filters'}
        </button>
        {showFilters && (
          <div className="chat-filters-panel">
            <label>
              Meeting Body
              <select
                value={filters.meeting_body || ''}
                onChange={(e) => setFilters({ ...filters, meeting_body: e.target.value || undefined })}
              >
                <option value="">All</option>
                <option value="Council">Council</option>
                <option value="Committee">Committee</option>
                <option value="Commission">Commission</option>
                <option value="Board">Board</option>
              </select>
            </label>
            <label>
              After
              <input
                type="date"
                value={filters.date_after || ''}
                onChange={(e) => setFilters({ ...filters, date_after: e.target.value || undefined })}
              />
            </label>
            <label>
              Before
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
