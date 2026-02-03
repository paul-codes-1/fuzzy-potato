import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useMeeting } from '../hooks/useMeetings'

function MeetingDetail() {
  const { clipId } = useParams()
  const { meeting, summary, transcript, agenda, minutes, loading, error } = useMeeting(clipId)
  const [activeTab, setActiveTab] = useState('summary')

  const formatDate = (dateStr) => {
    if (!dateStr) return 'Unknown date'
    const date = new Date(dateStr + 'T00:00:00')
    return date.toLocaleDateString('en-US', {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    })
  }

  // Estimate meeting duration from word count (avg speaking rate ~150 wpm)
  const estimateDuration = (wordCount) => {
    if (!wordCount) return null
    const minutes = Math.round(wordCount / 150)
    if (minutes < 60) return `${minutes} min`
    const hours = Math.floor(minutes / 60)
    const remainingMins = minutes % 60
    if (remainingMins === 0) return `${hours}h`
    return `${hours}h ${remainingMins}m`
  }

  if (loading) {
    return <div className="loading">Loading meeting details...</div>
  }

  if (error || !meeting) {
    return (
      <div className="empty-state">
        <h3>Meeting not found</h3>
        <p>{error || 'The requested meeting could not be loaded.'}</p>
        <Link to="/">Back to all meetings</Link>
      </div>
    )
  }

  const getFileUrl = (filename) => {
    return `/data/clips/${clipId}/${filename}`
  }

  const duration = estimateDuration(meeting.transcript_words)

  // Determine available tabs
  const tabs = []
  if (summary || meeting.files?.summary_html) tabs.push({ id: 'summary', label: 'Summary' })
  if (transcript || meeting.files?.transcript) tabs.push({ id: 'transcript', label: 'Transcript' })
  if (agenda || meeting.files?.agenda_txt) tabs.push({ id: 'agenda', label: 'Agenda' })
  if (minutes || meeting.files?.minutes_txt) tabs.push({ id: 'minutes', label: 'Official Minutes' })

  // Default to first available tab if current is not available
  const currentTab = tabs.find(t => t.id === activeTab) ? activeTab : (tabs[0]?.id || 'summary')

  return (
    <div className="meeting-detail container">
      <div className="meeting-detail-header">
        <Link to="/" className="back-link">
          ← Back to all meetings
        </Link>
        <h1>{meeting.title}</h1>
        <div className="meeting-meta">
          <span>{formatDate(meeting.date)}</span>
          {meeting.meeting_body && <span>• {meeting.meeting_body}</span>}
          {duration && <span>• ~{duration} estimated</span>}
          {meeting.transcript_words && (
            <span>• {meeting.transcript_words.toLocaleString()} words</span>
          )}
        </div>
        {meeting.topics && meeting.topics.length > 0 && (
          <div className="meeting-card-topics" style={{ marginTop: '16px' }}>
            {meeting.topics.map((topic, i) => (
              <span key={i} className="topic-tag">{topic}</span>
            ))}
          </div>
        )}
      </div>

      <div className="meeting-files">
        <h2>Download Files</h2>
        <div className="file-links">
          {meeting.files?.audio && (
            <a
              href={getFileUrl(meeting.files.audio)}
              className="file-link"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span aria-hidden="true">🎵</span>
              <span>Audio Recording</span>
            </a>
          )}
          {meeting.files?.transcript && (
            <a
              href={getFileUrl(meeting.files.transcript)}
              className="file-link"
              download
            >
              <span aria-hidden="true">📝</span>
              <span>Transcript (.txt)</span>
            </a>
          )}
          {meeting.files?.agenda_pdf && (
            <a
              href={getFileUrl(meeting.files.agenda_pdf)}
              className="file-link"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span aria-hidden="true">📋</span>
              <span>Agenda (PDF)</span>
            </a>
          )}
          {meeting.files?.summary_txt && (
            <a
              href={getFileUrl(meeting.files.summary_txt)}
              className="file-link"
              download
            >
              <span aria-hidden="true">📄</span>
              <span>Summary (.txt)</span>
            </a>
          )}
          {meeting.files?.minutes_pdf && (
            <a
              href={getFileUrl(meeting.files.minutes_pdf)}
              className="file-link"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span aria-hidden="true">📜</span>
              <span>Official Minutes (PDF)</span>
            </a>
          )}
        </div>
      </div>

      {/* Tabbed content viewer */}
      {tabs.length > 0 && (
        <div className="content-viewer">
          <div className="content-tabs" role="tablist">
            {tabs.map(tab => (
              <button
                key={tab.id}
                role="tab"
                aria-selected={currentTab === tab.id}
                className={`content-tab ${currentTab === tab.id ? 'active' : ''}`}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="content-panel" role="tabpanel">
            {currentTab === 'summary' && (
              <div className="meeting-summary">
                {summary ? (
                  <div dangerouslySetInnerHTML={{ __html: summary }} />
                ) : (
                  <p className="content-unavailable">
                    Summary not available.{' '}
                    {meeting.files?.summary_txt && (
                      <a href={getFileUrl(meeting.files.summary_txt)} target="_blank" rel="noopener noreferrer">
                        Download text version →
                      </a>
                    )}
                  </p>
                )}
              </div>
            )}

            {currentTab === 'transcript' && (
              <div className="meeting-transcript">
                {transcript ? (
                  <pre className="transcript-text">{transcript}</pre>
                ) : (
                  <p className="content-unavailable">
                    Transcript not available inline.{' '}
                    {meeting.files?.transcript && (
                      <a href={getFileUrl(meeting.files.transcript)} target="_blank" rel="noopener noreferrer">
                        Download transcript →
                      </a>
                    )}
                  </p>
                )}
              </div>
            )}

            {currentTab === 'agenda' && (
              <div className="meeting-agenda">
                {agenda ? (
                  <pre className="agenda-text">{agenda}</pre>
                ) : (
                  <p className="content-unavailable">
                    Agenda text not available.{' '}
                    {meeting.files?.agenda_pdf && (
                      <a href={getFileUrl(meeting.files.agenda_pdf)} target="_blank" rel="noopener noreferrer">
                        View PDF agenda →
                      </a>
                    )}
                  </p>
                )}
              </div>
            )}

            {currentTab === 'minutes' && (
              <div className="meeting-minutes">
                {minutes ? (
                  <pre className="minutes-text">{minutes}</pre>
                ) : (
                  <p className="content-unavailable">
                    Minutes text not available inline.{' '}
                    {meeting.files?.minutes_pdf && (
                      <a href={getFileUrl(meeting.files.minutes_pdf)} target="_blank" rel="noopener noreferrer">
                        View PDF minutes →
                      </a>
                    )}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {tabs.length === 0 && (
        <div className="empty-state">
          <p>No content available for this meeting.</p>
        </div>
      )}

      {/* Video Player Embed */}
      <div className="video-embed">
        <h2>Watch Meeting Video</h2>
        <p className="video-fallback-link">
          <a
            href={`https://lfucg.granicus.com/player/clip/${clipId}?view_id=14`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open video on Granicus →
          </a>
        </p>
        <div className="video-container">
          <embed
            width="100%"
            height="100%"
            frameBorder="0"
            allowFullScreen={true}
            src={`//lfucg.granicus.com/player/clip/${clipId}?view_id=14&redirect=true&autostart=0&embed=1`}
          />
        </div>
      </div>
    </div>
  )
}

export default MeetingDetail
