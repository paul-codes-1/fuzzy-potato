import { useState, useRef, useMemo, useEffect, useCallback } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { useMeeting } from '../hooks/useMeetings'

// Format seconds to MM:SS or HH:MM:SS
function formatTimestamp(seconds) {
  const hrs = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)

  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

// Parse search term to check for exact match mode (quoted)
function parseSearchTerm(term) {
  if (!term) return { isExact: false, term: '' }
  const trimmed = term.trim()
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length > 2) {
    return { isExact: true, term: trimmed.slice(1, -1) }
  }
  return { isExact: false, term: trimmed }
}

// Highlight search terms in text
function HighlightedText({ text, searchTerm }) {
  if (!searchTerm || !text) {
    return <>{text}</>
  }

  const { isExact, term } = parseSearchTerm(searchTerm)

  if (!term) {
    return <>{text}</>
  }

  // Escape special regex characters
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

  let regex
  if (isExact) {
    // Whole word match: surrounded by word boundaries (whitespace, punctuation, or start/end)
    regex = new RegExp(`((?:^|[\\s.,;:!?'"()\\[\\]{}\\-]))(${escaped})((?:[\\s.,;:!?'"()\\[\\]{}\\-]|$))`, 'gi')

    const parts = []
    let lastIndex = 0
    let match

    while ((match = regex.exec(text)) !== null) {
      // Add text before match
      if (match.index + match[1].length > lastIndex) {
        parts.push({ type: 'text', content: text.slice(lastIndex, match.index + match[1].length) })
      }
      // Add highlighted match
      parts.push({ type: 'highlight', content: match[2] })
      lastIndex = match.index + match[1].length + match[2].length
    }
    // Add remaining text
    if (lastIndex < text.length) {
      parts.push({ type: 'text', content: text.slice(lastIndex) })
    }

    return (
      <>
        {parts.map((part, i) =>
          part.type === 'highlight' ? (
            <mark key={i} className="search-highlight">{part.content}</mark>
          ) : (
            <span key={i}>{part.content}</span>
          )
        )}
      </>
    )
  } else {
    // Partial match (original behavior)
    regex = new RegExp(`(${escaped})`, 'gi')
    const parts = text.split(regex)

    return (
      <>
        {parts.map((part, i) =>
          regex.test(part) ? (
            <mark key={i} className="search-highlight">{part}</mark>
          ) : (
            <span key={i}>{part}</span>
          )
        )}
      </>
    )
  }
}

