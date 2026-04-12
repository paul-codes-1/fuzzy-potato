import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SEARCH_TYPES = [
  { value: 'meeting_search', label: 'Meeting search' },
  { value: 'vote_search', label: 'Vote search' },
  { value: 'rag_ask', label: 'Ask a question' },
  { value: 'rag_chat', label: 'Chat seed' },
]

const FREQUENCIES = [
  { value: 'off', label: 'Off' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
]

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

function getApiKey() {
  return (
    sessionStorage.getItem('saved_searches_api_key') ||
    sessionStorage.getItem('analytics_api_key') ||
    sessionStorage.getItem('admin_api_key') ||
    ''
  )
}

async function apiFetch(path, opts = {}) {
  const key = getApiKey()
  const headers = {
    'Content-Type': 'application/json',
    ...(key ? { 'X-API-Key': key } : {}),
    ...(opts.headers || {}),
  }
  const res = await fetch(path, { ...opts, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

function typeLabel(t) {
  return SEARCH_TYPES.find((s) => s.value === t)?.label || t
}

function formatDate(iso) {
  if (!iso) return 'Never'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

function SavedSearchModal({ initial, onClose, onSave, saving, title }) {
  const [name, setName] = useState(initial?.name || '')
  const [searchType, setSearchType] = useState(initial?.search_type || 'meeting_search')
  const [queryText, setQueryText] = useState(initial?.query_text || '')
  const [alertEnabled, setAlertEnabled] = useState(initial?.alert_enabled || false)
  const [alertFrequency, setAlertFrequency] = useState(initial?.alert_frequency || 'off')
  const isEdit = Boolean(initial?.id)
  const firstFieldRef = useRef(null)

  // Focus management: focus first field on open
  useEffect(() => {
    firstFieldRef.current?.focus()
  }, [])

  // ESC to close
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  function handleSubmit(e) {
    e.preventDefault()
    if (!name.trim() || !queryText.trim()) return
    onSave({
      name: name.trim(),
      search_type: searchType,
      query_text: queryText.trim(),
      filters: {},
      alert_enabled: alertEnabled,
      alert_frequency: alertEnabled && alertFrequency === 'off' ? 'daily' : alertFrequency,
    })
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="saved-search-modal-title"
      className="saved-searches-modal-backdrop"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 1000, padding: 16,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="saved-searches-modal"
        style={{
          background: 'var(--surface, #fff)',
          borderRadius: 8, padding: 24, maxWidth: 560, width: '100%',
          boxShadow: '0 10px 40px rgba(0,0,0,0.2)', maxHeight: '90vh', overflowY: 'auto',
        }}
      >
        <h3 id="saved-search-modal-title" style={{ marginTop: 0 }}>{title}</h3>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 12 }}>
            <label htmlFor="ss-name" style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>
              Name
            </label>
            <input
              id="ss-name"
              ref={firstFieldRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={200}
              style={{ width: '100%', padding: 8, boxSizing: 'border-box' }}
            />
          </div>

          <div style={{ marginBottom: 12 }}>
            <label htmlFor="ss-type" style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>
              Search type
            </label>
            <select
              id="ss-type"
              value={searchType}
              onChange={(e) => setSearchType(e.target.value)}
              disabled={isEdit}
              style={{ width: '100%', padding: 8, boxSizing: 'border-box' }}
            >
              {SEARCH_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            {isEdit && (
              <small style={{ color: 'var(--text-tertiary, #666)' }}>
                Search type cannot be changed after creation.
              </small>
            )}
          </div>

          <div style={{ marginBottom: 12 }}>
            <label htmlFor="ss-query" style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>
              Query
            </label>
            <textarea
              id="ss-query"
              value={queryText}
              onChange={(e) => setQueryText(e.target.value)}
              required
              maxLength={4000}
              rows={3}
              style={{ width: '100%', padding: 8, boxSizing: 'border-box', fontFamily: 'inherit' }}
            />
          </div>

          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={alertEnabled}
                onChange={(e) => setAlertEnabled(e.target.checked)}
              />
              <span>Enable email alerts</span>
            </label>
          </div>

          {alertEnabled && (
            <div style={{ marginBottom: 12 }}>
              <label htmlFor="ss-freq" style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>
                Frequency
              </label>
              <select
                id="ss-freq"
                value={alertFrequency}
                onChange={(e) => setAlertFrequency(e.target.value)}
                style={{ width: '100%', padding: 8, boxSizing: 'border-box' }}
              >
                {FREQUENCIES.filter((f) => f.value !== 'off').map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
              <div
                role="note"
                style={{
                  marginTop: 8, padding: 8, background: '#fefce8',
                  border: '1px solid #fde68a', borderRadius: 4,
                  fontSize: 13, color: '#92400e',
                }}
              >
                Alerts are emailed on the selected cadence for signed-in users with an email address. API-key-only saved searches can still be saved, but they do not have an email destination.
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <button type="button" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button type="submit" disabled={saving || !name.trim() || !queryText.trim()}>
              {saving ? 'Saving...' : (isEdit ? 'Save changes' : 'Create')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SavedSearches() {
  const navigate = useNavigate()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [status, setStatus] = useState('')
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch('/api/v1/saved-searches')
      setItems(data.saved_searches || [])
    } catch (err) {
      setError(err.message || 'Failed to load saved searches')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleCreateOrUpdate(values) {
    setSaving(true)
    setError(null)
    try {
      if (editing?.id) {
        await apiFetch(`/api/v1/saved-searches/${editing.id}`, {
          method: 'PATCH',
          body: JSON.stringify(values),
        })
        setStatus(`Updated "${values.name}"`)
      } else {
        await apiFetch('/api/v1/saved-searches', {
          method: 'POST',
          body: JSON.stringify(values),
        })
        setStatus(`Created "${values.name}"`)
      }
      setShowModal(false)
      setEditing(null)
      await load()
    } catch (err) {
      setError(err.message || 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  async function handleRun(item) {
    setStatus(`Running "${item.name}"...`)
    setError(null)
    try {
      const data = await apiFetch(`/api/v1/saved-searches/${item.id}/run`, {
        method: 'POST',
      })
      setStatus(`Ran "${item.name}"`)
      // For RAG types, the server sends back a redirect hint so we
      // hop to the appropriate page with the prefilled question.
      const results = data?.results
      if (results?.redirect === 'ask') {
        navigate(`/ask?q=${encodeURIComponent(results.question || '')}`)
        return
      }
      if (results?.redirect === 'chat') {
        navigate(`/chat?q=${encodeURIComponent(results.question || '')}`)
        return
      }
      // Otherwise reload the list to pick up the new last_run_at
      await load()
    } catch (err) {
      setError(err.message || 'Failed to run saved search')
    }
  }

  async function handleDelete(item) {
    setSaving(true)
    try {
      await apiFetch(`/api/v1/saved-searches/${item.id}`, { method: 'DELETE' })
      setStatus(`Deleted "${item.name}"`)
      setConfirmingDelete(null)
      await load()
    } catch (err) {
      setError(err.message || 'Failed to delete')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="container saved-searches-page" style={{ padding: '24px 16px' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h2 style={{ margin: 0 }}>Saved Searches</h2>
          <p style={{ marginTop: 4, color: 'var(--text-secondary, #666)' }}>
            Save your frequent searches and re-run them with one click.
          </p>
        </div>
        <button
          onClick={() => { setEditing(null); setShowModal(true) }}
          aria-label="Create a new saved search"
        >
          + New saved search
        </button>
      </header>

      {/* aria-live region for status updates */}
      <div role="status" aria-live="polite" className="sr-only">
        {status}
      </div>

      {error && (
        <div
          role="alert"
          style={{
            background: '#fef2f2', border: '1px solid #fecaca',
            color: '#991b1b', padding: 12, borderRadius: 6, marginBottom: 16,
          }}
        >
          {error}
        </div>
      )}

      {loading && (
        <div role="status" aria-live="polite">Loading saved searches...</div>
      )}

      {!loading && items.length === 0 && !error && (
        <div
          className="saved-searches-empty"
          style={{
            textAlign: 'center', padding: '48px 16px',
            background: 'var(--surface-alt, #f9fafb)', borderRadius: 8,
          }}
        >
          <h3 style={{ marginTop: 0 }}>No saved searches yet</h3>
          <p style={{ color: 'var(--text-secondary, #666)' }}>
            Save your first search to get started. Saved searches let you re-run
            common queries in one click.
          </p>
          <button onClick={() => { setEditing(null); setShowModal(true) }}>
            Create your first saved search
          </button>
        </div>
      )}

      {!loading && items.length > 0 && (
        <ul
          aria-label="Saved searches list"
          style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 12 }}
        >
          {items.map((item) => (
            <li
              key={item.id}
              className="saved-search-row"
              style={{
                background: 'var(--surface, #fff)',
                border: '1px solid var(--border-light, #e5e7eb)',
                borderRadius: 8, padding: 16,
                display: 'flex', justifyContent: 'space-between',
                alignItems: 'center', gap: 16, flexWrap: 'wrap',
              }}
            >
              <div style={{ flex: '1 1 300px', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <strong>{item.name}</strong>
                  <span
                    className="type-badge"
                    style={{
                      background: '#e0e7ff', color: '#3730a3',
                      padding: '2px 8px', borderRadius: 12, fontSize: 12,
                    }}
                  >
                    {typeLabel(item.search_type)}
                  </span>
                  {item.alert_enabled && (
                    <span
                      className="alert-badge"
                      style={{
                        background: '#fef3c7', color: '#92400e',
                        padding: '2px 8px', borderRadius: 12, fontSize: 12,
                      }}
                      aria-label={`Email alerts enabled: ${item.alert_frequency}`}
                    >
                      Alert: {item.alert_frequency}
                    </span>
                  )}
                </div>
                <div style={{ marginTop: 4, fontSize: 13, color: 'var(--text-secondary, #666)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {item.query_text}
                </div>
                <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-tertiary, #888)' }}>
                  Last run: {formatDate(item.last_run_at)}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => handleRun(item)} aria-label={`Run ${item.name}`}>
                  Run
                </button>
                <button
                  onClick={() => { setEditing(item); setShowModal(true) }}
                  aria-label={`Edit ${item.name}`}
                >
                  Edit
                </button>
                <button
                  onClick={() => setConfirmingDelete(item)}
                  aria-label={`Delete ${item.name}`}
                  className="danger"
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {showModal && (
        <SavedSearchModal
          initial={editing}
          title={editing?.id ? 'Edit saved search' : 'New saved search'}
          saving={saving}
          onClose={() => { setShowModal(false); setEditing(null) }}
          onSave={handleCreateOrUpdate}
        />
      )}

      {confirmingDelete && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-delete-title"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 1000, padding: 16,
          }}
        >
          <div style={{ background: '#fff', borderRadius: 8, padding: 24, maxWidth: 440 }}>
            <h3 id="confirm-delete-title" style={{ marginTop: 0 }}>Delete saved search?</h3>
            <p>
              Are you sure you want to delete <strong>{confirmingDelete.name}</strong>?
              This action cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setConfirmingDelete(null)} disabled={saving}>
                Cancel
              </button>
              <button
                onClick={() => handleDelete(confirmingDelete)}
                disabled={saving}
                className="danger"
              >
                {saving ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
