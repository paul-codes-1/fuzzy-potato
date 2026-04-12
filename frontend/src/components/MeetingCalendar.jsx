import { useState, useEffect, useCallback, useMemo } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getApiKey() {
  return sessionStorage.getItem('api_key') || sessionStorage.getItem('admin_api_key') || ''
}

async function apiFetch(path, options = {}) {
  const key = getApiKey()
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': key,
      ...(options.headers || {}),
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${res.status})`)
  }
  return res.json()
}

// ---------------------------------------------------------------------------
// Color mapping for meeting bodies
// ---------------------------------------------------------------------------

const BODY_COLORS = [
  '#0033A0', '#b45309', '#0f766e', '#7c3aed', '#be185d',
  '#15803d', '#c2410c', '#1d4ed8', '#9333ea', '#dc2626',
]

function bodyColor(body, bodies) {
  if (!body) return '#6b7280'
  const idx = bodies.indexOf(body)
  return BODY_COLORS[idx >= 0 ? idx % BODY_COLORS.length : 0]
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function daysInMonth(year, month) {
  return new Date(year, month + 1, 0).getDate()
}

function startDayOfMonth(year, month) {
  return new Date(year, month, 1).getDay()
}

function formatDate(dateStr) {
  try {
    const d = new Date(dateStr + 'T00:00:00')
    return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

function monthName(month) {
  return new Date(2000, month, 1).toLocaleDateString('en-US', { month: 'long' })
}

// ---------------------------------------------------------------------------
// Calendar Grid
// ---------------------------------------------------------------------------

function CalendarGrid({ year, month, byDate, bodies, selectedDate, onSelectDate }) {
  const totalDays = daysInMonth(year, month)
  const startDay = startDayOfMonth(year, month)
  const today = new Date()
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`

  const weeks = []
  let currentWeek = new Array(startDay).fill(null)

  for (let day = 1; day <= totalDays; day++) {
    currentWeek.push(day)
    if (currentWeek.length === 7) {
      weeks.push(currentWeek)
      currentWeek = []
    }
  }
  if (currentWeek.length > 0) {
    while (currentWeek.length < 7) currentWeek.push(null)
    weeks.push(currentWeek)
  }

  return (
    <div className="cal-grid">
      <div className="cal-weekdays">
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => (
          <div key={d} className="cal-weekday">{d}</div>
        ))}
      </div>
      {weeks.map((week, wi) => (
        <div key={wi} className="cal-week">
          {week.map((day, di) => {
            if (day === null) {
              return <div key={di} className="cal-day cal-day-empty" />
            }

            const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
            const meetings = byDate[dateStr] || []
            const isToday = dateStr === todayStr
            const isSelected = dateStr === selectedDate
            const hasMeetings = meetings.length > 0

            return (
              <div
                key={di}
                className={[
                  'cal-day',
                  isToday && 'cal-day-today',
                  isSelected && 'cal-day-selected',
                  hasMeetings && 'cal-day-has-meetings',
                ].filter(Boolean).join(' ')}
                onClick={() => hasMeetings && onSelectDate(dateStr)}
                role={hasMeetings ? 'button' : undefined}
                tabIndex={hasMeetings ? 0 : undefined}
                onKeyDown={(e) => { if (hasMeetings && (e.key === 'Enter' || e.key === ' ')) onSelectDate(dateStr) }}
              >
                <span className="cal-day-number">{day}</span>
                {hasMeetings && (
                  <div className="cal-day-dots">
                    {meetings.slice(0, 3).map((m, mi) => (
                      <span
                        key={mi}
                        className="cal-dot"
                        style={{ backgroundColor: bodyColor(m.meeting_body, bodies) }}
                        title={m.meeting_body || 'Meeting'}
                      />
                    ))}
                    {meetings.length > 3 && (
                      <span className="cal-dot-more">+{meetings.length - 3}</span>
                    )}
                  </div>
                )}
                {meetings.some(m => m.source === 'predicted') && (
                  <span className="cal-predicted-marker" title="Predicted meeting" />
                )}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Day Detail Panel
// ---------------------------------------------------------------------------

function DayDetail({ dateStr, meetings, bodies, onGoogleUrl, onDownloadIcal }) {
  if (!dateStr || !meetings || meetings.length === 0) {
    return null
  }

  return (
    <div className="cal-day-detail">
      <h3 className="cal-detail-date">{formatDate(dateStr)}</h3>
      <div className="cal-detail-meetings">
        {meetings.map((m, i) => (
          <div key={i} className="cal-detail-meeting">
            <div className="cal-detail-header">
              <span
                className="cal-body-badge"
                style={{ backgroundColor: bodyColor(m.meeting_body, bodies), color: '#fff' }}
              >
                {m.meeting_body || 'Meeting'}
              </span>
              {m.source === 'predicted' && (
                <span className="cal-predicted-badge">Predicted</span>
              )}
            </div>
            {m.title && <div className="cal-detail-title">{m.title}</div>}
            {m.time && <div className="cal-detail-time">{m.time}</div>}
            <div className="cal-detail-actions">
              {m.url && (
                <a href={m.url} target="_blank" rel="noopener noreferrer" className="cal-action-btn">
                  Watch Video
                </a>
              )}
              {m.meeting_body && (
                <button
                  className="cal-action-btn cal-action-secondary"
                  onClick={() => onGoogleUrl(m.meeting_body)}
                >
                  + Google Calendar
                </button>
              )}
              {m.meeting_body && (
                <button
                  className="cal-action-btn cal-action-secondary"
                  onClick={() => onDownloadIcal(m.meeting_body)}
                >
                  + Apple / Outlook
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Schedule Profiles Panel
// ---------------------------------------------------------------------------

function ScheduleProfiles({ schedules }) {
  if (!schedules || schedules.length === 0) return null

  return (
    <div className="cal-schedules">
      <h3 className="cal-section-title">Detected Schedules</h3>
      <div className="cal-schedule-list">
        {schedules.map((s, i) => (
          <div key={i} className="cal-schedule-card">
            <div className="cal-schedule-body">{s.meeting_body}</div>
            <div className="cal-schedule-desc">{s.description}</div>
            <div className="cal-schedule-meta">
              <span>{s.meeting_count} meetings on record</span>
              {s.confidence > 0 && (
                <span className="cal-confidence">
                  {Math.round(s.confidence * 100)}% confidence
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Upcoming Meetings Sidebar
// ---------------------------------------------------------------------------

function UpcomingSidebar({ meetings, bodies, onGoogleUrl, onDownloadIcal }) {
  const today = new Date().toISOString().slice(0, 10)
  const upcoming = meetings
    .filter((m) => m.date >= today)
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(0, 10)

  if (upcoming.length === 0) {
    return (
      <div className="cal-upcoming">
        <h3 className="cal-section-title">Upcoming Meetings</h3>
        <div className="cal-upcoming-empty">No upcoming meetings predicted</div>
      </div>
    )
  }

  return (
    <div className="cal-upcoming">
      <h3 className="cal-section-title">Upcoming Meetings</h3>
      <div className="cal-upcoming-list">
        {upcoming.map((m, i) => (
          <div key={i} className="cal-upcoming-item">
            <div className="cal-upcoming-date">{formatDate(m.date)}</div>
            <div className="cal-upcoming-info">
              <span
                className="cal-body-chip"
                style={{ borderColor: bodyColor(m.meeting_body, bodies) }}
              >
                {m.meeting_body || 'Meeting'}
              </span>
              {m.source === 'predicted' && (
                <span className="cal-predicted-tag">predicted</span>
              )}
            </div>
            {m.time && <div className="cal-upcoming-time">{m.time}</div>}
            <div className="cal-upcoming-actions">
              {m.meeting_body && (
                <>
                  <button
                    className="cal-action-link"
                    onClick={() => onGoogleUrl(m.meeting_body)}
                    title="Add to Google Calendar"
                  >
                    Google
                  </button>
                  <button
                    className="cal-action-link"
                    onClick={() => onDownloadIcal(m.meeting_body)}
                    title="Download .ics file"
                  >
                    iCal
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Subscribe Form
// ---------------------------------------------------------------------------

function SubscribeForm({ bodies }) {
  const [email, setEmail] = useState('')
  const [body, setBody] = useState('')
  const [daysBefore, setDaysBefore] = useState(1)
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubscribe = async (e) => {
    e.preventDefault()
    setLoading(true)
    setStatus(null)
    try {
      const payload = { email, days_before: daysBefore }
      if (body) payload.meeting_body = body
      await apiFetch('/api/v1/calendar/subscribe', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setStatus({ type: 'success', message: 'Subscribed! You will receive meeting reminders.' })
      setEmail('')
    } catch (err) {
      setStatus({ type: 'error', message: err.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="cal-subscribe">
      <h3 className="cal-section-title">Meeting Reminders</h3>
      <p className="cal-subscribe-desc">Get notified before upcoming meetings.</p>
      <form className="cal-subscribe-form" onSubmit={handleSubscribe}>
        <input
          type="email"
          className="cal-subscribe-input"
          placeholder="your@email.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <select
          className="cal-subscribe-select"
          value={body}
          onChange={(e) => setBody(e.target.value)}
        >
          <option value="">All meeting bodies</option>
          {bodies.map((b) => (
            <option key={b} value={b}>{b}</option>
          ))}
        </select>
        <select
          className="cal-subscribe-select"
          value={daysBefore}
          onChange={(e) => setDaysBefore(parseInt(e.target.value, 10))}
        >
          <option value={1}>1 day before</option>
          <option value={2}>2 days before</option>
          <option value={3}>3 days before</option>
          <option value={7}>1 week before</option>
        </select>
        <button type="submit" className="cal-subscribe-btn" disabled={loading}>
          {loading ? 'Subscribing...' : 'Subscribe'}
        </button>
      </form>
      {status && (
        <div className={`cal-subscribe-status cal-status-${status.type}`}>
          {status.message}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main MeetingCalendar Component
// ---------------------------------------------------------------------------

export default function MeetingCalendar() {
  const [calendarData, setCalendarData] = useState(null)
  const [schedules, setSchedules] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [currentYear, setCurrentYear] = useState(new Date().getFullYear())
  const [currentMonth, setCurrentMonth] = useState(new Date().getMonth())
  const [selectedDate, setSelectedDate] = useState(null)
  const [filterBody, setFilterBody] = useState('')

  // Fetch calendar data
  const fetchCalendar = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ months_back: '6', months_ahead: '6' })
      if (filterBody) params.set('meeting_body', filterBody)
      const data = await apiFetch(`/api/v1/calendar?${params}`)
      setCalendarData(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [filterBody])

  const fetchSchedules = useCallback(async () => {
    try {
      const data = await apiFetch('/api/v1/calendar/schedule')
      setSchedules(data.schedules || [])
    } catch {
      // Non-critical
    }
  }, [])

  useEffect(() => {
    fetchCalendar()
    fetchSchedules()
  }, [fetchCalendar, fetchSchedules])

  // Navigation
  const goToPrevMonth = () => {
    if (currentMonth === 0) {
      setCurrentMonth(11)
      setCurrentYear(currentYear - 1)
    } else {
      setCurrentMonth(currentMonth - 1)
    }
    setSelectedDate(null)
  }

  const goToNextMonth = () => {
    if (currentMonth === 11) {
      setCurrentMonth(0)
      setCurrentYear(currentYear + 1)
    } else {
      setCurrentMonth(currentMonth + 1)
    }
    setSelectedDate(null)
  }

  const goToToday = () => {
    setCurrentYear(new Date().getFullYear())
    setCurrentMonth(new Date().getMonth())
    setSelectedDate(null)
  }

  // Calendar add handlers
  const handleGoogleUrl = async (meetingBody) => {
    try {
      const data = await apiFetch(`/api/v1/calendar/google-url/${encodeURIComponent(meetingBody)}`)
      if (data.google_calendar_url) {
        window.open(data.google_calendar_url, '_blank')
      }
    } catch (err) {
      alert('Could not generate Google Calendar link: ' + err.message)
    }
  }

  const handleDownloadIcal = (meetingBody) => {
    const key = getApiKey()
    const url = meetingBody
      ? `${API_BASE}/api/v1/calendar/ical/${encodeURIComponent(meetingBody)}`
      : `${API_BASE}/api/v1/calendar/ical`

    // Open in new tab to trigger download with auth
    const a = document.createElement('a')
    a.href = url + `?api_key=${encodeURIComponent(key)}`
    a.download = ''
    a.click()
  }

  // Derived data
  const byDate = useMemo(() => calendarData?.by_date || {}, [calendarData])
  const bodies = useMemo(() => calendarData?.bodies || [], [calendarData])
  const allMeetings = useMemo(() => calendarData?.meetings || [], [calendarData])

  const selectedMeetings = selectedDate ? (byDate[selectedDate] || []) : []

  if (loading && !calendarData) {
    return (
      <div className="container" style={{ padding: '40px 20px', textAlign: 'center' }}>
        <div className="cal-loading">Loading calendar...</div>
      </div>
    )
  }

  if (error && !calendarData) {
    return (
      <div className="container" style={{ padding: '40px 20px' }}>
        <div className="cal-error">
          <h3>Calendar unavailable</h3>
          <p>{error}</p>
          <button onClick={fetchCalendar} className="cal-action-btn">Retry</button>
        </div>
      </div>
    )
  }

  return (
    <div className="container cal-container">
      <div className="cal-header">
        <h2 className="cal-title">Meeting Calendar</h2>
        <div className="cal-filters">
          <select
            className="cal-filter-select"
            value={filterBody}
            onChange={(e) => setFilterBody(e.target.value)}
          >
            <option value="">All meeting bodies</option>
            {bodies.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
          <button className="cal-action-btn cal-action-secondary" onClick={() => handleDownloadIcal(filterBody)}>
            Download .ics
          </button>
        </div>
      </div>

      {/* Body filter chips */}
      {bodies.length > 0 && (
        <div className="cal-body-chips">
          <button
            className={`cal-chip ${!filterBody ? 'cal-chip-active' : ''}`}
            onClick={() => setFilterBody('')}
          >
            All
          </button>
          {bodies.map((b) => (
            <button
              key={b}
              className={`cal-chip ${filterBody === b ? 'cal-chip-active' : ''}`}
              onClick={() => setFilterBody(filterBody === b ? '' : b)}
              style={filterBody === b ? { backgroundColor: bodyColor(b, bodies), color: '#fff', borderColor: bodyColor(b, bodies) } : { borderColor: bodyColor(b, bodies), color: bodyColor(b, bodies) }}
            >
              <span className="cal-chip-dot" style={{ backgroundColor: bodyColor(b, bodies) }} />
              {b}
            </button>
          ))}
        </div>
      )}

      <div className="cal-layout">
        {/* Main calendar area */}
        <div className="cal-main">
          {/* Month navigation */}
          <div className="cal-nav">
            <button className="cal-nav-btn" onClick={goToPrevMonth} aria-label="Previous month">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M12 4l-6 6 6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
            <h3 className="cal-nav-title">
              {monthName(currentMonth)} {currentYear}
            </h3>
            <button className="cal-nav-btn" onClick={goToNextMonth} aria-label="Next month">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M8 4l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
            <button className="cal-today-btn" onClick={goToToday}>Today</button>
          </div>

          {/* Calendar grid */}
          <CalendarGrid
            year={currentYear}
            month={currentMonth}
            byDate={byDate}
            bodies={bodies}
            selectedDate={selectedDate}
            onSelectDate={setSelectedDate}
          />

          {/* Day detail panel */}
          <DayDetail
            dateStr={selectedDate}
            meetings={selectedMeetings}
            bodies={bodies}
            onGoogleUrl={handleGoogleUrl}
            onDownloadIcal={handleDownloadIcal}
          />

          {/* Schedule profiles */}
          <ScheduleProfiles schedules={schedules} />
        </div>

        {/* Sidebar */}
        <div className="cal-sidebar">
          <UpcomingSidebar
            meetings={allMeetings}
            bodies={bodies}
            onGoogleUrl={handleGoogleUrl}
            onDownloadIcal={handleDownloadIcal}
          />
          <SubscribeForm bodies={bodies} />
        </div>
      </div>
    </div>
  )
}
