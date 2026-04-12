import { useState, useEffect, useMemo } from 'react'
import { useI18n } from '../i18n/I18nProvider'

const API_BASE = import.meta.env.VITE_API_URL || ''

function getApiKey() {
  return sessionStorage.getItem('api_key') || ''
}

async function apiFetch(path, params = {}) {
  const qs = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v))
  ).toString()
  const url = `${API_BASE}/api/v1/sentiment/${path}${qs ? '?' + qs : ''}`
  const res = await fetch(url, { headers: { 'X-API-Key': getApiKey() } })
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

/* ------------------------------------------------------------------ */
/* Sentiment bar: stacked green / red / gray                          */
/* ------------------------------------------------------------------ */
function SentimentBar({ positive = 0, negative = 0, neutral = 0, mixed = 0 }) {
  const total = positive + negative + neutral + mixed || 1
  const pPct = (positive / total) * 100
  const nPct = (negative / total) * 100
  const mPct = (mixed / total) * 100

  return (
    <div className="sentiment-bar-track">
      <div className="sentiment-bar-pos" style={{ width: `${pPct}%` }} title={`Positive: ${positive}`} />
      <div className="sentiment-bar-neg" style={{ width: `${nPct}%` }} title={`Negative: ${negative}`} />
      <div className="sentiment-bar-mix" style={{ width: `${mPct}%` }} title={`Mixed: ${mixed}`} />
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Mini SVG trend chart                                               */
/* ------------------------------------------------------------------ */
function TrendChart({ data }) {
  if (!data || data.length < 2) return <p className="sd-empty">Not enough data for trend chart.</p>

  const W = 480
  const H = 140
  const PAD = 24
  const plotW = W - PAD * 2
  const plotH = H - PAD * 2

  const xStep = plotW / (data.length - 1)
  const vals = data.map(d => d.avg_sentiment)
  const minV = Math.min(...vals, -1)
  const maxV = Math.max(...vals, 1)
  const range = maxV - minV || 1

  const points = data.map((d, i) => {
    const x = PAD + i * xStep
    const y = PAD + plotH - ((d.avg_sentiment - minV) / range) * plotH
    return { x, y, ...d }
  })

  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const zeroY = PAD + plotH - ((0 - minV) / range) * plotH

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="sd-trend-svg">
      {/* zero line */}
      <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="var(--border)" strokeDasharray="4 4" />
      <text x={PAD - 4} y={zeroY + 3} textAnchor="end" fontSize="9" fill="var(--text-tertiary)">0</text>

      {/* gradient area */}
      <defs>
        <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--blue-500)" stopOpacity="0.25" />
          <stop offset="100%" stopColor="var(--blue-500)" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <path
        d={`${line} L${points[points.length - 1].x},${PAD + plotH} L${points[0].x},${PAD + plotH} Z`}
        fill="url(#areaGrad)"
      />

      {/* trend line */}
      <path d={line} fill="none" stroke="var(--blue-500)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />

      {/* data points */}
      {points.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r="4" fill="var(--surface)" stroke="var(--blue-500)" strokeWidth="2" />
          <title>{p.date}: {p.avg_sentiment.toFixed(2)} ({p.comment_count} comments)</title>
          {/* date labels for first, last, and midpoint */}
          {(i === 0 || i === points.length - 1 || i === Math.floor(points.length / 2)) && (
            <text x={p.x} y={H - 4} textAnchor="middle" fontSize="8" fill="var(--text-tertiary)">
              {p.date.slice(5)}
            </text>
          )}
        </g>
      ))}
    </svg>
  )
}

