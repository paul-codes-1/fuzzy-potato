import { useState, useCallback } from 'react'
import { Link } from 'react-router-dom'

/* ───────────────────────────────────────────
   Default branding config
   ─────────────────────────────────────────── */
const DEFAULT_CONFIG = {
  display_name: 'City of Springfield',
  logo_url: '',
  primary_color: '#1a56db',
  accent_color: '#f59e0b',
  welcome_message: 'Search and explore your local government meetings.',
  footer_text: '',
  dark_mode: false,
}

/* ───────────────────────────────────────────
   Sample data for the live preview
   ─────────────────────────────────────────── */
const SAMPLE_MEETING = {
  date: '2026-03-24',
  body: 'City Council',
  title: 'Regular Meeting - City Council',
  topics: ['Budget', 'Zoning', 'Public Safety'],
  summary: 'Council approved the FY2027 preliminary budget framework and heard three public comments on the proposed downtown zoning overlay district.',
}

const SAMPLE_QA = {
  question: 'What was discussed about the downtown zoning overlay?',
  answer: 'The City Council reviewed a proposed zoning overlay for the downtown district that would allow mixed-use development up to 6 stories. Three residents spoke during public comment, with concerns focused on parking availability and building height.',
  sources: [
    { title: 'City Council - Mar 24, 2026', date: '2026-03-24' },
    { title: 'Planning Commission - Mar 10, 2026', date: '2026-03-10' },
  ],
}

/* ───────────────────────────────────────────
   Color utility: light text on dark bg?
   ─────────────────────────────────────────── */
function contrastText(hex) {
  if (!hex || hex.length < 7) return '#ffffff'
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
  return luminance > 0.6 ? '#1a1a2e' : '#ffffff'
}

