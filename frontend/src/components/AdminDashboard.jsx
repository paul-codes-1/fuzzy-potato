import { useState, useEffect, useRef, useCallback } from 'react'
import { useAdmin, useAdminAuth } from '../hooks/useAdmin'
import WhiteLabelPreview from './WhiteLabelPreview'

function AdminLogin({ onLogin }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [checking, setChecking] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!key.trim()) return
    setChecking(true)
    setError(null)

    // Validate the key by attempting to fetch tenants
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
      <div className="admin-login-card" role="region" aria-label="Admin authentication">
        <div className="admin-login-icon" aria-hidden="true">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0110 0v4" />
          </svg>
        </div>
        <h2>Admin Access</h2>
        <p className="admin-login-hint" id="admin-login-desc">Enter your admin API key to continue</p>
        <form onSubmit={handleSubmit} aria-describedby="admin-login-desc">
          <label htmlFor="admin-api-key-input" className="sr-only">Admin API key</label>
          <input
            id="admin-api-key-input"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Admin API key"
            className="admin-input"
            autoFocus
            aria-describedby={error ? 'admin-login-error' : undefined}
          />
          {error && <p className="admin-error" id="admin-login-error" role="alert">{error}</p>}
          <button type="submit" className="admin-btn admin-btn-primary admin-btn-full" disabled={checking || !key.trim()}>
            {checking ? 'Verifying...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  )
}

