function TopicFilter({
  meetingBodies,
  selectedBody,
  setSelectedBody,
  sortBy,
  setSortBy
}) {
  return (
    <div className="filters-container">
      {/* Sort controls */}
      <div className="sort-controls">
        <label htmlFor="sort-select">Sort by:</label>
        <select
          id="sort-select"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          className="sort-select"
        >
          <option value="date-desc">Newest first</option>
          <option value="date-asc">Oldest first</option>
          <option value="title">Title A-Z</option>
        </select>
      </div>

      <div className="filters">
        {/* Meeting body filters */}
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

      {/* Active filter indicator */}
      {selectedBody && (
        <div className="active-filters">
          <span>Filtering by:</span>
          <span className="active-filter-tag">
            {selectedBody}
            <button
              onClick={() => setSelectedBody(null)}
              aria-label={`Remove ${selectedBody} filter`}
            >
              ×
            </button>
          </span>
          <button
            className="clear-filters"
            onClick={() => setSelectedBody(null)}
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}

export default TopicFilter
