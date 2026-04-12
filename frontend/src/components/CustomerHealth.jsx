import { useState, useEffect, useCallback } from 'react'
import { useAdminAuth } from '../hooks/useAdmin'

function getAdminKey() {
  return sessionStorage.getItem('admin_api_key') || ''
}

async function healthFetch(path, options = {}) {
  const key = getAdminKey()
  if (!key) throw new Error('Not authenticated')
  const res = await fetch(`/api/v1/admin/health${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': key,
      ...options.headers,
    },
  })
  if (res.status === 401 || res.status === 403) {
    throw new Error('Admin access required')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

/* --------------------------------------------------------------------------
   Health Gauge (SVG arc)
   -------------------------------------------------------------------------- */

function HealthGauge({ score, size = 120 }) {
  const radius = (size - 16) / 2
  const cx = size / 2
  const cy = size / 2
  const circumference = Math.PI * radius
  const progress = (score / 100) * circumference
  const rotation = -180

  let color = '#ef4444' // red / churning
  if (score >= 80) color = '#22c55e' // green / healthy
  else if (score >= 50) color = '#f59e0b' // amber / at_risk

  const trackColor = 'var(--border-light)'

  return (
    <svg width={size} height={size / 2 + 16} viewBox={`0 0 ${size} ${size / 2 + 16}`}>
      {/* Track */}
      <path
        d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
        fill="none"
        stroke={trackColor}
        strokeWidth="10"
        strokeLinecap="round"
      />
      {/* Progress */}
      <path
        d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
        fill="none"
        stroke={color}
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${progress} ${circumference}`}
      />
      {/* Score text */}
      <text
        x={cx}
        y={cy - 4}
        textAnchor="middle"
        fontSize="22"
        fontWeight="700"
        fill="var(--text-primary)"
      >
        {Math.round(score)}
      </text>
      <text
        x={cx}
        y={cy + 12}
        textAnchor="middle"
        fontSize="10"
        fill="var(--text-tertiary)"
      >
        / 100
      </text>
    </svg>
  )
}

/* --------------------------------------------------------------------------
   Trend Arrow
   -------------------------------------------------------------------------- */

function TrendArrow({ trend }) {
  if (trend === 'improving') {
    return <span className="ch-trend ch-trend-up" title="Improving">&#x25B2;</span>
  }
  if (trend === 'declining') {
    return <span className="ch-trend ch-trend-down" title="Declining">&#x25BC;</span>
  }
  return <span className="ch-trend ch-trend-stable" title="Stable">&#x25C6;</span>
}

/* --------------------------------------------------------------------------
   Category Badge
   -------------------------------------------------------------------------- */

function CategoryBadge({ category }) {
  const cls = `ch-badge ch-badge-${category}`
  const label = category === 'at_risk' ? 'At Risk' : category.charAt(0).toUpperCase() + category.slice(1)
  return <span className={cls}>{label}</span>
}

/* --------------------------------------------------------------------------
   Radar Chart (SVG)
   -------------------------------------------------------------------------- */

