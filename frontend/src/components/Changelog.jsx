import { useState } from 'react'

const CHANGELOG_DATA = [
  {
    version: 'v1.5.0',
    date: '2026-04-09',
    label: 'Latest',
    sections: {
      Added: [
        'Backup/Restore system for tenant data with point-in-time recovery metadata and optional S3 upload targets.',
        'Customer Health scoring with weighted signal analysis (query frequency, meeting coverage, API error rate) and churn risk detection.',
        'Report Builder allowing custom PDF/CSV reports combining meeting summaries, vote histories, financial items, and sentiment trends.',
        'Usage Dashboard with per-tenant bandwidth, storage, and query consumption breakdowns.',
        'Admin backup and restore API endpoints.',
        'Health score trends endpoint for historical tracking.',
      ],
      Changed: [
        'Upgraded ChromaDB to v0.5.x with ~40% faster vector queries on large collections.',
        'Improved two-pass summary pipeline with better resolution number and amendment extraction from OCR\'d agenda PDFs.',
        'Admin dashboard redesigned with tabbed layout.',
      ],
      Fixed: [
        'Fixed race condition in concurrent RAG ingestion for multi-tenant processing.',
        'Fixed analytics event deduplication bug inflating query counts via Zapier polling.',
        'Corrected timezone handling in calendar iCal export for non-Eastern cities.',
      ],
      Security: [
        'Backup and restore operations are enterprise-plan gated.',
        'Customer health data is tenant-scoped unless accessed through admin routes.',
      ],
    },
  },
  {
    version: 'v1.4.0',
    date: '2026-02-20',
    sections: {
      Added: [
        'Web Push notifications with VAPID key management and configurable alert triggers.',
        'Meeting Calendar with automatic schedule detection and iCal feed generation.',
        'Sentiment Analysis for public comments using GPT-4o-mini classification with topic clustering.',
        'Highlights generator scoring key moments by significance (split votes, large appropriations, heated comments).',
        'Push subscribe/unsubscribe endpoints.',
        'Calendar iCal feed endpoint for external calendar apps.',
      ],
      Changed: [
        'Chat endpoint now supports Anthropic Claude models via the model parameter.',
        'Improved transcript chunking to respect sentence boundaries (85% fewer mid-sentence splits).',
        'Meeting detail page shows sentiment badges on public comment sections.',
      ],
      Fixed: [
        'Fixed Web Push delivery failures for stale browser subscriptions (410 Gone).',
        'Fixed calendar schedule detection falsely identifying special sessions as recurring.',
        'Resolved memory leak in sentiment batch processing for >500 comments.',
      ],
      Security: [
        'Push payloads replaced with summary snippets and deep links instead of full text.',
      ],
    },
  },
  {
    version: 'v1.3.0',
    date: '2025-12-15',
    sections: {
      Added: [
        'Slack integration with slash commands, @CivicLens mentions, and Block Kit formatted responses.',
        'Microsoft Teams integration with outgoing webhook handler and Adaptive Card summaries.',
        'Zapier integration with polling triggers, REST Hook subscriptions, and query/search actions.',
        'Webhook system with five event types, HMAC-SHA256 signing, and exponential backoff retries.',
        'Webhook delivery history inspection endpoint.',
      ],
      Changed: [
        'RAG query responses now include a sources array with clip IDs and relevance scores.',
        'Increased default webhook retry attempts from 2 to 3 with longer backoff.',
        'Integration and webhook endpoints now return more consistent JSON responses.',
      ],
      Fixed: [
        'Fixed Slack signature verification rejecting valid requests on clock drift.',
        'Fixed Zapier REST Hook unsubscribe returning 500 for already-removed subscriptions.',
        'Fixed webhook delivery thread pool exhaustion under high throughput (>50 clips/hour).',
      ],
    },
  },
  {
    version: 'v1.2.0',
    date: '2025-10-08',
    sections: {
      Added: [
        'SAML 2.0 SSO with per-tenant IdP configuration, auto-provisioning, and role-based access.',
        'Immutable audit logging with SHA-256 integrity hash chains and CSV export.',
        'Async data export with ZIP generation for meetings, votes, financials, and transcripts.',
        'FOIA request handler using RAG to find relevant meeting segments.',
        'Per-tenant email domain allow-lists for SSO user provisioning.',
      ],
      Changed: [
        'All API responses now include X-Request-ID header for distributed tracing.',
        'SecurityHeadersMiddleware sets restrictive security headers on API responses.',
        'Upgraded OpenAI SDK to v1.50+ for improved streaming reliability.',
      ],
      Fixed: [
        'Fixed JWT token refresh race condition invalidating concurrent sessions.',
        'Fixed audit log export handling for larger result sets.',
        'Fixed FOIA export missing agenda PDFs when original download URLs had expired.',
      ],
      Security: [
        'Audit logging uses append-only writes plus SHA-256 integrity hashes.',
        'SSO session tokens use dedicated JWT_SECRET.',
        'Added HSTS header on API responses.',
      ],
    },
  },
  {
    version: 'v1.1.0',
    date: '2025-08-01',
    sections: {
      Added: [
        'Multi-tenant architecture with tenant isolation, API keys, and per-tenant Granicus connections.',
        'Stripe billing with Starter, Pro, and Enterprise plans including Checkout Sessions and usage metering.',
        'Per-tenant branding with configurable logo, colors, CSS overrides, and welcome message.',
        'Tenant management CLI for creating, listing, deleting tenants, and rotating keys.',
        'Admin API for programmatic tenant CRUD.',
        'Self-service onboarding flow for new tenant signup.',
      ],
      Changed: [
        'Pipeline now accepts --tenant-id flag for tenant-scoped processing.',
        'ChromaDB collections namespaced by tenant ID for vector isolation.',
        'Frontend dynamically loads and applies tenant-specific branding.',
      ],
      Fixed: [
        'Fixed pipeline crash on empty Granicus RSS items for canceled meetings.',
        'Fixed rate limiter window reset at month boundaries for mid-month tenants.',
      ],
      Security: [
        'Tenant API keys introduced as mra_-prefixed secrets with rotation support.',
        'Admin endpoints require separate ADMIN_API_KEY.',
        'Tenant data is scoped by tenant ID across the API and ingestion paths.',
      ],
    },
  },
  {
    version: 'v1.0.0',
    date: '2025-06-01',
    label: 'Initial Release',
    sections: {
      Added: [
        'RAG-powered Q&A using ChromaDB with text-embedding-3-small and GPT-4o synthesis with citations.',
        'Multi-turn chat with conversation history and context-aware follow-ups.',
        'Meeting transcription pipeline using OpenAI Whisper with segment timestamps for video deep-linking.',
        'Two-pass summary system: GPT-4o structured extraction + Claude Sonnet narrative generation.',
        'Vote tracking with roll call extraction and per-member voting history.',
        'Topic extraction (GPT-4o-mini) generating 3-8 topics per meeting.',
        'Agenda and minutes download with PDF extraction and OCR fallback.',
        'Frontend SPA (React 18 + Vite) with meeting browser, FlexSearch, and responsive design.',
        'AWS Lambda handler for scheduled meeting sync via EventBridge.',
        'Docker support with multi-stage build.',
      ],
      Security: [
        'CORS middleware with configurable allowed origins.',
        'Input sanitization on all user-facing query parameters.',
        'Internal error details stripped in production mode.',
      ],
    },
  },
]

