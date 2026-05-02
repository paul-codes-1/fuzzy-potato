import { useFacets } from '../hooks/useFacets'

function TopicFilter({
  meetingBodies,
  selectedBody,
  setSelectedBody,
  selectedSpeaker,
  setSelectedSpeaker,
  sortBy,
  setSortBy,
}) {
  const { facets } = useFacets()
  const speakerOptions = (facets?.speakers) || []
  const hasFilters = !!(selectedBody || selectedSpeaker)

  return (
    <div className="filters-container">
      <div className="sort-controls">
        <label htmlFor="sort-select">Sort by:</label>
        <select
          id="sort-select"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          className="sort-select"
        >
          <option value="clip-desc">Latest clips first</option>
          <option value="date-desc">Newest by date</option>
          <option value="date-asc">Oldest by date</option>
          <option value="title">Title A-Z</option>
        </select>

        {speakerOptions.length > 0 && (
          <>
            <label htmlFor="speaker-select">Speaker:</label>
            <select
              id="speaker-select"
              value={selectedSpeaker || ''}
              onChange={(e) => setSelectedSpeaker(e.target.value || null)}
              className="sort-select"
            >
              <option value="">All speakers</option>
              {speakerOptions.map(s => (
                <option key={s.name} value={s.name}>
                  {s.name} ({s.count})
                </option>
              ))}
            </select>
          </>
        )}
      </div>

      <div className="filters">
        <button
          className={`filter-btn ${!selectedBody ? 'active' : ''}`}
          onClick={() => setSelectedBody(null)}
          aria-pressed={!selectedBody}
        >
          All Bodies
        </button>
        {meetingBodies.map(body => (
          <button
            key={body}
            className={`filter-btn ${selectedBody === body ? 'active' : ''}`}
            onClick={() => setSelectedBody(selectedBody === body ? null : body)}
            aria-pressed={selectedBody === body}
          >
            {body}
          </button>
        ))}
      </div>

      {hasFilters && (
        <div className="active-filters">
          <span>Filtering by:</span>
          {selectedBody && (
            <span className="active-filter-tag">
              {selectedBody}
              <button
                onClick={() => setSelectedBody(null)}
                aria-label={`Remove ${selectedBody} filter`}
              >
                ×
              </button>
            </span>
          )}
          {selectedSpeaker && (
            <span className="active-filter-tag">
              Speaker: {selectedSpeaker}
              <button
                onClick={() => setSelectedSpeaker(null)}
                aria-label={`Remove ${selectedSpeaker} speaker filter`}
              >
                ×
              </button>
            </span>
          )}
          <button
            className="clear-filters"
            onClick={() => { setSelectedBody(null); setSelectedSpeaker(null) }}
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}

export default TopicFilter
