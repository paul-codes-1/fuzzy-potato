import { useState, useEffect, useCallback, useRef } from 'react'

const REFRESH_INTERVAL = 60_000 // 60 seconds

const STATUS_CONFIG = {
  operational: { label: 'Operational', color: '#22c55e', bg: '#ecfdf5' },
  degraded: { label: 'Degraded', color: '#f59e0b', bg: '#fffbeb' },
  partial_outage: { label: 'Partial Outage', color: '#f59e0b', bg: '#fffbeb' },
  major_outage: { label: 'Major Outage', color: '#ef4444', bg: '#fef2f2' },
  maintenance: { label: 'Maintenance', color: '#6366f1', bg: '#eef2ff' },
}

const SEVERITY_CONFIG = {
  minor: { color: '#f59e0b', bg: '#fffbeb' },
  major: { color: '#f97316', bg: '#fff7ed' },
  critical: { color: '#ef4444', bg: '#fef2f2' },
}

const INCIDENT_STATUS_CONFIG = {
  investigating: { label: 'Investigating', color: '#ef4444' },
  identified: { label: 'Identified', color: '#f97316' },
  monitoring: { label: 'Monitoring', color: '#3b82f6' },
  resolved: { label: 'Resolved', color: '#22c55e' },
}

const COMPONENT_LABELS = {
  API: { name: 'API Server', description: 'Core REST API and authentication' },
  Pipeline: { name: 'Transcription Pipeline', description: 'Audio transcription and summary generation' },
  'RAG Search': { name: 'RAG Engine', description: 'Vector search and AI-powered Q&A' },
  'Slack Integration': { name: 'Slack Integration', description: 'Slash commands and notifications' },
  Widget: { name: 'Embeddable Widget', description: 'Drop-in widget for government websites' },
}

function formatRelativeTime(isoString) {
  if (!isoString) return ''
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now - date
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDays = Math.floor(diffHr / 24)
  return `${diffDays}d ago`
}