function RadarChart({ breakdown, size = 240 }) {
  const labels = [
    { key: 'query_frequency', label: 'Queries' },
    { key: 'feature_breadth', label: 'Features' },
    { key: 'user_engagement', label: 'Engagement' },
    { key: 'data_freshness', label: 'Freshness' },
    { key: 'alert_activity', label: 'Alerts' },
    { key: 'support_signals', label: 'Support' },
    { key: 'billing_health', label: 'Billing' },
  ]

  const cx = size / 2
  const cy = size / 2
  const maxR = size / 2 - 30
  const n = labels.length
  const angleStep = (2 * Math.PI) / n
  const startAngle = -Math.PI / 2

  function pointAt(index, value) {
    const angle = startAngle + index * angleStep
    const r = (value / 100) * maxR
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    }
  }

  // Grid rings
  const rings = [25, 50, 75, 100]

  // Data polygon
  const dataPoints = labels.map((l, i) => pointAt(i, breakdown[l.key] || 0))
  const polygonStr = dataPoints.map((p) => `${p.x},${p.y}`).join(' ')

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="ch-radar">
      {/* Grid rings */}
      {rings.map((ring) => {
        const pts = labels
          .map((_, i) => {
            const p = pointAt(i, ring)
            return `${p.x},${p.y}`
          })
          .join(' ')
        return (
          <polygon
            key={ring}
            points={pts}
            fill="none"
            stroke="var(--border-light)"
            strokeWidth="1"
          />
        )
      })}

      {/* Axis lines */}
      {labels.map((_, i) => {
        const p = pointAt(i, 100)
        return (
          <line
            key={`axis-${i}`}
            x1={cx}
            y1={cy}
            x2={p.x}
            y2={p.y}
            stroke="var(--border-light)"
            strokeWidth="1"
          />
        )
      })}

      {/* Data polygon */}
      <polygon
        points={polygonStr}
        fill="var(--blue-600)"
        fillOpacity="0.18"
        stroke="var(--blue-600)"
        strokeWidth="2"
      />

      {/* Data points */}
      {dataPoints.map((p, i) => (
        <circle key={`dp-${i}`} cx={p.x} cy={p.y} r="3.5" fill="var(--blue-600)" />
      ))}

      {/* Labels */}
      {labels.map((l, i) => {
        const angle = startAngle + i * angleStep
        const labelR = maxR + 18
        const lx = cx + labelR * Math.cos(angle)
        const ly = cy + labelR * Math.sin(angle)
        const anchor = Math.abs(angle) < 0.1 || Math.abs(angle - Math.PI) < 0.1
          ? 'middle'
          : angle > -Math.PI / 2 && angle < Math.PI / 2
            ? 'start'
            : 'end'
        return (
          <text
            key={`label-${i}`}
            x={lx}
            y={ly + 4}
            textAnchor={anchor}
            fontSize="10"
            fill="var(--text-secondary)"
          >
            {l.label}
          </text>
        )
      })}
    </svg>
  )
}

/* --------------------------------------------------------------------------
   Score Breakdown Bar
   -------------------------------------------------------------------------- */

function BreakdownBar({ label, value, weight }) {
  let color = '#ef4444'
  if (value >= 70) color = '#22c55e'
  else if (value >= 40) color = '#f59e0b'

  return (
    <div className="ch-breakdown-row">
      <div className="ch-breakdown-label">
        <span>{label}</span>
        <span className="ch-breakdown-weight">{Math.round(weight * 100)}%</span>
      </div>
      <div className="ch-breakdown-bar-track">
        <div
          className="ch-breakdown-bar-fill"
          style={{ width: `${Math.min(value, 100)}%`, background: color }}
        />
      </div>
      <span className="ch-breakdown-value">{Math.round(value)}</span>
    </div>
  )
}

/* --------------------------------------------------------------------------
   Expanded Detail Panel
   -------------------------------------------------------------------------- */

