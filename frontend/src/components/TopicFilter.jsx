import { useState } from 'react'

function TopicFilter({
  meetingBodies,
  allTopics,
  selectedBody,
  setSelectedBody,
  selectedTopic,
  setSelectedTopic,
  sortBy,
  setSortBy
}) {
  const [showAllTopics, setShowAllTopics] = useState(false)

  // Show top 6 topics inline, rest in expandable section
  const inlineTopics = allTopics.slice(0, 6)
  const moreTopics = allTopics.slice(6)

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

        {/* Divider if we have both */}
        {meetingBodies.length > 0 && allTopics.length > 0 && (
          <span className="filter-divider" aria-hidden="true"></span>
        )}

        {/* Inline topic filters */}
        {inlineTopics.map(({ topic, count }) => (
          <button
            key={topic}
            className={`filter-btn ${selectedTopic === topic ? 'active' : ''}`}
            onClick={() => setSelectedTopic(selectedTopic === topic ? null : topic)}
            aria-pressed={selectedTopic === topic}
            title={`${count} meeting${count !== 1 ? 's' : ''}`}
          >
            {topic}
          </button>
        ))}

        {/* More topics toggle */}
        {moreTopics.length > 0 && (
          <button
            className="filter-btn filter-btn-more"
            onClick={() => setShowAllTopics(!showAllTopics)}
            aria-expanded={showAllTopics}
          >
            {showAllTopics ? 'Less' : `+${moreTopics.length} more`}
          </button>
        )}
      </div>

      {/* Expanded topics */}
      {showAllTopics && moreTopics.length > 0 && (
        <div className="filters filters-expanded">
          {moreTopics.map(({ topic, count }) => (
            <button
              key={topic}
              className={`filter-btn ${selectedTopic === topic ? 'active' : ''}`}
              onClick={() => setSelectedTopic(selectedTopic === topic ? null : topic)}
              aria-pressed={selectedTopic === topic}
              title={`${count} meeting${count !== 1 ? 's' : ''}`}
            >
              {topic}
              <span className="topic-count">({count})</span>
            </button>
          ))}
        </div>
      )}

      {/* Active filter indicator */}
      {(selectedBody || selectedTopic) && (
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
          {selectedTopic && (
            <span className="active-filter-tag">
              {selectedTopic}
              <button
                onClick={() => setSelectedTopic(null)}
                aria-label={`Remove ${selectedTopic} filter`}
              >
                ×
              </button>
            </span>
          )}
          <button
            className="clear-filters"
            onClick={() => {
              setSelectedBody(null)
              setSelectedTopic(null)
            }}
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}

export default TopicFilter