/* ------------------------------------------------------------------ */
/* Topic card                                                         */
/* ------------------------------------------------------------------ */
function TopicCard({ topic, onSelect }) {
  const score = topic.avg_sentiment
  const label = score > 0.25 ? 'Positive' : score < -0.25 ? 'Negative' : 'Mixed/Neutral'
  const cls = score > 0.25 ? 'sd-score-pos' : score < -0.25 ? 'sd-score-neg' : 'sd-score-neutral'

  return (
    <div className="sd-topic-card" onClick={() => onSelect(topic.topic)} role="button" tabIndex={0}>
      <div className="sd-topic-header">
        <span className="sd-topic-name">{topic.topic}</span>
        <span className={`sd-score-badge ${cls}`}>{label} ({score.toFixed(2)})</span>
      </div>
      <SentimentBar
        positive={topic.positive}
        negative={topic.negative}
        neutral={topic.neutral}
        mixed={topic.mixed}
      />
      <div className="sd-topic-count">{topic.comment_count} comment{topic.comment_count !== 1 ? 's' : ''}</div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Controversial item                                                 */
/* ------------------------------------------------------------------ */
function ControversialItem({ item }) {
  return (
    <div className="sd-controversial-item">
      <div className="sd-controversial-header">
        <span className="sd-controversial-topic">{item.topic}</span>
        <span className="sd-controversy-score" title="Controversy score">{item.controversy_score.toFixed(1)}</span>
      </div>
      <SentimentBar positive={item.positive} negative={item.negative} neutral={item.neutral} mixed={item.mixed} />
      <div className="sd-controversial-stats">
        {item.positive} for / {item.negative} against / {item.comment_count} total
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Main dashboard component                                           */
/* ------------------------------------------------------------------ */
export default function SentimentDashboard() {
  const { t } = useI18n()

  const [dashboard, setDashboard] = useState(null)
  const [topics, setTopics] = useState([])
  const [controversial, setControversial] = useState([])
  const [selectedTopic, setSelectedTopic] = useState(null)
  const [trend, setTrend] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Filters
  const [meetingBody, setMeetingBody] = useState('')
  const [dateAfter, setDateAfter] = useState('')
  const [dateBefore, setDateBefore] = useState('')

  const filters = useMemo(() => ({
    meeting_body: meetingBody,
    date_after: dateAfter,
    date_before: dateBefore,
  }), [meetingBody, dateAfter, dateBefore])

  // Load dashboard + topics + controversial
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    Promise.all([
      apiFetch('dashboard', filters),
      apiFetch('topics', { ...filters, limit: '20' }),
      apiFetch('controversial', { ...filters, limit: '5' }),
    ])
      .then(([dash, top, cont]) => {
        if (cancelled) return
        setDashboard(dash)
        setTopics(top.topics || [])
        setControversial(cont.controversial || [])
      })
      .catch(err => { if (!cancelled) setError(err.message) })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [filters])

  // Load trend when topic selected
  useEffect(() => {
    if (!selectedTopic) { setTrend(null); return }
    apiFetch(`topics/${encodeURIComponent(selectedTopic)}`, filters)
      .then(data => setTrend(data.trend || []))
      .catch(() => setTrend(null))
  }, [selectedTopic, filters])

  const overview = dashboard?.overview

  if (error) {
    return (
      <div className="container sd-container">
        <div className="sd-error">Failed to load sentiment data: {error}</div>
      </div>
    )
  }

  return (
    <div className="container sd-container">
      <h2 className="sd-page-title">Public Comment Sentiment</h2>
      <p className="sd-page-subtitle">AI analysis of public comments across all council meetings.</p>

      {/* Filter bar */}
      <div className="sd-filter-bar">
        <input
          type="text"
          placeholder="Meeting body..."
          value={meetingBody}
          onChange={e => setMeetingBody(e.target.value)}
          className="sd-filter-input"
        />
        <input type="date" value={dateAfter} onChange={e => setDateAfter(e.target.value)} className="sd-filter-input" />
        <input type="date" value={dateBefore} onChange={e => setDateBefore(e.target.value)} className="sd-filter-input" />
      </div>

      {loading ? (
        <div className="loading"><div className="spinner" /><p>Loading sentiment data...</p></div>
      ) : (
        <>
          {/* Overview cards */}
          {overview && (
            <div className="sd-overview-grid">
              <div className="sd-overview-card">
                <div className="sd-overview-value">{overview.total_comments}</div>
                <div className="sd-overview-label">Comments Analyzed</div>
              </div>
              <div className="sd-overview-card">
                <div className="sd-overview-value">{overview.meetings_analyzed}</div>
                <div className="sd-overview-label">Meetings</div>
              </div>
              <div className="sd-overview-card">
                <div className="sd-overview-value">{overview.unique_speakers}</div>
                <div className="sd-overview-label">Speakers</div>
              </div>
              <div className="sd-overview-card">
                <div className="sd-overview-value">{overview.unique_topics}</div>
                <div className="sd-overview-label">Topics</div>
              </div>
              <div className="sd-overview-card sd-sentiment-split">
                <div className="sd-split-row">
                  <span className="sd-split-pos">{Math.round(overview.positive_ratio * 100)}% positive</span>
                  <span className="sd-split-neg">{Math.round(overview.negative_ratio * 100)}% negative</span>
                  <span className="sd-split-neutral">{Math.round(overview.neutral_ratio * 100)}% neutral</span>
                </div>
                <SentimentBar
                  positive={overview.positive}
                  negative={overview.negative}
                  neutral={overview.neutral}
                  mixed={overview.mixed}
                />
              </div>
            </div>
          )}

          {/* Trend chart for selected topic */}
          {selectedTopic && (
            <div className="sd-trend-section">
              <div className="sd-trend-header">
                <h3>Trend: {selectedTopic}</h3>
                <button className="sd-trend-close" onClick={() => setSelectedTopic(null)}>Close</button>
              </div>
              {trend && trend.length > 0 ? <TrendChart data={trend} /> : <p className="sd-empty">No trend data available.</p>}
            </div>
          )}

          {/* Topics grid */}
          <div className="sd-section">
            <h3 className="sd-section-title">Top Topics</h3>
            {topics.length === 0 ? (
              <p className="sd-empty">No topics found for the current filters.</p>
            ) : (
              <div className="sd-topics-grid">
                {topics.map(t => (
                  <TopicCard key={t.topic} topic={t} onSelect={setSelectedTopic} />
                ))}
              </div>
            )}
          </div>

          {/* Controversial topics */}
          {controversial.length > 0 && (
            <div className="sd-section">
              <h3 className="sd-section-title">Most Controversial</h3>
              <p className="sd-section-desc">Topics where public opinion is most divided.</p>
              <div className="sd-controversial-list">
                {controversial.map(c => (
                  <ControversialItem key={c.topic} item={c} />
                ))}
              </div>
            </div>
          )}

          {/* Hot issues */}
          {dashboard?.hot_issues?.length > 0 && (
            <div className="sd-section">
              <h3 className="sd-section-title">Hot Issues</h3>
              <p className="sd-section-desc">Topics with the strongest negative sentiment.</p>
              <div className="sd-hot-list">
                {dashboard.hot_issues.map(h => (
                  <div key={h.topic} className="sd-hot-item">
                    <span className="sd-hot-topic">{h.topic}</span>
                    <span className="sd-hot-score">{h.avg_sentiment.toFixed(2)}</span>
                    <span className="sd-hot-count">{h.count} comments</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
