import { useState, useEffect, useCallback } from 'react'

const API_KEY_STORAGE = 'analytics_api_key'

function getApiKey() {
  return sessionStorage.getItem(API_KEY_STORAGE) || sessionStorage.getItem('admin_api_key') || ''
}

async function apiFetch(path) {
  const key = getApiKey()
  const res = await fetch(path, {
    headers: { 'X-API-Key': key },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Simple SVG Bar Chart
// ---------------------------------------------------------------------------

function BarChart({ data, width = 600, height = 200 }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ color: 'var(--text-tertiary)', padding: '40px 0', textAlign: 'center' }}>
        No data for this period
      </div>
    )
  }

  const padding = { top: 20, right: 20, bottom: 50, left: 50 }
  const chartW = width - padding.left - padding.right
  const chartH = height - padding.top - padding.bottom
  const maxVal = Math.max(...data.map((d) => d.count), 1)
  const barWidth = Math.max(4, Math.min(40, (chartW / data.length) * 0.7))
  const barGap = (chartW - barWidth * data.length) / Math.max(data.length - 1, 1)

  // Y-axis ticks
  const tickCount = 4
  const ticks = Array.from({ length: tickCount + 1 }, (_, i) =>
    Math.round((maxVal / tickCount) * i)
  )

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      style={{ width: '100%', maxWidth: width, height: 'auto' }}
      role="img"
      aria-label={`Bar chart showing query counts over ${data.length} time periods. Maximum: ${Math.max(...data.map((d) => d.count))} queries.`}
    >
      {/* Grid lines */}
      {ticks.map((tick) => {
        const y = padding.top + chartH - (tick / maxVal) * chartH
        return (
          <g key={tick}>
            <line
              x1={padding.left}
              y1={y}
              x2={width - padding.right}
              y2={y}
              stroke="var(--border-light)"
              strokeDasharray="4,4"
            />
            <text
              x={padding.left - 8}
              y={y + 4}
              textAnchor="end"
              fontSize="11"
              fill="var(--text-tertiary)"
            >
              {tick}
            </text>
          </g>
        )
      })}

      {/* Bars */}
      {data.map((d, i) => {
        const x = padding.left + i * (barWidth + barGap)
        const barH = (d.count / maxVal) * chartH
        const y = padding.top + chartH - barH
        const label = d.date ? d.date.slice(5) : `${i}`
        return (
          <g key={i}>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={barH}
              rx={Math.min(3, barWidth / 2)}
              fill="var(--blue-600)"
              opacity={0.85}
            >
              <title>{`${d.date}: ${d.count} queries`}</title>
            </rect>
            {/* X-axis labels (show every Nth to avoid overlap) */}
            {(data.length <= 14 || i % Math.ceil(data.length / 14) === 0) && (
              <text
                x={x + barWidth / 2}
                y={height - padding.bottom + 16}
                textAnchor="middle"
                fontSize="10"
                fill="var(--text-tertiary)"
                transform={`rotate(-35, ${x + barWidth / 2}, ${height - padding.bottom + 16})`}
              >
                {label}
              </text>
            )}
          </g>
        )
      })}

      {/* Axes */}
      <line
        x1={padding.left}
        y1={padding.top}
        x2={padding.left}
        y2={padding.top + chartH}
        stroke="var(--border)"
      />
      <line
        x1={padding.left}
        y1={padding.top + chartH}
        x2={width - padding.right}
        y2={padding.top + chartH}
        stroke="var(--border)"
      />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Stats Card
// ---------------------------------------------------------------------------

