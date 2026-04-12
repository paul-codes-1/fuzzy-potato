import { useState, useEffect, useCallback, useRef } from 'react'

const USAGE_KEY_STORAGE = 'usage_api_key'
const REFRESH_INTERVAL = 60_000 // 60 seconds

function getApiKey() {
  return (
    sessionStorage.getItem(USAGE_KEY_STORAGE) ||
    sessionStorage.getItem('analytics_api_key') ||
    sessionStorage.getItem('admin_api_key') ||
    ''
  )
}

function setApiKey(key) {
  sessionStorage.setItem(USAGE_KEY_STORAGE, key)
}

function clearApiKey() {
  sessionStorage.removeItem(USAGE_KEY_STORAGE)
}

async function apiFetch(path, key) {
  const apiKey = key || getApiKey()
  const res = await fetch(path, {
    headers: { 'X-API-Key': apiKey },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

async function apiPost(path, body, key) {
  const apiKey = key || getApiKey()
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'X-API-Key': apiKey,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

export function useUsageAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => !!getApiKey())

  const login = useCallback((key) => {
    setApiKey(key)
    setIsAuthenticated(true)
  }, [])

  const logout = useCallback(() => {
    clearApiKey()
    setIsAuthenticated(false)
  }, [])

  return { isAuthenticated, login, logout, getApiKey }
}

export function useUsage() {
  const [usage, setUsage] = useState(null)
  const [billing, setBilling] = useState(null)
  const [queriesOverTime, setQueriesOverTime] = useState([])
  const [topQuestions, setTopQuestions] = useState([])
  const [integrations, setIntegrations] = useState({
    slack: null,
    teams: null,
    webhooks: [],
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const intervalRef = useRef(null)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [usageData, billingData, queriesData, topicsData] = await Promise.all([
        apiFetch('/api/v1/analytics/usage?period=30d'),
        apiFetch('/api/v1/billing/usage').catch(() => null),
        apiFetch('/api/v1/analytics/queries?period=30d'),
        apiFetch('/api/v1/analytics/popular-topics?period=30d&limit=10'),
      ])

      setUsage(usageData)
      setBilling(billingData)
      setQueriesOverTime(queriesData.data || [])
      setTopQuestions(topicsData.top_questions || [])

      // Fetch integration statuses in parallel (non-critical, don't fail on error)
      const [slackConfig, teamsConfig, webhookList] = await Promise.all([
        apiFetch('/api/v1/integrations/slack/config').catch(() => null),
        apiFetch('/api/v1/integrations/teams/config').catch(() => null),
        apiFetch('/api/v1/webhooks').catch(() => ({ webhooks: [] })),
      ])

      setIntegrations({
        slack: slackConfig,
        teams: teamsConfig,
        webhooks: webhookList?.webhooks || [],
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  // Auto-refresh every 60s
  useEffect(() => {
    if (getApiKey()) {
      fetchAll()
      intervalRef.current = setInterval(fetchAll, REFRESH_INTERVAL)
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [fetchAll])

  const refresh = useCallback(() => {
    return fetchAll()
  }, [fetchAll])

  const openBillingPortal = useCallback(async () => {
    const returnUrl = window.location.href
    const data = await apiPost('/api/v1/billing/portal', { return_url: returnUrl })
    if (data.portal_url) {
      window.open(data.portal_url, '_blank')
    }
    return data
  }, [])

  const exportCsv = useCallback(async (period = '30d') => {
    const key = getApiKey()
    const res = await fetch(`/api/v1/analytics/export/queries?period=${period}`, {
      headers: { 'X-API-Key': key },
    })
    if (!res.ok) throw new Error('Export failed')
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `usage_export_${period}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }, [])

  return {
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
  }
}
