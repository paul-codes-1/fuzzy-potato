import { useState, useCallback } from 'react'

const STEPS = [
  { id: 'welcome', label: 'Plan' },
  { id: 'city', label: 'City' },
  { id: 'account', label: 'Account' },
  { id: 'apikey', label: 'API Key' },
  { id: 'demo', label: 'Demo' },
  { id: 'next', label: 'Next Steps' },
]

const PLANS = [
  {
    id: 'starter',
    name: 'Starter',
    price: '$49',
    period: '/mo',
    description: 'For small cities getting started',
    queries: '1,000',
    clips: '500',
    features: [
      'Up to 500 meeting clips',
      '1,000 AI queries/month',
      'Full-text search',
      'Email support',
    ],
  },
  {
    id: 'pro',
    name: 'Pro',
    price: '$199',
    period: '/mo',
    description: 'For growing municipalities',
    queries: '10,000',
    clips: '5,000',
    popular: true,
    features: [
      'Up to 5,000 meeting clips',
      '10,000 AI queries/month',
      'Custom branding',
      'Slack & Teams integration',
      'Analytics dashboard',
      'Priority support',
    ],
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    price: 'Custom',
    period: '',
    description: 'For large government organizations',
    queries: 'Unlimited',
    clips: 'Unlimited',
    features: [
      'Unlimited meeting clips',
      'Unlimited AI queries',
      'Custom branding & domain',
      'All integrations',
      'SSO / SAML',
      'Dedicated support & SLA',
      'On-prem deployment option',
    ],
  },
]

const INITIAL_DATA = {
  plan: 'pro',
  cityName: '',
  granicusHost: '',
  granicusViewId: '',
  adminEmail: '',
  orgName: '',
}

/**
 * Validates a Granicus host URL by attempting to reach its ViewPublisher endpoint.
 * Returns { valid: true } or { valid: false, error: '...' }.
 */
async function validateGranicusHost(host) {
  const cleaned = host.replace(/^https?:\/\//, '').replace(/\/+$/, '')
  const url = `https://${cleaned}/ViewPublisher.php?view_id=1`
  try {
    const res = await fetch(url, { method: 'HEAD', mode: 'no-cors' })
    // no-cors always returns opaque response; treat any non-error as valid
    return { valid: true, detectedHost: cleaned }
  } catch {
    return { valid: false, error: 'Could not reach this Granicus host. Check the URL and try again.' }
  }
}

export function useOnboarding() {
  const [currentStep, setCurrentStep] = useState(0)
  const [formData, setFormData] = useState(INITIAL_DATA)
  const [errors, setErrors] = useState({})
  const [apiKey, setApiKey] = useState(null)
  const [tenantId, setTenantId] = useState(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState(null)
  const [demoResult, setDemoResult] = useState(null)
  const [demoLoading, setDemoLoading] = useState(false)
  const [demoError, setDemoError] = useState(null)
  const [hostValidating, setHostValidating] = useState(false)
  const [hostValid, setHostValid] = useState(null)

  const step = STEPS[currentStep]
  const isFirst = currentStep === 0
  const isLast = currentStep === STEPS.length - 1
  const progress = ((currentStep + 1) / STEPS.length) * 100

  const updateField = useCallback((field, value) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
    setErrors((prev) => ({ ...prev, [field]: undefined }))
    if (field === 'granicusHost') {
      setHostValid(null)
    }
  }, [])

  const validateStep = useCallback(() => {
    const errs = {}

    if (currentStep === 0) {
      if (!formData.plan) errs.plan = 'Please select a plan'
    }

    if (currentStep === 1) {
      if (!formData.cityName.trim()) errs.cityName = 'City name is required'
      if (!formData.granicusHost.trim()) {
        errs.granicusHost = 'Granicus host URL is required'
      } else {
        const host = formData.granicusHost.replace(/^https?:\/\//, '').replace(/\/+$/, '')
        if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(host)) {
          errs.granicusHost = 'Enter a valid URL (e.g. yourcity.granicus.com)'
        }
      }
    }

    if (currentStep === 2) {
      if (!formData.adminEmail.trim()) {
        errs.adminEmail = 'Email is required'
      } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.adminEmail)) {
        errs.adminEmail = 'Enter a valid email address'
      }
      if (!formData.orgName.trim()) errs.orgName = 'Organization name is required'
    }

    setErrors(errs)
    return Object.keys(errs).length === 0
  }, [currentStep, formData])

  const detectGranicusHost = useCallback(async () => {
    if (!formData.granicusHost.trim()) return
    setHostValidating(true)
    setHostValid(null)
    try {
      const result = await validateGranicusHost(formData.granicusHost)
      setHostValid(result.valid)
      if (result.valid && result.detectedHost) {
        setFormData((prev) => ({ ...prev, granicusHost: result.detectedHost }))
      }
      if (!result.valid) {
        setErrors((prev) => ({ ...prev, granicusHost: result.error }))
      }
    } finally {
      setHostValidating(false)
    }
  }, [formData.granicusHost])

  const createTenant = useCallback(async () => {
    setCreating(true)
    setCreateError(null)
    try {
      const payload = {
        name: formData.orgName,
        granicus_host: formData.granicusHost.replace(/^https?:\/\//, '').replace(/\/+$/, ''),
        plan: formData.plan,
        admin_email: formData.adminEmail,
        city_name: formData.cityName,
      }
      if (formData.granicusViewId) {
        payload.granicus_view_id = parseInt(formData.granicusViewId, 10)
      }

      const res = await fetch('/api/v1/admin/tenants', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': sessionStorage.getItem('admin_api_key') || '',
        },
        body: JSON.stringify(payload),
      })

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || body.error || `Failed to create tenant (${res.status})`)
      }

      const data = await res.json()
      setApiKey(data.api_key)
      setTenantId(data.id || data.tenant_id)
      return data
    } catch (err) {
      setCreateError(err.message)
      throw err
    } finally {
      setCreating(false)
    }
  }, [formData])

  const runDemoQuery = useCallback(async (question) => {
    if (!apiKey) return
    setDemoLoading(true)
    setDemoError(null)
    setDemoResult(null)
    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': apiKey,
        },
        body: JSON.stringify({ question }),
      })

      if (!res.ok) {
        throw new Error(`Query failed (${res.status})`)
      }

      const data = await res.json()
      setDemoResult(data)
      return data
    } catch (err) {
      setDemoError(err.message)
    } finally {
      setDemoLoading(false)
    }
  }, [apiKey])

  const goNext = useCallback(async () => {
    if (!validateStep()) return false

    // Step 2 -> 3: create the tenant
    if (currentStep === 2) {
      try {
        await createTenant()
      } catch {
        return false
      }
    }

    if (currentStep < STEPS.length - 1) {
      setCurrentStep((s) => s + 1)
    }
    return true
  }, [currentStep, validateStep, createTenant])

  const goBack = useCallback(() => {
    if (currentStep > 0) {
      setCurrentStep((s) => s - 1)
    }
  }, [currentStep])

  const goToStep = useCallback((index) => {
    if (index <= currentStep) {
      setCurrentStep(index)
    }
  }, [currentStep])

  return {
    // State
    currentStep,
    step,
    steps: STEPS,
    plans: PLANS,
    formData,
    errors,
    apiKey,
    tenantId,
    creating,
    createError,
    demoResult,
    demoLoading,
    demoError,
    hostValidating,
    hostValid,
    progress,
    isFirst,
    isLast,

    // Actions
    updateField,
    goNext,
    goBack,
    goToStep,
    detectGranicusHost,
    runDemoQuery,
  }
}

export default useOnboarding