function StatCard({ label, value, sub }) {
  return (
    <div className="analytics-stat-card">
      <div className="analytics-stat-value">{value}</div>
      <div className="analytics-stat-label">{label}</div>
      {sub && <div className="analytics-stat-sub">{sub}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// API Key Input (reuses admin key if present, or allows separate entry)
// ---------------------------------------------------------------------------

function ApiKeyPrompt({ onReady }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [checking, setChecking] = useState(false)

  // Auto-detect if admin key or tenant key is already stored
  useEffect(() => {
    const existing = getApiKey()
    if (existing) {
      onReady()
    }
  }, [onReady])

  async function handleSubmit(e) {
    e.preventDefault()
    if (!key.trim()) return
    setChecking(true)
    setError(null)
    try {
      sessionStorage.setItem(API_KEY_STORAGE, key.trim())
      const res = await fetch('/api/v1/analytics/usage', {
        headers: { 'X-API-Key': key.trim() },
      })
      if (!res.ok) {
        sessionStorage.removeItem(API_KEY_STORAGE)
        setError('Invalid API key')
        return
      }
      onReady()
    } catch {
      sessionStorage.removeItem(API_KEY_STORAGE)
      setError('Could not connect to API')
    } finally {
      setChecking(false)
    }
  }

  if (getApiKey()) return null

  return (
    <div className="admin-login-wrapper">
      <div className="admin-login-card" role="region" aria-label="Analytics authentication">
        <div className="admin-login-icon" aria-hidden="true">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12V7H5a2 2 0 010-4h14v4" />
            <path d="M3 5v14a2 2 0 002 2h16v-5" />
            <path d="M18 12a2 2 0 100 4 2 2 0 000-4z" />
          </svg>
        </div>
        <h2>Analytics</h2>
        <p className="admin-login-hint" id="analytics-login-desc">Enter your API key to view usage analytics</p>
        <form onSubmit={handleSubmit} aria-describedby="analytics-login-desc">
          <label htmlFor="analytics-api-key-input" className="sr-only">API key</label>
          <input
            id="analytics-api-key-input"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="API key"
            className="admin-input"
            autoFocus
            aria-describedby={error ? 'analytics-login-error' : undefined}
          />
          {error && <p className="admin-error" id="analytics-login-error" role="alert">{error}</p>}
          <button
            type="submit"
            className="admin-btn admin-btn-primary admin-btn-full"
            disabled={checking || !key.trim()}
          >
            {checking ? 'Verifying...' : 'View Analytics'}
          </button>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Period Selector
// ---------------------------------------------------------------------------

function PeriodSelector({ period, onChange }) {
  const periods = [
    { value: '7d', label: '7 days' },
    { value: '30d', label: '30 days' },
    { value: '90d', label: '90 days' },
  ]
  return (
    <div className="analytics-period-selector" role="group" aria-label="Time period">
      {periods.map((p) => (
        <button
          key={p.value}
          className={`analytics-period-btn ${period === p.value ? 'active' : ''}`}
          onClick={() => onChange(p.value)}
          aria-pressed={period === p.value}
        >
          {p.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Query Log Table
// ---------------------------------------------------------------------------

function QueryLogTable({ queries }) {
  if (!queries || queries.length === 0) {
    return <p style={{ color: 'var(--text-tertiary)', padding: '16px 0' }}>No queries recorded yet.</p>
  }

  function formatTime(iso) {
    if (!iso) return '-'
    const d = new Date(iso)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
      ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div className="analytics-table-wrapper">
      <table className="analytics-table" aria-label="Recent query log">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Question</th>
            <th scope="col">Model</th>
            <th scope="col" style={{ textAlign: 'right' }}>Response (ms)</th>
            <th scope="col" style={{ textAlign: 'right' }}>Sources</th>
          </tr>
        </thead>
        <tbody>
          {queries.map((q, i) => (
            <tr key={i}>
              <td className="analytics-td-nowrap">{formatTime(q.timestamp)}</td>
              <td className="analytics-td-question" title={q.question}>
                {q.question.length > 80 ? q.question.slice(0, 80) + '...' : q.question}
              </td>
              <td className="analytics-td-nowrap">{q.model || '-'}</td>
              <td style={{ textAlign: 'right' }}>{q.response_time_ms?.toFixed(0) || '-'}</td>
              <td style={{ textAlign: 'right' }}>{q.chunks_retrieved ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------

function AnalyticsDashboard() {
  const [ready, setReady] = useState(!!getApiKey())
  const [period, setPeriod] = useState('30d')
  const [usage, setUsage] = useState(null)
  const [queriesOverTime, setQueriesOverTime] = useState([])
  const [recentQueries, setRecentQueries] = useState([])
  const [popularTopics, setPopularTopics] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const loadData = useCallback(async (p) => {
    setLoading(true)
    setError(null)
    try {
      const [usageData, queriesData, recentData, topicsData] = await Promise.all([
        apiFetch(`/api/v1/analytics/usage?period=${p}`),
        apiFetch(`/api/v1/analytics/queries?period=${p}`),
        apiFetch('/api/v1/analytics/recent?limit=50'),
        apiFetch(`/api/v1/analytics/popular-topics?period=${p}`),
      ])
      setUsage(usageData)
      setQueriesOverTime(queriesData.data || [])
      setRecentQueries(recentData.queries || [])
      setPopularTopics(topicsData)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (ready) {
      loadData(period)
    }
  }, [ready, period, loadData])

  function handlePeriodChange(p) {
    setPeriod(p)
  }

  if (!ready) {
    return <ApiKeyPrompt onReady={() => setReady(true)} />
  }

  const topBody = popularTopics?.meeting_bodies?.[0]?.meeting_body || '-'

  return (
    <div className="container" style={{ paddingTop: 24, paddingBottom: 40 }}>
      <div className="analytics-header">
        <h2 className="analytics-title">Usage Analytics</h2>
        <PeriodSelector period={period} onChange={handlePeriodChange} />
      </div>

      {error && (
        <div className="admin-error-banner" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Stats Cards */}
      <div className="analytics-stats-grid">
        <StatCard
          label="Total Queries"
          value={loading ? '...' : (usage?.total_queries ?? 0).toLocaleString()}
        />
        <StatCard
          label="Avg Response Time"
          value={loading ? '...' : `${usage?.avg_response_time_ms ?? 0} ms`}
        />
        <StatCard
          label="Meetings Processed"
          value={loading ? '...' : (usage?.meetings_processed ?? 0).toLocaleString()}
        />
        <StatCard
          label="Top Meeting Body"
          value={loading ? '...' : topBody}
        />
      </div>

      {/* Chart */}
      <div className="analytics-card">
        <h3 className="analytics-card-title">Queries Over Time</h3>
        {loading ? (
          <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading...</div>
        ) : (
          <BarChart data={queriesOverTime} />
        )}
      </div>

      {/* Popular Topics */}
      {popularTopics?.meeting_bodies?.length > 0 && (
        <div className="analytics-card">
          <h3 className="analytics-card-title">Popular Meeting Bodies</h3>
          <div className="analytics-topics-list">
            {popularTopics.meeting_bodies.map((t, i) => (
              <div key={i} className="analytics-topic-row">
                <span className="analytics-topic-name">{t.meeting_body}</span>
                <div className="analytics-topic-bar-wrapper">
                  <div
                    className="analytics-topic-bar"
                    style={{
                      width: `${(t.count / (popularTopics.meeting_bodies[0]?.count || 1)) * 100}%`,
                    }}
                  />
                </div>
                <span className="analytics-topic-count">{t.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent Queries */}
      <div className="analytics-card">
        <h3 className="analytics-card-title">Recent Queries</h3>
        <QueryLogTable queries={recentQueries} />
      </div>

      {/* Export */}
      <div style={{ marginTop: 16, textAlign: 'right' }}>
        <a
          href={`/api/v1/analytics/export/queries?period=${period}`}
          className="analytics-export-link"
          onClick={(e) => {
            e.preventDefault()
            const key = getApiKey()
            fetch(`/api/v1/analytics/export/queries?period=${period}`, {
              headers: { 'X-API-Key': key },
            })
              .then((res) => res.blob())
              .then((blob) => {
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `queries_${period}.csv`
                a.click()
                URL.revokeObjectURL(url)
              })
          }}
        >
          Export CSV
        </a>
      </div>
    </div>
  )
}

export default AnalyticsDashboard
