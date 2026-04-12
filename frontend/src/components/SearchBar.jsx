import { useI18n } from '../i18n/I18nProvider'
import SaveSearchButton from './SaveSearchButton'

function SearchBar({
  query,
  setQuery,
  flexSearchLoading,
  flexSearchLoaded,
  flexSearchProgress
}) {
  const { t } = useI18n()
  const { loaded, total } = flexSearchProgress || { loaded: 0, total: 0 }
  const showProgress = flexSearchLoading && total > 1

  return (
    <div className="search-bar-container" role="search" aria-label="Search meetings">
      <div className="search-input-wrapper">
        <span className="search-icon" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </span>
        <label htmlFor="meeting-search-input" className="sr-only">Search meetings</label>
        <input
          id="meeting-search-input"
          type="search"
          className="search-input"
          placeholder={t('search.placeholder')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button
            className="search-clear-btn"
            onClick={() => setQuery('')}
            aria-label={t('search.clearSearch')}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
        {flexSearchLoading && (
          <span className="search-loading-indicator" title={showProgress ? t('search.loadingChunks', { loaded, total }) : t('search.loadingIndex')}>
            <span className="spinner"></span>
            {showProgress && (
              <span className="loading-progress">{loaded}/{total}</span>
            )}
          </span>
        )}
        {flexSearchLoaded && (
          <span className="search-ready-indicator" title={t('search.searchReady')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          </span>
        )}
      </div>
      {query && query.trim().length > 0 && (
        <div className="search-bar-actions" style={{ marginLeft: 8 }}>
          <SaveSearchButton
            searchType="meeting_search"
            queryText={query}
            label="Save search"
          />
        </div>
      )}
    </div>
  )
}

export default SearchBar
