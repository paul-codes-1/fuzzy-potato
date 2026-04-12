import { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getApiHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  const apiKey = sessionStorage.getItem('api_key')
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }
  return headers
}

// Significance badge
function SignificanceBadge({ level }) {
  const classes = {
    high: 'diff-sig diff-sig-high',
    medium: 'diff-sig diff-sig-medium',
    low: 'diff-sig diff-sig-low',
  }
  return (
    <span className={classes[level] || 'diff-sig diff-sig-low'}>
      {level}
    </span>
  )
}

// Change type icon and label
function ChangeTypeLabel({ type }) {
  const config = {
    agenda_new: { label: 'New Agenda Item', className: 'diff-type-new' },
    agenda_carried: { label: 'Carried Over', className: 'diff-type-carried' },
    attendance: { label: 'Attendance', className: 'diff-type-attendance' },
    speaker_new: { label: 'New Speaker', className: 'diff-type-speaker' },
    budget: { label: 'Budget', className: 'diff-type-budget' },
    ordinance: { label: 'Ordinance', className: 'diff-type-ordinance' },
    other: { label: 'Other', className: 'diff-type-other' },
  }
  const c = config[type] || config.other
  return <span className={`diff-type-badge ${c.className}`}>{c.label}</span>
}

// Single change card
function ChangeCard({ change }) {
  return (
    <div className={`diff-change-card diff-change-${change.significance}`}>
      <div className="diff-change-header">
        <ChangeTypeLabel type={change.type} />
        <SignificanceBadge level={change.significance} />
      </div>
      <p className="diff-change-desc">{change.description}</p>
      {change.details && Object.keys(change.details).length > 0 && (
        <div className="diff-change-details">
          {change.details.previous_outcome && change.details.current_outcome && (
            <div className="diff-outcome-change">
              <span className="diff-outcome-before">{change.details.previous_outcome}</span>
              <span className="diff-arrow">&rarr;</span>
              <span className="diff-outcome-after">{change.details.current_outcome}</span>
            </div>
          )}
          {change.details.previous_amount && change.details.current_amount && (
            <div className="diff-amount-change">
              <span className="diff-amount-before">{change.details.previous_amount}</span>
              <span className="diff-arrow">&rarr;</span>
              <span className="diff-amount-after">{change.details.current_amount}</span>
            </div>
          )}
          {change.details.amount && !change.details.previous_amount && (
            <div className="diff-amount-new">{change.details.amount}</div>
          )}
          {change.details.ayes != null && (
            <div className="diff-vote-tally">
              <span className="vote-for">Ayes: {change.details.ayes}</span>
              {change.details.nays != null && (
                <span className="vote-against">Nays: {change.details.nays}</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}


// "What's New" summary card
function WhatsNewCard({ summary }) {
  if (!summary) return null
  return (
    <div className="diff-whats-new">
      <h3>What's New</h3>
      <pre className="diff-whats-new-text">{summary}</pre>
    </div>
  )
}


/**
 * MeetingDiff - shown as a tab within MeetingDetail.
 * Fetches diff data for the given clip from the API.
 */
export function MeetingDiffTab({ clipId }) {
  const [diff, setDiff] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filterType, setFilterType] = useState('all')
  const [filterSignificance, setFilterSignificance] = useState('all')

  useEffect(() => {
    async function fetchDiff() {
      setLoading(true)
      setError(null)
      try {
        const res = await fetch(`${API_BASE}/api/v1/diff/meeting/${clipId}`, {
          headers: getApiHeaders(),
        })
        if (res.status === 404) {
          setDiff(null)
          setError('No previous meeting found for comparison.')
          return
        }
        if (!res.ok) {
          throw new Error(`Failed to load diff (${res.status})`)
        }
        const data = await res.json()
        setDiff(data.diff)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    if (clipId) {
      fetchDiff()
    }
  }, [clipId])

  // Unique change types present
  const changeTypes = useMemo(() => {
    if (!diff?.changes) return []
    return [...new Set(diff.changes.map(c => c.type))]
  }, [diff])

  // Filtered changes
  const filteredChanges = useMemo(() => {
    if (!diff?.changes) return []
    return diff.changes.filter(c => {
      if (filterType !== 'all' && c.type !== filterType) return false
      if (filterSignificance !== 'all' && c.significance !== filterSignificance) return false
      return true
    })
  }, [diff, filterType, filterSignificance])

  // Group by type for summary counts
  const changeCounts = useMemo(() => {
    if (!diff?.changes) return {}
    const counts = {}
    for (const c of diff.changes) {
      counts[c.type] = (counts[c.type] || 0) + 1
    }
    return counts
  }, [diff])

  if (loading) {
    return <div className="loading">Loading changes...</div>
  }

  if (error && !diff) {
    return (
      <div className="diff-empty-state">
        <p>{error}</p>
        <p className="diff-empty-hint">
          This is either the first meeting of this body in the archive, or it hasn't been processed yet.
        </p>
      </div>
    )
  }

  if (!diff || !diff.changes || diff.changes.length === 0) {
    return (
      <div className="diff-empty-state">
        <p>No changes detected from the previous meeting.</p>
      </div>
    )
  }

  return (
    <div className="meeting-diff">
      {/* Comparison header */}
      <div className="diff-compare-header">
        <div className="diff-meeting-ref">
          <span className="diff-label">Previous</span>
          <Link to={`/meeting/${diff.clip_id_before}`} className="diff-clip-link">
            {diff.date_before} (Clip {diff.clip_id_before})
          </Link>
        </div>
        <span className="diff-arrow-large">&rarr;</span>
        <div className="diff-meeting-ref">
          <span className="diff-label">Current</span>
          <span className="diff-clip-current">
            {diff.date_after} (Clip {diff.clip_id_after})
          </span>
        </div>
      </div>

      {/* Summary counts */}
      <div className="diff-summary-bar">
        {Object.entries(changeCounts).map(([type, count]) => (
          <button
            key={type}
            className={`diff-summary-chip ${filterType === type ? 'active' : ''}`}
            onClick={() => setFilterType(filterType === type ? 'all' : type)}
          >
            <ChangeTypeLabel type={type} />
            <span className="diff-count">{count}</span>
          </button>
        ))}
        <span className="diff-summary-total">{diff.changes.length} total changes</span>
      </div>

      {/* Significance filter */}
      <div className="diff-filters">
        <label className="diff-filter-label">Priority:</label>
        {['all', 'high', 'medium', 'low'].map(level => (
          <button
            key={level}
            className={`diff-filter-btn ${filterSignificance === level ? 'active' : ''}`}
            onClick={() => setFilterSignificance(level)}
          >
            {level === 'all' ? 'All' : level.charAt(0).toUpperCase() + level.slice(1)}
          </button>
        ))}
      </div>

      {/* What's New summary */}
      <WhatsNewCard summary={diff.whats_new_summary} />

      {/* Change cards */}
      <div className="diff-changes-list">
        {filteredChanges.map((change, i) => (
          <ChangeCard key={i} change={change} />
        ))}
        {filteredChanges.length === 0 && (
          <p className="diff-no-match">No changes match the current filters.</p>
        )}
      </div>
    </div>
  )
}


/**
 * WhatsNewPage - standalone page showing latest changes across all bodies.
 * Can be mounted at /whats-new route.
 */
export function WhatsNewPage() {
  const [diffs, setDiffs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    async function fetchWhatsNew() {
      try {
        const res = await fetch(`${API_BASE}/api/v1/diff/whats-new?limit=20`, {
          headers: getApiHeaders(),
        })
        if (!res.ok) throw new Error(`Failed to load (${res.status})`)
        const data = await res.json()
        setDiffs(data.diffs || [])
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }
    fetchWhatsNew()
  }, [])

  if (loading) return <div className="loading">Loading latest changes...</div>
  if (error) return <div className="empty-state"><p>Error: {error}</p></div>
  if (diffs.length === 0) {
    return (
      <div className="container">
        <h2>What's New</h2>
        <p>No meeting diffs have been generated yet.</p>
      </div>
    )
  }

  return (
    <div className="container whats-new-page">
      <h2>What's New Across All Meeting Bodies</h2>
      <div className="whats-new-list">
        {diffs.map((diff, i) => {
          const highChanges = diff.changes.filter(c => c.significance === 'high')
          const mediumChanges = diff.changes.filter(c => c.significance === 'medium')
          return (
            <div key={i} className="whats-new-card">
              <div className="whats-new-card-header">
                <div>
                  <strong>{diff.meeting_body}</strong>
                  <span className="whats-new-dates">
                    {diff.date_before} &rarr; {diff.date_after}
                  </span>
                </div>
                <div className="whats-new-counts">
                  {highChanges.length > 0 && (
                    <span className="diff-sig diff-sig-high">{highChanges.length} high</span>
                  )}
                  {mediumChanges.length > 0 && (
                    <span className="diff-sig diff-sig-medium">{mediumChanges.length} med</span>
                  )}
                  <span className="diff-total-small">{diff.changes.length} total</span>
                </div>
              </div>
              {highChanges.slice(0, 3).map((c, j) => (
                <div key={j} className="whats-new-highlight">
                  <ChangeTypeLabel type={c.type} />
                  <span>{c.description}</span>
                </div>
              ))}
              <div className="whats-new-card-actions">
                <Link to={`/meeting/${diff.clip_id_after}`} className="diff-view-link">
                  View Meeting &rarr;
                </Link>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}


export default MeetingDiffTab
