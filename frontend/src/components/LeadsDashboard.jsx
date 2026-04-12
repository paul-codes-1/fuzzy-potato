import { useState, useEffect, useCallback } from 'react'
import { useLeads } from '../hooks/useLeads'
import { useAdminAuth } from '../hooks/useAdmin'

const STATUS_OPTIONS = [
  'new',
  'contacted',
  'qualified',
  'disqualified',
  'converted',
  'archived',
]

const INTENT_OPTIONS = [
  'demo',
  'pilot',
  'pricing',
  'partnership',
  'nonprofit-discount',
  'support',
  'other',
]

function StatsPanel({ stats }) {
  if (!stats) return null
  return (
    <div className="admin-stats-panel">
      <div className="admin-stat-card">
        <span className="admin-stat-label">Today</span>
        <span className="admin-stat-value">{stats.today}</span>
      </div>
      <div className="admin-stat-card">
        <span className="admin-stat-label">This week</span>
        <span className="admin-stat-value">{stats.week}</span>
      </div>
      <div className="admin-stat-card">
        <span className="admin-stat-label">This month</span>
        <span className="admin-stat-value">{stats.month}</span>
      </div>
      <div className="admin-stat-card">
        <span className="admin-stat-label">All time</span>
        <span className="admin-stat-value">{stats.total}</span>
      </div>
      <div className="admin-stat-card">
        <span className="admin-stat-label">New</span>
        <span className="admin-stat-value">{stats.by_status?.new ?? 0}</span>
      </div>
      <div className="admin-stat-card">
        <span className="admin-stat-label">Contacted</span>
        <span className="admin-stat-value">{stats.by_status?.contacted ?? 0}</span>
      </div>
      <div className="admin-stat-card">
        <span className="admin-stat-label">Qualified</span>
        <span className="admin-stat-value">{stats.by_status?.qualified ?? 0}</span>
      </div>
      <div className="admin-stat-card">
        <span className="admin-stat-label">Converted</span>
        <span className="admin-stat-value">{stats.by_status?.converted ?? 0}</span>
      </div>
    </div>
  )
}

