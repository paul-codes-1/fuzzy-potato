import { useI18n } from '../i18n/I18nProvider'

function TopicFilter({
  meetingBodies,
  selectedBody,
  setSelectedBody,
  sortBy,
  setSortBy
}) {
  const { t } = useI18n()

  return (
    <div className="filters-container">
      {/* Sort controls */}
      <div className="sort-controls">
        <label htmlFor="sort-select">{t('filters.sortBy')}</label>
        <select
          id="sort-select"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          className="sort-select"
        >
          <option value="date-desc">{t('filters.newestFirst')}</option>
          <option value="date-asc">{t('filters.oldestFirst')}</option>
          <option value="title">{t('filters.titleAZ')}</option>
        </select>
      </div>

      <div className="filters">
        {/* Meeting body filters */}
        <button
          className={`filter-btn ${!selectedBody ? 'active' : ''}`}
          onClick={() => setSelectedBody(null)}
          aria-pressed={!selectedBody}
        >
          {t('filters.allBodies')}
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
          <span>{t('filters.filteringBy')}</span>
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
            {t('filters.clearAll')}
          </button>
        </div>
      )}
    </div>
  )
}

export default TopicFilter
