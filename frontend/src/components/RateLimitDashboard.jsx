import { useState, useEffect, useCallback } from 'react'
import { useI18n } from '../i18n/I18nProvider'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PLAN_LIMITS = {
  starter: 100,
  pro: 1000,
  enterprise: null, // unlimited
}

const PLAN_LABELS = {
  starter: 'Starter',
  pro: 'Pro',
  enterprise: 'Enterprise',
}

function getApiKey() {
  return (
    sessionStorage.getItem('usage_api_key') ||
    sessionStorage.getItem('analytics_api_key') ||
    sessionStorage.getItem('admin_api_key') ||
    ''
  )
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
// Color helpers
// ---------------------------------------------------------------------------

function usageColor(pct) {
  if (pct >= 85) return { bg: '#fef2f2', fill: '#dc2626', text: '#991b1b' }
  if (pct >= 60) return { bg: '#fefce8', fill: '#d97706', text: '#92400e' }
  return { bg: '#f0fdf4', fill: '#16a34a', text: '#166534' }
}

function usageColorClass(pct) {
  if (pct >= 85) return 'usage-meter-critical'
  if (pct >= 60) return 'usage-meter-warning'
  return 'usage-meter-ok'
}

// ---------------------------------------------------------------------------
// API Key Login Prompt
// ---------------------------------------------------------------------------

function ApiKeyPrompt({ onReady }) {
  const { t } = useI18n()
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [checking, setChecking] = useState(false)

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
      sessionStorage.setItem('usage_api_key', key.trim())
      const res = await fetch('/api/v1/analytics/summary', {
        headers: { 'X-API-Key': key.trim() },
      })
      if (!res.ok) {
        sessionStorage.removeItem('usage_api_key')
        setError(t('rate_limit.invalid_key', 'Invalid API key'))
        return
      }
      onReady()
    } catch {
      sessionStorage.removeItem('usage_api_key')
      setError(t('rate_limit.connection_error', 'Could not connect to API'))
    } finally {
      setChecking(false)
    }
  }

  if (getApiKey()) return null

  return (
    <div className="admin-login-wrapper">
      <div className="admin-login-card" role="region" aria-label={t('rate_limit.auth_label', 'Rate limit dashboard authentication')}>
        <div className="admin-login-icon" aria-hidden="true">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 20V10" />
            <path d="M18 20V4" />
            <path d="M6 20v-4" />
          </svg>
        </div>
        <h2>{t('rate_limit.title', 'Rate Limits')}</h2>
        <p className="admin-login-hint" id="rate-limit-login-desc">
          {t('rate_limit.login_hint', 'Enter your API key to view usage and rate limits')}
        </p>
        <form onSubmit={handleSubmit} aria-describedby="rate-limit-login-desc">
          <label htmlFor="rate-limit-api-key-input" className="sr-only">
            {t('rate_limit.api_key', 'API key')}
          </label>
          <input
            id="rate-limit-api-key-input"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={t('rate_limit.api_key', 'API key')}
            className="admin-input"
            autoFocus
            aria-describedby={error ? 'rate-limit-login-error' : undefined}
          />
          {error && (
            <p className="admin-error" id="rate-limit-login-error" role="alert">
              {error}
            </p>
          )}
          <button
            type="submit"
            className="admin-btn admin-btn-primary admin-btn-full"
            disabled={checking || !key.trim()}
          >
            {checking
              ? t('rate_limit.verifying', 'Verifying...')
              : t('rate_limit.view_dashboard', 'View Rate Limits')}
          </button>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Progress Bar
// ---------------------------------------------------------------------------

function RateLimitMeter({ used, limit, label }) {
  const { t, formatNumber } = useI18n()
  const isUnlimited = !limit || limit <= 0

  if (isUnlimited) {
    return (
      <div
        className="usage-meter"
        role="meter"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-label={label}
      >
        <div className="usage-meter-header">
          <span className="usage-meter-label">{label}</span>
          <span className="usage-meter-count">
            {formatNumber ? formatNumber(used || 0) : (used || 0).toLocaleString()} / {t('rate_limit.unlimited', 'Unlimited')}
          </span>
        </div>
        <div className="usage-meter-track">
          <div className="usage-meter-fill usage-meter-ok" style={{ width: '8%' }} />
        </div>
        <div className="usage-meter-footer">
          <span className="usage-meter-pct" style={{ color: 'var(--text-tertiary)' }}>
            {t('rate_limit.no_limit', 'No rate limit applied')}
          </span>
        </div>
      </div>
    )
  }

  const pct = Math.min((used / limit) * 100, 100)
  const colorClass = usageColorClass(pct)
  const remaining = Math.max(limit - used, 0)

  return (
    <div
      className="usage-meter"
      role="meter"
      aria-valuenow={used}
      aria-valuemin={0}
      aria-valuemax={limit}
      aria-label={label}
    >
      <div className="usage-meter-header">
        <span className="usage-meter-label">{label}</span>
        <span className="usage-meter-count">
          {used.toLocaleString()} / {limit.toLocaleString()}
        </span>
      </div>
      <div className="usage-meter-track">
        <div
          className={`usage-meter-fill ${colorClass}`}
          style={{ width: `${Math.max(pct, 1)}%` }}
        />
      </div>
      <div className="usage-meter-footer">
        <span className="usage-meter-pct">{pct.toFixed(1)}% {t('rate_limit.used', 'used')}</span>
        <span className="usage-meter-remaining">
          {remaining.toLocaleString()} {t('rate_limit.remaining', 'remaining')}
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Breakdown Card (daily / weekly / monthly)
// ---------------------------------------------------------------------------

function BreakdownCard({ title, count, limit, periodLabel }) {
  const { t } = useI18n()
  const isUnlimited = !limit || limit <= 0
  const pct = isUnlimited ? 0 : Math.min((count / limit) * 100, 100)
  const colors = usageColor(pct)

  return (
    <div className="analytics-stat-card" style={{ position: 'relative' }}>
      <div className="analytics-stat-value">{(count || 0).toLocaleString()}</div>
      <div className="analytics-stat-label">{title}</div>
      {!isUnlimited && (
        <div
          style={{
            marginTop: 8,
            height: 4,
            borderRadius: 2,
            background: 'var(--border-light)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              height: '100%',
              width: `${Math.max(pct, 1)}%`,
              borderRadius: 2,
              background: colors.fill,
              transition: 'width 0.3s ease',
            }}
          />
        </div>
      )}
      <div className="analytics-stat-sub" style={{ color: isUnlimited ? 'var(--text-tertiary)' : colors.text }}>
        {isUnlimited
          ? t('rate_limit.unlimited', 'Unlimited')
          : `${pct.toFixed(0)}% ${t('rate_limit.of_limit', 'of')} ${periodLabel}`}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Days Until Limit Estimate
// ---------------------------------------------------------------------------

function EstimatedDaysRemaining({ used, limit, daysElapsed }) {
  const { t } = useI18n()

  if (!limit || limit <= 0) {
    return (
      <div className="analytics-card" style={{ textAlign: 'center', padding: '20px 16px' }}>
        <div style={{ fontSize: 14, color: 'var(--text-tertiary)' }}>
          {t('rate_limit.unlimited_plan', 'Your enterprise plan has no query limit.')}
        </div>
      </div>
    )
  }

  const remaining = Math.max(limit - used, 0)

  if (used <= 0 || daysElapsed <= 0) {
    return (
      <div className="analytics-card" style={{ textAlign: 'center', padding: '20px 16px' }}>
        <div style={{ fontSize: 14, color: 'var(--text-tertiary)' }}>
          {t('rate_limit.no_usage_yet', 'Not enough usage data to estimate pace.')}
        </div>
      </div>
    )
  }

  const dailyRate = used / daysElapsed
  const daysLeft = dailyRate > 0 ? Math.floor(remaining / dailyRate) : Infinity
  const daysInMonth = 30
  const daysRemainingInMonth = Math.max(daysInMonth - daysElapsed, 0)

  const willExceed = daysLeft <= daysRemainingInMonth
  const pct = Math.min((used / limit) * 100, 100)
  const colors = usageColor(pct)

  return (
    <div className="analytics-card" style={{ padding: '20px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div
          style={{
            width: 48,
            height: 48,
            borderRadius: '50%',
            background: colors.bg,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={colors.fill} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
        </div>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, color: colors.text }}>
            {daysLeft === Infinity
              ? '--'
              : daysLeft === 0
                ? t('rate_limit.limit_reached', 'Limit reached')
                : `${daysLeft} ${t('rate_limit.days', 'days')}`}
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>
            {willExceed
              ? t('rate_limit.will_exceed', 'At current pace, you will hit your limit before the month ends')
              : t('rate_limit.estimated_remaining', 'Estimated days until limit reached at current pace')}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>
            {t('rate_limit.daily_avg', 'Daily average')}: {dailyRate.toFixed(1)} {t('rate_limit.queries_per_day', 'queries/day')}
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Upgrade CTA
// ---------------------------------------------------------------------------

function UpgradeBanner({ plan, pct, onUpgrade }) {
  const { t } = useI18n()

  if (plan === 'enterprise' || pct < 70) return null

  const nextPlan = plan === 'starter' ? 'pro' : 'enterprise'
  const nextLabel = PLAN_LABELS[nextPlan]
  const nextLimit = PLAN_LIMITS[nextPlan]

  return (
    <div
      style={{
        background: pct >= 85
          ? 'linear-gradient(135deg, #fef2f2 0%, #fff1f2 100%)'
          : 'linear-gradient(135deg, #fefce8 0%, #fef9c3 100%)',
        border: `1px solid ${pct >= 85 ? '#fecaca' : '#fde68a'}`,
        borderRadius: 8,
        padding: '16px 20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 16,
        marginBottom: 20,
      }}
      role="region"
      aria-label={t('rate_limit.upgrade_banner', 'Plan upgrade suggestion')}
    >
      <div>
        <div style={{ fontWeight: 600, fontSize: 15, color: pct >= 85 ? '#991b1b' : '#92400e' }}>
          {pct >= 85
            ? t('rate_limit.approaching_limit', 'You are approaching your query limit')
            : t('rate_limit.usage_growing', 'Your usage is growing')}
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
          {t('rate_limit.upgrade_suggestion', 'Upgrade to {{plan}} for {{limit}} queries/month', {
            plan: nextLabel,
            limit: nextLimit ? nextLimit.toLocaleString() : t('rate_limit.unlimited', 'unlimited'),
          })}
        </div>
      </div>
      <button
        onClick={onUpgrade}
        style={{
          background: pct >= 85 ? '#dc2626' : '#d97706',
          color: '#fff',
          border: 'none',
          borderRadius: 6,
          padding: '8px 20px',
          fontWeight: 600,
          fontSize: 14,
          cursor: 'pointer',
          whiteSpace: 'nowrap',
          flexShrink: 0,
        }}
      >
        {t('rate_limit.upgrade_now', 'Upgrade Now')}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function RateLimitDashboard() {
  const { t } = useI18n()
  const [ready, setReady] = useState(!!getApiKey())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const summary = await apiFetch('/api/v1/analytics/summary')
      setData(summary)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (ready) {
      loadData()
    }
  }, [ready, loadData])

  if (!ready) {
    return <ApiKeyPrompt onReady={() => setReady(true)} />
  }

  // Derive plan and usage from summary data
  const plan = data?.plan || 'starter'
  const limit = PLAN_LIMITS[plan] ?? (data?.monthly_limit || null)
  const monthlyUsed = data?.queries_this_month ?? data?.total_queries ?? 0
  const weeklyUsed = data?.queries_this_week ?? Math.round(monthlyUsed / 4)
  const dailyUsed = data?.queries_today ?? Math.round(monthlyUsed / 30)
  const pct = limit ? Math.min((monthlyUsed / limit) * 100, 100) : 0

  // Estimate how many days into the billing period we are
  const now = new Date()
  const dayOfMonth = now.getDate()

  async function handleUpgrade() {
    try {
      const key = getApiKey()
      const res = await fetch('/api/v1/billing/portal', {
        method: 'POST',
        headers: { 'X-API-Key': key, 'Content-Type': 'application/json' },
      })
      if (res.ok) {
        const result = await res.json()
        if (result.url) {
          window.location.href = result.url
        }
      }
    } catch {
      // billing portal may not be available in dev mode
    }
  }

  return (
    <div className="container" style={{ paddingTop: 24, paddingBottom: 40 }}>
      {/* Header */}
      <div className="analytics-header">
        <div>
          <h2 className="analytics-title">
            {t('rate_limit.dashboard_title', 'Rate Limits & Usage')}
          </h2>
          <p style={{ fontSize: 14, color: 'var(--text-secondary)', margin: '4px 0 0' }}>
            {t('rate_limit.dashboard_subtitle', 'Monitor your API usage against plan limits')}
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span
            style={{
              display: 'inline-block',
              padding: '4px 12px',
              borderRadius: 12,
              fontSize: 12,
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
              background: 'var(--blue-50, #eff6ff)',
              color: 'var(--blue-700, #1d4ed8)',
            }}
          >
            {PLAN_LABELS[plan] || plan}
          </span>
          <button
            className="usage-refresh-btn"
            onClick={loadData}
            disabled={loading}
            aria-label={t('rate_limit.refresh', 'Refresh data')}
            style={{ marginLeft: 4 }}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={loading ? 'usage-spin' : ''}
            >
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15" />
            </svg>
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="admin-error-banner" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      {/* Upgrade CTA */}
      {!loading && data && (
        <UpgradeBanner plan={plan} pct={pct} onUpgrade={handleUpgrade} />
      )}

      {/* Main Usage Meter */}
      <div style={{ marginBottom: 24 }}>
        <RateLimitMeter
          used={monthlyUsed}
          limit={limit}
          label={t('rate_limit.queries_this_month', 'Queries this month')}
        />
      </div>

      {/* Breakdown Cards */}
      <div className="analytics-stats-grid">
        <BreakdownCard
          title={t('rate_limit.today', 'Today')}
          count={loading ? 0 : dailyUsed}
          limit={limit ? Math.round(limit / 30) : null}
          periodLabel={t('rate_limit.daily_limit', 'daily avg')}
        />
        <BreakdownCard
          title={t('rate_limit.this_week', 'This Week')}
          count={loading ? 0 : weeklyUsed}
          limit={limit ? Math.round(limit / 4) : null}
          periodLabel={t('rate_limit.weekly_limit', 'weekly avg')}
        />
        <BreakdownCard
          title={t('rate_limit.this_month', 'This Month')}
          count={loading ? 0 : monthlyUsed}
          limit={limit}
          periodLabel={t('rate_limit.monthly_limit', 'monthly limit')}
        />
        <BreakdownCard
          title={t('rate_limit.plan_limit', 'Plan Limit')}
          count={limit || 0}
          limit={null}
          periodLabel=""
        />
      </div>

      {/* Days Remaining Estimate */}
      <div style={{ marginTop: 20 }}>
        <h3 className="analytics-card-title" style={{ marginBottom: 12 }}>
          {t('rate_limit.forecast', 'Usage Forecast')}
        </h3>
        {loading ? (
          <div className="analytics-card" style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)' }}>
            {t('rate_limit.loading', 'Loading...')}
          </div>
        ) : (
          <EstimatedDaysRemaining
            used={monthlyUsed}
            limit={limit}
            daysElapsed={dayOfMonth}
          />
        )}
      </div>

      {/* Plan comparison hint */}
      {plan !== 'enterprise' && !loading && (
        <div
          className="analytics-card"
          style={{
            marginTop: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
          }}
        >
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>
              {t('rate_limit.need_more', 'Need more capacity?')}
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>
              {plan === 'starter'
                ? t('rate_limit.starter_hint', 'Pro plan includes 1,000 queries/month for $799/mo')
                : t('rate_limit.pro_hint', 'Enterprise plan includes unlimited queries for $2,499/mo')}
            </div>
          </div>
          <button
            className="admin-btn admin-btn-primary"
            onClick={handleUpgrade}
            style={{ whiteSpace: 'nowrap', flexShrink: 0 }}
          >
            {t('rate_limit.compare_plans', 'Compare Plans')}
          </button>
        </div>
      )}
    </div>
  )
}
