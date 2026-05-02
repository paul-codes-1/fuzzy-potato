import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMeetings } from '../hooks/useMeetings'
import { useSearch } from '../hooks/useSearch'
import SearchBar from './SearchBar'
import TopicFilter from './TopicFilter'

const PER_PAGE = 24

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

// Render a server-supplied snippet. Server returns HTML-escaped text
// with <mark> highlights already inserted (rag/search.py:_safe_snippet),
// so dangerouslySetInnerHTML is safe.
function HighlightedSnippet({ html }) {
  if (!html) return null
  return (
    <div className="meeting-card-snippet">
      <span className="snippet-label">Match found:</span>
      <span
        className="snippet-text"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  )
}

function MeetingCard({ meeting, snippet, href, onClick }) {
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

  const duration = estimateDuration(meeting.transcript_words)

  return (
    <a className="meeting-card" href={href} onClick={onClick} role="article">
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
      {(meeting.agenda_preview || meeting.transcript_preview) && !snippet && (
        <div className="meeting-card-preview">{meeting.agenda_preview || meeting.transcript_preview}</div>
      )}
      {snippet && <HighlightedSnippet html={snippet} />}
    </a>
  )
}

function Pagination({ currentPage, totalPages, onPageChange }) {
  if (totalPages <= 1) return null

  const pages = []
  const maxVisible = 7

  if (totalPages <= maxVisible) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else {
    pages.push(1)
    let start = Math.max(2, currentPage - 1)
    let end = Math.min(totalPages - 1, currentPage + 1)

    if (currentPage <= 3) {
      end = Math.min(5, totalPages - 1)
    } else if (currentPage >= totalPages - 2) {
      start = Math.max(2, totalPages - 4)
    }

    if (start > 2) pages.push('...')
    for (let i = start; i <= end; i++) pages.push(i)
    if (end < totalPages - 1) pages.push('...')
    pages.push(totalPages)
  }

  return (
    <div className="pagination">
      <button
        className="pagination-btn"
        disabled={currentPage === 1}
        onClick={() => onPageChange(currentPage - 1)}
      >
        Previous
      </button>
      <div className="pagination-pages">
        {pages.map((page, i) =>
          page === '...' ? (
            <span key={`ellipsis-${i}`} className="pagination-ellipsis">...</span>
          ) : (
            <button
              key={page}
              className={`pagination-page ${page === currentPage ? 'active' : ''}`}
              onClick={() => onPageChange(page)}
            >
              {page}
            </button>
          )
        )}
      </div>
      <button
        className="pagination-btn"
        disabled={currentPage === totalPages}
        onClick={() => onPageChange(currentPage + 1)}
      >
        Next
      </button>
    </div>
  )
}

function MeetingList() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { meetings, loading, error } = useMeetings()
  const {
    query,
    setQuery,
    selectedBody,
    setSelectedBody,
    selectedSpeaker,
    setSelectedSpeaker,
    sortBy,
    setSortBy,
    isSearchMode,
    meetingBodies,
    filteredMeetings,
    searchSnippets,
    isSearching,
    searchError,
  } = useSearch(meetings, searchParams, setSearchParams)

  const currentPage = Math.max(1, parseInt(searchParams.get('page') || '1', 10))
  const totalPages = Math.max(1, Math.ceil(filteredMeetings.length / PER_PAGE))
  const safePage = Math.min(currentPage, totalPages)

  const paginatedMeetings = filteredMeetings.slice(
    (safePage - 1) * PER_PAGE,
    safePage * PER_PAGE
  )

  const handlePageChange = (page) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      if (page <= 1) {
        next.delete('page')
      } else {
        next.set('page', String(page))
      }
      return next
    })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

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
            isSearching={isSearching}
          />
          <TopicFilter
            meetingBodies={meetingBodies}
            selectedBody={selectedBody}
            setSelectedBody={setSelectedBody}
            selectedSpeaker={selectedSpeaker}
            setSelectedSpeaker={setSelectedSpeaker}
            sortBy={sortBy}
            setSortBy={setSortBy}
            isSearchMode={isSearchMode}
          />
        </div>
      </div>

      {searchError && (
        <div className="container">
          <div className="search-error">Search unavailable: {searchError}</div>
        </div>
      )}

      <div className="results-summary container">
        <p>
          {filteredMeetings.length} meeting{filteredMeetings.length !== 1 ? 's' : ''}
          {query && ` matching "${query}"`}
          {totalPages > 1 && ` • Page ${safePage} of ${totalPages}`}
        </p>
      </div>

      {filteredMeetings.length === 0 ? (
        <div className="empty-state">
          <h3>No meetings found</h3>
          <p>Try adjusting your search or filters</p>
        </div>
      ) : (
        <>
          <div className="meeting-list container">
            {paginatedMeetings.map(meeting => (
              <MeetingCard
                key={meeting.clip_id}
                meeting={meeting}
                snippet={searchSnippets.get(meeting.clip_id)}
                href={
                  query.trim()
                    ? `/meeting/${meeting.clip_id}?highlight=${encodeURIComponent(query.trim())}`
                    : `/meeting/${meeting.clip_id}`
                }
                onClick={(e) => {
                  if (e.button !== 0 || e.metaKey || e.ctrlKey) return
                  e.preventDefault()
                  if (query.trim()) {
                    navigate(`/meeting/${meeting.clip_id}?highlight=${encodeURIComponent(query.trim())}`)
                  } else {
                    navigate(`/meeting/${meeting.clip_id}`)
                  }
                }}
              />
            ))}
          </div>
          <Pagination
            currentPage={safePage}
            totalPages={totalPages}
            onPageChange={handlePageChange}
          />
        </>
      )}
    </>
  )
}

export default MeetingList
