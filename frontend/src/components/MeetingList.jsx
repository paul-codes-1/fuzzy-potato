import { useNavigate, useSearchParams } from 'react-router-dom'
import { useEffect } from 'react'
import { useMeetings } from '../hooks/useMeetings'
import { useSearch } from '../hooks/useSearch'
import { useI18n } from '../i18n/I18nProvider'
import { useAnnounce } from './A11yAnnouncer'
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

// Render snippet with highlighted match
function HighlightedSnippet({ snippet }) {
  if (!snippet) return null

  const { text, matchStart, matchEnd, prefix, suffix } = snippet
  const before = text.slice(0, matchStart)
  const match = text.slice(matchStart, matchEnd)
  const after = text.slice(matchEnd)

  return (
    <div className="meeting-card-snippet">
      <span className="snippet-label">{snippet.matchLabel || 'Match found:'}</span>
      <span className="snippet-text">
        {prefix}{before}
        <mark className="snippet-highlight">{match}</mark>
        {after}{suffix}
      </span>
    </div>
  )
}

function MeetingCard({ meeting, snippet, href, onClick, t, formatDate }) {
  const duration = estimateDuration(meeting.transcript_words)

  return (
    <a className="meeting-card" href={href} onClick={onClick} role="listitem" aria-label={`${meeting.title}, ${formatDate(meeting.date) || t('meetingList.unknownDate')}`}>
      <div className="meeting-card-header">
        <div className="meeting-card-date">{formatDate(meeting.date) || t('meetingList.unknownDate')}</div>
        {duration && (
          <div className="meeting-card-duration" title={t('meetingList.estimatedDuration')}>
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
      {snippet && (
        <HighlightedSnippet snippet={snippet} />
      )}
    </a>
  )
}

function Pagination({ currentPage, totalPages, onPageChange, t }) {
  if (totalPages <= 1) return null

  // Build page numbers to show
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
    <nav className="pagination" aria-label="Meeting list pagination">
      <button
        className="pagination-btn"
        disabled={currentPage === 1}
        onClick={() => onPageChange(currentPage - 1)}
        aria-label={t('pagination.previous')}
      >
        {t('pagination.previous')}
      </button>
      <div className="pagination-pages">
        {pages.map((page, i) =>
          page === '...' ? (
            <span key={`ellipsis-${i}`} className="pagination-ellipsis" aria-hidden="true">...</span>
          ) : (
            <button
              key={page}
              className={`pagination-page ${page === currentPage ? 'active' : ''}`}
              onClick={() => onPageChange(page)}
              aria-label={`Page ${page}`}
              aria-current={page === currentPage ? 'page' : undefined}
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
        aria-label={t('pagination.next')}
      >
        {t('pagination.next')}
      </button>
    </nav>
  )
}

function MeetingList() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { meetings, loading, error } = useMeetings()
  const { t, formatDate } = useI18n()
  const announce = useAnnounce()
  const {
    query,
    setQuery,
    selectedBody,
    setSelectedBody,
    sortBy,
    setSortBy,
    meetingBodies,
    filteredMeetings,
    searchSnippets,
    flexSearchLoading,
    flexSearchLoaded,
    flexSearchProgress
  } = useSearch(meetings, searchParams, setSearchParams)

  // Announce search result count changes to screen readers
  useEffect(() => {
    if (!loading && meetings.length > 0) {
      const msg = query
        ? `${filteredMeetings.length} meetings matching "${query}"`
        : `${filteredMeetings.length} meetings`
      announce(msg)
    }
  }, [filteredMeetings.length, query, loading])

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
    return <div className="loading" role="status" aria-live="polite">{t('meetingList.loading')}</div>
  }

  if (error) {
    return (
      <div className="empty-state" role="alert">
        <h3>{t('meetingList.errorTitle')}</h3>
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
            flexSearchLoading={flexSearchLoading}
            flexSearchLoaded={flexSearchLoaded}
            flexSearchProgress={flexSearchProgress}
          />
          <TopicFilter
            meetingBodies={meetingBodies}
            selectedBody={selectedBody}
            setSelectedBody={setSelectedBody}
            sortBy={sortBy}
            setSortBy={setSortBy}
          />
        </div>
      </div>

      <div className="results-summary container">
        <p>
          {t('meetingList.meetingCount', { count: filteredMeetings.length })}
          {query && ` ${t('meetingList.matching', { query })}`}
          {totalPages > 1 && ` \u2022 ${t('meetingList.pageOf', { page: safePage, total: totalPages })}`}
        </p>
      </div>

      {filteredMeetings.length === 0 ? (
        <div className="empty-state" role="status" aria-live="polite">
          <h2>{t('meetingList.noMeetings')}</h2>
          <p>{t('meetingList.noMeetingsHint')}</p>
        </div>
      ) : (
        <>
          <div className="meeting-list container" role="list" aria-label="Meeting results">
            {paginatedMeetings.map(meeting => (
              <MeetingCard
                key={meeting.clip_id}
                meeting={meeting}
                snippet={searchSnippets.get(meeting.clip_id)}
                t={t}
                formatDate={formatDate}
                href={
                  query.trim()
                    ? `/meeting/${meeting.clip_id}?highlight=${encodeURIComponent(query.trim())}`
                    : `/meeting/${meeting.clip_id}`
                }
                onClick={(e) => {
                  // Let browser handle middle-click, ctrl+click, cmd+click natively
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
            t={t}
          />
        </>
      )}
    </>
  )
}

export default MeetingList