function lighten(hex, amount) {
  if (!hex || hex.length < 7) return '#eaf0fa'
  const r = Math.min(255, parseInt(hex.slice(1, 3), 16) + Math.round(255 * amount))
  const g = Math.min(255, parseInt(hex.slice(3, 5), 16) + Math.round(255 * amount))
  const b = Math.min(255, parseInt(hex.slice(5, 7), 16) + Math.round(255 * amount))
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`
}

/* ───────────────────────────────────────────
   Configuration Panel (left side)
   ─────────────────────────────────────────── */
function ConfigPanel({ config, onChange }) {
  function update(field) {
    return (e) => onChange({ ...config, [field]: e.target.value })
  }

  return (
    <div className="wlp-config">
      <div className="wlp-config-header">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
        </svg>
        <h3>Branding Configuration</h3>
      </div>
      <p className="wlp-config-subtitle">
        Customize the look and feel. Changes appear instantly in the preview.
      </p>

      <div className="wlp-fields">
        <label className="wlp-label">
          City / Organization Name
          <input
            className="wlp-input"
            type="text"
            value={config.display_name}
            onChange={update('display_name')}
            placeholder="e.g. City of Springfield"
          />
        </label>

        <label className="wlp-label">
          Logo URL
          <input
            className="wlp-input"
            type="url"
            value={config.logo_url}
            onChange={update('logo_url')}
            placeholder="https://example.com/logo.png"
          />
          {config.logo_url && (
            <div className="wlp-logo-preview">
              <img
                src={config.logo_url}
                alt="Logo preview"
                onError={(e) => { e.target.style.display = 'none' }}
                onLoad={(e) => { e.target.style.display = 'block' }}
              />
            </div>
          )}
        </label>

        <div className="wlp-color-row">
          <label className="wlp-label wlp-label-color">
            Primary Color
            <div className="wlp-color-input-wrap">
              <input
                className="wlp-color-picker"
                type="color"
                value={config.primary_color}
                onChange={update('primary_color')}
              />
              <input
                className="wlp-input wlp-input-hex"
                type="text"
                value={config.primary_color}
                onChange={update('primary_color')}
                maxLength={7}
              />
            </div>
          </label>

          <label className="wlp-label wlp-label-color">
            Accent Color
            <div className="wlp-color-input-wrap">
              <input
                className="wlp-color-picker"
                type="color"
                value={config.accent_color}
                onChange={update('accent_color')}
              />
              <input
                className="wlp-input wlp-input-hex"
                type="text"
                value={config.accent_color}
                onChange={update('accent_color')}
                maxLength={7}
              />
            </div>
          </label>
        </div>

        <label className="wlp-label">
          Welcome Message
          <textarea
            className="wlp-input wlp-textarea"
            value={config.welcome_message}
            onChange={update('welcome_message')}
            placeholder="A short description shown below your header"
            rows={3}
          />
        </label>

        <label className="wlp-label">
          Footer Text
          <input
            className="wlp-input"
            type="text"
            value={config.footer_text}
            onChange={update('footer_text')}
            placeholder="e.g. Powered by CivicLens | City of Springfield"
          />
        </label>

        <div className="wlp-toggle-row">
          <span className="wlp-toggle-label">Dark Mode</span>
          <button
            type="button"
            className={`wlp-toggle ${config.dark_mode ? 'wlp-toggle-on' : ''}`}
            onClick={() => onChange({ ...config, dark_mode: !config.dark_mode })}
            role="switch"
            aria-checked={config.dark_mode}
          >
            <span className="wlp-toggle-knob" />
          </button>
        </div>
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Live Preview (right side)
   ─────────────────────────────────────────── */
function LivePreview({ config }) {
  const dark = config.dark_mode
  const bg = dark ? '#0f172a' : '#f5f5f5'
  const surface = dark ? '#1e293b' : '#ffffff'
  const textPrimary = dark ? '#f1f5f9' : '#1a1a2e'
  const textSecondary = dark ? '#94a3b8' : '#6b7280'
  const border = dark ? '#334155' : '#e5e5ea'
  const headerBg = dark ? '#020617' : config.primary_color
  const headerText = dark ? '#f1f5f9' : contrastText(config.primary_color)

  return (
    <div className="wlp-preview" style={{ background: bg }}>
      <div className="wlp-preview-label">Live Preview</div>

      {/* --- Header --- */}
      <div className="wlp-prev-header" style={{ background: headerBg, color: headerText }}>
        <div className="wlp-prev-header-inner">
          <div className="wlp-prev-header-title">
            {config.logo_url && (
              <img
                src={config.logo_url}
                alt=""
                className="wlp-prev-logo"
                onError={(e) => { e.target.style.display = 'none' }}
              />
            )}
            <span className="wlp-prev-name">{config.display_name || 'CivicLens'}</span>
          </div>
          <div className="wlp-prev-nav">
            <span style={{ opacity: 0.9 }}>Browse</span>
            <span style={{ opacity: 0.7 }}>Chat</span>
            <span style={{ opacity: 0.7 }}>Ask</span>
            <span style={{ opacity: 0.7 }}>Votes</span>
            <span
              className="wlp-prev-cta"
              style={{ background: config.accent_color, color: contrastText(config.accent_color) }}
            >
              Get Started
            </span>
          </div>
        </div>
        <p className="wlp-prev-subtitle" style={{ color: headerText, opacity: 0.8 }}>
          {config.welcome_message || 'Search and explore your local government meetings.'}
        </p>
        {/* Search bar mock */}
        <div className="wlp-prev-search" style={{ background: dark ? '#1e293b' : 'rgba(255,255,255,0.15)' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <span style={{ opacity: 0.5, fontSize: '0.8rem' }}>Search meetings, topics, votes...</span>
        </div>
      </div>

      {/* --- Meeting Card --- */}
      <div className="wlp-prev-body" style={{ background: bg }}>
        <div className="wlp-prev-card" style={{ background: surface, borderColor: border }}>
          <div className="wlp-prev-card-header">
            <span className="wlp-prev-card-badge" style={{ background: lighten(config.primary_color, 0.7), color: config.primary_color }}>
              {SAMPLE_MEETING.body}
            </span>
            <span className="wlp-prev-card-date" style={{ color: textSecondary }}>{SAMPLE_MEETING.date}</span>
          </div>
          <h4 className="wlp-prev-card-title" style={{ color: textPrimary }}>{SAMPLE_MEETING.title}</h4>
          <p className="wlp-prev-card-summary" style={{ color: textSecondary }}>{SAMPLE_MEETING.summary}</p>
          <div className="wlp-prev-card-topics">
            {SAMPLE_MEETING.topics.map((t) => (
              <span key={t} className="wlp-prev-topic" style={{ background: dark ? '#334155' : '#f0f0f5', color: textSecondary }}>
                {t}
              </span>
            ))}
          </div>
        </div>

        {/* --- Q&A Preview --- */}
        <div className="wlp-prev-qa" style={{ background: surface, borderColor: border }}>
          <div className="wlp-prev-qa-header" style={{ color: textPrimary }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={config.primary_color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
            </svg>
            <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>AI Q&A</span>
          </div>
          <div className="wlp-prev-qa-q" style={{ color: textPrimary }}>
            <strong>Q:</strong> {SAMPLE_QA.question}
          </div>
          <div className="wlp-prev-qa-a" style={{ color: textSecondary }}>
            {SAMPLE_QA.answer}
          </div>
          <div className="wlp-prev-qa-sources">
            {SAMPLE_QA.sources.map((s, i) => (
              <span key={i} className="wlp-prev-source" style={{ borderColor: border, color: config.primary_color }}>
                {s.title}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* --- Footer --- */}
      <div className="wlp-prev-footer" style={{ background: surface, borderColor: border, color: textSecondary }}>
        {config.footer_text || `Powered by CivicLens | ${config.display_name || 'Your City'}`}
      </div>
    </div>
  )
}

/* ───────────────────────────────────────────
   Export Actions
   ─────────────────────────────────────────── */
function ExportActions({ config }) {
  const [copied, setCopied] = useState(false)

  const exportConfig = {
    display_name: config.display_name,
    logo_url: config.logo_url,
    primary_color: config.primary_color,
    accent_color: config.accent_color,
    welcome_message: config.welcome_message,
    footer_text: config.footer_text,
  }

  function handleCopy() {
    navigator.clipboard.writeText(JSON.stringify(exportConfig, null, 2))
    setCopied(true)
    setTimeout(() => setCopied(false), 2500)
  }

  const onboardingParams = new URLSearchParams({
    brand_name: config.display_name,
    brand_primary: config.primary_color,
    brand_accent: config.accent_color,
  }).toString()

  return (
    <div className="wlp-export">
      <button type="button" className="wlp-btn wlp-btn-secondary" onClick={handleCopy}>
        {copied ? (
          <>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
            Copied!
          </>
        ) : (
          <>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
              <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
            </svg>
            Copy Branding JSON
          </>
        )}
      </button>
      <Link to={`/onboarding?${onboardingParams}`} className="wlp-btn wlp-btn-primary">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12h14" />
          <path d="M12 5l7 7-7 7" />
        </svg>
        Start Trial with This Branding
      </Link>
    </div>
  )
}

/* ───────────────────────────────────────────
   Main Component
   ─────────────────────────────────────────── */
export default function WhiteLabelPreview() {
  const [config, setConfig] = useState(DEFAULT_CONFIG)

  const handleChange = useCallback((newConfig) => {
    setConfig(newConfig)
  }, [])

  return (
    <div className="wlp-container">
      <div className="wlp-page-header">
        <div className="container">
          <h2 className="wlp-page-title">White-Label Preview</h2>
          <p className="wlp-page-desc">
            See exactly how CivicLens looks with your branding. Adjust colors, logo, and messaging,
            then start a trial or export the configuration.
          </p>
        </div>
      </div>

      <div className="container">
        <div className="wlp-layout">
          <ConfigPanel config={config} onChange={handleChange} />
          <div className="wlp-right">
            <LivePreview config={config} />
            <ExportActions config={config} />
          </div>
        </div>
      </div>

      {/* Scoped styles */}
      <style>{`
        /* ── Page Layout ── */
        .wlp-container {
          padding-bottom: 3rem;
        }
        .wlp-page-header {
          background: var(--blue-900);
          color: white;
          padding: 2rem 0;
          margin-bottom: 2rem;
        }
        .wlp-page-title {
          font-size: 1.5rem;
          font-weight: 700;
          letter-spacing: -0.02em;
          margin-bottom: 0.25rem;
        }
        .wlp-page-desc {
          opacity: 0.8;
          font-size: 0.95rem;
          max-width: 600px;
        }
        .wlp-layout {
          display: grid;
          grid-template-columns: 380px 1fr;
          gap: 1.5rem;
          align-items: start;
        }
        @media (max-width: 960px) {
          .wlp-layout {
            grid-template-columns: 1fr;
          }
        }

        /* ── Config Panel ── */
        .wlp-config {
          background: var(--surface);
          border-radius: var(--radius-lg);
          border: 1px solid var(--border);
          padding: 1.5rem;
          box-shadow: var(--shadow-card);
          position: sticky;
          top: 1rem;
        }
        .wlp-config-header {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          margin-bottom: 0.25rem;
          color: var(--text-primary);
        }
        .wlp-config-header h3 {
          font-size: 1.1rem;
          font-weight: 600;
        }
        .wlp-config-subtitle {
          font-size: 0.85rem;
          color: var(--text-secondary);
          margin-bottom: 1.25rem;
        }
        .wlp-fields {
          display: flex;
          flex-direction: column;
          gap: 1rem;
        }
        .wlp-label {
          display: flex;
          flex-direction: column;
          gap: 0.35rem;
          font-size: 0.82rem;
          font-weight: 600;
          color: var(--text-primary);
        }
        .wlp-input {
          padding: 0.55rem 0.75rem;
          border-radius: var(--radius-sm);
          border: 1px solid var(--border);
          font-size: 0.875rem;
          background: var(--bg);
          color: var(--text-primary);
          outline: none;
          transition: border-color var(--transition);
          font-family: inherit;
        }
        .wlp-input:focus {
          border-color: var(--blue-500);
          box-shadow: 0 0 0 3px rgba(51, 102, 204, 0.12);
        }
        .wlp-textarea {
          resize: vertical;
          min-height: 60px;
        }

        /* ── Color Pickers ── */
        .wlp-color-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 0.75rem;
        }
        .wlp-label-color {
          gap: 0.35rem;
        }
        .wlp-color-input-wrap {
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }
        .wlp-color-picker {
          width: 36px;
          height: 36px;
          border: 2px solid var(--border);
          border-radius: var(--radius-sm);
          cursor: pointer;
          padding: 0;
          background: none;
          flex-shrink: 0;
        }
        .wlp-color-picker::-webkit-color-swatch-wrapper { padding: 2px; }
        .wlp-color-picker::-webkit-color-swatch { border: none; border-radius: 4px; }
        .wlp-input-hex {
          font-family: 'SF Mono', 'Fira Code', monospace;
          font-size: 0.8rem;
          width: 100%;
        }

        /* ── Logo Preview ── */
        .wlp-logo-preview {
          margin-top: 0.25rem;
          padding: 0.5rem;
          background: var(--bg);
          border-radius: var(--radius-sm);
          border: 1px dashed var(--border);
          text-align: center;
        }
        .wlp-logo-preview img {
          max-height: 48px;
          max-width: 100%;
          object-fit: contain;
        }

        /* ── Toggle ── */
        .wlp-toggle-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0.5rem 0;
        }
        .wlp-toggle-label {
          font-size: 0.82rem;
          font-weight: 600;
          color: var(--text-primary);
        }
        .wlp-toggle {
          width: 44px;
          height: 24px;
          border-radius: 12px;
          background: var(--border);
          border: none;
          cursor: pointer;
          position: relative;
          transition: background var(--transition);
          padding: 0;
        }
        .wlp-toggle-on {
          background: var(--blue-700);
        }
        .wlp-toggle-knob {
          position: absolute;
          top: 2px;
          left: 2px;
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: white;
          box-shadow: 0 1px 3px rgba(0,0,0,0.2);
          transition: transform var(--transition);
        }
        .wlp-toggle-on .wlp-toggle-knob {
          transform: translateX(20px);
        }

        /* ── Right Column ── */
        .wlp-right {
          display: flex;
          flex-direction: column;
          gap: 1rem;
        }

        /* ── Live Preview ── */
        .wlp-preview {
          border-radius: var(--radius-lg);
          border: 1px solid var(--border);
          overflow: hidden;
          box-shadow: var(--shadow-lg);
          position: relative;
          font-size: 0.85rem;
        }
        .wlp-preview-label {
          position: absolute;
          top: 0.5rem;
          right: 0.75rem;
          font-size: 0.65rem;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: rgba(255,255,255,0.6);
          z-index: 2;
        }

        /* ── Preview Header ── */
        .wlp-prev-header {
          padding: 1rem 1.25rem 0.75rem;
          position: relative;
        }
        .wlp-prev-header::before {
          content: '';
          position: absolute;
          top: -40%;
          right: -8%;
          width: 200px;
          height: 200px;
          background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 70%);
          pointer-events: none;
        }
        .wlp-prev-header-inner {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 0.5rem;
        }
        .wlp-prev-header-title {
          display: flex;
          align-items: center;
          gap: 0.4rem;
        }
        .wlp-prev-logo {
          height: 1.4rem;
          object-fit: contain;
        }
        .wlp-prev-name {
          font-weight: 700;
          font-size: 1rem;
          letter-spacing: -0.01em;
        }
        .wlp-prev-nav {
          display: flex;
          align-items: center;
          gap: 0.6rem;
          font-size: 0.72rem;
          font-weight: 500;
        }
        .wlp-prev-cta {
          padding: 0.25rem 0.6rem;
          border-radius: var(--radius-full);
          font-size: 0.68rem;
          font-weight: 600;
        }
        .wlp-prev-subtitle {
          font-size: 0.78rem;
          margin-bottom: 0.6rem;
        }
        .wlp-prev-search {
          display: flex;
          align-items: center;
          gap: 0.4rem;
          padding: 0.4rem 0.65rem;
          border-radius: var(--radius-full);
        }

        /* ── Preview Body ── */
        .wlp-prev-body {
          padding: 0.75rem 1rem;
          display: flex;
          flex-direction: column;
          gap: 0.75rem;
        }

        /* ── Meeting Card ── */
        .wlp-prev-card {
          border: 1px solid;
          border-radius: var(--radius-md);
          padding: 0.85rem;
          box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }
        .wlp-prev-card-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.4rem;
        }
        .wlp-prev-card-badge {
          font-size: 0.68rem;
          font-weight: 600;
          padding: 0.15rem 0.5rem;
          border-radius: var(--radius-full);
        }
        .wlp-prev-card-date {
          font-size: 0.72rem;
        }
        .wlp-prev-card-title {
          font-size: 0.88rem;
          font-weight: 600;
          margin-bottom: 0.3rem;
        }
        .wlp-prev-card-summary {
          font-size: 0.78rem;
          line-height: 1.5;
          margin-bottom: 0.4rem;
        }
        .wlp-prev-card-topics {
          display: flex;
          gap: 0.35rem;
          flex-wrap: wrap;
        }
        .wlp-prev-topic {
          font-size: 0.68rem;
          padding: 0.15rem 0.45rem;
          border-radius: var(--radius-full);
          font-weight: 500;
        }

        /* ── Q&A Preview ── */
        .wlp-prev-qa {
          border: 1px solid;
          border-radius: var(--radius-md);
          padding: 0.85rem;
        }
        .wlp-prev-qa-header {
          display: flex;
          align-items: center;
          gap: 0.35rem;
          margin-bottom: 0.5rem;
          padding-bottom: 0.4rem;
          border-bottom: 1px solid var(--border);
        }
        .wlp-prev-qa-q {
          font-size: 0.8rem;
          margin-bottom: 0.4rem;
          line-height: 1.45;
        }
        .wlp-prev-qa-a {
          font-size: 0.78rem;
          line-height: 1.5;
          margin-bottom: 0.5rem;
        }
        .wlp-prev-qa-sources {
          display: flex;
          gap: 0.35rem;
          flex-wrap: wrap;
        }
        .wlp-prev-source {
          font-size: 0.68rem;
          padding: 0.2rem 0.5rem;
          border: 1px solid;
          border-radius: var(--radius-full);
          font-weight: 500;
        }

        /* ── Preview Footer ── */
        .wlp-prev-footer {
          padding: 0.6rem 1rem;
          font-size: 0.72rem;
          text-align: center;
          border-top: 1px solid;
        }

        /* ── Export Actions ── */
        .wlp-export {
          display: flex;
          gap: 0.75rem;
          flex-wrap: wrap;
        }
        .wlp-btn {
          display: inline-flex;
          align-items: center;
          gap: 0.4rem;
          padding: 0.65rem 1.25rem;
          border-radius: var(--radius-sm);
          font-size: 0.875rem;
          font-weight: 600;
          border: none;
          cursor: pointer;
          transition: all var(--transition);
          text-decoration: none;
          white-space: nowrap;
        }
        .wlp-btn-primary {
          background: var(--blue-700);
          color: white;
        }
        .wlp-btn-primary:hover {
          background: var(--blue-600);
          text-decoration: none;
        }
        .wlp-btn-secondary {
          background: var(--surface);
          color: var(--text-primary);
          border: 1px solid var(--border);
        }
        .wlp-btn-secondary:hover {
          background: var(--surface-secondary);
        }
      `}</style>
    </div>
  )
}