function formatDateTime(isoString) {
  if (!isoString) return ''
  const d = new Date(isoString)
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// ---------------------------------------------------------------------------
// Overall status banner
// ---------------------------------------------------------------------------

function OverallBanner({ status, updatedAt }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.operational
  const isAllGood = status === 'operational'

  const bannerBg = isAllGood
    ? 'linear-gradient(135deg, #22c55e, #16a34a)'
    : status === 'major_outage'
      ? 'linear-gradient(135deg, #ef4444, #dc2626)'
      : 'linear-gradient(135deg, #f59e0b, #d97706)'

  return (
    <div
      style={{
        background: bannerBg,
        borderRadius: 'var(--radius-lg)',
        padding: '28px 32px',
        color: '#fff',
        marginBottom: '32px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.12)',
      }}
      role="status"
      aria-live="polite"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
        <span style={{ fontSize: '1.6rem' }} aria-hidden="true">
          {isAllGood ? '\u2713' : status === 'major_outage' ? '\u2717' : '\u26A0'}
        </span>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.35rem', fontWeight: 700 }}>
            {isAllGood
              ? 'All Systems Operational'
              : status === 'major_outage'
                ? 'Major Outage'
                : status === 'partial_outage'
                  ? 'Partial Outage'
                  : status === 'degraded'
                    ? 'Degraded Performance'
                    : 'Under Maintenance'}
          </h2>
          {updatedAt && (
            <p style={{ margin: '4px 0 0', fontSize: '0.85rem', opacity: 0.85 }}>
              Last checked {formatRelativeTime(updatedAt)}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single component row
// ---------------------------------------------------------------------------

function ComponentRow({ component, uptime }) {
  const config = STATUS_CONFIG[component.state] || STATUS_CONFIG.operational
  const labelInfo = COMPONENT_LABELS[component.name] || { name: component.name, description: '' }
  const uptimeVal = uptime?.['90d'] ?? 100

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '16px 20px',
        borderBottom: '1px solid var(--border-light)',
        gap: '12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: 1, minWidth: 0 }}>
        {/* Status dot */}
        <span
          style={{
            width: '10px',
            height: '10px',
            borderRadius: '50%',
            backgroundColor: config.color,
            flexShrink: 0,
            boxShadow: `0 0 6px ${config.color}40`,
          }}
          aria-hidden="true"
        />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: '0.95rem', color: 'var(--text-primary)' }}>
            {labelInfo.name}
          </div>
          {labelInfo.description && (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginTop: '2px' }}>
              {labelInfo.description}
            </div>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexShrink: 0 }}>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>
          {formatRelativeTime(component.updated_at)}
        </span>
        <span
          style={{
            fontSize: '0.82rem',
            fontWeight: 600,
            color: uptimeVal >= 99.5 ? '#22c55e' : uptimeVal >= 95 ? '#f59e0b' : '#ef4444',
            fontVariantNumeric: 'tabular-nums',
            minWidth: '52px',
            textAlign: 'right',
          }}
        >
          {uptimeVal.toFixed(2)}%
        </span>
        <span
          style={{
            display: 'inline-block',
            padding: '3px 10px',
            borderRadius: 'var(--radius-full)',
            fontSize: '0.75rem',
            fontWeight: 600,
            color: config.color,
            backgroundColor: config.bg,
            minWidth: '90px',
            textAlign: 'center',
          }}
        >
          {config.label}
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component list card
// ---------------------------------------------------------------------------

function ComponentList({ components, uptimes }) {
  return (
    <div
      style={{
        background: 'var(--surface)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
        overflow: 'hidden',
        marginBottom: '32px',
      }}
    >
      <div
        style={{
          padding: '16px 20px',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <h3 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: 'var(--text-primary)' }}>
          Components
        </h3>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>90-day uptime</span>
      </div>
      {components.map((comp) => (
        <ComponentRow key={comp.name} component={comp} uptime={uptimes[comp.name]} />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 90-day uptime chart (horizontal bar of daily status blocks)
// ---------------------------------------------------------------------------

function UptimeChart({ daily, componentName }) {
  if (!daily || daily.length === 0) return null

  const colorMap = {
    operational: '#22c55e',
    degraded: '#f59e0b',
    partial_outage: '#f59e0b',
    major_outage: '#ef4444',
    maintenance: '#6366f1',
  }

  return (
    <div style={{ marginBottom: '8px' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '6px',
        }}
      >
        <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>
          {COMPONENT_LABELS[componentName]?.name || componentName}
        </span>
      </div>
      <div
        style={{
          display: 'flex',
          gap: '1.5px',
          height: '28px',
          borderRadius: 'var(--radius-sm)',
          overflow: 'hidden',
        }}
        role="img"
        aria-label={`90-day uptime chart for ${componentName}`}
      >
        {daily.map((day, i) => (
          <div
            key={day.date}
            style={{
              flex: 1,
              backgroundColor: colorMap[day.status] || '#d1d5db',
              minWidth: '2px',
              cursor: 'default',
              transition: 'opacity 0.15s',
              borderRadius: i === 0 ? '3px 0 0 3px' : i === daily.length - 1 ? '0 3px 3px 0' : '0',
            }}
            title={`${day.date}: ${day.uptime_pct}% uptime`}
            onMouseEnter={(e) => { e.currentTarget.style.opacity = '0.7' }}
            onMouseLeave={(e) => { e.currentTarget.style.opacity = '1' }}
          />
        ))}
      </div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginTop: '4px',
          fontSize: '0.7rem',
          color: 'var(--text-tertiary)',
        }}
      >
        <span>{daily[0]?.date}</span>
        <span>Today</span>
      </div>
    </div>
  )
}

function UptimeSection({ daily }) {
  const componentNames = Object.keys(daily || {})
  if (componentNames.length === 0) return null

  return (
    <div
      style={{
        background: 'var(--surface)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
        padding: '20px 24px',
        marginBottom: '32px',
      }}
    >
      <h3 style={{ margin: '0 0 20px', fontSize: '1rem', fontWeight: 700, color: 'var(--text-primary)' }}>
        90-Day Uptime
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {componentNames.map((name) => (
          <UptimeChart key={name} daily={daily[name]} componentName={name} />
        ))}
      </div>
      {/* Legend */}
      <div
        style={{
          display: 'flex',
          gap: '16px',
          marginTop: '16px',
          paddingTop: '12px',
          borderTop: '1px solid var(--border-light)',
          flexWrap: 'wrap',
        }}
      >
        {[
          { label: 'Operational', color: '#22c55e' },
          { label: 'Degraded', color: '#f59e0b' },
          { label: 'Outage', color: '#ef4444' },
          { label: 'Maintenance', color: '#6366f1' },
        ].map((item) => (
          <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span
              style={{
                width: '12px',
                height: '12px',
                borderRadius: '2px',
                backgroundColor: item.color,
                flexShrink: 0,
              }}
            />
            <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Incident timeline
// ---------------------------------------------------------------------------

function IncidentCard({ incident }) {
  const sevConfig = SEVERITY_CONFIG[incident.severity] || SEVERITY_CONFIG.minor
  const statusConfig = INCIDENT_STATUS_CONFIG[incident.status] || INCIDENT_STATUS_CONFIG.investigating

  return (
    <div
      style={{
        background: 'var(--surface)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
        overflow: 'hidden',
        marginBottom: '12px',
      }}
    >
      <div style={{ padding: '16px 20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', marginBottom: '8px' }}>
          <h4 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700, color: 'var(--text-primary)' }}>
            {incident.title}
          </h4>
          <span
            style={{
              padding: '2px 8px',
              borderRadius: 'var(--radius-full)',
              fontSize: '0.7rem',
              fontWeight: 600,
              color: sevConfig.color,
              backgroundColor: sevConfig.bg,
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}
          >
            {incident.severity}
          </span>
          <span
            style={{
              padding: '2px 8px',
              borderRadius: 'var(--radius-full)',
              fontSize: '0.7rem',
              fontWeight: 600,
              color: statusConfig.color,
              backgroundColor: `${statusConfig.color}18`,
            }}
          >
            {statusConfig.label}
          </span>
        </div>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
          {formatDateTime(incident.created_at)}
          {incident.resolved_at && ` \u2014 Resolved ${formatDateTime(incident.resolved_at)}`}
        </div>
        {incident.components_affected?.length > 0 && (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '6px' }}>
            {incident.components_affected.map((comp) => (
              <span
                key={comp}
                style={{
                  fontSize: '0.72rem',
                  padding: '2px 8px',
                  borderRadius: 'var(--radius-full)',
                  backgroundColor: 'var(--bg-secondary)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-light)',
                }}
              >
                {COMPONENT_LABELS[comp]?.name || comp}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Timeline of updates */}
      {incident.updates?.length > 0 && (
        <div style={{ borderTop: '1px solid var(--border-light)', padding: '12px 20px' }}>
          {incident.updates.map((update, idx) => {
            const uConfig = INCIDENT_STATUS_CONFIG[update.status] || INCIDENT_STATUS_CONFIG.investigating
            return (
              <div
                key={update.id}
                style={{
                  display: 'flex',
                  gap: '12px',
                  paddingBottom: idx < incident.updates.length - 1 ? '10px' : 0,
                  position: 'relative',
                }}
              >
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: '12px', flexShrink: 0 }}>
                  <div
                    style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      backgroundColor: uConfig.color,
                      flexShrink: 0,
                      marginTop: '5px',
                    }}
                  />
                  {idx < incident.updates.length - 1 && (
                    <div style={{ width: '1px', flex: 1, backgroundColor: 'var(--border-light)', marginTop: '4px' }} />
                  )}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '0.8rem', fontWeight: 600, color: uConfig.color }}>
                    {uConfig.label}
                  </div>
                  <p style={{ margin: '2px 0 0', fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {update.message}
                  </p>
                  <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                    {formatDateTime(update.created_at)}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function IncidentsSection({ incidents }) {
  if (!incidents || incidents.length === 0) {
    return (
      <div
        style={{
          background: 'var(--surface)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--border)',
          boxShadow: 'var(--shadow-sm)',
          padding: '32px 20px',
          textAlign: 'center',
          marginBottom: '32px',
        }}
      >
        <p style={{ margin: 0, color: 'var(--text-tertiary)', fontSize: '0.9rem' }}>
          No incidents reported in the last 90 days.
        </p>
      </div>
    )
  }

  return (
    <div style={{ marginBottom: '32px' }}>
      <h3 style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px' }}>
        Recent Incidents
      </h3>
      {incidents.map((incident) => (
        <IncidentCard key={incident.id} incident={incident} />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Subscribe to updates
// ---------------------------------------------------------------------------

function SubscribeForm() {
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!email.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch('/api/v1/digest/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), frequency: 'instant', topics: ['status'] }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || 'Subscription failed')
      }
      setSubmitted(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (submitted) {
    return (
      <div
        style={{
          background: '#ecfdf5',
          borderRadius: 'var(--radius-md)',
          border: '1px solid #bbf7d0',
          padding: '20px 24px',
          textAlign: 'center',
        }}
      >
        <p style={{ margin: 0, color: '#16a34a', fontWeight: 600, fontSize: '0.95rem' }}>
          Subscribed. Check your email to confirm.
        </p>
      </div>
    )
  }

  return (
    <div
      style={{
        background: 'var(--surface)',
        borderRadius: 'var(--radius-md)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--shadow-sm)',
        padding: '24px',
      }}
    >
      <h3 style={{ margin: '0 0 6px', fontSize: '1rem', fontWeight: 700, color: 'var(--text-primary)' }}>
        Subscribe to Updates
      </h3>
      <p style={{ margin: '0 0 16px', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
        Get notified when components go down or incidents are resolved.
      </p>
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
        <label htmlFor="status-subscribe-email" className="sr-only">Email address</label>
        <input
          id="status-subscribe-email"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          style={{
            flex: '1 1 240px',
            padding: '10px 14px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border)',
            fontSize: '0.9rem',
            background: 'var(--bg-primary)',
            color: 'var(--text-primary)',
            outline: 'none',
          }}
        />
        <button
          type="submit"
          disabled={submitting || !email.trim()}
          style={{
            padding: '10px 24px',
            borderRadius: 'var(--radius-md)',
            border: 'none',
            backgroundColor: 'var(--blue-700)',
            color: '#fff',
            fontSize: '0.9rem',
            fontWeight: 600,
            cursor: submitting ? 'wait' : 'pointer',
            opacity: submitting ? 0.7 : 1,
            transition: 'opacity 0.15s',
          }}
        >
          {submitting ? 'Subscribing...' : 'Subscribe'}
        </button>
      </form>
      {error && (
        <p style={{ margin: '10px 0 0', fontSize: '0.82rem', color: '#ef4444' }} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main StatusPage
// ---------------------------------------------------------------------------

export default function StatusPage() {
  const [statusData, setStatusData] = useState(null)
  const [uptimeData, setUptimeData] = useState(null)
  const [incidents, setIncidents] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastRefresh, setLastRefresh] = useState(null)
  const intervalRef = useRef(null)

  const fetchData = useCallback(async () => {
    try {
      const [statusRes, uptimeRes, incidentsRes] = await Promise.all([
        fetch('/api/v1/status'),
        fetch('/api/v1/status/uptime'),
        fetch('/api/v1/status/history?days=90'),
      ])

      if (!statusRes.ok || !uptimeRes.ok || !incidentsRes.ok) {
        throw new Error('Failed to fetch status data')
      }

      const [status, uptime, inc] = await Promise.all([
        statusRes.json(),
        uptimeRes.json(),
        incidentsRes.json(),
      ])

      setStatusData(status)
      setUptimeData(uptime)
      setIncidents(inc.incidents || [])
      setLastRefresh(new Date().toISOString())
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    intervalRef.current = setInterval(fetchData, REFRESH_INTERVAL)
    return () => clearInterval(intervalRef.current)
  }, [fetchData])

  if (loading) {
    return (
      <div className="container" style={{ paddingTop: '48px', paddingBottom: '48px', textAlign: 'center' }}>
        <p style={{ color: 'var(--text-tertiary)', fontSize: '1rem' }}>Loading status...</p>
      </div>
    )
  }

  if (error && !statusData) {
    return (
      <div className="container" style={{ paddingTop: '48px', paddingBottom: '48px' }}>
        <div
          style={{
            background: '#fef2f2',
            borderRadius: 'var(--radius-md)',
            border: '1px solid #fecaca',
            padding: '24px',
            textAlign: 'center',
          }}
          role="alert"
        >
          <p style={{ margin: 0, color: '#ef4444', fontWeight: 600 }}>
            Unable to load status page: {error}
          </p>
          <button
            onClick={fetchData}
            style={{
              marginTop: '12px',
              padding: '8px 20px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid #fecaca',
              background: '#fff',
              color: '#ef4444',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="container" style={{ paddingTop: '32px', paddingBottom: '48px', maxWidth: '800px' }}>
      {/* Header */}
      <div style={{ marginBottom: '8px' }}>
        <h2
          style={{
            fontSize: '1.5rem',
            fontWeight: 700,
            color: 'var(--text-primary)',
            marginBottom: '4px',
          }}
        >
          System Status
        </h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', margin: 0 }}>
          Current operational status and incident history for CivicLens.
        </p>
      </div>

      {/* Auto-refresh indicator */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '24px' }}>
        <span
          style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            backgroundColor: '#22c55e',
            animation: 'pulse 2s infinite',
          }}
        />
        <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
          Auto-refreshes every 60s
          {lastRefresh && ` \u00b7 Last updated ${formatRelativeTime(lastRefresh)}`}
        </span>
      </div>

      {/* Overall banner */}
      <OverallBanner
        status={statusData?.status || 'operational'}
        updatedAt={statusData?.updated_at}
      />

      {/* Component list */}
      <ComponentList
        components={statusData?.components || []}
        uptimes={uptimeData?.uptime || {}}
      />

      {/* 90-day uptime chart */}
      <UptimeSection daily={uptimeData?.daily || {}} />

      {/* Active incidents from status summary */}
      {statusData?.active_incidents?.length > 0 && (
        <div style={{ marginBottom: '32px' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 700, color: '#ef4444', marginBottom: '12px' }}>
            Active Incidents
          </h3>
          {statusData.active_incidents.map((incident) => (
            <IncidentCard key={incident.id} incident={incident} />
          ))}
        </div>
      )}

      {/* Scheduled maintenance */}
      {statusData?.scheduled_maintenance?.length > 0 && (
        <div style={{ marginBottom: '32px' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 700, color: '#6366f1', marginBottom: '12px' }}>
            Scheduled Maintenance
          </h3>
          {statusData.scheduled_maintenance.map((maint) => (
            <div
              key={maint.id}
              style={{
                background: '#eef2ff',
                borderRadius: 'var(--radius-md)',
                border: '1px solid #c7d2fe',
                padding: '16px 20px',
                marginBottom: '10px',
              }}
            >
              <div style={{ fontWeight: 600, fontSize: '0.95rem', color: '#4338ca' }}>{maint.title}</div>
              <div style={{ fontSize: '0.8rem', color: '#6366f1', marginTop: '4px' }}>
                {formatDateTime(maint.scheduled_start)} &ndash; {formatDateTime(maint.scheduled_end)}
              </div>
              {maint.components?.length > 0 && (
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '8px' }}>
                  {maint.components.map((comp) => (
                    <span
                      key={comp}
                      style={{
                        fontSize: '0.72rem',
                        padding: '2px 8px',
                        borderRadius: 'var(--radius-full)',
                        backgroundColor: '#ddd6fe',
                        color: '#5b21b6',
                      }}
                    >
                      {COMPONENT_LABELS[comp]?.name || comp}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Incident history */}
      <IncidentsSection incidents={incidents} />

      {/* Subscribe */}
      <SubscribeForm />

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  )
}