const SECTION_CONFIG = {
  Added: { color: '#34c759', bg: '#ecfdf5', icon: '+' },
  Changed: { color: '#3366cc', bg: '#eaf0fa', icon: '~' },
  Fixed: { color: '#f59e0b', bg: '#fffbeb', icon: '!' },
  Security: { color: '#8b5cf6', bg: '#f5f3ff', icon: '#' },
}

function SectionBadge({ type }) {
  const config = SECTION_CONFIG[type] || { color: '#6b7280', bg: '#f3f4f6', icon: '?' }
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '2px 10px',
        borderRadius: 'var(--radius-full)',
        fontSize: '0.75rem',
        fontWeight: 600,
        color: config.color,
        backgroundColor: config.bg,
        letterSpacing: '0.02em',
      }}
    >
      <span style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{config.icon}</span>
      {type}
    </span>
  )
}

function VersionBadge({ version, label }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
      }}
    >
      <span
        style={{
          padding: '4px 14px',
          borderRadius: 'var(--radius-full)',
          fontSize: '0.95rem',
          fontWeight: 700,
          color: '#fff',
          background: 'linear-gradient(135deg, var(--blue-700), var(--blue-500))',
          fontFamily: 'monospace',
          letterSpacing: '0.03em',
        }}
      >
        {version}
      </span>
      {label && (
        <span
          style={{
            padding: '2px 8px',
            borderRadius: 'var(--radius-full)',
            fontSize: '0.7rem',
            fontWeight: 600,
            color: '#fff',
            backgroundColor: '#34c759',
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
          }}
        >
          {label}
        </span>
      )}
    </span>
  )
}

