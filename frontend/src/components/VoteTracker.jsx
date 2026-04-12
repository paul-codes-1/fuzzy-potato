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
// Tabs
// ---------------------------------------------------------------------------

const TABS = [
  { id: 'votes', label: 'Votes' },
  { id: 'members', label: 'Members' },
  { id: 'financial', label: 'Financial' },
  { id: 'alerts', label: 'Alerts' },
]

// ---------------------------------------------------------------------------
// SVG Heatmap — members x meetings voting pattern
// ---------------------------------------------------------------------------

function VotingHeatmap({ votes }) {
  if (!votes || votes.length === 0) {
    return <div className="vt-empty">No vote data available for heatmap</div>
  }

  // Collect all members and dates
  const memberSet = new Set()
  const dateSet = new Set()
  const voteMap = {} // "member::date" -> "for" | "against" | "abstain"

  votes.forEach((v) => {
    const date = v.meeting_date || 'unknown'
    dateSet.add(date)
    ;(v.votes_for || []).forEach((m) => {
      memberSet.add(m)
      voteMap[`${m}::${date}`] = 'for'
    })
    ;(v.votes_against || []).forEach((m) => {
      memberSet.add(m)
      voteMap[`${m}::${date}`] = 'against'
    })
    if (v.motion_by) memberSet.add(v.motion_by)
    if (v.second_by) memberSet.add(v.second_by)
  })

  const members = [...memberSet].sort()
  const dates = [...dateSet].sort()

  if (members.length === 0 || dates.length === 0) {
    return <div className="vt-empty">Not enough data for heatmap</div>
  }

  const cellSize = 22
  const labelWidth = 120
  const headerHeight = 80
  const width = labelWidth + dates.length * cellSize + 20
  const height = headerHeight + members.length * cellSize + 20

  const colorMap = {
    for: '#34c759',
    against: '#ff3b30',
    abstain: '#ffcc00',
  }

  return (
    <div className="vt-heatmap-wrapper">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: '100%', maxWidth: Math.min(width, 900), height: 'auto' }}
        className="vt-heatmap-svg"
        role="img"
        aria-label={`Voting pattern heatmap showing ${members.length} members across ${dates.length} meeting dates. Green indicates a vote for, red indicates a vote against, and grey indicates absent.`}
      >
        {/* Date labels (rotated) */}
        {dates.map((d, i) => (
          <text
            key={d}
            x={labelWidth + i * cellSize + cellSize / 2}
            y={headerHeight - 8}
            textAnchor="end"
            fontSize="9"
            fill="var(--text-secondary)"
            transform={`rotate(-45 ${labelWidth + i * cellSize + cellSize / 2} ${headerHeight - 8})`}
          >
            {d.slice(5)}
          </text>
        ))}

        {/* Member labels and cells */}
        {members.map((member, mi) => (
          <g key={member}>
            <text
              x={labelWidth - 6}
              y={headerHeight + mi * cellSize + cellSize / 2 + 4}
              textAnchor="end"
              fontSize="10"
              fill="var(--text-primary)"
            >
              {member.length > 14 ? member.slice(0, 12) + '..' : member}
            </text>
            {dates.map((date, di) => {
              const key = `${member}::${date}`
              const vote = voteMap[key]
              return (
                <rect
                  key={key}
                  x={labelWidth + di * cellSize + 1}
                  y={headerHeight + mi * cellSize + 1}
                  width={cellSize - 2}
                  height={cellSize - 2}
                  rx="3"
                  fill={vote ? colorMap[vote] : 'var(--surface-secondary)'}
                  opacity={vote ? 0.85 : 0.3}
                >
                  <title>
                    {member} - {date}: {vote || 'absent/not recorded'}
                  </title>
                </rect>
              )
            })}
          </g>
        ))}

        {/* Legend */}
        <g transform={`translate(${labelWidth}, ${height - 14})`}>
          <rect x="0" y="0" width="10" height="10" rx="2" fill="#34c759" opacity="0.85" />
          <text x="14" y="9" fontSize="9" fill="var(--text-secondary)">For</text>
          <rect x="50" y="0" width="10" height="10" rx="2" fill="#ff3b30" opacity="0.85" />
          <text x="64" y="9" fontSize="9" fill="var(--text-secondary)">Against</text>
          <rect x="120" y="0" width="10" height="10" rx="2" fill="var(--surface-secondary)" opacity="0.3" />
          <text x="134" y="9" fontSize="9" fill="var(--text-secondary)">Absent</text>
        </g>
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Outcome badge
// ---------------------------------------------------------------------------