function CreateTenantForm({ plans, onSubmit, onCancel }) {
  const [form, setForm] = useState({
    name: '',
    granicus_host: '',
    granicus_view_id: '',
    plan: plans[0]?.name || 'free',
  })
  const [submitting, setSubmitting] = useState(false)
  const modalRef = useRef(null)

  function update(field) {
    return (e) => setForm((f) => ({ ...f, [field]: e.target.value }))
  }

  // Focus trap: keep focus inside modal
  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape') {
      onCancel()
      return
    }
    if (e.key !== 'Tab') return
    const modal = modalRef.current
    if (!modal) return
    const focusable = modal.querySelectorAll('input, select, button, [tabindex]:not([tabindex="-1"])')
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }, [onCancel])

  // Auto-focus first input and trap focus
  useEffect(() => {
    const modal = modalRef.current
    if (modal) {
      const firstInput = modal.querySelector('input')
      if (firstInput) firstInput.focus()
    }
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    try {
      const result = await onSubmit({
        ...form,
        granicus_view_id: form.granicus_view_id ? parseInt(form.granicus_view_id, 10) : undefined,
      })
      return result
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="admin-form-backdrop" onClick={onCancel} role="presentation">
      <div
        className="admin-form-modal"
        onClick={(e) => e.stopPropagation()}
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-label="Create tenant"
        onKeyDown={handleKeyDown}
      >
        <h3>Create Tenant</h3>
        <form onSubmit={handleSubmit}>
          <label className="admin-label">
            Tenant Name
            <input className="admin-input" value={form.name} onChange={update('name')} placeholder="City of Elk Grove" required />
          </label>
          <label className="admin-label">
            Granicus Host
            <input className="admin-input" value={form.granicus_host} onChange={update('granicus_host')} placeholder="elkgrovecity.granicus.com" required />
          </label>
          <label className="admin-label">
            Granicus View ID
            <input className="admin-input" type="number" value={form.granicus_view_id} onChange={update('granicus_view_id')} placeholder="Optional" />
          </label>
          <label className="admin-label">
            Plan
            <select className="admin-input" value={form.plan} onChange={update('plan')}>
              {plans.map((p) => (
                <option key={p.name} value={p.name}>{p.name} &mdash; {p.queries_per_month} queries/mo</option>
              ))}
            </select>
          </label>
          <div className="admin-form-actions">
            <button type="button" className="admin-btn admin-btn-ghost" onClick={onCancel}>Cancel</button>
            <button type="submit" className="admin-btn admin-btn-primary" disabled={submitting || !form.name || !form.granicus_host}>
              {submitting ? 'Creating...' : 'Create Tenant'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function TenantCard({ tenant, plans, onDelete, onRotateKey, onUpdatePlan }) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [newKey, setNewKey] = useState(null)
  const [copied, setCopied] = useState(false)
  const [changingPlan, setChangingPlan] = useState(false)
  const [selectedPlan, setSelectedPlan] = useState(tenant.plan)
  const [busy, setBusy] = useState(false)

  async function handleRotate() {
    setBusy(true)
    try {
      const result = await onRotateKey(tenant.id)
      setNewKey(result.api_key || result.key)
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    setBusy(true)
    try {
      await onDelete(tenant.id)
    } finally {
      setBusy(false)
      setConfirmDelete(false)
    }
  }

  async function handlePlanChange() {
    if (selectedPlan === tenant.plan) {
      setChangingPlan(false)
      return
    }
    setBusy(true)
    try {
      await onUpdatePlan(tenant.id, selectedPlan)
      setChangingPlan(false)
    } finally {
      setBusy(false)
    }
  }

  function copyKey() {
    navigator.clipboard.writeText(newKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const usage = tenant.usage || {}
  const queriesUsed = usage.queries_used ?? 0
  const queriesLimit = usage.queries_limit ?? 0
  const usagePercent = queriesLimit > 0 ? Math.min((queriesUsed / queriesLimit) * 100, 100) : 0

  return (
    <div className="admin-tenant-card">
      <div className="admin-tenant-header">
        <div>
          <h3 className="admin-tenant-name">{tenant.name}</h3>
          <span className="admin-tenant-id">ID: {tenant.id}</span>
        </div>
        <span className={`admin-plan-badge admin-plan-${tenant.plan}`}>{tenant.plan}</span>
      </div>

      <div className="admin-tenant-details">
        <div className="admin-detail-row">
          <span className="admin-detail-label">Granicus Host</span>
          <span className="admin-detail-value">{tenant.granicus_host}</span>
        </div>
        {tenant.granicus_view_id && (
          <div className="admin-detail-row">
            <span className="admin-detail-label">View ID</span>
            <span className="admin-detail-value">{tenant.granicus_view_id}</span>
          </div>
        )}
        <div className="admin-detail-row">
          <span className="admin-detail-label">Created</span>
          <span className="admin-detail-value">{new Date(tenant.created_at).toLocaleDateString()}</span>
        </div>
      </div>

      {/* Usage stats */}
      {queriesLimit > 0 && (
        <div className="admin-usage">
          <div className="admin-usage-header">
            <span className="admin-detail-label">Queries</span>
            <span className="admin-detail-value">{queriesUsed.toLocaleString()} / {queriesLimit.toLocaleString()}</span>
          </div>
          <div className="admin-usage-bar">
            <div
              className={`admin-usage-fill ${usagePercent > 90 ? 'admin-usage-danger' : usagePercent > 70 ? 'admin-usage-warn' : ''}`}
              style={{ width: `${usagePercent}%` }}
            />
          </div>
        </div>
      )}

      {/* New key display */}
      {newKey && (
        <div className="admin-new-key">
          <p className="admin-new-key-label">New API Key (save it now):</p>
          <div className="admin-new-key-row">
            <code className="admin-key-code">{newKey}</code>
            <button className="admin-btn admin-btn-small" onClick={copyKey}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      {/* Plan change */}
      {changingPlan && (
        <div className="admin-plan-change">
          <select className="admin-input admin-input-sm" value={selectedPlan} onChange={(e) => setSelectedPlan(e.target.value)}>
            {plans.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
          <button className="admin-btn admin-btn-small admin-btn-primary" onClick={handlePlanChange} disabled={busy}>
            Save
          </button>
          <button className="admin-btn admin-btn-small admin-btn-ghost" onClick={() => setChangingPlan(false)}>
            Cancel
          </button>
        </div>
      )}

      {/* Delete confirmation */}
      {confirmDelete && (
        <div className="admin-confirm-delete">
          <p>Delete <strong>{tenant.name}</strong>? This cannot be undone.</p>
          <div className="admin-form-actions">
            <button className="admin-btn admin-btn-ghost admin-btn-small" onClick={() => setConfirmDelete(false)}>
              Cancel
            </button>
            <button className="admin-btn admin-btn-danger admin-btn-small" onClick={handleDelete} disabled={busy}>
              {busy ? 'Deleting...' : 'Confirm Delete'}
            </button>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="admin-tenant-actions">
        <button className="admin-btn admin-btn-small" onClick={handleRotate} disabled={busy}>
          Rotate Key
        </button>
        <button className="admin-btn admin-btn-small" onClick={() => setChangingPlan(true)} disabled={busy || changingPlan}>
          Change Plan
        </button>
        <button className="admin-btn admin-btn-small admin-btn-danger-outline" onClick={() => setConfirmDelete(true)} disabled={busy || confirmDelete}>
          Delete
        </button>
      </div>
    </div>
  )
}

export default function AdminDashboard() {
  const { isAuthenticated, login, logout } = useAdminAuth()
  const { tenants, plans, loading, error, clearError, fetchTenants, fetchPlans, createTenant, deleteTenant, rotateKey, updatePlan } = useAdmin()
  const [showCreate, setShowCreate] = useState(false)
  const [createResult, setCreateResult] = useState(null)
  const [showBrandingPreview, setShowBrandingPreview] = useState(false)

  useEffect(() => {
    if (isAuthenticated) {
      fetchTenants().catch(() => {
        // Auth failed, will show login
      })
      fetchPlans()
    }
  }, [isAuthenticated, fetchTenants, fetchPlans])

  if (!isAuthenticated) {
    return <AdminLogin onLogin={login} />
  }

  async function handleCreate(data) {
    const result = await createTenant(data)
    setCreateResult(result)
    setShowCreate(false)
  }

  return (
    <div className="container admin-container">
      <div className="admin-top-bar">
        <h2>Tenant Management</h2>
        <div className="admin-top-actions">
          <button className="admin-btn admin-btn-primary" onClick={() => setShowCreate(true)}>
            + New Tenant
          </button>
          <button className="admin-btn admin-btn-ghost" onClick={() => setShowBrandingPreview((v) => !v)}>
            {showBrandingPreview ? 'Hide Branding' : 'Branding Preview'}
          </button>
          <button className="admin-btn admin-btn-ghost" onClick={logout}>
            Sign Out
          </button>
        </div>
      </div>

      {error && (
        <div className="admin-error-banner" role="alert">
          <span>{error}</span>
          <button onClick={clearError} className="admin-error-dismiss" aria-label="Dismiss error">&times;</button>
        </div>
      )}

      {/* Show newly created tenant key */}
      {createResult && createResult.api_key && (
        <div className="admin-new-key admin-new-key-banner">
          <p><strong>Tenant created!</strong> Copy the API key now &mdash; it will not be shown again:</p>
          <div className="admin-new-key-row">
            <code className="admin-key-code">{createResult.api_key}</code>
            <button className="admin-btn admin-btn-small" onClick={() => {
              navigator.clipboard.writeText(createResult.api_key)
            }}>Copy</button>
            <button className="admin-btn admin-btn-small admin-btn-ghost" onClick={() => setCreateResult(null)}>Dismiss</button>
          </div>
        </div>
      )}

      {loading && tenants.length === 0 ? (
        <div className="admin-loading">Loading tenants...</div>
      ) : tenants.length === 0 ? (
        <div className="admin-empty">
          <p>No tenants yet. Create one to get started.</p>
        </div>
      ) : (
        <div className="admin-tenant-grid">
          {tenants.map((t) => (
            <TenantCard
              key={t.id}
              tenant={t}
              plans={plans}
              onDelete={deleteTenant}
              onRotateKey={rotateKey}
              onUpdatePlan={updatePlan}
            />
          ))}
        </div>
      )}

      {/* Branding Preview (toggled from top bar) */}
      {showBrandingPreview && (
        <div className="admin-branding-section" style={{ marginTop: '1.5rem' }}>
          <WhiteLabelPreview />
        </div>
      )}

      {showCreate && (
        <CreateTenantForm
          plans={plans.length > 0 ? plans : [{ name: 'free', queries_per_month: 100 }, { name: 'starter', queries_per_month: 1000 }, { name: 'pro', queries_per_month: 10000 }]}
          onSubmit={handleCreate}
          onCancel={() => setShowCreate(false)}
        />
      )}
    </div>
  )
}