function LeadDetail({ lead, onClose, onStatusChange, onAddNote }) {
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleAddNote(e) {
    e.preventDefault()
    if (!note.trim()) return
    setSaving(true)
    try {
      await onAddNote(lead.public_id, note.trim())
      setNote('')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="admin-form-backdrop" onClick={onClose} role="presentation">
      <div
        className="admin-form-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Lead detail"
      >
        <div className="admin-top-bar">
          <h3>{lead.name}</h3>
          <button className="admin-btn admin-btn-ghost" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>

        <div className="admin-tenant-details">
          <div className="admin-detail-row">
            <span className="admin-detail-label">Email</span>
            <span className="admin-detail-value">
              <a href={`mailto:${lead.email}`}>{lead.email}</a>
            </span>
          </div>
          {lead.organization && (
            <div className="admin-detail-row">
              <span className="admin-detail-label">Organization</span>
              <span className="admin-detail-value">{lead.organization}</span>
            </div>
          )}
          {lead.role && (
            <div className="admin-detail-row">
              <span className="admin-detail-label">Role</span>
              <span className="admin-detail-value">{lead.role}</span>
            </div>
          )}
          {lead.phone && (
            <div className="admin-detail-row">
              <span className="admin-detail-label">Phone</span>
              <span className="admin-detail-value">{lead.phone}</span>
            </div>
          )}
          <div className="admin-detail-row">
            <span className="admin-detail-label">Intent</span>
            <span className="admin-detail-value">{lead.intent}</span>
          </div>
          {lead.source_page && (
            <div className="admin-detail-row">
              <span className="admin-detail-label">Source page</span>
              <span className="admin-detail-value">{lead.source_page}</span>
            </div>
          )}
          {(lead.utm_source || lead.utm_medium || lead.utm_campaign) && (
            <div className="admin-detail-row">
              <span className="admin-detail-label">UTM</span>
              <span className="admin-detail-value">
                {[lead.utm_source, lead.utm_medium, lead.utm_campaign]
                  .filter(Boolean)
                  .join(' / ')}
              </span>
            </div>
          )}
          <div className="admin-detail-row">
            <span className="admin-detail-label">Submitted</span>
            <span className="admin-detail-value">
              {new Date(lead.created_at).toLocaleString()}
            </span>
          </div>
        </div>

        <h4 className="admin-section-heading">Message</h4>
        <div className="admin-message-box">{lead.message}</div>

        <h4 className="admin-section-heading">Status</h4>
        <select
          className="admin-input admin-input-sm"
          value={lead.status}
          onChange={(e) => onStatusChange(lead.public_id, e.target.value)}
          aria-label="Lead status"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <h4 className="admin-section-heading">Notes</h4>
        {lead.notes && <pre className="admin-notes-box">{lead.notes}</pre>}
        <form onSubmit={handleAddNote} className="admin-note-form">
          <textarea
            className="admin-input"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Add a note..."
            rows={3}
          />
          <button
            type="submit"
            className="admin-btn admin-btn-primary admin-btn-small"
            disabled={saving || !note.trim()}
          >
            {saving ? 'Saving...' : 'Add note'}
          </button>
        </form>
      </div>
    </div>
  )
}

function LeadsTable({ leads, onRowClick, onInlineStatus }) {
  if (leads.length === 0) {
    return <div className="admin-empty">No leads match your filters.</div>
  }
  return (
    <div className="admin-tenant-grid">
      <table className="admin-leads-table">
        <thead>
          <tr>
            <th>Submitted</th>
            <th>Name</th>
            <th>Organization</th>
            <th>Intent</th>
            <th>Status</th>
            <th>Email</th>
          </tr>
        </thead>
        <tbody>
          {leads.map((l) => (
            <tr
              key={l.public_id}
              className="admin-leads-row"
              onClick={() => onRowClick(l)}
              style={{ cursor: 'pointer' }}
            >
              <td>{new Date(l.created_at).toLocaleDateString()}</td>
              <td>{l.name}</td>
              <td>{l.organization || '-'}</td>
              <td>{l.intent}</td>
              <td onClick={(e) => e.stopPropagation()}>
                <select
                  className="admin-input admin-input-sm"
                  value={l.status}
                  onChange={(e) => onInlineStatus(l.public_id, e.target.value)}
                  aria-label={`Status for ${l.name}`}
                >
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </td>
              <td>{l.email}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function LeadsDashboard() {
  const { isAuthenticated } = useAdminAuth()
  const {
    leads,
    stats,
    loading,
    error,
    clearError,
    fetchLeads,
    fetchStats,
    updateStatus,
    addNote,
  } = useLeads()

  const [filters, setFilters] = useState({ status: '', intent: '', search: '' })
  const [selected, setSelected] = useState(null)

  const refresh = useCallback(() => {
    fetchLeads(filters).catch(() => {})
    fetchStats()
  }, [fetchLeads, fetchStats, filters])

  useEffect(() => {
    if (isAuthenticated) {
      refresh()
    }
  }, [isAuthenticated, refresh])

  if (!isAuthenticated) {
    return (
      <div className="container admin-container">
        <p>Please sign in through the admin dashboard first.</p>
      </div>
    )
  }

  async function handleInlineStatus(publicId, status) {
    try {
      const updated = await updateStatus(publicId, status)
      if (selected && selected.public_id === publicId) {
        setSelected({ ...selected, ...updated })
      }
      fetchStats()
    } catch {
      /* error shown via banner */
    }
  }

  async function handleAddNote(publicId, note) {
    const updated = await addNote(publicId, note)
    if (selected && selected.public_id === publicId) {
      setSelected({ ...selected, ...updated })
    }
    return updated
  }

  return (
    <div className="container admin-container">
      <div className="admin-top-bar">
        <h2>Leads</h2>
        <button className="admin-btn admin-btn-ghost" onClick={refresh} disabled={loading}>
          {loading ? 'Loading...' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="admin-error-banner" role="alert">
          <span>{error}</span>
          <button onClick={clearError} className="admin-error-dismiss" aria-label="Dismiss error">
            &times;
          </button>
        </div>
      )}

      <StatsPanel stats={stats} />

      <div className="admin-filter-bar" role="search">
        <label className="admin-label">
          Status
          <select
            className="admin-input admin-input-sm"
            value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
          >
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="admin-label">
          Intent
          <select
            className="admin-input admin-input-sm"
            value={filters.intent}
            onChange={(e) => setFilters((f) => ({ ...f, intent: e.target.value }))}
          >
            <option value="">All intents</option>
            {INTENT_OPTIONS.map((i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
        </label>
        <label className="admin-label">
          Search
          <input
            className="admin-input admin-input-sm"
            type="search"
            placeholder="Name, org, email"
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          />
        </label>
        <button
          className="admin-btn admin-btn-primary admin-btn-small"
          onClick={() => fetchLeads(filters)}
        >
          Apply
        </button>
      </div>

      {loading && leads.length === 0 ? (
        <div className="admin-loading">Loading leads...</div>
      ) : (
        <LeadsTable
          leads={leads}
          onRowClick={setSelected}
          onInlineStatus={handleInlineStatus}
        />
      )}

      {selected && (
        <LeadDetail
          lead={selected}
          onClose={() => setSelected(null)}
          onStatusChange={handleInlineStatus}
          onAddNote={handleAddNote}
        />
      )}
    </div>
  )
}
