import { useNavigate } from 'react-router-dom'
import { useMeetings } from '../hooks/useMeetings'
import { useSearch } from '../hooks/useSearch'
import SearchBar from './SearchBar'
import TopicFilter from './TopicFilter'

// Detect if text contains mostly non-Latin characters (garbage transcription)
function isGarbageText(text) {
  if (!text) return true
  // Count Latin letters vs total letters
  const latinLetters = (text.match(/[a-zA-Z]/g) || []).length
  const allLetters = (text.match(/\p{L}/gu) || []).length
  // If less than 50% are Latin, consider it garbage
  return allLetters > 0 && latinLetters / allLetters < 0.5
}

// Clean preview text - remove music notes and other noise at start
function cleanPreview(text) {
  if (!text) return null
  if (isGarbageText(text)) return null
  // Remove leading music notes, special chars, and find first real sentence
  return text
    .replace(/^[\u266a\u266b\u266c\s]+/, '') // Remove music notes at start
    .replace(/^\s*\[.*?\]\s*/g, '') // Remove [bracketed content] at start
    .trim()
}

// Estimate meeting duration from word count (avg speaking rate ~150 wpm)
function estimateDuration(wordCount) {
  if (!wordCount) return null
  const minutes = Math.round(wordCount / 150)
  if (minutes < 60) return `~${minutes} min`
  const hours = Math.floor(minutes / 60)
  const remainingMins = minutes % 60
  if (remainingMins === 0) return `~${hours}h`
  return `~${hours}h ${remainingMins}m`
}

// Render snippet with highlighted match
function HighlightedSnippet({ snippet }) {
  if (!snippet) return null

  const { text, matchStart, matchEnd, prefix, suffix } = snippet
  const before = text.slice(0, matchStart)
  const match = text.slice(matchStart, matchEnd)
  const after = text.slice(matchEnd)

  return (
    <div className="meeting-card-snippet">
      <span className="snippet-label">Match found:</span>
      <span className="snippet-text">
        {prefix}{before}
        <mark className="snippet-highlight">{match}</mark>
        {after}{suffix}
      </span>
    </div>
  )
}

function MeetingCard({ meeting, snippet, onClick }) {
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

  const preview = cleanPreview(meeting.transcript_preview)
  const duration = estimateDuration(meeting.transcript_words)

  return (
    <div className="meeting-card" onClick={onClick} role="article">
      <div className="meeting-card-header">
        <div className="meeting-card-date">{formatDate(meeting.date)}</div>
        {duration && (
          <div className="meeting-card-duration" title="Estimated duration">
            {duration}
          </div>
        )}
      </div>
      <div className="meeting-card-title">{meeting.title}</div>
      {meeting.meeting_body && (
        <span className="meeting-card-body">{meeting.meeting_body}</span>
      )}
      {meeting.topics && meeting.topics.length > 0 && (
        <div className="meeting-card-topics">
          {meeting.topics.slice(0, 4).map((topic, i) => (
            <span key={i} className="topic-tag">{topic}</span>
          ))}
          {meeting.topics.length > 4 && (
            <span className="topic-tag topic-tag-more">+{meeting.topics.length - 4} more</span>
          )}
        </div>
      )}
      {snippet ? (
        <HighlightedSnippet snippet={snippet} />
      ) : preview ? (
        <div className="meeting-card-preview">{preview}...</div>
      ) : null}
    </div>
  )
}

function MeetingList() {
  const navigate = useNavigate()
  const { meetings, loading, error } = useMeetings()
  const {
    query,
    setQuery,
    selectedBody,
    setSelectedBody,
    selectedTopic,
    setSelectedTopic,
    sortBy,
    setSortBy,
    searchMode,
    setSearchMode,
    meetingBodies,
    allTopics,
    filteredMeetings,
    searchSnippets,
    flexSearchLoading,
    flexSearchLoaded,
    flexSearchProgress
  } = useSearch(meetings)

  if (loading) {
    return <div className="loading">Loading meetings...</div>
  }

  if (error) {
    return (
      <div className="empty-state">
        <h3>Error loading meetings</h3>
        <p>{error}</p>
      </div>
    )
  }

  return (
    <>
      <div className="container">
        <div className="search-container">
          <SearchBar
            query={query}
            setQuery={setQuery}
            searchMode={searchMode}
            setSearchMode={setSearchMode}
            flexSearchLoading={flexSearchLoading}
            flexSearchLoaded={flexSearchLoaded}
            flexSearchProgress={flexSearchProgress}
          />
          <TopicFilter
            meetingBodies={meetingBodies}
            allTopics={allTopics}
            selectedBody={selectedBody}
            setSelectedBody={setSelectedBody}
            selectedTopic={selectedTopic}
            setSelectedTopic={setSelectedTopic}
            sortBy={sortBy}
            setSortBy={setSortBy}
          />
        </div>
      </div>

      <div className="results-summary container">
        <p>
          {filteredMeetings.length} meeting{filteredMeetings.length !== 1 ? 's' : ''}
          {query && ` matching "${query}"`}
        </p>
      </div>

      {filteredMeetings.length === 0 ? (
        <div className="empty-state">
          <h3>No meetings found</h3>
          <p>Try adjusting your search or filters</p>
        </div>
      ) : (
        <div className="meeting-list container">
          {filteredMeetings.map(meeting => (
            <MeetingCard
              key={meeting.clip_id}
              meeting={meeting}
              snippet={searchSnippets.get(meeting.clip_id)}
              onClick={() => {
                // Pass search term in URL if doing full-text transcript search
                if (searchMode === 'full' && query.trim()) {
                  const params = new URLSearchParams()
                  params.set('highlight', query.trim())
                  navigate(`/meeting/${meeting.clip_id}?${params.toString()}`)
                } else {
                  navigate(`/meeting/${meeting.clip_id}`)
                }
              }}
            />
          ))}
        </div>
      )}
    </>
  )
}

export default MeetingList
