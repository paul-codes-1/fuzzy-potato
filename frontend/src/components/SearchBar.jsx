function SearchBar({
  query,
  setQuery,
  flexSearchLoading,
  flexSearchLoaded,
  flexSearchProgress
}) {
  const { loaded, total } = flexSearchProgress || { loaded: 0, total: 0 }
  const showProgress = flexSearchLoading && total > 1

  return (
    <div className="search-bar-container">
      <div className="search-input-wrapper">
        <span className="search-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </span>
        <input
          type="text"
          className="search-input"
          placeholder='Search transcripts... (use "quotes" for exact match)'
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button
            className="search-clear-btn"
            onClick={() => setQuery('')}
            aria-label="Clear search"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
        {flexSearchLoading && (
          <span className="search-loading-indicator" title={showProgress ? `Loading ${loaded}/${total} chunks...` : 'Loading search index...'}>
            <span className="spinner"></span>
            {showProgress && (
              <span className="loading-progress">{loaded}/{total}</span>
            )}
          </span>
        )}
        {flexSearchLoaded && (
          <span className="search-ready-indicator" title="Full-text search ready">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          </span>
        )}
      </div>
    </div>
  )
}

export default SearchBar