function OutcomeBadge({ outcome }) {
  const cls =
    (outcome || '').toLowerCase() === 'passed'
      ? 'vt-badge-passed'
      : (outcome || '').toLowerCase() === 'failed'
        ? 'vt-badge-failed'
        : 'vt-badge-other'
  return <span className={`vt-badge ${cls}`}>{outcome || 'unknown'}</span>
}

// ---------------------------------------------------------------------------
// Votes Tab
// ---------------------------------------------------------------------------

function VotesTab() {
  const [votes, setVotes] = useState([])
  const [stats, setStats] = useState(null)
  const [total, setTotal] = useState(0)
  const [filters, setFilters] = useState({ member: '', outcome: '', keyword: '', date_after: '', date_before: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(0)
  const limit = 25

  const fetchVotes = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (filters.member) params.set('member', filters.member)
      if (filters.outcome) params.set('outcome', filters.outcome)
      if (filters.keyword) params.set('keyword', filters.keyword)
      if (filters.date_after) params.set('date_after', filters.date_after)
      if (filters.date_before) params.set('date_before', filters.date_before)
      params.set('limit', limit)
      params.set('offset', page * limit)
      const data = await apiFetch(`/api/v1/votes?${params}`)
      setVotes(data.votes || [])
      setTotal(data.total || 0)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filters, page])

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/v1/votes/stats')
      setStats(data)
    } catch {
      // non-critical
    }
  }, [])

  useEffect(() => { fetchVotes() }, [fetchVotes])
  useEffect(() => { fetchStats() }, [fetchStats])

  const handleFilterChange = (key, value) => {
    setFilters((f) => ({ ...f, [key]: value }))
    setPage(0)
  }

  const totalPages = Math.ceil(total / limit)

  return (
    <div className="vt-tab-content">
      {/* Stats cards */}
      {stats && (
        <div className="vt-stats-grid">
          <div className="vt-stat-card">
            <div className="vt-stat-value">{stats.total_votes}</div>
            <div className="vt-stat-label">Total Votes</div>
          </div>
          <div className="vt-stat-card">
            <div className="vt-stat-value vt-stat-passed">{stats.passed}</div>
            <div className="vt-stat-label">Passed</div>
          </div>
          <div className="vt-stat-card">
            <div className="vt-stat-value vt-stat-failed">{stats.failed}</div>
            <div className="vt-stat-label">Failed</div>
          </div>
          <div className="vt-stat-card">
            <div className="vt-stat-value">{stats.pass_rate}%</div>
            <div className="vt-stat-label">Pass Rate</div>
          </div>
        </div>
      )}

      {/* Heatmap */}
      {votes.length > 0 && (
        <div className="vt-section">
          <h3 className="vt-section-title">Voting Patterns</h3>
          <VotingHeatmap votes={votes} />
        </div>
      )}

      {/* Filters */}
      <fieldset className="vt-filters">
        <legend className="sr-only">Filter votes</legend>
        <label className="sr-only" htmlFor="vt-member-filter">Member name</label>
        <input
          id="vt-member-filter"
          type="text"
          placeholder="Member name..."
          value={filters.member}
          onChange={(e) => handleFilterChange('member', e.target.value)}
          className="vt-filter-input"
          aria-label="Filter by member name"
        />
        <label className="sr-only" htmlFor="vt-outcome-filter">Outcome</label>
        <select
          id="vt-outcome-filter"
          value={filters.outcome}
          onChange={(e) => handleFilterChange('outcome', e.target.value)}
          className="vt-filter-select"
          aria-label="Filter by outcome"
        >
          <option value="">All outcomes</option>
          <option value="passed">Passed</option>
          <option value="failed">Failed</option>
        </select>
        <label className="sr-only" htmlFor="vt-keyword-filter">Search description</label>
        <input
          id="vt-keyword-filter"
          type="text"
          placeholder="Search description..."
          value={filters.keyword}
          onChange={(e) => handleFilterChange('keyword', e.target.value)}
          className="vt-filter-input"
          aria-label="Filter by keyword in description"
        />
        <label className="sr-only" htmlFor="vt-date-after">Date after</label>
        <input
          id="vt-date-after"
          type="date"
          value={filters.date_after}
          onChange={(e) => handleFilterChange('date_after', e.target.value)}
          className="vt-filter-input vt-filter-date"
          aria-label="Filter votes after date"
        />
        <label className="sr-only" htmlFor="vt-date-before">Date before</label>
        <input
          id="vt-date-before"
          type="date"
          value={filters.date_before}
          onChange={(e) => handleFilterChange('date_before', e.target.value)}
          className="vt-filter-input vt-filter-date"
          aria-label="Filter votes before date"
        />
      </fieldset>

      {error && <div className="vt-error" role="alert">{error}</div>}
      {loading && <div className="vt-loading" role="status" aria-live="polite">Loading votes...</div>}

      {/* Votes table */}
      {!loading && votes.length > 0 && (
        <>
          <div className="vt-table-wrapper">
            <table className="vt-table" aria-label="Vote records">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Identifier</th>
                  <th scope="col">Description</th>
                  <th scope="col">Motion By</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Ayes</th>
                  <th scope="col">Nays</th>
                  <th scope="col">Clip</th>
                </tr>
              </thead>
              <tbody>
                {votes.map((v) => (
                  <tr key={v.id}>
                    <td className="vt-td-nowrap">{v.meeting_date}</td>
                    <td className="vt-td-nowrap">{v.identifier || '--'}</td>
                    <td className="vt-td-desc">{v.description || '--'}</td>
                    <td>{v.motion_by || '--'}</td>
                    <td><OutcomeBadge outcome={v.outcome} /></td>
                    <td>{v.ayes}</td>
                    <td>{v.nays}</td>
                    <td>
                      <a href={`/meeting/${v.clip_id}`} className="vt-clip-link">
                        #{v.clip_id}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="vt-pagination">
              <button
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="vt-page-btn"
              >
                Previous
              </button>
              <span className="vt-page-info">
                Page {page + 1} of {totalPages} ({total} results)
              </span>
              <button
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
                className="vt-page-btn"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      {!loading && votes.length === 0 && !error && (
        <div className="vt-empty">No votes found matching your filters.</div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Members Tab
// ---------------------------------------------------------------------------

function MembersTab() {
  const [stats, setStats] = useState(null)
  const [selectedMember, setSelectedMember] = useState(null)
  const [memberRecord, setMemberRecord] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    apiFetch('/api/v1/votes/stats')
      .then(setStats)
      .catch((e) => setError(e.message))
  }, [])

  const loadMember = async (name) => {
    setSelectedMember(name)
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch(`/api/v1/votes/member/${encodeURIComponent(name)}`)
      setMemberRecord(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="vt-tab-content">
      {error && <div className="vt-error" role="alert">{error}</div>}

      {/* Most active movers */}
      {stats && stats.most_active_movers && stats.most_active_movers.length > 0 && (
        <div className="vt-section">
          <h3 className="vt-section-title">Most Active Movers</h3>
          <div className="vt-member-grid">
            {stats.most_active_movers.map((m) => (
              <button
                key={m.member}
                onClick={() => loadMember(m.member)}
                className={`vt-member-card ${selectedMember === m.member ? 'vt-member-active' : ''}`}
              >
                <div className="vt-member-name">{m.member}</div>
                <div className="vt-member-count">{m.count} motions</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Selected member record */}
      {loading && <div className="vt-loading" role="status" aria-live="polite">Loading member record...</div>}
      {memberRecord && !loading && (
        <div className="vt-section">
          <h3 className="vt-section-title">
            Voting Record: {memberRecord.member}
          </h3>
          <div className="vt-stats-grid">
            <div className="vt-stat-card">
              <div className="vt-stat-value">{memberRecord.total_votes}</div>
              <div className="vt-stat-label">Total Votes</div>
            </div>
            <div className="vt-stat-card">
              <div className="vt-stat-value vt-stat-passed">{memberRecord.voted_for}</div>
              <div className="vt-stat-label">Voted For</div>
            </div>
            <div className="vt-stat-card">
              <div className="vt-stat-value vt-stat-failed">{memberRecord.voted_against}</div>
              <div className="vt-stat-label">Voted Against</div>
            </div>
            <div className="vt-stat-card">
              <div className="vt-stat-value">{memberRecord.motioned}</div>
              <div className="vt-stat-label">Motioned</div>
            </div>
          </div>

          {memberRecord.votes && memberRecord.votes.length > 0 && (
            <div className="vt-table-wrapper">
              <table className="vt-table" aria-label={`Voting record for ${memberRecord.member}`}>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Identifier</th>
                    <th scope="col">Description</th>
                    <th scope="col">Outcome</th>
                    <th scope="col">Role</th>
                  </tr>
                </thead>
                <tbody>
                  {memberRecord.votes.slice(0, 50).map((v) => {
                    const name = memberRecord.member.toLowerCase()
                    const roles = []
                    if ((v.motion_by || '').toLowerCase().includes(name)) roles.push('Motion')
                    if ((v.second_by || '').toLowerCase().includes(name)) roles.push('Second')
                    if ((v.votes_for || []).some((x) => x.toLowerCase().includes(name))) roles.push('Aye')
                    if ((v.votes_against || []).some((x) => x.toLowerCase().includes(name))) roles.push('Nay')
                    return (
                      <tr key={v.id}>
                        <td className="vt-td-nowrap">{v.meeting_date}</td>
                        <td className="vt-td-nowrap">{v.identifier || '--'}</td>
                        <td className="vt-td-desc">{v.description || '--'}</td>
                        <td><OutcomeBadge outcome={v.outcome} /></td>
                        <td>{roles.join(', ') || '--'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Contested items */}
      {stats && stats.most_contested && stats.most_contested.length > 0 && (
        <div className="vt-section">
          <h3 className="vt-section-title">Most Contested Votes</h3>
          <div className="vt-table-wrapper">
            <table className="vt-table" aria-label="Most contested votes">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Item</th>
                  <th scope="col">Description</th>
                  <th scope="col">Ayes</th>
                  <th scope="col">Nays</th>
                  <th scope="col">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {stats.most_contested.map((v) => (
                  <tr key={v.id}>
                    <td className="vt-td-nowrap">{v.meeting_date}</td>
                    <td className="vt-td-nowrap">{v.identifier || '--'}</td>
                    <td className="vt-td-desc">{v.description || '--'}</td>
                    <td>{v.ayes}</td>
                    <td>{v.nays}</td>
                    <td><OutcomeBadge outcome={v.outcome} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Financial Tab
// ---------------------------------------------------------------------------

function FinancialTab() {
  const [items, setItems] = useState([])
  const [summary, setSummary] = useState(null)
  const [total, setTotal] = useState(0)
  const [filters, setFilters] = useState({ keyword: '', item_type: '', min_amount: '', max_amount: '', date_after: '', date_before: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(0)
  const limit = 25

  const fetchItems = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (filters.keyword) params.set('keyword', filters.keyword)
      if (filters.item_type) params.set('item_type', filters.item_type)
      if (filters.min_amount) params.set('min_amount', filters.min_amount)
      if (filters.max_amount) params.set('max_amount', filters.max_amount)
      if (filters.date_after) params.set('date_after', filters.date_after)
      if (filters.date_before) params.set('date_before', filters.date_before)
      params.set('limit', limit)
      params.set('offset', page * limit)
      const data = await apiFetch(`/api/v1/financial?${params}`)
      setItems(data.items || [])
      setTotal(data.total || 0)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filters, page])

  const fetchSummary = useCallback(async () => {
    try {
      const data = await apiFetch('/api/v1/financial/summary')
      setSummary(data)
    } catch {
      // non-critical
    }
  }, [])

  useEffect(() => { fetchItems() }, [fetchItems])
  useEffect(() => { fetchSummary() }, [fetchSummary])

  const handleFilterChange = (key, value) => {
    setFilters((f) => ({ ...f, [key]: value }))
    setPage(0)
  }

  const totalPages = Math.ceil(total / limit)

  return (
    <div className="vt-tab-content">
      {/* Summary cards */}
      {summary && (
        <div className="vt-stats-grid">
          <div className="vt-stat-card">
            <div className="vt-stat-value">{summary.total_items}</div>
            <div className="vt-stat-label">Total Items</div>
          </div>
          <div className="vt-stat-card">
            <div className="vt-stat-value">{summary.total_amount_display}</div>
            <div className="vt-stat-label">Total Amount</div>
          </div>
        </div>
      )}

      {/* Type breakdown */}
      {summary && summary.by_type && summary.by_type.length > 0 && (
        <div className="vt-section">
          <h3 className="vt-section-title">By Type</h3>
          <div className="vt-type-bars">
            {summary.by_type.map((t) => (
              <div key={t.type} className="vt-type-row">
                <span className="vt-type-name">{t.type || 'unspecified'}</span>
                <span className="vt-type-amount">{t.total_display}</span>
                <span className="vt-type-count">{t.count} items</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <fieldset className="vt-filters">
        <legend className="sr-only">Filter financial items</legend>
        <input
          type="text"
          placeholder="Search descriptions..."
          value={filters.keyword}
          onChange={(e) => handleFilterChange('keyword', e.target.value)}
          className="vt-filter-input"
          aria-label="Search financial item descriptions"
        />
        <input
          type="text"
          placeholder="Type (e.g. appropriation)"
          value={filters.item_type}
          onChange={(e) => handleFilterChange('item_type', e.target.value)}
          className="vt-filter-input"
          aria-label="Filter by item type"
        />
        <input
          type="number"
          placeholder="Min $ amount"
          value={filters.min_amount}
          onChange={(e) => handleFilterChange('min_amount', e.target.value)}
          className="vt-filter-input vt-filter-amount"
          aria-label="Minimum dollar amount"
        />
        <input
          type="number"
          placeholder="Max $ amount"
          value={filters.max_amount}
          onChange={(e) => handleFilterChange('max_amount', e.target.value)}
          className="vt-filter-input vt-filter-amount"
          aria-label="Maximum dollar amount"
        />
        <input
          type="date"
          value={filters.date_after}
          onChange={(e) => handleFilterChange('date_after', e.target.value)}
          className="vt-filter-input vt-filter-date"
          aria-label="Filter items after date"
        />
        <input
          type="date"
          value={filters.date_before}
          onChange={(e) => handleFilterChange('date_before', e.target.value)}
          className="vt-filter-input vt-filter-date"
          aria-label="Filter items before date"
        />
      </fieldset>

      {error && <div className="vt-error" role="alert">{error}</div>}
      {loading && <div className="vt-loading" role="status" aria-live="polite">Loading financial items...</div>}

      {!loading && items.length > 0 && (
        <>
          <div className="vt-table-wrapper">
            <table className="vt-table" aria-label="Financial items">
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Description</th>
                  <th scope="col">Amount</th>
                  <th scope="col">Type</th>
                  <th scope="col">Vendor/Recipient</th>
                  <th scope="col">Clip</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="vt-td-nowrap">{item.meeting_date}</td>
                    <td className="vt-td-desc">{item.description || '--'}</td>
                    <td className="vt-td-amount">{item.amount || '--'}</td>
                    <td>{item.type || '--'}</td>
                    <td>{item.vendor_or_recipient || '--'}</td>
                    <td>
                      <a href={`/meeting/${item.clip_id}`} className="vt-clip-link">
                        #{item.clip_id}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="vt-pagination">
              <button
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="vt-page-btn"
              >
                Previous
              </button>
              <span className="vt-page-info">
                Page {page + 1} of {totalPages} ({total} results)
              </span>
              <button
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
                className="vt-page-btn"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      {!loading && items.length === 0 && !error && (
        <div className="vt-empty">No financial items found matching your filters.</div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Alerts Tab
// ---------------------------------------------------------------------------

const ALERT_TYPE_LABELS = {
  keyword: 'Keyword',
  member: 'Council Member',
  financial: 'Financial Threshold',
  vote: 'Vote Outcome',
}

function AlertsTab() {
  const [alerts, setAlerts] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [editingAlert, setEditingAlert] = useState(null)
  const [expandedAlert, setExpandedAlert] = useState(null)
  const [matches, setMatches] = useState({})

  // Form state
  const [formName, setFormName] = useState('')
  const [formType, setFormType] = useState('keyword')
  const [formKeywords, setFormKeywords] = useState('')
  const [formMembers, setFormMembers] = useState('')
  const [formThreshold, setFormThreshold] = useState('')
  const [formOutcome, setFormOutcome] = useState('passed')

  const fetchAlerts = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/api/v1/alerts')
      setAlerts(data.alerts || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchAlerts() }, [fetchAlerts])

  const buildConfig = () => {
    switch (formType) {
      case 'keyword':
        return { keywords: formKeywords.split(',').map((k) => k.trim()).filter(Boolean) }
      case 'member':
        return { members: formMembers.split(',').map((m) => m.trim()).filter(Boolean) }
      case 'financial':
        return { threshold: parseFloat(formThreshold) || 0 }
      case 'vote':
        return { outcome: formOutcome }
      default:
        return {}
    }
  }

  const resetForm = () => {
    setFormName('')
    setFormType('keyword')
    setFormKeywords('')
    setFormMembers('')
    setFormThreshold('')
    setFormOutcome('passed')
    setEditingAlert(null)
    setShowForm(false)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    try {
      const config = buildConfig()
      if (editingAlert) {
        await apiFetch(`/api/v1/alerts/${editingAlert.id}`, {
          method: 'PUT',
          body: JSON.stringify({ name: formName, config }),
        })
      } else {
        await apiFetch('/api/v1/alerts', {
          method: 'POST',
          body: JSON.stringify({ name: formName, type: formType, config }),
        })
      }
      resetForm()
      fetchAlerts()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this alert?')) return
    try {
      await apiFetch(`/api/v1/alerts/${id}`, { method: 'DELETE' })
      fetchAlerts()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleToggle = async (alert) => {
    try {
      await apiFetch(`/api/v1/alerts/${alert.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !alert.enabled }),
      })
      fetchAlerts()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleEdit = (alert) => {
    setEditingAlert(alert)
    setFormName(alert.name)
    setFormType(alert.type)
    const cfg = alert.config || {}
    setFormKeywords((cfg.keywords || []).join(', '))
    setFormMembers((cfg.members || []).join(', '))
    setFormThreshold(cfg.threshold || '')
    setFormOutcome(cfg.outcome || 'passed')
    setShowForm(true)
  }

  const loadMatches = async (alertId) => {
    if (expandedAlert === alertId) {
      setExpandedAlert(null)
      return
    }
    setExpandedAlert(alertId)
    try {
      const data = await apiFetch(`/api/v1/alerts/${alertId}/matches?limit=20`)
      setMatches((prev) => ({ ...prev, [alertId]: data.matches || [] }))
    } catch {
      // ignore
    }
  }

  return (
    <div className="vt-tab-content">
      <div className="vt-alerts-header">
        <h3 className="vt-section-title">Policy Alerts</h3>
        <button onClick={() => { resetForm(); setShowForm(!showForm) }} className="vt-btn-primary">
          {showForm ? 'Cancel' : '+ New Alert'}
        </button>
      </div>

      {error && <div className="vt-error" role="alert">{error}</div>}

      {/* Alert form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="vt-alert-form" aria-label={editingAlert ? 'Edit alert' : 'Create new alert'}>
          <div className="vt-form-row">
            <label className="vt-form-label" htmlFor="vt-alert-name">Name</label>
            <input
              id="vt-alert-name"
              type="text"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="e.g. Zoning changes"
              className="vt-filter-input"
              required
            />
          </div>

          {!editingAlert && (
            <div className="vt-form-row">
              <label className="vt-form-label" htmlFor="vt-alert-type">Type</label>
              <select id="vt-alert-type" value={formType} onChange={(e) => setFormType(e.target.value)} className="vt-filter-select">
                <option value="keyword">Keyword Match</option>
                <option value="member">Council Member Tracking</option>
                <option value="financial">Financial Threshold</option>
                <option value="vote">Vote Outcome</option>
              </select>
            </div>
          )}

          {/* Type-specific config */}
          {formType === 'keyword' && (
            <div className="vt-form-row">
              <label className="vt-form-label" htmlFor="vt-alert-keywords">Keywords (comma-separated)</label>
              <input
                id="vt-alert-keywords"
                type="text"
                value={formKeywords}
                onChange={(e) => setFormKeywords(e.target.value)}
                placeholder="zoning, short-term rental, budget"
                className="vt-filter-input"
              />
            </div>
          )}
          {formType === 'member' && (
            <div className="vt-form-row">
              <label className="vt-form-label" htmlFor="vt-alert-members">Members (comma-separated)</label>
              <input
                id="vt-alert-members"
                type="text"
                value={formMembers}
                onChange={(e) => setFormMembers(e.target.value)}
                placeholder="Brown, Curtis, Beasley"
                className="vt-filter-input"
              />
            </div>
          )}
          {formType === 'financial' && (
            <div className="vt-form-row">
              <label className="vt-form-label" htmlFor="vt-alert-threshold">Threshold ($)</label>
              <input
                id="vt-alert-threshold"
                type="number"
                value={formThreshold}
                onChange={(e) => setFormThreshold(e.target.value)}
                placeholder="100000"
                className="vt-filter-input"
              />
            </div>
          )}
          {formType === 'vote' && (
            <div className="vt-form-row">
              <label className="vt-form-label" htmlFor="vt-alert-outcome">Outcome</label>
              <select id="vt-alert-outcome" value={formOutcome} onChange={(e) => setFormOutcome(e.target.value)} className="vt-filter-select">
                <option value="passed">Passed</option>
                <option value="failed">Failed</option>
              </select>
            </div>
          )}

          <button type="submit" className="vt-btn-primary">
            {editingAlert ? 'Update Alert' : 'Create Alert'}
          </button>
        </form>
      )}

      {/* Alert list */}
      {loading && <div className="vt-loading" role="status" aria-live="polite">Loading alerts...</div>}
      {!loading && alerts.length === 0 && (
        <div className="vt-empty">
          No alerts configured yet. Create one to start monitoring policy topics.
        </div>
      )}
      {alerts.map((alert) => (
        <div key={alert.id} className={`vt-alert-card ${!alert.enabled ? 'vt-alert-disabled' : ''}`}>
          <div className="vt-alert-row">
            <div className="vt-alert-info">
              <span className="vt-alert-name">{alert.name}</span>
              <span className={`vt-badge vt-badge-type-${alert.type}`}>
                {ALERT_TYPE_LABELS[alert.type]}
              </span>
              {!alert.enabled && <span className="vt-badge vt-badge-other">Disabled</span>}
            </div>
            <div className="vt-alert-actions">
              <button onClick={() => loadMatches(alert.id)} className="vt-btn-sm">
                {expandedAlert === alert.id ? 'Hide' : 'Matches'}
              </button>
              <button onClick={() => handleToggle(alert)} className="vt-btn-sm">
                {alert.enabled ? 'Disable' : 'Enable'}
              </button>
              <button onClick={() => handleEdit(alert)} className="vt-btn-sm">Edit</button>
              <button onClick={() => handleDelete(alert.id)} className="vt-btn-sm vt-btn-danger">Delete</button>
            </div>
          </div>

          <div className="vt-alert-config">
            {alert.type === 'keyword' && <span>Keywords: {(alert.config.keywords || []).join(', ')}</span>}
            {alert.type === 'member' && <span>Members: {(alert.config.members || []).join(', ')}</span>}
            {alert.type === 'financial' && <span>Threshold: ${alert.config.threshold?.toLocaleString()}</span>}
            {alert.type === 'vote' && <span>Outcome: {alert.config.outcome}</span>}
          </div>

          {/* Matches */}
          {expandedAlert === alert.id && matches[alert.id] && (
            <div className="vt-matches">
              {matches[alert.id].length === 0 ? (
                <div className="vt-empty">No matches yet.</div>
              ) : (
                matches[alert.id].map((m) => (
                  <div key={m.id} className="vt-match-item">
                    <span className="vt-match-text">{m.matched_text}</span>
                    <a href={`/meeting/${m.clip_id}`} className="vt-clip-link">
                      Clip #{m.clip_id}
                    </a>
                    <span className="vt-match-date">
                      {m.context?.meeting_date || m.created_at?.slice(0, 10)}
                    </span>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main VoteTracker Component
// ---------------------------------------------------------------------------

export default function VoteTracker() {
  const [activeTab, setActiveTab] = useState('votes')

  return (
    <div className="container vt-container">
      <div className="vt-header">
        <h2 className="vt-title">Vote Tracker & Policy Monitor</h2>
        <p className="vt-subtitle">
          Track votes, monitor financial items, and set alerts for policy areas you care about.
        </p>
      </div>

      <div className="vt-tabs" role="tablist" aria-label="Vote tracker sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            id={`vt-tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            aria-controls={`vt-panel-${tab.id}`}
            onClick={() => setActiveTab(tab.id)}
            className={`vt-tab ${activeTab === tab.id ? 'vt-tab-active' : ''}`}
            tabIndex={activeTab === tab.id ? 0 : -1}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`vt-panel-${activeTab}`} aria-labelledby={`vt-tab-${activeTab}`}>
        {activeTab === 'votes' && <VotesTab />}
        {activeTab === 'members' && <MembersTab />}
        {activeTab === 'financial' && <FinancialTab />}
        {activeTab === 'alerts' && <AlertsTab />}
      </div>
    </div>
  )
}
