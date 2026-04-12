import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getApiKey() {
  return sessionStorage.getItem('api_key') || sessionStorage.getItem('admin_api_key') || ''
}

async function apiFetch(path) {
  const key = getApiKey()
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': key,
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TYPE_CONFIG = {
  contentious_vote: { label: 'Contentious Vote', icon: 'fire', color: '#ff3b30' },
  unanimous_vote:   { label: 'Unanimous Vote',   icon: 'check', color: '#34c759' },
  financial:        { label: 'Financial',         icon: 'dollar', color: '#ff9500' },
  public_comment:   { label: 'Public Comment',    icon: 'mic', color: '#5856d6' },
  first_reading:    { label: 'First Reading',     icon: 'book', color: '#007aff' },
  appointment:      { label: 'Appointment',       icon: 'person', color: '#30b0c7' },
  recognition:      { label: 'Recognition',       icon: 'star', color: '#ffcc00' },
  agenda_item:      { label: 'Agenda Item',       icon: 'list', color: '#8e8e93' },
}

const TABS = [
  { id: 'recent', label: 'Recent' },
  { id: 'trending', label: 'Trending' },
  { id: 'by_type', label: 'By Type' },
]

// ---------------------------------------------------------------------------
// Type icon SVGs (inline, small)
// ---------------------------------------------------------------------------

function TypeIcon({ type, size = 18 }) {
  const cfg = TYPE_CONFIG[type] || TYPE_CONFIG.agenda_item
  const color = cfg.color

  const icons = {
    fire: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" />
      </svg>
    ),
    check: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12" />
      </svg>
    ),
    dollar: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="12" y1="1" x2="12" y2="23" />
        <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
      </svg>
    ),
    mic: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="23" />
        <line x1="8" y1="23" x2="16" y2="23" />
      </svg>
    ),
    book: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
      </svg>
    ),
    person: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </svg>
    ),
    star: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill={color} stroke={color} strokeWidth="1">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
      </svg>
    ),
    list: (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round">
        <line x1="8" y1="6" x2="21" y2="6" />
        <line x1="8" y1="12" x2="21" y2="12" />
        <line x1="8" y1="18" x2="21" y2="18" />
        <line x1="3" y1="6" x2="3.01" y2="6" />
        <line x1="3" y1="12" x2="3.01" y2="12" />
        <line x1="3" y1="18" x2="3.01" y2="18" />
      </svg>
    ),
  }

  return (
    <span className="hl-type-icon" title={cfg.label}>
      {icons[cfg.icon] || icons.list}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Importance badge
// ---------------------------------------------------------------------------

function ImportanceBadge({ score }) {
  let cls = 'hl-importance'
  if (score >= 8) cls += ' hl-importance-high'
  else if (score >= 5) cls += ' hl-importance-mid'
  else cls += ' hl-importance-low'

  return <span className={cls}>{score}/10</span>
}

// ---------------------------------------------------------------------------
// Format timestamp
// ---------------------------------------------------------------------------

function formatTimestamp(seconds) {
  if (seconds == null) return null
  const s = Math.round(seconds)
  const mins = Math.floor(s / 60)
  const secs = s % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function granicusUrl(clipId, seconds) {
  const host = import.meta.env.VITE_GRANICUS_HOST || 'elkgrove.granicus.com'
  const viewId = import.meta.env.VITE_GRANICUS_VIEW_ID || '14'
  return `https://${host}/player/clip/${clipId}?view_id=${viewId}&entrytime=${Math.round(seconds || 0)}`
}

// ---------------------------------------------------------------------------
// Share button
// ---------------------------------------------------------------------------

function ShareButton({ clipId, startTime }) {
  const [copied, setCopied] = useState(false)

  const handleShare = useCallback(() => {
    const url = granicusUrl(clipId, startTime)
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {
      // Fallback
      window.prompt('Copy this URL:', url)
    })
  }, [clipId, startTime])

  return (
    <button
      className="hl-share-btn"
      onClick={handleShare}
      title="Copy video link with timestamp"
    >
      {copied ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" />
          <polyline points="16 6 12 2 8 6" />
          <line x1="12" y1="2" x2="12" y2="15" />
        </svg>
      )}
      {copied ? 'Copied' : 'Share'}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Highlight card
// ---------------------------------------------------------------------------

function HighlightCard({ highlight, showMeeting = true }) {
  const cfg = TYPE_CONFIG[highlight.type] || TYPE_CONFIG.agenda_item
  const ts = formatTimestamp(highlight.start_time)

  return (
    <div className="hl-card">
      <div className="hl-card-left">
        <div className="hl-card-icon-wrap" style={{ backgroundColor: cfg.color + '18' }}>
          <TypeIcon type={highlight.type} />
        </div>
      </div>
      <div className="hl-card-body">
        <div className="hl-card-header">
          <span className="hl-card-type-label" style={{ color: cfg.color }}>{cfg.label}</span>
          <ImportanceBadge score={highlight.importance_score} />
        </div>
        <h3 className="hl-card-title">{highlight.title}</h3>
        {highlight.description && (
          <p className="hl-card-desc">{highlight.description}</p>
        )}
        <div className="hl-card-footer">
          {showMeeting && (
            <Link to={`/meeting/${highlight.clip_id}`} className="hl-card-meeting">
              {highlight.meeting_title || `Clip ${highlight.clip_id}`}
            </Link>
          )}
          {showMeeting && highlight.meeting_date && (
            <span className="hl-card-date">{highlight.meeting_date}</span>
          )}
          {highlight.meeting_body && (
            <span className="hl-card-body-tag">{highlight.meeting_body}</span>
          )}
          {ts && (
            <a
              href={granicusUrl(highlight.clip_id, highlight.start_time)}
              target="_blank"
              rel="noopener noreferrer"
              className="hl-timestamp-link"
            >
              {ts}
            </a>
          )}
          <ShareButton clipId={highlight.clip_id} startTime={highlight.start_time} />
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Timeline view for a single meeting
// ---------------------------------------------------------------------------

function TimelineView({ highlights }) {
  if (!highlights.length) {
    return <div className="hl-empty">No highlights for this meeting.</div>
  }

  // Sort by start_time (nulls last), then importance
  const sorted = [...highlights].sort((a, b) => {
    if (a.start_time == null && b.start_time == null) return b.importance_score - a.importance_score
    if (a.start_time == null) return 1
    if (b.start_time == null) return -1
    return a.start_time - b.start_time
  })

  return (
    <div className="hl-timeline">
      {sorted.map((h, i) => (
        <div key={h.id} className="hl-timeline-item">
          <div className="hl-timeline-marker">
            <div className="hl-timeline-dot" style={{
              backgroundColor: (TYPE_CONFIG[h.type] || TYPE_CONFIG.agenda_item).color
            }} />
            {i < sorted.length - 1 && <div className="hl-timeline-line" />}
          </div>
          <div className="hl-timeline-content">
            <HighlightCard highlight={h} showMeeting={false} />
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Type filter buttons
// ---------------------------------------------------------------------------

function TypeFilter({ types, counts, activeType, onSelect }) {
  return (
    <div className="hl-type-filters">
      <button
        className={`hl-type-filter-btn ${!activeType ? 'hl-type-filter-active' : ''}`}
        onClick={() => onSelect(null)}
      >
        All
      </button>
      {types.map((t) => {
        const cfg = TYPE_CONFIG[t] || TYPE_CONFIG.agenda_item
        const count = counts[t] || 0
        return (
          <button
            key={t}
            className={`hl-type-filter-btn ${activeType === t ? 'hl-type-filter-active' : ''}`}
            onClick={() => onSelect(t)}
            style={activeType === t ? { borderColor: cfg.color, color: cfg.color } : {}}
          >
            <TypeIcon type={t} size={14} />
            {cfg.label}
            {count > 0 && <span className="hl-type-count">{count}</span>}
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Highlights page
// ---------------------------------------------------------------------------

export default function Highlights() {
  const [activeTab, setActiveTab] = useState('recent')
  const [highlights, setHighlights] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Type filter state
  const [types, setTypes] = useState([])
  const [typeCounts, setTypeCounts] = useState({})
  const [activeType, setActiveType] = useState(null)

  // Trending controls
  const [trendingDays, setTrendingDays] = useState(30)

  // Load type metadata
  useEffect(() => {
    apiFetch('/api/v1/highlights/types')
      .then((data) => {
        setTypes(data.types || [])
        setTypeCounts(data.counts || {})
      })
      .catch(() => {
        // Non-critical
      })
  }, [])

  // Fetch highlights when tab/filter changes
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    let url
    if (activeTab === 'recent') {
      url = '/api/v1/highlights/recent?limit=30'
    } else if (activeTab === 'trending') {
      url = `/api/v1/highlights/trending?days=${trendingDays}&limit=30`
    } else if (activeTab === 'by_type' && activeType) {
      url = `/api/v1/highlights/type/${activeType}?limit=50`
    } else if (activeTab === 'by_type') {
      url = '/api/v1/highlights/recent?limit=50'
    } else {
      url = '/api/v1/highlights/recent?limit=30'
    }

    apiFetch(url)
      .then((data) => {
        if (!cancelled) {
          setHighlights(data.highlights || [])
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to load highlights')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => { cancelled = true }
  }, [activeTab, activeType, trendingDays])

  return (
    <div className="container hl-container">
      <div className="hl-page-header">
        <h2 className="hl-page-title">This Week in Council</h2>
        <p className="hl-page-subtitle">
          Key moments, contentious votes, and notable decisions from recent meetings
        </p>
      </div>

      {/* Tabs */}
      <div className="hl-tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`hl-tab ${activeTab === tab.id ? 'hl-tab-active' : ''}`}
            onClick={() => {
              setActiveTab(tab.id)
              if (tab.id !== 'by_type') setActiveType(null)
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Trending time range selector */}
      {activeTab === 'trending' && (
        <div className="hl-trending-controls">
          {[7, 14, 30, 90].map((d) => (
            <button
              key={d}
              className={`hl-range-btn ${trendingDays === d ? 'hl-range-active' : ''}`}
              onClick={() => setTrendingDays(d)}
            >
              {d}d
            </button>
          ))}
        </div>
      )}

      {/* Type filter (shown on By Type tab) */}
      {activeTab === 'by_type' && (
        <TypeFilter
          types={types}
          counts={typeCounts}
          activeType={activeType}
          onSelect={setActiveType}
        />
      )}

      {/* Loading */}
      {loading && (
        <div className="hl-loading">
          <div className="ask-spinner" />
          <p>Loading highlights...</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="hl-error">
          <p>{error}</p>
        </div>
      )}

      {/* Highlight cards */}
      {!loading && !error && highlights.length === 0 && (
        <div className="hl-empty">
          <p>No highlights found. Process meetings with <code>--upgrade-summaries</code> to generate highlights from extracted facts.</p>
        </div>
      )}

      {!loading && !error && highlights.length > 0 && (
        <div className="hl-cards-list">
          {highlights.map((h) => (
            <HighlightCard key={h.id} highlight={h} />
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Export the timeline sub-component for use in MeetingDetail
// ---------------------------------------------------------------------------

export { TimelineView, HighlightCard, TypeIcon, ImportanceBadge }
