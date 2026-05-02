import { useState, useRef, useMemo, useEffect, useCallback } from 'react'
import { useParams, Link, useSearchParams } from 'react-router-dom'
import { useMeeting } from '../hooks/useMeetings'
import { useRelatedClips } from '../hooks/useRelatedClips'
import { useFeedsLink } from '../hooks/useFeedsLink'
import {
  cleanTitle,
  buildSeoTitle,
  buildSeoDescription,
  setMetaTag,
  setCanonical,
  setMarkdownAlternate,
  buildMeetingGraph,
  setJsonLdScript,
} from '../utils/seo'

const SITE_URL = 'https://meetings.lexingtonky.news'
const DEFAULT_DESCRIPTION =
  'Searchable archive of Lexington-Fayette Urban County Government council and committee meetings — transcripts, summaries, agendas, and minutes.'

function formatRevisionDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  } catch {
    return ''
  }
}

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

// Pre-formatted text block with highlighting and scroll-to-first-match
function HighlightedPre({ text, searchTerm, firstMatchRef, className = 'agenda-text' }) {
  if (!searchTerm || !text) {
    return <pre className={className}>{text}</pre>
  }

  const { isExact, term } = parseSearchTerm(searchTerm)
  if (!term) {
    return <pre className={className}>{text}</pre>
  }

  // Build a regex to find all matches
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  let regex
  if (isExact) {
    regex = new RegExp(`((?:^|[\\s.,;:!?'"()\\[\\]{}\\-]))(${escaped})((?:[\\s.,;:!?'"()\\[\\]{}\\-]|$))`, 'gi')
  } else {
    // Match any word from the query
    const words = term.split(/\s+/).filter(w => w.length > 0)
    const wordPattern = words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
    regex = new RegExp(`(${wordPattern})`, 'gi')
  }

  // Split text into parts, wrapping matches in <mark> and attaching ref to first match
  const parts = []
  let lastIndex = 0
  let firstMatchFound = false
  let match

  if (isExact) {
    while ((match = regex.exec(text)) !== null) {
      const beforeEnd = match.index + match[1].length
      if (beforeEnd > lastIndex) {
        parts.push({ type: 'text', content: text.slice(lastIndex, beforeEnd) })
      }
      parts.push({ type: 'highlight', content: match[2], isFirst: !firstMatchFound })
      firstMatchFound = true
      lastIndex = beforeEnd + match[2].length
    }
  } else {
    while ((match = regex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push({ type: 'text', content: text.slice(lastIndex, match.index) })
      }
      parts.push({ type: 'highlight', content: match[0], isFirst: !firstMatchFound })
      firstMatchFound = true
      lastIndex = match.index + match[0].length
    }
  }
  if (lastIndex < text.length) {
    parts.push({ type: 'text', content: text.slice(lastIndex) })
  }

  return (
    <pre className={className}>
      {parts.map((part, i) =>
        part.type === 'highlight' ? (
          <mark
            key={i}
            ref={part.isFirst && firstMatchRef ? firstMatchRef : null}
            className="search-highlight"
          >
            {part.content}
          </mark>
        ) : (
          <span key={i}>{part.content}</span>
        )
      )}
    </pre>
  )
}

