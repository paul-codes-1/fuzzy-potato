import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useOnboarding } from '../hooks/useOnboarding'

/* ───────────────────────────────────────────
   Progress Bar
   ─────────────────────────────────────────── */
function ProgressBar({ steps, currentStep, onStepClick }) {
  return (
    <div className="ob-progress">
      <div className="ob-progress-bar">
        <div
          className="ob-progress-fill"
          style={{ width: `${((currentStep + 1) / steps.length) * 100}%` }}
        />
      </div>
      <div className="ob-steps">
        {steps.map((s, i) => (
          <button
            key={s.id}
            className={`ob-step-dot ${i === currentStep ? 'active' : ''} ${i < currentStep ? 'done' : ''}`}
            onClick={() => onStepClick(i)}
            disabled={i > currentStep}
            type="button"
          >
            <span className="ob-step-num">
              {i < currentStep ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              ) : (
                i + 1
              )}
            </span>
            <span className="ob-step-label">{s.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Step 1 — Plan Selection
   ─────────────────────────────────────────── */
function StepPlan({ plans, selectedPlan, onSelect }) {
  return (
    <div className="ob-step-content">
      <div className="ob-step-header">
        <h2>Welcome to CivicLens</h2>
        <p>Choose a plan that fits your city. You can upgrade or downgrade anytime.</p>
      </div>
      <div className="ob-plan-grid">
        {plans.map((plan) => (
          <button
            key={plan.id}
            type="button"
            className={`ob-plan-card ${selectedPlan === plan.id ? 'selected' : ''} ${plan.popular ? 'popular' : ''}`}
            onClick={() => onSelect('plan', plan.id)}
          >
            {plan.popular && <span className="ob-plan-badge">Most Popular</span>}
            <div className="ob-plan-top">
              <h3 className="ob-plan-name">{plan.name}</h3>
              <div className="ob-plan-price">
                <span className="ob-plan-amount">{plan.price}</span>
                {plan.period && <span className="ob-plan-period">{plan.period}</span>}
              </div>
              <p className="ob-plan-desc">{plan.description}</p>
            </div>
            <ul className="ob-plan-features">
              {plan.features.map((f, i) => (
                <li key={i}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  {f}
                </li>
              ))}
            </ul>
            <div className="ob-plan-select-indicator">
              <span className="ob-radio">{selectedPlan === plan.id && <span className="ob-radio-dot" />}</span>
              <span>{selectedPlan === plan.id ? 'Selected' : 'Select Plan'}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Step 2 — City Configuration
   ─────────────────────────────────────────── */
function StepCity({ formData, errors, onChange, onDetect, hostValidating, hostValid }) {
  return (
    <div className="ob-step-content">
      <div className="ob-step-header">
        <h2>Configure Your City</h2>
        <p>Connect CivicLens to your Granicus-hosted meeting archive.</p>
      </div>
      <div className="ob-form">
        <label className="ob-label">
          City Name
          <input
            className={`ob-input ${errors.cityName ? 'ob-input-error' : ''}`}
            type="text"
            value={formData.cityName}
            onChange={(e) => onChange('cityName', e.target.value)}
            placeholder="e.g. City of Elk Grove"
          />
          {errors.cityName && <span className="ob-field-error">{errors.cityName}</span>}
        </label>

        <label className="ob-label">
          Granicus Host URL
          <div className="ob-input-group">
            <input
              className={`ob-input ${errors.granicusHost ? 'ob-input-error' : ''} ${hostValid === true ? 'ob-input-valid' : ''}`}
              type="text"
              value={formData.granicusHost}
              onChange={(e) => onChange('granicusHost', e.target.value)}
              placeholder="e.g. elkgrovecity.granicus.com"
              onBlur={onDetect}
            />
            <button
              type="button"
              className="ob-detect-btn"
              onClick={onDetect}
              disabled={hostValidating || !formData.granicusHost.trim()}
            >
              {hostValidating ? (
                <span className="ob-detect-spinner" />
              ) : hostValid === true ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              ) : (
                'Verify'
              )}
            </button>
          </div>
          {errors.granicusHost && <span className="ob-field-error">{errors.granicusHost}</span>}
          {hostValid === true && <span className="ob-field-success">Host verified</span>}
          <span className="ob-help-text">
            This is the subdomain your city uses for Granicus. Find it in any meeting video URL.
          </span>
        </label>

        <label className="ob-label">
          View ID <span className="ob-optional">(optional)</span>
          <input
            className="ob-input"
            type="number"
            value={formData.granicusViewId}
            onChange={(e) => onChange('granicusViewId', e.target.value)}
            placeholder="e.g. 42"
          />
          <span className="ob-help-text">
            The Granicus View ID filters which clips to process. Leave blank to process all available meetings.
          </span>
        </label>
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Step 3 — Account Setup
   ─────────────────────────────────────────── */
function StepAccount({ formData, errors, onChange }) {
  return (
    <div className="ob-step-content">
      <div className="ob-step-header">
        <h2>Set Up Your Account</h2>
        <p>We will create your CivicLens tenant and generate an API key.</p>
      </div>
      <div className="ob-form">
        <label className="ob-label">
          Organization Name
          <input
            className={`ob-input ${errors.orgName ? 'ob-input-error' : ''}`}
            type="text"
            value={formData.orgName}
            onChange={(e) => onChange('orgName', e.target.value)}
            placeholder="e.g. City of Elk Grove"
          />
          {errors.orgName && <span className="ob-field-error">{errors.orgName}</span>}
        </label>

        <label className="ob-label">
          Admin Email
          <input
            className={`ob-input ${errors.adminEmail ? 'ob-input-error' : ''}`}
            type="email"
            value={formData.adminEmail}
            onChange={(e) => onChange('adminEmail', e.target.value)}
            placeholder="admin@yourcity.gov"
          />
          {errors.adminEmail && <span className="ob-field-error">{errors.adminEmail}</span>}
        </label>

        <div className="ob-summary-card">
          <h4>Setup Summary</h4>
          <div className="ob-summary-row">
            <span>Plan</span>
            <span className="ob-summary-val">{formData.plan.charAt(0).toUpperCase() + formData.plan.slice(1)}</span>
          </div>
          <div className="ob-summary-row">
            <span>City</span>
            <span className="ob-summary-val">{formData.cityName || '--'}</span>
          </div>
          <div className="ob-summary-row">
            <span>Granicus Host</span>
            <span className="ob-summary-val">{formData.granicusHost || '--'}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Step 4 — API Key Reveal
   ─────────────────────────────────────────── */
function StepApiKey({ apiKey, createError }) {
  const [copied, setCopied] = useState(false)

  function copyKey() {
    if (!apiKey) return
    navigator.clipboard.writeText(apiKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2500)
  }

  if (createError) {
    return (
      <div className="ob-step-content">
        <div className="ob-step-header">
          <h2>Something went wrong</h2>
          <p>{createError}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="ob-step-content">
      <div className="ob-step-header">
        <h2>Your API Key</h2>
        <p>This key authenticates all requests to CivicLens. Copy it now.</p>
      </div>
      <div className="ob-apikey-container">
        <div className="ob-apikey-display">
          <code className="ob-apikey-code">{apiKey || 'Generating...'}</code>
          <button type="button" className="ob-copy-btn" onClick={copyKey} disabled={!apiKey}>
            {copied ? (
              <>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                Copied
              </>
            ) : (
              <>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                  <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                </svg>
                Copy
              </>
            )}
          </button>
        </div>
        <div className="ob-apikey-warning">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <span>Save this key somewhere safe. It will not be shown again after you leave this page.</span>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Step 5 — Demo Query
   ─────────────────────────────────────────── */
function StepDemo({ demoResult, demoLoading, demoError, onRunQuery }) {
  const [question, setQuestion] = useState('What topics were discussed at the most recent city council meeting?')

  function handleRun(e) {
    e.preventDefault()
    if (question.trim()) {
      onRunQuery(question.trim())
    }
  }

  return (
    <div className="ob-step-content">
      <div className="ob-step-header">
        <h2>Try Your First Query</h2>
        <p>
          Your meeting archive is being processed in the background. Try a sample query to see CivicLens in action.
        </p>
      </div>

      <form onSubmit={handleRun} className="ob-demo-form">
        <div className="ob-demo-input-row">
          <input
            className="ob-input"
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask a question..."
          />
          <button type="submit" className="ob-demo-run-btn" disabled={demoLoading || !question.trim()}>
            {demoLoading ? 'Searching...' : 'Ask'}
          </button>
        </div>
      </form>

      {demoLoading && (
        <div className="ob-demo-loading">
          <div className="ob-spinner" />
          <p>Searching your meeting archive...</p>
        </div>
      )}

      {demoError && (
        <div className="ob-demo-notice">
          <p>
            Your archive is still being processed. This is normal for new instances -- meetings will
            appear as they finish transcription. Check back in a few minutes.
          </p>
        </div>
      )}

      {demoResult && (
        <div className="ob-demo-result">
          <div className="ob-demo-answer">
            <h4>Answer</h4>
            <p>{demoResult.answer}</p>
          </div>
          {demoResult.sources && demoResult.sources.length > 0 && (
            <div className="ob-demo-sources">
              <h4>Sources ({demoResult.sources.length})</h4>
              {demoResult.sources.slice(0, 3).map((src, i) => (
                <div key={i} className="ob-demo-source">
                  <span className="ob-demo-source-title">{src.title}</span>
                  <span className="ob-demo-source-date">{src.date}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ───────────────────────────────────────────
   Step 6 — Next Steps
   ─────────────────────────────────────────── */
function StepNext() {
  const items = [
    {
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="16 18 22 12 16 6" />
          <polyline points="8 6 2 12 8 18" />
        </svg>
      ),
      title: 'Embed the Widget',
      description: 'Add a search widget to your city website with a single script tag.',
      link: '#',
      linkText: 'View embed docs',
    },
    {
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
        </svg>
      ),
      title: 'Connect Slack or Teams',
      description: 'Get meeting summaries delivered to your team channels automatically.',
      link: '#',
      linkText: 'Set up integration',
    },
    {
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 01-3.46 0" />
        </svg>
      ),
      title: 'Set Up Alerts',
      description: 'Get notified when specific topics, ordinances, or keywords appear in meetings.',
      link: '#',
      linkText: 'Configure alerts',
    },
    {
      icon: (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
          <line x1="3" y1="9" x2="21" y2="9" />
          <line x1="9" y1="21" x2="9" y2="9" />
        </svg>
      ),
      title: 'Admin Dashboard',
      description: 'Monitor usage, manage API keys, and view analytics.',
      link: '/admin',
      linkText: 'Open dashboard',
    },
  ]

  return (
    <div className="ob-step-content">
      <div className="ob-step-header ob-step-header-done">
        <div className="ob-done-check">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 11.08V12a10 10 0 11-5.93-9.14" />
            <polyline points="22 4 12 14.01 9 11.01" />
          </svg>
        </div>
        <h2>You are all set!</h2>
        <p>Your CivicLens instance is live. Here is what to do next.</p>
      </div>
      <div className="ob-next-grid">
        {items.map((item, i) => (
          <div key={i} className="ob-next-card">
            <div className="ob-next-icon">{item.icon}</div>
            <div className="ob-next-body">
              <h4>{item.title}</h4>
              <p>{item.description}</p>
              {item.link.startsWith('/') ? (
                <Link to={item.link} className="ob-next-link">{item.linkText}</Link>
              ) : (
                <a href={item.link} className="ob-next-link">{item.linkText}</a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Main Wizard
   ─────────────────────────────────────────── */
export default function Onboarding() {
  const {
    currentStep,
    steps,
    plans,
    formData,
    errors,
    apiKey,
    creating,
    createError,
    demoResult,
    demoLoading,
    demoError,
    hostValidating,
    hostValid,
    isFirst,
    isLast,
    updateField,
    goNext,
    goBack,
    goToStep,
    detectGranicusHost,
    runDemoQuery,
  } = useOnboarding()

  function renderStep() {
    switch (currentStep) {
      case 0:
        return <StepPlan plans={plans} selectedPlan={formData.plan} onSelect={updateField} />
      case 1:
        return (
          <StepCity
            formData={formData}
            errors={errors}
            onChange={updateField}
            onDetect={detectGranicusHost}
            hostValidating={hostValidating}
            hostValid={hostValid}
          />
        )
      case 2:
        return <StepAccount formData={formData} errors={errors} onChange={updateField} />
      case 3:
        return <StepApiKey apiKey={apiKey} createError={createError} />
      case 4:
        return (
          <StepDemo
            demoResult={demoResult}
            demoLoading={demoLoading}
            demoError={demoError}
            onRunQuery={runDemoQuery}
          />
        )
      case 5:
        return <StepNext />
      default:
        return null
    }
  }

  return (
    <div className="ob-container">
      <div className="ob-wrapper">
        <ProgressBar steps={steps} currentStep={currentStep} onStepClick={goToStep} />

        <div className="ob-body">
          {renderStep()}
        </div>

        <div className="ob-footer">
          {!isFirst && (
            <button type="button" className="ob-btn ob-btn-back" onClick={goBack}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="19" y1="12" x2="5" y2="12" />
                <polyline points="12 19 5 12 12 5" />
              </svg>
              Back
            </button>
          )}
          {isFirst && <span />}
          {!isLast ? (
            <button
              type="button"
              className="ob-btn ob-btn-next"
              onClick={goNext}
              disabled={creating}
            >
              {creating ? (
                <>
                  <span className="ob-btn-spinner" />
                  Creating...
                </>
              ) : currentStep === 2 ? (
                'Create Account'
              ) : (
                <>
                  Continue
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="5" y1="12" x2="19" y2="12" />
                    <polyline points="12 5 19 12 12 19" />
                  </svg>
                </>
              )}
            </button>
          ) : (
            <Link to="/" className="ob-btn ob-btn-next">
              Go to Dashboard
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="5" y1="12" x2="19" y2="12" />
                <polyline points="12 5 19 12 12 19" />
              </svg>
            </Link>
          )}
        </div>
      </div>
    </div>
  )
}
