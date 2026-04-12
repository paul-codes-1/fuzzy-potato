import { useState, useCallback } from 'react'

const API_BASE = '/api/v1/admin/leads'

function getAdminKey() {
  return sessionStorage.getItem('admin_api_key') || ''
}

async function leadsFetch(path = '', options = {}) {
  const key = getAdminKey()
  if (!key) throw new Error('Not authenticated')

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': key,
      ...options.headers,
    },
  })

  if (res.status === 401 || res.status === 403) {
    sessionStorage.removeItem('admin_api_key')
    throw new Error('Invalid or expired admin key')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  if (res.status === 204) return null
  return res.json()
}

export function useLeads() {
  const [leads, setLeads] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const clearError = useCallback(() => setError(null), [])

  const fetchLeads = useCallback(async ({ status = '', intent = '', search = '' } = {}) => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (status) params.set('status', status)
      if (intent) params.set('intent', intent)
      if (search) params.set('search', search)
      const qs = params.toString()
      const data = await leadsFetch(qs ? `?${qs}` : '')
      setLeads(data.leads || [])
      return data.leads || []
    } catch (err) {
      setError(err.message)
      throw err
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchStats = useCallback(async () => {
    try {
      const data = await leadsFetch('/stats')
      setStats(data)
      return data
    } catch (err) {
      setError(err.message)
    }
  }, [])

  const fetchLead = useCallback(async (publicId) => {
    return leadsFetch(`/${publicId}`)
  }, [])

  const updateStatus = useCallback(async (publicId, status, assignedTo) => {
    setError(null)
    try {
      const body = { status }
      if (assignedTo !== undefined) body.assigned_to = assignedTo
      const data = await leadsFetch(`/${publicId}/status`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
      setLeads((prev) => prev.map((l) => (l.public_id === publicId ? { ...l, ...data } : l)))
      return data
    } catch (err) {
      setError(err.message)
      throw err
    }
  }, [])

  const addNote = useCallback(async (publicId, note, author) => {
    setError(null)
    try {
      const data = await leadsFetch(`/${publicId}/notes`, {
        method: 'POST',
        body: JSON.stringify({ note, author }),
      })
      setLeads((prev) => prev.map((l) => (l.public_id === publicId ? { ...l, ...data } : l)))
      return data
    } catch (err) {
      setError(err.message)
      throw err
    }
  }, [])

  return {
    leads,
    stats,
    loading,
    error,
    clearError,
    fetchLeads,
    fetchStats,
    fetchLead,
    updateStatus,
    addNote,
  }
}