function MeetingDetail() {
  const { clipId } = useParams()
  const [searchParams] = useSearchParams()
  const highlightTerm = searchParams.get('highlight') || ''
  const { meeting, extractedFacts, transcript, transcriptSegments, agenda, minutes, loading, error } = useMeeting(clipId)
  const feedsLink = useFeedsLink(clipId)
  const [activeTab, setActiveTab] = useState('overview')
  const [videoStartTime, setVideoStartTime] = useState(null)
  const [videoLoading, setVideoLoading] = useState(false)
  const [summaryText, setSummaryText] = useState('')
  const videoContainerRef = useRef(null)
  const firstMatchRef = useRef(null)

  // Pull summary.txt for the SEO description. Separate from useMeeting so
  // browsers/agents that read the meta tag get real summary text without
  // making the listing page ever fetch it.
  useEffect(() => {
    if (!meeting?.files?.summary_txt) {
      setSummaryText('')
      return
    }
    let cancelled = false
    fetch(`/data/clips/${clipId}/${meeting.files.summary_txt}`)
      .then(r => (r.ok ? r.text() : ''))
      .then(t => { if (!cancelled) setSummaryText(t) })
      .catch(() => { if (!cancelled) setSummaryText('') })
    return () => { cancelled = true }
  }, [clipId, meeting])

  // Drive document.title, meta description, and og:/twitter: tags off the
  // currently-loaded meeting. SPA serves the same index.html for every
  // route, so without this clip pages would all share the site-level
  // defaults baked into index.html. (Social-card crawlers that don't run
  // JS still get the defaults — per-clip social previews need pre-render
  // or Lambda@Edge, tracked separately.)
  useEffect(() => {
    if (!meeting) return
    const seoTitle = buildSeoTitle(meeting.title, meeting.date)
    const description =
      buildSeoDescription(summaryText, meeting.agenda_preview || meeting.transcript_preview || '') ||
      DEFAULT_DESCRIPTION
    const url = `${SITE_URL}/meeting/${clipId}`

    const previousTitle = document.title
    document.title = `${seoTitle} | LFUCG Meeting Archive`
    setMetaTag('name', 'description', description)
    setMetaTag('property', 'og:title', seoTitle)
    setMetaTag('property', 'og:description', description)
    setMetaTag('property', 'og:url', url)
    setMetaTag('property', 'og:type', 'article')
    setMetaTag('name', 'twitter:card', 'summary')
    setMetaTag('name', 'twitter:title', seoTitle)
    setMetaTag('name', 'twitter:description', description)
    setCanonical(url)
    setMarkdownAlternate(`${SITE_URL}/data/clips/${clipId}/clip.md`)

    // JSON-LD @graph for Google: Article + Event + Organization + WebSite.
    // Per the March 2026 core-update guidance, dateModified should reflect
    // the latest revision (summary regen / re-process), datePublished stays
    // pinned to the meeting date — re-bumping datePublished on every cron
    // looks like freshness manipulation.
    const graph = buildMeetingGraph({
      clipId,
      title: meeting.title,
      date: meeting.date,
      meetingBody: meeting.meeting_body,
      description,
      granicusUrl: meeting.url,
      summaryUpdatedAt: meeting.summary_updated_at,
      processedAt: meeting.processed_at,
      transcriptWords: meeting.transcript_words,
    })
    setJsonLdScript('meeting-jsonld', graph)

    return () => {
      document.title = previousTitle
      setJsonLdScript('meeting-jsonld', null)
      setMarkdownAlternate(null)
    }
  }, [meeting, summaryText, clipId])

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

  // Check if plain text contains the search term
  const textContainsTerm = useCallback((text, searchTerm) => {
    if (!text || !searchTerm) return false
    const { isExact, term } = parseSearchTerm(searchTerm)
    if (!term) return false
    if (isExact) {
      const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      return new RegExp(`(?:^|[\\s.,;:!?'"()\\[\\]{}\\-])${escaped}(?:[\\s.,;:!?'"()\\[\\]{}\\-]|$)`, 'i').test(text)
    }
    const words = term.toLowerCase().split(/\s+/).filter(w => w.length > 0)
    return words.some(word => wordStartMatch(text, word))
  }, [])

  // Determine which tab to auto-switch to based on where the match is found
  const bestMatchTab = useMemo(() => {
    if (!highlightTerm) return null
    // Prefer transcript (has timestamps), then minutes, then agenda
    if (firstMatchIndex !== -1) return 'transcript'
    if (transcript && textContainsTerm(transcript, highlightTerm)) return 'transcript'
    if (minutes && textContainsTerm(minutes, highlightTerm)) return 'minutes'
    if (agenda && textContainsTerm(agenda, highlightTerm)) return 'agenda'
    return null
  }, [highlightTerm, firstMatchIndex, transcript, minutes, agenda, textContainsTerm])

  // Auto-switch to best tab and scroll to first match if highlight term is present
  useEffect(() => {
    if (!highlightTerm || !bestMatchTab) return

    setActiveTab(bestMatchTab)

    // Poll for the ref to be attached, then scroll
    let cancelled = false
    const tryScroll = (attempts) => {
      if (cancelled) return
      if (firstMatchRef.current) {
        firstMatchRef.current.scrollIntoView({ behavior: 'instant', block: 'center' })
      } else if (attempts > 0) {
        setTimeout(() => tryScroll(attempts - 1), 200)
      }
    }
    setTimeout(() => tryScroll(20), 300)

    return () => { cancelled = true }
  }, [highlightTerm, bestMatchTab])

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
    if (extractedFacts) result.push({ id: 'overview', label: 'Overview' })
    if (transcript || meeting.files?.transcript) result.push({ id: 'transcript', label: 'Transcript' })
    if (agenda || meeting.files?.agenda_txt) result.push({ id: 'agenda', label: 'Agenda' })
    if (minutes || meeting.files?.minutes_txt) result.push({ id: 'minutes', label: 'Official Minutes' })
    return result
  }, [extractedFacts, transcript, agenda, minutes, meeting])

  // Fetch related-meeting suggestions in parallel with the page;
  // hook always runs (passing null on cold mounts) so hook order is
  // stable across the loading/error early returns below.
  const { clips: relatedClips } = useRelatedClips(meeting?.clip_id || null, { limit: 5 })

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
        <h1>{cleanTitle(meeting.title)}</h1>
        <div className="meeting-meta">
          <span>{formatDate(meeting.date)}</span>
          {meeting.meeting_body && <span>• {meeting.meeting_body}</span>}
          {duration && <span>• ~{duration} estimated</span>}
          {meeting.transcript_words && (
            <span>• {meeting.transcript_words.toLocaleString()} words</span>
          )}
        </div>
        {feedsLink && (
          <p className="feeds-link">
            <a href={feedsLink.link} target="_blank" rel="noopener noreferrer">
              Read the article on Lexington Times →
            </a>
          </p>
        )}
      </div>

      <aside className="ai-disclosure" role="note" aria-label="AI transcription disclosure">
        <strong>How this page was made.</strong>{' '}
        {meeting.transcript_source === 'granicus_vtt' ? (
          <>
            The transcript on this page is a <strong>closed-caption placeholder</strong>{' '}
            from the {meeting.url ? (
              <a href={meeting.url} target="_blank" rel="noopener noreferrer">
                official Granicus video
              </a>
            ) : 'official Granicus video'}{' '}
            — captured live by a stenographer, with speaker attributions but typos
            and broken sentences. A higher-quality OpenAI Whisper-1 pass replaces
            it later. Structured facts and the narrative summary are not yet
            generated for placeholder transcripts.
          </>
        ) : meeting.transcript_source === 'whisper-1+vtt-speakers' ? (
          <>
            Audio from the {meeting.url ? (
              <a href={meeting.url} target="_blank" rel="noopener noreferrer">
                official Granicus video
              </a>
            ) : 'official Granicus video'}{' '}
            was auto-transcribed via OpenAI Whisper-1, with speaker labels folded
            in from the Granicus closed-captioning track. Structured facts were
            extracted with GPT-4o, and the narrative summary was generated by
            Anthropic Claude Sonnet. Speaker labels and verbatim wording may
            contain errors.
          </>
        ) : (
          <>
            Audio from the {meeting.url ? (
              <a href={meeting.url} target="_blank" rel="noopener noreferrer">
                official Granicus video
              </a>
            ) : 'official Granicus video'}{' '}
            was auto-transcribed via OpenAI Whisper-1. Structured facts were
            extracted with GPT-4o, and the narrative summary was generated by
            Anthropic Claude Sonnet. Speaker labels and verbatim wording may
            contain errors.
          </>
        )}
        {(meeting.summary_updated_at || meeting.processed_at) && (
          <> Last revised <time dateTime={meeting.summary_updated_at || meeting.processed_at}>
            {formatRevisionDate(meeting.summary_updated_at || meeting.processed_at)}
          </time>.</>
        )}{' '}
        <a href={`/about/methodology`}>Full methodology</a> ·{' '}
        <a href={`mailto:editor@lexingtonky.news?subject=${encodeURIComponent(`Correction: clip ${clipId}`)}`}>
          Spot an error?
        </a>
      </aside>

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

          {highlightTerm && (
            <div className="search-highlight-banner">
              Highlighting: "<strong>{highlightTerm}</strong>"
              <Link to={`/meeting/${clipId}`} className="clear-highlight">
                Clear
              </Link>
            </div>
          )}

          <div className="content-panel" role="tabpanel">
            {currentTab === 'overview' && extractedFacts && (
              <div className="meeting-overview">
                {/* Meeting Info */}
                {extractedFacts.meeting_info && (
                  <section className="facts-section">
                    <h3>Meeting Information</h3>
                    <div className="facts-grid">
                      {extractedFacts.meeting_info.body && <div><strong>Body:</strong> {extractedFacts.meeting_info.body}</div>}
                      {extractedFacts.meeting_info.date && <div><strong>Date:</strong> {extractedFacts.meeting_info.date}</div>}
                      {extractedFacts.meeting_info.time && <div><strong>Time:</strong> {extractedFacts.meeting_info.time}</div>}
                      {extractedFacts.meeting_info.presiding_officer && <div><strong>Presiding:</strong> {extractedFacts.meeting_info.presiding_officer}</div>}
                      {extractedFacts.meeting_info.location && <div><strong>Location:</strong> {extractedFacts.meeting_info.location}</div>}
                    </div>
                  </section>
                )}

                {/* Attendance */}
                {extractedFacts.attendance?.present?.length > 0 && (
                  <section className="facts-section">
                    <h3>Attendance</h3>
                    <div><strong>Present ({extractedFacts.attendance.present.length}):</strong> {extractedFacts.attendance.present.join(', ')}</div>
                    {extractedFacts.attendance.absent?.length > 0 && (
                      <div><strong>Absent:</strong> {extractedFacts.attendance.absent.join(', ')}</div>
                    )}
                    {extractedFacts.attendance.late?.length > 0 && (
                      <div><strong>Late:</strong> {extractedFacts.attendance.late.join(', ')}</div>
                    )}
                  </section>
                )}

                {/* Votes & Decisions */}
                {extractedFacts.motions_and_votes?.length > 0 && (
                  <section className="facts-section">
                    <h3>Votes &amp; Decisions</h3>
                    {extractedFacts.motions_and_votes.map((vote, i) => (
                      <div key={i} className="facts-card">
                        <div className="facts-card-header">
                          {vote.identifier && <strong>{vote.identifier}</strong>}
                          <span className={`vote-badge vote-${vote.outcome}`}>{vote.outcome}</span>
                        </div>
                        <p>{vote.description}</p>
                        {(vote.ayes != null || vote.nays != null) && (
                          <div className="vote-tally">
                            {vote.ayes != null && <span className="vote-for">Ayes: {vote.ayes}</span>}
                            {vote.nays != null && <span className="vote-against">Nays: {vote.nays}</span>}
                            {vote.abstentions > 0 && <span>Abstentions: {vote.abstentions}</span>}
                          </div>
                        )}
                        {vote.votes_against?.length > 0 && (
                          <div><strong>Opposed:</strong> {vote.votes_against.join(', ')}</div>
                        )}
                        {vote.transcript_approx_time && (
                          <button
                            className="timestamp-link"
                            onClick={() => {
                              const [m, s] = vote.transcript_approx_time.split(':').map(Number)
                              jumpToTime(m * 60 + (s || 0))
                            }}
                          >
                            {vote.transcript_approx_time}
                          </button>
                        )}
                      </div>
                    ))}
                  </section>
                )}

                {/* Financial Items */}
                {extractedFacts.financial_items?.length > 0 && (
                  <section className="facts-section">
                    <h3>Budget &amp; Financial Items</h3>
                    {extractedFacts.financial_items.map((item, i) => (
                      <div key={i} className="facts-card">
                        <div className="facts-card-header">
                          <strong className="financial-amount">{item.amount}</strong>
                          {item.type && <span className="facts-tag">{item.type}</span>}
                        </div>
                        <p>{item.description}</p>
                        {item.identifier && <div><strong>Reference:</strong> {item.identifier}</div>}
                        {item.vendor_or_recipient && <div><strong>Vendor/Recipient:</strong> {item.vendor_or_recipient}</div>}
                      </div>
                    ))}
                  </section>
                )}

                {/* Agenda Items */}
                {extractedFacts.agenda_items?.length > 0 && (
                  <section className="facts-section">
                    <h3>Agenda Items</h3>
                    {extractedFacts.agenda_items.map((item, i) => (
                      <div key={i} className="facts-card">
                        <div className="facts-card-header">
                          <strong>{item.title}</strong>
                          {item.outcome && <span className={`vote-badge vote-${item.outcome}`}>{item.outcome}</span>}
                        </div>
                        {item.identifier && <div className="facts-tag">{item.identifier} ({item.type})</div>}
                        <p>{item.summary}</p>
                        {item.key_speakers?.length > 0 && (
                          <div><strong>Key speakers:</strong> {item.key_speakers.join(', ')}</div>
                        )}
                        {item.transcript_approx_time && (
                          <button
                            className="timestamp-link"
                            onClick={() => {
                              const [m, s] = item.transcript_approx_time.split(':').map(Number)
                              jumpToTime(m * 60 + (s || 0))
                            }}
                          >
                            {item.transcript_approx_time}
                          </button>
                        )}
                      </div>
                    ))}
                  </section>
                )}

                {/* Public Comments */}
                {extractedFacts.public_comments?.length > 0 && (
                  <section className="facts-section">
                    <h3>Public Comments</h3>
                    {extractedFacts.public_comments.map((comment, i) => (
                      <div key={i} className="facts-card">
                        {comment.speaker && <strong>{comment.speaker}</strong>}
                        {comment.topic && <span> — {comment.topic}</span>}
                        <p>{comment.summary}</p>
                        {comment.transcript_approx_time && (
                          <button
                            className="timestamp-link"
                            onClick={() => {
                              const [m, s] = comment.transcript_approx_time.split(':').map(Number)
                              jumpToTime(m * 60 + (s || 0))
                            }}
                          >
                            {comment.transcript_approx_time}
                          </button>
                        )}
                      </div>
                    ))}
                  </section>
                )}

                {/* Appointments */}
                {extractedFacts.appointments?.length > 0 && (
                  <section className="facts-section">
                    <h3>Appointments</h3>
                    {extractedFacts.appointments.map((appt, i) => (
                      <div key={i} className="facts-card">
                        <strong>{appt.person}</strong> — {appt.action} to {appt.body_or_role}
                      </div>
                    ))}
                  </section>
                )}

                {/* Contentious Items */}
                {extractedFacts.contentious_items?.length > 0 && (
                  <section className="facts-section">
                    <h3>Contested Items</h3>
                    {extractedFacts.contentious_items.map((item, i) => (
                      <div key={i} className="facts-card">
                        <div className="facts-card-header">
                          <strong>{item.topic}</strong>
                          <span className="facts-tag">{item.nature?.replace(/_/g, ' ')}</span>
                        </div>
                        <p>{item.details}</p>
                      </div>
                    ))}
                  </section>
                )}
              </div>
            )}

            {currentTab === 'transcript' && (
              <div className="meeting-transcript">
                {transcriptSegments && transcriptSegments.length > 0 ? (
                  <div className={`transcript-segments${highlightTerm ? ' transcript-segments-expanded' : ''}`}>
                    <p className="transcript-hint">
                      Click a timestamp to jump to that point in the video below
                    </p>
                    {(() => {
                      const { isExact, term } = parseSearchTerm(highlightTerm)
                      let prevSpeaker = null
                      return transcriptSegments.map((segment, idx) => {
                        const isFirstMatch = idx === firstMatchIndex
                        const hasMatch = term ? segmentMatches(segment.text, term, isExact) : false
                        const showSpeaker = segment.speaker && segment.speaker !== prevSpeaker
                        prevSpeaker = segment.speaker || prevSpeaker

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
                              {showSpeaker && (
                                <strong className="segment-speaker">{segment.speaker}: </strong>
                              )}
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
                  <HighlightedPre
                    text={agenda}
                    searchTerm={highlightTerm}
                    firstMatchRef={bestMatchTab === 'agenda' ? firstMatchRef : null}
                  />
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
                  <HighlightedPre
                    text={minutes}
                    searchTerm={highlightTerm}
                    firstMatchRef={bestMatchTab === 'minutes' ? firstMatchRef : null}
                    className="minutes-text"
                  />
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

      {relatedClips.length > 0 && (
        <section className="related-meetings">
          <h2>Related meetings</h2>
          <ul className="related-meetings-list">
            {relatedClips.map(r => (
              <li key={r.clip_id} className="related-meeting-card">
                <Link to={`/meeting/${r.clip_id}`}>
                  <div className="related-meeting-title">{cleanTitle(r.title)}</div>
                  <div className="related-meeting-meta">
                    {r.date && <span>{r.date}</span>}
                    {r.meeting_body && <span> • {r.meeting_body}</span>}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

export default MeetingDetail