function TenantDetail({ data, onClose }) {
  if (!data) return null

  const weights = {
    query_frequency: 0.25,
    feature_breadth: 0.20,
    user_engagement: 0.15,
    data_freshness: 0.15,
    alert_activity: 0.10,
    support_signals: 0.10,
    billing_health: 0.05,
  }

  const breakdownLabels = {
    query_frequency: 'Query Frequency',
    feature_breadth: 'Feature Breadth',
    user_engagement: 'User Engagement',
    data_freshness: 'Data Freshness',
    alert_activity: 'Alert Activity',
    support_signals: 'Support Health',
    billing_health: 'Billing Health',
  }

  return (
    <div className="ch-detail-backdrop" onClick={onClose}>
      <div className="ch-detail-panel" onClick={(e) => e.stopPropagation()}>
        <div className="ch-detail-header">
          <div>
            <h3>{data.tenant_id}</h3>
            {data.tenant_name && <span className="ch-detail-name">{data.tenant_name}</span>}
          </div>
          <button className="ch-close-btn" onClick={onClose}>&#x2715;</button>
        </div>

        <div className="ch-detail-top">
          <div className="ch-detail-gauge">
            <HealthGauge score={data.score} size={140} />
            <CategoryBadge category={data.category} />
            <TrendArrow trend={data.trend} />
          </div>
          <div className="ch-detail-radar">
            <RadarChart breakdown={data.breakdown} size={220} />
          </div>
        </div>

        <div className="ch-detail-section">
          <h4>Score Breakdown</h4>
          {Object.entries(breakdownLabels).map(([key, label]) => (
            <BreakdownBar
              key={key}
              label={label}
              value={data.breakdown[key] || 0}
              weight={weights[key]}
            />
          ))}
        </div>

        {data.recommendations && data.recommendations.length > 0 && (
          <div className="ch-detail-section">
            <h4>Recommendations</h4>
            <ul className="ch-rec-list">
              {data.recommendations.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        {data.expansion_signals && data.expansion_signals.length > 0 && (
          <div className="ch-detail-section ch-expansion-section">
            <h4>Expansion Signals</h4>
            <ul className="ch-rec-list ch-expansion-list">
              {data.expansion_signals.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="ch-detail-footer">
          <span className="ch-detail-ts">
            Computed: {data.computed_at ? new Date(data.computed_at).toLocaleString() : '-'}
          </span>
          {data.plan && <span className={`admin-plan-badge admin-plan-${data.plan}`}>{data.plan}</span>}
        </div>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------------------
   Admin Login Gate (reuses pattern from AdminDashboard)
   -------------------------------------------------------------------------- */

function AdminLogin({ onLogin }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [checking, setChecking] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!key.trim()) return
    setChecking(true)
    setError(null)
    try {
      sessionStorage.setItem('admin_api_key', key.trim())
      const res = await fetch('/api/v1/admin/tenants', {
        headers: { 'X-API-Key': key.trim() },
      })
      if (!res.ok) {
        sessionStorage.removeItem('admin_api_key')
        setError('Invalid admin API key')
        return
      }
      onLogin(key.trim())
    } catch {
      sessionStorage.removeItem('admin_api_key')
      setError('Could not connect to API')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="admin-login-wrapper">
      <div className="admin-login-card">
        <div className="admin-login-icon">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0110 0v4" />
          </svg>
        </div>
        <h2>Customer Health</h2>
        <p className="admin-login-hint">Enter your admin API key to view health scores</p>
        <form onSubmit={handleSubmit}>
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Admin API key"
            className="admin-input"
            autoFocus
          />
          {error && <p className="admin-error">{error}</p>}
          <button type="submit" className="admin-btn admin-btn-primary admin-btn-full" disabled={checking || !key.trim()}>
            {checking ? 'Verifying...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------------------
   Sortable Table
   -------------------------------------------------------------------------- */

function SortableHeader({ label, field, sortField, sortDir, onSort }) {
  const active = sortField === field
  const arrow = active ? (sortDir === 'asc' ? ' \u25B4' : ' \u25BE') : ''
  return (
    <th
      className="ch-sortable-th"
      onClick={() => onSort(field)}
    >
      {label}{arrow}
    </th>
  )
}

/* --------------------------------------------------------------------------
   Main Dashboard
   -------------------------------------------------------------------------- */

export default function CustomerHealth() {
  const { isAuthenticated, login, logout } = useAdminAuth()

  const [scores, setScores] = useState([])
  const [summary, setSummary] = useState({ healthy: 0, at_risk: 0, churning: 0, total: 0 })
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [selectedTenant, setSelectedTenant] = useState(null)
  const [detailData, setDetailData] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [sortField, setSortField] = useState('score')
  const [sortDir, setSortDir] = useState('asc')
  const [filterCategory, setFilterCategory] = useState('all')

  const loadScores = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await healthFetch('/scores')
      setScores(data.scores || [])
      setSummary({
        healthy: data.healthy || 0,
        at_risk: data.at_risk || 0,
        churning: data.churning || 0,
        total: data.total || 0,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  async function handleRefresh() {
    setRefreshing(true)
    setError(null)
    try {
      const data = await healthFetch('/refresh', { method: 'POST' })
      setScores(data.scores || [])
      setSummary({
        healthy: data.summary?.healthy || 0,
        at_risk: data.summary?.at_risk || 0,
        churning: data.summary?.churning || 0,
        total: data.refreshed || 0,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setRefreshing(false)
    }
  }

  async function openDetail(tenantId) {
    setSelectedTenant(tenantId)
    setDetailLoading(true)
    try {
      const data = await healthFetch(`/scores/${tenantId}`)
      setDetailData(data)
    } catch (err) {
      setError(err.message)
      setSelectedTenant(null)
    } finally {
      setDetailLoading(false)
    }
  }

  function closeDetail() {
    setSelectedTenant(null)
    setDetailData(null)
  }

  function handleSort(field) {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir('asc')
    }
  }

  useEffect(() => {
    if (isAuthenticated) {
      loadScores()
    }
  }, [isAuthenticated, loadScores])

  if (!isAuthenticated) {
    return <AdminLogin onLogin={login} />
  }

  // Filter
  const filtered = filterCategory === 'all'
    ? scores
    : scores.filter((s) => s.category === filterCategory)

  // Sort
  const sorted = [...filtered].sort((a, b) => {
    let av = a[sortField]
    let bv = b[sortField]
    if (typeof av === 'string') av = av.toLowerCase()
    if (typeof bv === 'string') bv = bv.toLowerCase()
    if (av < bv) return sortDir === 'asc' ? -1 : 1
    if (av > bv) return sortDir === 'asc' ? 1 : -1
    return 0
  })

  const atRiskTenants = scores.filter((s) => s.category === 'at_risk' || s.category === 'churning')
  const expansionTenants = scores.filter((s) => s.expansion_signals && s.expansion_signals.length > 0)

  return (
    <div className="container ch-container">
      <div className="ch-top-bar">
        <h2>Customer Health Dashboard</h2>
        <div className="ch-top-actions">
          <button
            className="admin-btn admin-btn-primary"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing...' : 'Refresh Scores'}
          </button>
          <button className="admin-btn admin-btn-ghost" onClick={logout}>
            Sign Out
          </button>
        </div>
      </div>

      {error && (
        <div className="admin-error-banner">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="admin-error-dismiss">&times;</button>
        </div>
      )}

      {/* At-risk alert banner */}
      {atRiskTenants.length > 0 && (
        <div className="ch-alert-banner">
          <div className="ch-alert-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <div>
            <strong>{atRiskTenants.length} tenant{atRiskTenants.length !== 1 ? 's' : ''} need attention</strong>
            <span className="ch-alert-sub">
              {' '}&mdash; {atRiskTenants.filter((t) => t.category === 'churning').length} churning,{' '}
              {atRiskTenants.filter((t) => t.category === 'at_risk').length} at risk
            </span>
          </div>
        </div>
      )}

      {/* Summary cards */}
      <div className="ch-summary-grid">
        <div className="ch-summary-card">
          <div className="ch-summary-value">{summary.total}</div>
          <div className="ch-summary-label">Total Tenants</div>
        </div>
        <div className="ch-summary-card ch-summary-healthy">
          <div className="ch-summary-value">{summary.healthy}</div>
          <div className="ch-summary-label">Healthy</div>
        </div>
        <div className="ch-summary-card ch-summary-atrisk">
          <div className="ch-summary-value">{summary.at_risk}</div>
          <div className="ch-summary-label">At Risk</div>
        </div>
        <div className="ch-summary-card ch-summary-churning">
          <div className="ch-summary-value">{summary.churning}</div>
          <div className="ch-summary-label">Churning</div>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="ch-filter-bar">
        {['all', 'healthy', 'at_risk', 'churning'].map((cat) => (
          <button
            key={cat}
            className={`ch-filter-btn ${filterCategory === cat ? 'active' : ''}`}
            onClick={() => setFilterCategory(cat)}
          >
            {cat === 'all' ? 'All' : cat === 'at_risk' ? 'At Risk' : cat.charAt(0).toUpperCase() + cat.slice(1)}
          </button>
        ))}
      </div>

      {/* Tenant table */}
      {loading && scores.length === 0 ? (
        <div className="ch-loading">Loading health scores...</div>
      ) : sorted.length === 0 ? (
        <div className="ch-empty">No tenants found.</div>
      ) : (
        <div className="ch-table-wrapper">
          <table className="ch-table">
            <thead>
              <tr>
                <SortableHeader label="Tenant" field="tenant_id" sortField={sortField} sortDir={sortDir} onSort={handleSort} />
                <SortableHeader label="Score" field="score" sortField={sortField} sortDir={sortDir} onSort={handleSort} />
                <th>Trend</th>
                <SortableHeader label="Category" field="category" sortField={sortField} sortDir={sortDir} onSort={handleSort} />
                <th>Signals</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((s) => (
                <tr key={s.tenant_id} className={`ch-row ch-row-${s.category}`}>
                  <td className="ch-td-tenant">{s.tenant_id}</td>
                  <td>
                    <div className="ch-score-cell">
                      <div className="ch-score-mini-bar">
                        <div
                          className={`ch-score-mini-fill ch-fill-${s.category}`}
                          style={{ width: `${s.score}%` }}
                        />
                      </div>
                      <span className="ch-score-num">{Math.round(s.score)}</span>
                    </div>
                  </td>
                  <td><TrendArrow trend={s.trend} /></td>
                  <td><CategoryBadge category={s.category} /></td>
                  <td className="ch-td-signals">
                    {s.expansion_signals && s.expansion_signals.length > 0 && (
                      <span className="ch-signal-badge ch-signal-expansion" title={s.expansion_signals.join('; ')}>
                        Expand
                      </span>
                    )}
                    {s.recommendations && s.recommendations.length > 0 && (
                      <span className="ch-signal-badge ch-signal-rec" title={s.recommendations.join('; ')}>
                        {s.recommendations.length} rec{s.recommendations.length !== 1 ? 's' : ''}
                      </span>
                    )}
                  </td>
                  <td>
                    <button
                      className="admin-btn admin-btn-small"
                      onClick={() => openDetail(s.tenant_id)}
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Expansion opportunities */}
      {expansionTenants.length > 0 && (
        <div className="ch-section">
          <h3 className="ch-section-title">Expansion Opportunities</h3>
          <div className="ch-expansion-grid">
            {expansionTenants.map((t) => (
              <div key={t.tenant_id} className="ch-expansion-card">
                <div className="ch-expansion-card-header">
                  <strong>{t.tenant_id}</strong>
                  <span className="ch-score-num">{Math.round(t.score)}</span>
                </div>
                <ul className="ch-expansion-signals">
                  {t.expansion_signals.map((sig, i) => (
                    <li key={i}>{sig}</li>
                  ))}
                </ul>
                <button
                  className="admin-btn admin-btn-small admin-btn-primary"
                  onClick={() => openDetail(t.tenant_id)}
                >
                  View Details
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Detail panel */}
      {selectedTenant && (
        detailLoading ? (
          <div className="ch-detail-backdrop">
            <div className="ch-detail-panel ch-detail-loading">
              Loading details...
            </div>
          </div>
        ) : (
          <TenantDetail data={detailData} onClose={closeDetail} />
        )
      )}
    </div>
  )
}
