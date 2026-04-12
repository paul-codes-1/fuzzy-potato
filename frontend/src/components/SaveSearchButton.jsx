import { useState, useRef, useEffect } from 'react'

// ---------------------------------------------------------------------------
// Reusable "Save this search" button with inline popover modal.
// Used by SearchBar, AskQuestion, and any future search UI that wants to
// offer a one-click save without navigating away.
// ---------------------------------------------------------------------------

function getApiKey() {
  return (
    sessionStorage.getItem('saved_searches_api_key') ||
    sessionStorage.getItem('analytics_api_key') ||
    sessionStorage.getItem('admin_api_key') ||
    ''
  )
}

async function saveSearch({ name, searchType, queryText, filters }) {
  const key = getApiKey()
  const headers = {
    'Content-Type': 'application/json',
    ...(key ? { 'X-API-Key': key } : {}),
  }
  const res = await fetch('/api/v1/saved-searches', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      name,
      search_type: searchType,
      query_text: queryText,
      filters: filters || {},
      alert_enabled: false,
      alert_frequency: 'off',
    }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

export default function SaveSearchButton({
  searchType,
  queryText,
  filters = {},
  label = 'Save this search',
  defaultName = '',
  onSaved,
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(defaultName || (queryText || '').slice(0, 60))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(false)
  const inputRef = useRef(null)

  useEffect(() => {
    if (open) {
      setName(defaultName || (queryText || '').slice(0, 60))
      setError(null)
      setSuccess(false)
      // Focus the input once the popover opens
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open, defaultName, queryText])

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && open) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (!queryText || !String(queryText).trim()) {
    return null
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    setError(null)
    try {
      const saved = await saveSearch({
        name: name.trim(),
        searchType,
        queryText: String(queryText).trim(),
        filters,
      })
      setSuccess(true)
      if (onSaved) onSaved(saved)
      // Auto-close after a beat so the user sees the confirmation
      setTimeout(() => setOpen(false), 1200)
    } catch (err) {
      setError(err.message || 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="save-search-button-wrapper" style={{ display: 'inline-block', position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="save-search-button"
      >
        <span aria-hidden="true" style={{ marginRight: 4 }}>★</span>
        {label}
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="false"
          aria-labelledby="save-search-popover-title"
          className="save-search-popover"
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            right: 0,
            zIndex: 100,
            background: 'var(--surface, #fff)',
            border: '1px solid var(--border-light, #e5e7eb)',
            boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
            borderRadius: 8,
            padding: 16,
            width: 320,
          }}
        >
          <form onSubmit={handleSubmit}>
            <label
              id="save-search-popover-title"
              htmlFor="save-search-name-input"
              style={{ display: 'block', fontWeight: 600, marginBottom: 6 }}
            >
              Name this search
            </label>
            <input
              ref={inputRef}
              id="save-search-name-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              required
              disabled={saving || success}
              style={{ width: '100%', padding: 8, boxSizing: 'border-box', marginBottom: 8 }}
            />
            {error && (
              <div role="alert" style={{ color: '#991b1b', fontSize: 13, marginBottom: 8 }}>
                {error}
              </div>
            )}
            {success && (
              <div role="status" style={{ color: '#166534', fontSize: 13, marginBottom: 8 }}>
                Saved!
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" onClick={() => setOpen(false)} disabled={saving}>
                Cancel
              </button>
              <button type="submit" disabled={saving || success || !name.trim()}>
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
