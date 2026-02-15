function SearchBar({
  query,
  setQuery,
  searchMode,
  setSearchMode,
  flexSearchLoading,
  flexSearchLoaded,
  flexSearchProgress
}) {
  const placeholder = searchMode === 'full'
    ? 'Search transcripts... (use "quotes" for exact word match)'
    : 'Search meetings by title, topic, or content...'

  const { loaded, total } = flexSearchProgress || { loaded: 0, total: 0 }
  const showProgress = searchMode === 'full' && flexSearchLoading && total > 1

  return (
    <div className="search-bar-container">
      <input
        type="text"
        className="search-input"
        placeholder={placeholder}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="search-mode-toggle">
        <label className="search-mode-option">
          <input
            type="radio"
            name="searchMode"
            value="quick"
            checked={searchMode === 'quick'}
            onChange={() => setSearchMode('quick')}
          />
          <span>Quick search</span>
        </label>
        <label className="search-mode-option">
          <input
            type="radio"
            name="searchMode"
            value="full"
            checked={searchMode === 'full'}
            onChange={() => setSearchMode('full')}
          />
          <span>Search full transcripts</span>
          {searchMode === 'full' && flexSearchLoading && (
            <span className="search-loading-indicator" title={showProgress ? `Loading ${loaded}/${total} chunks...` : 'Loading search index...'}>
              <span className="spinner"></span>
              {showProgress && (
                <span className="loading-progress">{loaded}/{total}</span>
              )}
            </span>
          )}
          {searchMode === 'full' && flexSearchLoaded && (
            <span className="search-ready-indicator" title="Full-text search ready">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </span>
          )}
        </label>
      </div>
    </div>
  )
}

export default SearchBar
