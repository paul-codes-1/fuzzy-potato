import { useState, useRef, useEffect } from 'react'
import { useChat } from '../hooks/useChat'
import ChatMessage from './ChatMessage'
import ModelSelector from './ModelSelector'

const SUGGESTED_QUESTIONS = [
  "What budget changes were approved in 2024?",
  "How many zoning approvals were there last year?",
  "What did council say about short-term rentals?",
  "What infrastructure projects were discussed recently?",
]

export default function ChatLFUCG() {
  const { messages, filters, modelProvider, loading, error,
          sendMessage, setModelProvider, setFilters, clearChat, retry } = useChat()
  const [input, setInput] = useState('')
  const [showFilters, setShowFilters] = useState(false)
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
        <h2>ChatLFUCG</h2>
        <button className="chat-new-btn" onClick={clearChat}>New Chat</button>
      </div>

      {messages.length === 0 && !loading ? (
        <div className="chat-empty-state">
          <div className="chatlfucg-logo">🐴</div>
          <h3>ChatLFUCG</h3>
          <p>Ask questions about Lexington city council meetings, votes, budgets, and more.</p>
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
            placeholder="Ask about city meetings..."
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