function MeetingDetail() {
  const { clipId } = useParams()
  const [searchParams] = useSearchParams()
  const highlightTerm = searchParams.get('highlight') || ''
  const { meeting, summary, transcript, transcriptSegments, agenda, minutes, loading, error } = useMeeting(clipId)
  const [activeTab, setActiveTab] = useState('summary')
  const [videoStartTime, setVideoStartTime] = useState(null)
  const [videoLoading, setVideoLoading] = useState(false)
  const videoContainerRef = useRef(null)
  const firstMatchRef = useRef(null)

  // Check if a word appears at a word boundary in text (prefix match, like FlexSearch forward tokenizer)
  const wordStartMatch = (text, word) => {
    const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    // Match word at start of a word boundary (beginning of string or after non-letter)
    const regex = new RegExp(`(?:^|[^a-zA-Z])${escaped}`, 'i')
    return regex.test(text)
  }

  // Check if a segment matches the search term
  const segmentMatches = (segText, term, isExact) => {
    if (isExact) {
      const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const regex = new RegExp(`(?:^|[\\s.,;:!?'"()\\[\\]{}\\-])${escaped}(?:[\\s.,;:!?'"()\\[\\]{}\\-]|$)`, 'i')
      return regex.test(segText)
    }
    // For multi-word queries, match if ANY word appears at a word boundary
    const words = term.toLowerCase().split(/\s+/).filter(w => w.length > 0)
    return words.some(word => wordStartMatch(segText, word))
  }

  // Find index of first matching segment
  const firstMatchIndex = useMemo(() => {
    if (!highlightTerm || !transcriptSegments) return -1

    const { isExact, term } = parseSearchTerm(highlightTerm)
    if (!term) return -1

    if (!isExact) {
      const words = term.toLowerCase().split(/\s+/).filter(w => w.length > 0)
      // First pass: find segment with all words at word boundaries
      const allWordsIdx = transcriptSegments.findIndex(seg =>
        words.every(w => wordStartMatch(seg.text, w))
      )
      if (allWordsIdx !== -1) return allWordsIdx
      // Second pass: find segment with the longest word (most specific)
      const sorted = [...words].sort((a, b) => b.length - a.length)
      return transcriptSegments.findIndex(seg => wordStartMatch(seg.text, sorted[0]))
    }

    return transcriptSegments.findIndex(seg => segmentMatches(seg.text, term, true))
  }, [highlightTerm, transcriptSegments])

  // Auto-switch to transcript tab and scroll to first match if highlight term is present
  useEffect(() => {
    if (!highlightTerm || !transcriptSegments || firstMatchIndex === -1) return

    setActiveTab('transcript')

    // Poll for the ref to be attached, then scroll (container is expanded when highlighting)
    let cancelled = false
    const tryScroll = (attempts) => {
      if (cancelled) return
      if (firstMatchRef.current) {
        // Use instant scroll first to ensure it lands, then no layout interruption
        firstMatchRef.current.scrollIntoView({ behavior: 'instant', block: 'center' })
      } else if (attempts > 0) {
        setTimeout(() => tryScroll(attempts - 1), 200)
      }
    }
    // Longer initial delay to let the expanded transcript fully render
    setTimeout(() => tryScroll(20), 300)

    return () => { cancelled = true }
  }, [highlightTerm, transcriptSegments, firstMatchIndex])

  // Jump to a specific time in the embedded video
  const jumpToTime = useCallback((seconds) => {
    setVideoLoading(true)
    setVideoStartTime(Math.floor(seconds))
    if (videoContainerRef.current) {
      videoContainerRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [])

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

  const getFileUrl = useCallback((filename) => {
    return `/data/clips/${clipId}/${filename}`
  }, [clipId])

  // Determine available tabs
  const tabs = useMemo(() => {
    if (!meeting) return []
    const result = []
    if (summary || meeting.files?.summary_html) result.push({ id: 'summary', label: 'Summary' })
    if (transcript || meeting.files?.transcript) result.push({ id: 'transcript', label: 'Transcript' })
    if (agenda || meeting.files?.agenda_txt) result.push({ id: 'agenda', label: 'Agenda' })
    if (minutes || meeting.files?.minutes_txt) result.push({ id: 'minutes', label: 'Official Minutes' })
    return result
  }, [summary, transcript, agenda, minutes, meeting])

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

  const duration = estimateDuration(meeting.transcript_words)

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
          {meeting.files?.audio && window.location.protocol !== 'https:' && (
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
                {highlightTerm && (
                  <div className="search-highlight-banner">
                    Highlighting: "<strong>{highlightTerm}</strong>"
                    <Link to={`/meeting/${clipId}`} className="clear-highlight">
                      Clear
                    </Link>
                  </div>
                )}
                {transcriptSegments && transcriptSegments.length > 0 ? (
                  <div className={`transcript-segments${highlightTerm ? ' transcript-segments-expanded' : ''}`}>
                    <p className="transcript-hint">
                      Click a timestamp to jump to that point in the video below
                    </p>
                    {(() => {
                      const { isExact, term } = parseSearchTerm(highlightTerm)
                      return transcriptSegments.map((segment, idx) => {
                        const isFirstMatch = idx === firstMatchIndex
                        const hasMatch = term ? segmentMatches(segment.text, term, isExact) : false

                        return (
                          <div
                            key={idx}
                            ref={isFirstMatch ? firstMatchRef : null}
                            className={`transcript-segment ${hasMatch ? 'has-match' : ''}`}
                          >
                            <button
                              onClick={() => jumpToTime(segment.start)}
                              className="timestamp-link"
                              title={`Jump to ${formatTimestamp(segment.start)} in video`}
                            >
                              {formatTimestamp(segment.start)}
                            </button>
                            <span className="segment-text">
                              <HighlightedText text={segment.text} searchTerm={highlightTerm} />
                            </span>
                          </div>
                        )
                      })
                    })()}
                  </div>
                ) : transcript ? (
                  <pre className="transcript-text">
                    <HighlightedText text={transcript} searchTerm={highlightTerm} />
                  </pre>
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
      <div className="video-embed" ref={videoContainerRef}>
        <h2>
          Watch Meeting Video
          {videoStartTime !== null && (
            <span className="video-timestamp-indicator">
              {' '}— Starting at {formatTimestamp(videoStartTime)}
            </span>
          )}
        </h2>
        <p className="video-fallback-link">
          <a
            href={`https://lfucg.granicus.com/player/clip/${clipId}?view_id=14${videoStartTime ? `&entrytime=${videoStartTime}` : ''}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open video on Granicus →
          </a>
          {videoStartTime !== null && (
            <button
              onClick={() => setVideoStartTime(null)}
              className="reset-video-btn"
            >
              Reset to start
            </button>
          )}
        </p>
        <div className="video-container">
          {videoLoading && (
            <div className="video-loading-overlay">
              <div className="video-loading-spinner"></div>
              <span>Loading video at {formatTimestamp(videoStartTime)}...</span>
            </div>
          )}
          <iframe
            width="100%"
            height="100%"
            frameBorder="0"
            allowFullScreen
            onLoad={() => setVideoLoading(false)}
            src={`//lfucg.granicus.com/player/clip/${clipId}?view_id=14&redirect=true&embed=1${videoStartTime ? `&entrytime=${videoStartTime}&autostart=1` : '&autostart=0'}`}
          />
        </div>
      </div>
    </div>
  )
}

export default MeetingDetail
