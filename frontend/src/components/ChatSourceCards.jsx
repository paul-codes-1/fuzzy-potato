import { useState } from 'react'
import { Link } from 'react-router-dom'

function formatTimestamp(seconds) {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

export default function ChatSourceCards({ sources }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="chat-sources">
      <button
        className="chat-sources-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? '\u25BE' : '\u25B8'} Sources ({sources.length})
      </button>
      {expanded && (
        <div className="chat-source-cards">
          {sources.map((source, i) => (
            <div key={`${source.clip_id}-${i}`} className="ask-source-card">
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
          ))}
        </div>
      )}
    </div>
  )
}