function ReleaseEntry({ release, isLast }) {
  const [expanded, setExpanded] = useState(true)
  const formattedDate = new Date(release.date + 'T00:00:00').toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })

  return (
    <div style={{ display: 'flex', gap: '24px', position: 'relative' }}>
      {/* Timeline spine */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          flexShrink: 0,
          width: '20px',
        }}
      >
        <div
          style={{
            width: '12px',
            height: '12px',
            borderRadius: '50%',
            backgroundColor: 'var(--blue-700)',
            border: '3px solid var(--blue-100)',
            flexShrink: 0,
            marginTop: '6px',
          }}
        />
        {!isLast && (
          <div
            style={{
              width: '2px',
              flex: 1,
              backgroundColor: 'var(--border)',
              marginTop: '4px',
            }}
          />
        )}
      </div>

      {/* Content */}
      <div
        style={{
          flex: 1,
          paddingBottom: isLast ? 0 : '32px',
        }}
      >
        <button
          onClick={() => setExpanded(!expanded)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '12px',
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            padding: 0,
            width: '100%',
            textAlign: 'left',
          }}
          aria-expanded={expanded}
          aria-label={`${release.version} released ${formattedDate}. Click to ${expanded ? 'collapse' : 'expand'}.`}
        >
          <VersionBadge version={release.version} label={release.label} />
          <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            {formattedDate}
          </span>
          <span
            style={{
              marginLeft: 'auto',
              color: 'var(--text-tertiary)',
              fontSize: '0.85rem',
              transition: 'transform var(--transition)',
              transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)',
              display: 'inline-block',
            }}
          >
            &#9660;
          </span>
        </button>

        {expanded && (
          <div
            style={{
              marginTop: '16px',
              background: 'var(--surface)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              boxShadow: 'var(--shadow-sm)',
              overflow: 'hidden',
            }}
          >
            {Object.entries(release.sections).map(([sectionName, items], idx) => (
              <div
                key={sectionName}
                style={{
                  padding: '16px 20px',
                  borderTop: idx > 0 ? '1px solid var(--border-light)' : 'none',
                }}
              >
                <div style={{ marginBottom: '10px' }}>
                  <SectionBadge type={sectionName} />
                </div>
                <ul
                  style={{
                    listStyle: 'none',
                    padding: 0,
                    margin: 0,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                  }}
                >
                  {items.map((item, i) => (
                    <li
                      key={i}
                      style={{
                        fontSize: '0.9rem',
                        lineHeight: 1.6,
                        color: 'var(--text-primary)',
                        paddingLeft: '16px',
                        position: 'relative',
                      }}
                    >
                      <span
                        style={{
                          position: 'absolute',
                          left: 0,
                          color: 'var(--text-tertiary)',
                        }}
                      >
                        &bull;
                      </span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Changelog() {
  const [filter, setFilter] = useState('all')

  const sectionTypes = ['all', 'Added', 'Changed', 'Fixed', 'Security']

  const filteredData =
    filter === 'all'
      ? CHANGELOG_DATA
      : CHANGELOG_DATA.map((release) => {
          const filtered = {}
          if (release.sections[filter]) {
            filtered[filter] = release.sections[filter]
          }
          return { ...release, sections: filtered }
        }).filter((release) => Object.keys(release.sections).length > 0)

  return (
    <div className="container" style={{ paddingTop: '32px', paddingBottom: '48px' }}>
      <div style={{ marginBottom: '28px' }}>
        <h2
          style={{
            fontSize: '1.5rem',
            fontWeight: 700,
            color: 'var(--text-primary)',
            marginBottom: '6px',
          }}
        >
          API Changelog
        </h2>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
          A history of updates, improvements, and fixes to the CivicLens platform.
        </p>
      </div>

      {/* Filter bar */}
      <div
        style={{
          display: 'flex',
          gap: '8px',
          marginBottom: '28px',
          flexWrap: 'wrap',
        }}
        role="toolbar"
        aria-label="Filter changelog by change type"
      >
        {sectionTypes.map((type) => {
          const isActive = filter === type
          return (
            <button
              key={type}
              onClick={() => setFilter(type)}
              aria-pressed={isActive}
              style={{
                padding: '6px 16px',
                borderRadius: 'var(--radius-full)',
                border: '1px solid ' + (isActive ? 'var(--blue-700)' : 'var(--border)'),
                background: isActive ? 'var(--blue-700)' : 'var(--surface)',
                color: isActive ? '#fff' : 'var(--text-secondary)',
                fontSize: '0.85rem',
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all var(--transition)',
              }}
            >
              {type === 'all' ? 'All Changes' : type}
            </button>
          )
        })}
      </div>

      {/* Timeline */}
      <div>
        {filteredData.map((release, idx) => (
          <ReleaseEntry
            key={release.version}
            release={release}
            isLast={idx === filteredData.length - 1}
          />
        ))}
      </div>

      {filteredData.length === 0 && (
        <div
          style={{
            textAlign: 'center',
            padding: '48px 0',
            color: 'var(--text-tertiary)',
          }}
        >
          No entries match the selected filter.
        </div>
      )}
    </div>
  )
}
