import { useState, useEffect, useCallback } from 'react'
import { useUsage, useUsageAuth } from '../hooks/useUsage'

// ---------------------------------------------------------------------------
// API Key Login Prompt
// ---------------------------------------------------------------------------

function UsageLogin({ onLogin }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [checking, setChecking] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!key.trim()) return
    setChecking(true)
    setError(null)

    try {
      sessionStorage.setItem('usage_api_key', key.trim())
      const res = await fetch('/api/v1/analytics/usage', {
        headers: { 'X-API-Key': key.trim() },
      })
      if (!res.ok) {
        sessionStorage.removeItem('usage_api_key')
        setError('Invalid API key')
        return
      }
      onLogin(key.trim())
    } catch {
      sessionStorage.removeItem('usage_api_key')
      setError('Could not connect to API')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="admin-login-wrapper">
      <div className="admin-login-card" role="region" aria-label="Usage dashboard authentication">
        <div className="admin-login-icon" aria-hidden="true">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 20V10" />
            <path d="M18 20V4" />
            <path d="M6 20v-4" />
          </svg>
        </div>
        <h2>API Usage</h2>
        <p className="admin-login-hint" id="usage-login-desc">Enter your API key to view usage and billing</p>
        <form onSubmit={handleSubmit} aria-describedby="usage-login-desc">
          <label htmlFor="usage-api-key-input" className="sr-only">API key</label>
          <input
            id="usage-api-key-input"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="API key"
            className="admin-input"
            autoFocus
            aria-describedby={error ? 'usage-login-error' : undefined}
          />
          {error && <p className="admin-error" id="usage-login-error" role="alert">{error}</p>}
          <button
            type="submit"
            className="admin-btn admin-btn-primary admin-btn-full"
            disabled={checking || !key.trim()}
          >
            {checking ? 'Verifying...' : 'View Dashboard'}
          </button>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Usage Meter (progress bar with color thresholds)
// ---------------------------------------------------------------------------

function UsageMeter({ used, limit, label }) {
  if (!limit || limit <= 0) {
    return (
      <div className="usage-meter">
        <div className="usage-meter-header">
          <span className="usage-meter-label">{label || 'Queries this month'}</span>
          <span className="usage-meter-count">{(used || 0).toLocaleString()} / Unlimited</span>
        </div>
        <div className="usage-meter-track">
          <div className="usage-meter-fill usage-meter-ok" style={{ width: '8%' }} />
        </div>
      </div>
    )
  }

  const pct = Math.min((used / limit) * 100, 100)
  const colorClass = pct >= 90 ? 'usage-meter-critical' : pct >= 70 ? 'usage-meter-warning' : 'usage-meter-ok'

  return (
    <div className="usage-meter" role="meter" aria-valuenow={used} aria-valuemin={0} aria-valuemax={limit} aria-label={label || 'Queries this month'}>
      <div className="usage-meter-header">
        <span className="usage-meter-label">{label || 'Queries this month'}</span>
        <span className="usage-meter-count">{used.toLocaleString()} / {limit.toLocaleString()}</span>
      </div>
      <div className="usage-meter-track">
        <div className={`usage-meter-fill ${colorClass}`} style={{ width: `${Math.max(pct, 1)}%` }} />
      </div>
      <div className="usage-meter-footer">
        <span className="usage-meter-pct">{pct.toFixed(0)}% used</span>
        <span className="usage-meter-remaining">{Math.max(limit - used, 0).toLocaleString()} remaining</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stats Cards
// ---------------------------------------------------------------------------

function StatCard({ icon, label, value, sub }) {
  return (
    <div className="usage-stat-card">
      <div className="usage-stat-icon" aria-hidden="true">{icon}</div>
      <div className="usage-stat-body">
        <div className="usage-stat-value">{value}</div>
        <div className="usage-stat-label">{label}</div>
        {sub && <div className="usage-stat-sub">{sub}</div>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SVG Bar Chart (30 days)
// ---------------------------------------------------------------------------

function DailyChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div className="usage-chart-empty">
        No query data yet. Start making API requests to see usage.
      </div>
    )
  }

  const width = 640
  const height = 200
  const pad = { top: 16, right: 16, bottom: 44, left: 44 }
  const chartW = width - pad.left - pad.right
  const chartH = height - pad.top - pad.bottom
  const maxVal = Math.max(...data.map((d) => d.count), 1)
  const barW = Math.max(3, Math.min(16, (chartW / data.length) * 0.65))
  const gap = (chartW - barW * data.length) / Math.max(data.length - 1, 1)

  const ticks = 4
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => Math.round((maxVal / ticks) * i))

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="usage-chart-svg"
      role="img"
      aria-label={`Daily query volume over ${data.length} days`}
    >
      {tickVals.map((tick) => {
        const y = pad.top + chartH - (tick / maxVal) * chartH
        return (
          <g key={tick}>
            <line x1={pad.left} y1={y} x2={width - pad.right} y2={y} stroke="var(--border-light)" strokeDasharray="3,3" />
            <text x={pad.left - 6} y={y + 3} textAnchor="end" fontSize="10" fill="var(--text-tertiary)">{tick}</text>
          </g>
        )
      })}

      {data.map((d, i) => {
        const x = pad.left + i * (barW + gap)
        const barH = Math.max((d.count / maxVal) * chartH, 1)
        const y = pad.top + chartH - barH
        const label = d.date ? d.date.slice(5) : ''
        const showLabel = data.length <= 15 || i % Math.ceil(data.length / 12) === 0
        return (
          <g key={i}>
            <rect x={x} y={y} width={barW} height={barH} rx={2} fill="var(--blue-500)" opacity={0.85}>
              <title>{d.date}: {d.count} queries</title>
            </rect>
            {showLabel && (
              <text
                x={x + barW / 2}
                y={height - pad.bottom + 14}
                textAnchor="middle"
                fontSize="9"
                fill="var(--text-tertiary)"
                transform={`rotate(-40, ${x + barW / 2}, ${height - pad.bottom + 14})`}
              >
                {label}
              </text>
            )}
          </g>
        )
      })}

      <line x1={pad.left} y1={pad.top} x2={pad.left} y2={pad.top + chartH} stroke="var(--border)" />
      <line x1={pad.left} y1={pad.top + chartH} x2={width - pad.right} y2={pad.top + chartH} stroke="var(--border)" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Top Queries
// ---------------------------------------------------------------------------

function TopQueries({ questions }) {
  if (!questions || questions.length === 0) {
    return <p className="usage-empty-hint">No queries recorded yet.</p>
  }
  const maxCount = questions[0]?.count || 1
  return (
    <div className="usage-top-queries">
      {questions.slice(0, 8).map((q, i) => (
        <div key={i} className="usage-query-row">
          <span className="usage-query-rank">{i + 1}</span>
          <div className="usage-query-body">
            <span className="usage-query-text" title={q.question}>
              {q.question.length > 90 ? q.question.slice(0, 90) + '...' : q.question}
            </span>
            <div className="usage-query-bar-wrap">
              <div
                className="usage-query-bar"
                style={{ width: `${(q.count / maxCount) * 100}%` }}
              />
            </div>
          </div>
          <span className="usage-query-count">{q.count}</span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Integrations Status
// ---------------------------------------------------------------------------

function IntegrationStatus({ integrations }) {
  const items = [
    {
      name: 'Slack',
      connected: !!integrations.slack?.team_name,
      detail: integrations.slack?.team_name || 'Not connected',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="13" y="2" width="3" height="8" rx="1.5" />
          <path d="M19 8.5V10h1.5A1.5 1.5 0 1019 8.5" />
          <rect x="8" y="14" width="3" height="8" rx="1.5" />
          <path d="M5 15.5V14H3.5A1.5 1.5 0 105 15.5" />
          <rect x="14" y="13" width="8" height="3" rx="1.5" />
          <path d="M15.5 19H14v1.5a1.5 1.5 0 101.5-1.5" />
          <rect x="2" y="8" width="8" height="3" rx="1.5" />
          <path d="M8.5 5H10V3.5A1.5 1.5 0 108.5 5" />
        </svg>
      ),
    },
    {
      name: 'Microsoft Teams',
      connected: !!integrations.teams?.incoming_webhook_url,
      detail: integrations.teams?.incoming_webhook_url ? 'Connected' : 'Not connected',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M23 21v-2a4 4 0 00-3-3.87" />
          <path d="M16 3.13a4 4 0 010 7.75" />
        </svg>
      ),
    },
    {
      name: 'Webhooks',
      connected: (integrations.webhooks || []).length > 0,
      detail: (integrations.webhooks || []).length > 0
        ? `${integrations.webhooks.length} registered`
        : 'None registered',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71" />
          <path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71" />
        </svg>
      ),
    },
  ]

  return (
    <div className="usage-integrations">
      {items.map((item) => (
        <div key={item.name} className="usage-integration-row">
          <div className="usage-integration-icon">{item.icon}</div>
          <div className="usage-integration-info">
            <span className="usage-integration-name">{item.name}</span>
            <span className="usage-integration-detail">{item.detail}</span>
          </div>
          <span className={`usage-integration-badge ${item.connected ? 'usage-badge-on' : 'usage-badge-off'}`}>
            {item.connected ? 'Connected' : 'Off'}
          </span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Plan Card
// ---------------------------------------------------------------------------

const PLAN_INFO = {
  starter: { label: 'Starter', price: '$299/mo', queries: '100', color: 'var(--blue-500)' },
  pro: { label: 'Pro', price: '$799/mo', queries: '1,000', color: 'var(--blue-700)' },
  enterprise: { label: 'Enterprise', price: '$2,499/mo', queries: 'Unlimited', color: 'var(--blue-900)' },
}

function PlanCard({ plan, subscriptionStatus, onUpgrade }) {
  const info = PLAN_INFO[plan] || { label: plan, price: '-', queries: '-', color: 'var(--blue-500)' }
  const canUpgrade = plan !== 'enterprise'

  return (
    <div className="usage-plan-card">
      <div className="usage-plan-header">
        <span className="usage-plan-tier" style={{ background: info.color }}>{info.label}</span>
        {subscriptionStatus && subscriptionStatus !== 'none' && (
          <span className={`usage-plan-status ${subscriptionStatus === 'active' ? 'usage-status-active' : 'usage-status-other'}`}>
            {subscriptionStatus}
          </span>
        )}
      </div>
      <div className="usage-plan-price">{info.price}</div>
      <ul className="usage-plan-features">
        <li>{info.queries} queries per month</li>
        <li>Full meeting archive access</li>
        <li>RAG-powered Q&A</li>
        {plan !== 'starter' && <li>Slack & Teams integrations</li>}
        {plan === 'enterprise' && <li>Dedicated support</li>}
        {plan === 'enterprise' && <li>Custom branding</li>}
      </ul>
      {canUpgrade && (
        <button className="usage-upgrade-btn" onClick={onUpgrade}>
          Upgrade Plan
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// API Key Management
// ---------------------------------------------------------------------------

function ApiKeySection() {
  const [masked, setMasked] = useState(true)
  const [copied, setCopied] = useState(false)
  const key = sessionStorage.getItem('usage_api_key') ||
    sessionStorage.getItem('analytics_api_key') ||
    sessionStorage.getItem('admin_api_key') || ''

  function maskKey(k) {
    if (!k || k.length < 12) return '****'
    return k.slice(0, 6) + '****' + k.slice(-4)
  }

  function handleCopy() {
    navigator.clipboard.writeText(key)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="usage-api-key-section">
      <div className="usage-api-key-header">
        <span className="usage-api-key-label">API Key</span>
        <div className="usage-api-key-actions">
          <button
            className="usage-key-btn"
            onClick={() => setMasked(!masked)}
            aria-label={masked ? 'Show API key' : 'Hide API key'}
          >
            {masked ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24" />
                <line x1="1" y1="1" x2="23" y2="23" />
              </svg>
            )}
          </button>
          <button className="usage-key-btn" onClick={handleCopy} aria-label="Copy API key">
            {copied ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
              </svg>
            )}
          </button>
        </div>
      </div>
      <code className="usage-api-key-value">{masked ? maskKey(key) : key}</code>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------

export default function UsageDashboard() {
  const { isAuthenticated, login, logout } = useUsageAuth()
  const {
    usage,
    billing,
    queriesOverTime,
    topQuestions,
    integrations,
    loading,
    error,
    refresh,
    openBillingPortal,
    exportCsv,
  } = useUsage()

  const [exporting, setExporting] = useState(false)
  const [portalLoading, setPortalLoading] = useState(false)

  if (!isAuthenticated) {
    return <UsageLogin onLogin={login} />
  }

  const plan = billing?.plan || 'starter'
  const monthlyLimit = billing?.monthly_limit ?? (plan === 'enterprise' ? null : 100)
  const queriesUsed = billing?.queries_used ?? usage?.total_queries ?? 0
  const subscriptionStatus = billing?.subscription_status || 'none'

  async function handleExport() {
    setExporting(true)
    try {
      await exportCsv('30d')
    } catch {
      // silently fail
    } finally {
      setExporting(false)
    }
  }

  async function handleUpgrade() {
    setPortalLoading(true)
    try {
      await openBillingPortal()
    } catch {
      // portal may not be available in dev mode
    } finally {
      setPortalLoading(false)
    }
  }

  return (
    <div className="container usage-container">
      <div className="usage-header">
        <div>
          <h2 className="usage-title">API Usage & Billing</h2>
          <p className="usage-subtitle">Monitor your usage, manage your plan, and track integrations</p>
        </div>
        <div className="usage-header-actions">
          <button className="usage-refresh-btn" onClick={refresh} disabled={loading} aria-label="Refresh data">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={loading ? 'usage-spin' : ''}>
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
            </svg>
          </button>
          <button className="usage-signout-btn" onClick={logout}>Sign Out</button>
        </div>
      </div>

      {error && (
        <div className="admin-error-banner" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Usage Meter */}
      <div className="usage-section">
        <UsageMeter
          used={queriesUsed}
          limit={monthlyLimit}
          label="Queries this month"
        />
      </div>

      {/* Stats Cards */}
      <div className="usage-stats-grid">
        <StatCard
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
          }
          label="Queries (30d)"
          value={loading ? '...' : (usage?.total_queries ?? 0).toLocaleString()}
        />
        <StatCard
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
          }
          label="Avg Response"
          value={loading ? '...' : `${usage?.avg_response_time_ms ?? 0} ms`}
        />
        <StatCard
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          }
          label="Meetings Processed"
          value={loading ? '...' : (usage?.meetings_processed ?? 0).toLocaleString()}
        />
        <StatCard
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
              <line x1="16" y1="2" x2="16" y2="6" />
              <line x1="8" y1="2" x2="8" y2="6" />
              <line x1="3" y1="10" x2="21" y2="10" />
            </svg>
          }
          label="Period"
          value="Last 30 days"
          sub={usage?.first_query ? `Since ${new Date(usage.first_query).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : null}
        />
      </div>

      {/* Two-column layout: chart + plan */}
      <div className="usage-two-col">
        <div className="usage-col-main">
          {/* Daily Usage Chart */}
          <div className="analytics-card">
            <h3 className="analytics-card-title">Daily Query Volume</h3>
            {loading ? (
              <div className="usage-chart-empty">Loading...</div>
            ) : (
              <DailyChart data={queriesOverTime} />
            )}
          </div>

          {/* Top Queries */}
          <div className="analytics-card">
            <h3 className="analytics-card-title">Top Queries</h3>
            <TopQueries questions={topQuestions} />
          </div>
        </div>

        <div className="usage-col-side">
          {/* Plan Card */}
          <PlanCard
            plan={plan}
            subscriptionStatus={subscriptionStatus}
            onUpgrade={handleUpgrade}
          />

          {/* Integrations */}
          <div className="analytics-card">
            <h3 className="analytics-card-title">Integrations</h3>
            <IntegrationStatus integrations={integrations} />
          </div>

          {/* API Key */}
          <div className="analytics-card">
            <h3 className="analytics-card-title">API Key</h3>
            <ApiKeySection />
          </div>
        </div>
      </div>

      {/* Export row */}
      <div className="usage-export-row">
        <button className="usage-export-btn" onClick={handleExport} disabled={exporting}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          {exporting ? 'Exporting...' : 'Export Usage CSV'}
        </button>
        {portalLoading && <span className="usage-portal-hint">Opening billing portal...</span>}
      </div>
    </div>
  )
}
