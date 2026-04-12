import { useState, useCallback } from 'react'

const API_BASE = '/api/v1/admin'

function getAdminKey() {
  return sessionStorage.getItem('admin_api_key') || ''
}

export function useAdminAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(
    () => !!sessionStorage.getItem('admin_api_key')
  )

  const login = useCallback((key) => {
    sessionStorage.setItem('admin_api_key', key)
    setIsAuthenticated(true)
  }, [])

  const logout = useCallback(() => {
    sessionStorage.removeItem('admin_api_key')
    setIsAuthenticated(false)
  }, [])

  return { isAuthenticated, login, logout }
}

async function adminFetch(path, options = {}) {
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
    throw new Error(body.detail || body.error || `Request failed (${res.status})`)
  }

  if (res.status === 204) return null
  return res.json()
}

export function useAdmin() {
  const [tenants, setTenants] = useState([])
  const [plans, setPlans] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const clearError = useCallback(() => setError(null), [])

  const fetchTenants = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await adminFetch('/tenants')
      setTenants(data.tenants || data)
      return data
    } catch (err) {
      setError(err.message)
      throw err
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchPlans = useCallback(async () => {
    try {
      const data = await adminFetch('/plans')
      setPlans(data.plans || data)
      return data
    } catch (err) {
      setError(err.message)
    }
  }, [])

  const createTenant = useCallback(async ({ name, granicus_host, granicus_view_id, plan }) => {
    setError(null)
    try {
      const data = await adminFetch('/tenants', {
        method: 'POST',
        body: JSON.stringify({ name, granicus_host, granicus_view_id, plan }),
      })
      await fetchTenants()
      return data
    } catch (err) {
      setError(err.message)
      throw err
    }
  }, [fetchTenants])

  const deleteTenant = useCallback(async (id) => {
    setError(null)
    try {
      await adminFetch(`/tenants/${id}`, { method: 'DELETE' })
      setTenants((prev) => prev.filter((t) => t.id !== id))
    } catch (err) {
      setError(err.message)
      throw err
    }
  }, [])

  const rotateKey = useCallback(async (id) => {
    setError(null)
    try {
      const data = await adminFetch(`/tenants/${id}/rotate-key`, { method: 'POST' })
      return data
    } catch (err) {
      setError(err.message)
      throw err
    }
  }, [])

  const updatePlan = useCallback(async (id, plan) => {
    setError(null)
    try {
      const data = await adminFetch(`/tenants/${id}/plan`, {
        method: 'PATCH',
        body: JSON.stringify({ plan }),
      })
      await fetchTenants()
      return data
    } catch (err) {
      setError(err.message)
      throw err
    }
  }, [fetchTenants])

  return {
    tenants,
    plans,
    loading,
    error,
    clearError,
    fetchTenants,
    fetchPlans,
    createTenant,
    deleteTenant,
    rotateKey,
    updatePlan,
  }
}
