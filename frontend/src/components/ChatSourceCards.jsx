import { useState } from 'react'
import { Link } from 'react-router-dom'

function formatTimestamp(seconds) {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function SourceCard({ source }) {
  return (
    <div className="ask-source-card">
      <div className="ask-source-header">
        <Link to={`/meeting/${source.clip_id}`} className="ask-source-title">
          {source.title}
        </Link>
        <span className="ask-source-date">{source.date}</span>
        <span className="ask-source-body">{source.meeting_body}</span>
      </div>
      {source.timestamp != null && (
        <a
          href={source.granicus_url}
          target="_blank"
          rel="noopener noreferrer"
          className="ask-timestamp-badge"
        >
          {formatTimestamp(source.timestamp)}
        </a>
      )}
      {source.excerpt && (
        <p className="ask-source-excerpt">{source.excerpt}</p>
      )}
    </div>
  )
}

function SourceToggle({ label, sources }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="chat-sources">
      <button
        className="chat-sources-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? '▾' : '▸'} {label} ({sources.length})
      </button>
      {expanded && (
        <div className="chat-source-cards">
          {sources.map((source, i) => (
            <SourceCard key={`${source.clip_id}-${i}`} source={source} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ChatSourceCards({ sources }) {
  // Split on the backend's `cited` flag. If no source carries the flag
  // (older backend), fall back to the original single "Sources" toggle.
  const hasCitedFlags = sources.some(s => s.cited !== undefined)

  if (!hasCitedFlags) {
    return <SourceToggle label="Sources" sources={sources} />
  }

  const citedSources = sources.filter(s => s.cited)
  const uncitedSources = sources.filter(s => !s.cited)

  return (
    <>
      {citedSources.length > 0 && (
        <SourceToggle label="Cited in this answer" sources={citedSources} />
      )}
      {uncitedSources.length > 0 && (
        <SourceToggle label="Other retrieved excerpts" sources={uncitedSources} />
      )}
    </>
  )
}
