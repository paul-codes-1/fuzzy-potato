import { useState, useEffect, useCallback } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getApiKey() {
  return sessionStorage.getItem('api_key') || sessionStorage.getItem('admin_api_key') || ''
}

async function apiFetch(path, options = {}) {
  const key = getApiKey()
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': key,
      ...(options.headers || {}),
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MEETING_BODIES = [
  'Urban County Council',
  'Planning Commission',
  'Board of Adjustment',
  'Council Committee',
  'Budget Committee',
  'Public Safety Committee',
  'General Government Committee',
  'WQFB',
]

const TABS = [
  { id: 'subscribe', label: 'Subscribe' },
  { id: 'preferences', label: 'My Preferences' },
  { id: 'preview', label: 'Preview Digest' },
]

// ---------------------------------------------------------------------------
// Subscribe Form
// ---------------------------------------------------------------------------

function SubscribeForm({ onSubscribed }) {
  const [email, setEmail] = useState('')
  const [frequency, setFrequency] = useState('weekly')
  const [selectedBodies, setSelectedBodies] = useState([])
  const [topicInput, setTopicInput] = useState('')
  const [topics, setTopics] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const toggleBody = (body) => {
    setSelectedBodies((prev) =>
      prev.includes(body) ? prev.filter((b) => b !== body) : [...prev, body]
    )
  }

  const addTopic = () => {
    const trimmed = topicInput.trim()
    if (trimmed && !topics.includes(trimmed)) {
      setTopics([...topics, trimmed])
      setTopicInput('')
    }
  }

  const removeTopic = (t) => setTopics(topics.filter((x) => x !== t))

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      addTopic()
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const result = await apiFetch('/api/v1/digest/subscribe', {
        method: 'POST',
        body: JSON.stringify({
          email,
          frequency,
          meeting_bodies: selectedBodies.length > 0 ? selectedBodies : null,
          topics: topics.length > 0 ? topics : null,
        }),
      })
      onSubscribed(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className="dg-form" onSubmit={handleSubmit}>
      <div className="dg-form-section">
        <label className="dg-label" htmlFor="dg-email">Email Address</label>
        <input
          id="dg-email"
          type="email"
          className="dg-input"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />
      </div>

      <div className="dg-form-section">
        <label className="dg-label">Frequency</label>
        <div className="dg-toggle">
          <button
            type="button"
            className={`dg-toggle-btn ${frequency === 'daily' ? 'active' : ''}`}
            onClick={() => setFrequency('daily')}
          >
            Daily
          </button>
          <button
            type="button"
            className={`dg-toggle-btn ${frequency === 'weekly' ? 'active' : ''}`}
            onClick={() => setFrequency('weekly')}
          >
            Weekly
          </button>
        </div>
        <p className="dg-hint">
          {frequency === 'daily'
            ? 'Receive a digest every morning at 7am with the previous day\'s meetings.'
            : 'Receive a digest every Monday at 7am with the past week\'s meetings.'}
        </p>
      </div>

      <div className="dg-form-section">
        <label className="dg-label">Meeting Bodies (optional)</label>
        <p className="dg-hint">Select which meeting bodies to include, or leave empty for all.</p>
        <div className="dg-checkbox-grid">
          {MEETING_BODIES.map((body) => (
            <label key={body} className="dg-checkbox-item">
              <input
                type="checkbox"
                checked={selectedBodies.includes(body)}
                onChange={() => toggleBody(body)}
              />
              <span>{body}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="dg-form-section">
        <label className="dg-label">Topic Keywords (optional)</label>
        <p className="dg-hint">Add keywords to filter for topics you care about.</p>
        <div className="dg-topic-input-row">
          <input
            type="text"
            className="dg-input"
            value={topicInput}
            onChange={(e) => setTopicInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g., zoning, budget, parks"
          />
          <button type="button" className="dg-add-btn" onClick={addTopic}>Add</button>
        </div>
        {topics.length > 0 && (
          <div className="dg-topics-list">
            {topics.map((t) => (
              <span key={t} className="dg-topic-tag">
                {t}
                <button type="button" className="dg-topic-remove" onClick={() => removeTopic(t)}>
                  x
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {error && <div className="dg-error">{error}</div>}

      <button type="submit" className="dg-submit" disabled={loading}>
        {loading ? 'Subscribing...' : 'Subscribe to Digest'}
      </button>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Confirmation Message
// ---------------------------------------------------------------------------

function ConfirmationMessage({ result, onBack }) {
  return (
    <div className="dg-confirmation">
      <div className="dg-confirmation-icon">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#1a56db" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 2L11 13" />
          <path d="M22 2L15 22L11 13L2 9L22 2Z" />
        </svg>
      </div>
      <h3 className="dg-confirmation-title">Check Your Email</h3>
      <p className="dg-confirmation-text">
        {result.message || 'A confirmation email has been sent. Click the link in the email to activate your digest subscription.'}
      </p>
      {result.status === 'already_subscribed' && (
        <p className="dg-confirmation-note">
          You are already subscribed. Visit the Preferences tab to update your settings.
        </p>
      )}
      <button className="dg-back-btn" onClick={onBack}>
        {result.status === 'already_subscribed' ? 'OK' : 'Subscribe Another Email'}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Preferences Panel
// ---------------------------------------------------------------------------

function PreferencesPanel() {
  const [email, setEmail] = useState('')
  const [prefs, setPrefs] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [message, setMessage] = useState(null)

  // Editable fields
  const [frequency, setFrequency] = useState('weekly')
  const [selectedBodies, setSelectedBodies] = useState([])
  const [topics, setTopics] = useState([])
  const [topicInput, setTopicInput] = useState('')

  const lookUp = async () => {
    setLoading(true)
    setError(null)
    setMessage(null)
    setPrefs(null)
    try {
      const result = await apiFetch(`/api/v1/digest/preferences?email=${encodeURIComponent(email)}`)
      setPrefs(result)
      setFrequency(result.frequency)
      setSelectedBodies(result.meeting_bodies || [])
      setTopics(result.topics || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const toggleBody = (body) => {
    setSelectedBodies((prev) =>
      prev.includes(body) ? prev.filter((b) => b !== body) : [...prev, body]
    )
  }

  const addTopic = () => {
    const trimmed = topicInput.trim()
    if (trimmed && !topics.includes(trimmed)) {
      setTopics([...topics, trimmed])
      setTopicInput('')
    }
  }

  const removeTopic = (t) => setTopics(topics.filter((x) => x !== t))

  const save = async () => {
    setSaving(true)
    setError(null)
    setMessage(null)
    try {
      const result = await apiFetch('/api/v1/digest/preferences', {
        method: 'PUT',
        body: JSON.stringify({
          email,
          frequency,
          meeting_bodies: selectedBodies,
          topics,
        }),
      })
      setMessage(result.message || 'Preferences saved.')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="dg-prefs">
      <div className="dg-prefs-lookup">
        <label className="dg-label" htmlFor="dg-prefs-email">Your Email</label>
        <div className="dg-topic-input-row">
          <input
            id="dg-prefs-email"
            type="email"
            className="dg-input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            onKeyDown={(e) => e.key === 'Enter' && lookUp()}
          />
          <button className="dg-add-btn" onClick={lookUp} disabled={loading}>
            {loading ? 'Looking up...' : 'Look Up'}
          </button>
        </div>
      </div>

      {error && <div className="dg-error">{error}</div>}
      {message && <div className="dg-success">{message}</div>}

      {prefs && (
        <div className="dg-prefs-form">
          <div className="dg-prefs-status">
            <span className={`dg-status-badge ${prefs.confirmed ? 'confirmed' : 'pending'}`}>
              {prefs.confirmed ? 'Confirmed' : 'Pending Confirmation'}
            </span>
            <span className="dg-prefs-since">Subscribed since {prefs.created_at?.split('T')[0]}</span>
          </div>

          <div className="dg-form-section">
            <label className="dg-label">Frequency</label>
            <div className="dg-toggle">
              <button
                type="button"
                className={`dg-toggle-btn ${frequency === 'daily' ? 'active' : ''}`}
                onClick={() => setFrequency('daily')}
              >
                Daily
              </button>
              <button
                type="button"
                className={`dg-toggle-btn ${frequency === 'weekly' ? 'active' : ''}`}
                onClick={() => setFrequency('weekly')}
              >
                Weekly
              </button>
            </div>
          </div>

          <div className="dg-form-section">
            <label className="dg-label">Meeting Bodies</label>
            <div className="dg-checkbox-grid">
              {MEETING_BODIES.map((body) => (
                <label key={body} className="dg-checkbox-item">
                  <input
                    type="checkbox"
                    checked={selectedBodies.includes(body)}
                    onChange={() => toggleBody(body)}
                  />
                  <span>{body}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="dg-form-section">
            <label className="dg-label">Topic Keywords</label>
            <div className="dg-topic-input-row">
              <input
                type="text"
                className="dg-input"
                value={topicInput}
                onChange={(e) => setTopicInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') { e.preventDefault(); addTopic() }
                }}
                placeholder="Add topic keyword..."
              />
              <button type="button" className="dg-add-btn" onClick={addTopic}>Add</button>
            </div>
            {topics.length > 0 && (
              <div className="dg-topics-list">
                {topics.map((t) => (
                  <span key={t} className="dg-topic-tag">
                    {t}
                    <button type="button" className="dg-topic-remove" onClick={() => removeTopic(t)}>x</button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <button className="dg-submit" onClick={save} disabled={saving}>
            {saving ? 'Saving...' : 'Save Preferences'}
          </button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Digest Preview
// ---------------------------------------------------------------------------

function DigestPreview() {
  const [frequency, setFrequency] = useState('weekly')
  const [sinceDays, setSinceDays] = useState(7)
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const loadPreview = async () => {
    setLoading(true)
    setError(null)
    setPreview(null)
    try {
      const result = await apiFetch('/api/v1/digest/preview', {
        method: 'POST',
        body: JSON.stringify({ frequency, since_days: sinceDays }),
      })
      setPreview(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="dg-preview">
      <div className="dg-preview-controls">
        <div className="dg-form-section">
          <label className="dg-label">Frequency</label>
          <div className="dg-toggle">
            <button
              type="button"
              className={`dg-toggle-btn ${frequency === 'daily' ? 'active' : ''}`}
              onClick={() => setFrequency('daily')}
            >
              Daily
            </button>
            <button
              type="button"
              className={`dg-toggle-btn ${frequency === 'weekly' ? 'active' : ''}`}
              onClick={() => setFrequency('weekly')}
            >
              Weekly
            </button>
          </div>
        </div>
        <div className="dg-form-section">
          <label className="dg-label" htmlFor="dg-since">Look back (days)</label>
          <input
            id="dg-since"
            type="number"
            className="dg-input dg-input-sm"
            value={sinceDays}
            onChange={(e) => setSinceDays(Math.max(1, Math.min(90, Number(e.target.value))))}
            min={1}
            max={90}
          />
        </div>
        <button className="dg-submit dg-preview-btn" onClick={loadPreview} disabled={loading}>
          {loading ? 'Generating...' : 'Generate Preview'}
        </button>
      </div>

      {error && <div className="dg-error">{error}</div>}

      {preview && preview.status === 'empty' && (
        <div className="dg-empty">{preview.message}</div>
      )}

      {preview && preview.status === 'preview' && (
        <div className="dg-preview-result">
          <div className="dg-preview-meta">
            <span className="dg-preview-stat">{preview.meeting_count} meetings</span>
            <span className="dg-preview-stat">{preview.votes_count} votes</span>
            <span className="dg-preview-stat">{preview.financial_count} financial items</span>
          </div>
          <h4 className="dg-preview-subject">{preview.subject}</h4>
          <div className="dg-preview-frame">
            <iframe
              title="Digest Preview"
              srcDoc={preview.html_body}
              className="dg-preview-iframe"
              sandbox=""
            />
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function DigestSubscribe() {
  const [tab, setTab] = useState('subscribe')
  const [subscribeResult, setSubscribeResult] = useState(null)

  return (
    <div className="container">
      <div className="dg-page">
        <div className="dg-header">
          <h2 className="dg-title">Email Digest</h2>
          <p className="dg-subtitle">
            Get a regular summary of council meetings, votes, and financial decisions delivered to your inbox.
          </p>
        </div>

        <div className="dg-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`dg-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="dg-body">
          {tab === 'subscribe' && !subscribeResult && (
            <SubscribeForm onSubscribed={setSubscribeResult} />
          )}
          {tab === 'subscribe' && subscribeResult && (
            <ConfirmationMessage
              result={subscribeResult}
              onBack={() => setSubscribeResult(null)}
            />
          )}
          {tab === 'preferences' && <PreferencesPanel />}
          {tab === 'preview' && <DigestPreview />}
        </div>
      </div>
    </div>
  )
}
