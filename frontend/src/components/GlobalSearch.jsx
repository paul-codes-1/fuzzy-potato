import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useI18n } from '../i18n/I18nProvider'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const DEBOUNCE_MS = 200
const MAX_RECENT = 5
const RECENT_STORAGE_KEY = 'civiclens_recent_searches'

function getApiKey() {
  return sessionStorage.getItem('api_key') || sessionStorage.getItem('admin_api_key') || ''
}

// Category display config
const CATEGORY_CONFIG = {
  meeting: { label: 'Meetings', icon: 'M' },
  vote: { label: 'Votes', icon: 'V' },
  financial: { label: 'Financial Items', icon: '$' },
  speaker: { label: 'Speakers', icon: 'S' },
  topic: { label: 'Topics', icon: 'T' },
  agenda_item: { label: 'Agenda Items', icon: 'A' },
}

const AUTOCOMPLETE_CATEGORY_LABELS = {
  meeting_body: 'Body',
  speaker: 'Speaker',
  topic: 'Topic',
  meeting: 'Meeting',
  recent: 'Recent',
}

function getRecentSearches() {
  try {
    return JSON.parse(localStorage.getItem(RECENT_STORAGE_KEY) || '[]')
  } catch {
    return []
  }
}

function addRecentSearch(query) {
  if (!query.trim()) return
  const recent = getRecentSearches().filter((q) => q !== query)
  recent.unshift(query)
  localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)))
}

function HighlightedText({ html }) {
  return <span dangerouslySetInnerHTML={{ __html: html }} />
}

function GlobalSearch() {
  const { t } = useI18n()
  const navigate = useNavigate()

  const [query, setQuery] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [results, setResults] = useState([])
  const [suggestions, setSuggestions] = useState([])
  const [totalResults, setTotalResults] = useState(0)
  const [loading, setLoading] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [tookMs, setTookMs] = useState(0)

  const containerRef = useRef(null)
  const inputRef = useRef(null)
  const debounceRef = useRef(null)
  const abortRef = useRef(null)

  // Flatten results + suggestions into a navigable list
  const allItems = []
  if (suggestions.length > 0) {
    suggestions.forEach((s) => allItems.push({ kind: 'suggestion', data: s }))
  }
  if (results.length > 0) {
    results.forEach((r) => allItems.push({ kind: 'result', data: r }))
  }
  if (query.trim() && totalResults > results.length) {
    allItems.push({ kind: 'see_all' })
  }
  if (!query.trim()) {
    getRecentSearches().forEach((q) =>
      allItems.push({ kind: 'recent', data: { text: q } })
    )
  }

  // ---- API calls ----

  const fetchResults = useCallback(async (q) => {
    if (!q.trim()) {
      setResults([])
      setSuggestions([])
      setTotalResults(0)
      setTookMs(0)
      return
    }

    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    try {
      const key = getApiKey()
      const headers = {}
      if (key) headers['X-API-Key'] = key

      // Fire search and autocomplete in parallel
      const [searchRes, acRes] = await Promise.all([
        fetch(
          `${API_BASE}/api/v1/search?` +
            new URLSearchParams({ q, limit: '8' }),
          { headers, signal: controller.signal }
        ),
        fetch(
          `${API_BASE}/api/v1/search/autocomplete?` +
            new URLSearchParams({ q, limit: '5' }),
          { headers, signal: controller.signal }
        ),
      ])

      if (searchRes.ok) {
        const data = await searchRes.json()
        setResults(data.results || [])
        setTotalResults(data.total || 0)
        setTookMs(data.took_ms || 0)
      }

      if (acRes.ok) {
        const acData = await acRes.json()
        setSuggestions(acData.suggestions || [])
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('GlobalSearch fetch error:', err)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  // ---- Debounced input handler ----

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (!query.trim()) {
      setResults([])
      setSuggestions([])
      setTotalResults(0)
      setTookMs(0)
      setActiveIndex(-1)
      return
    }
    debounceRef.current = setTimeout(() => {
      fetchResults(query)
    }, DEBOUNCE_MS)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [query, fetchResults])

  // ---- Click outside to close ----

  useEffect(() => {
    function handleClick(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  // ---- Keyboard navigation ----

  function handleKeyDown(e) {
    if (!isOpen && e.key !== 'Escape') {
      if (query.trim() || e.key === 'ArrowDown') setIsOpen(true)
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActiveIndex((prev) => Math.min(prev + 1, allItems.length - 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setActiveIndex((prev) => Math.max(prev - 1, -1))
        break
      case 'Enter':
        e.preventDefault()
        if (activeIndex >= 0 && activeIndex < allItems.length) {
          selectItem(allItems[activeIndex])
        } else if (query.trim()) {
          navigateToFullSearch()
        }
        break
      case 'Escape':
        setIsOpen(false)
        setActiveIndex(-1)
        inputRef.current?.blur()
        break
    }
  }

  // ---- Item selection ----

  function selectItem(item) {
    if (item.kind === 'suggestion') {
      setQuery(item.data.text)
      addRecentSearch(item.data.text)
      if (item.data.clip_id) {
        navigate(`/meeting/${item.data.clip_id}`)
        setIsOpen(false)
      } else {
        fetchResults(item.data.text)
      }
    } else if (item.kind === 'result') {
      addRecentSearch(query)
      navigate(`/meeting/${item.data.clip_id}`)
      setIsOpen(false)
    } else if (item.kind === 'recent') {
      setQuery(item.data.text)
      fetchResults(item.data.text)
    } else if (item.kind === 'see_all') {
      navigateToFullSearch()
    }
    setActiveIndex(-1)
  }

  function navigateToFullSearch() {
    addRecentSearch(query)
    navigate(`/?q=${encodeURIComponent(query)}`)
    setIsOpen(false)
  }

  // ---- Render helpers ----

  function renderCategoryBadge(type) {
    const config = CATEGORY_CONFIG[type]
    if (!config) return null
    return (
      <span className={`gs-badge gs-badge--${type}`} title={config.label}>
        {config.icon}
      </span>
    )
  }

  const showDropdown = isOpen && (allItems.length > 0 || loading)

  return (
    <div className="gs-container" ref={containerRef}>
      <div className="gs-input-wrapper">
        <svg
          className="gs-search-icon"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          className="gs-input"
          placeholder={t('globalSearch.placeholder') || 'Search meetings, votes, speakers...'}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setIsOpen(true)
            setActiveIndex(-1)
          }}
          onFocus={() => setIsOpen(true)}
          onKeyDown={handleKeyDown}
          role="combobox"
          aria-expanded={showDropdown}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-controls="gs-dropdown"
        />
        {query && (
          <button
            className="gs-clear"
            onClick={() => {
              setQuery('')
              setResults([])
              setSuggestions([])
              setActiveIndex(-1)
              inputRef.current?.focus()
            }}
            aria-label="Clear search"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        )}
        {loading && <span className="gs-spinner" />}
        <kbd className="gs-kbd">/</kbd>
      </div>

      {showDropdown && (
        <div className="gs-dropdown" id="gs-dropdown" role="listbox">
          {/* Autocomplete suggestions */}
          {suggestions.length > 0 && (
            <div className="gs-section">
              <div className="gs-section-label">Suggestions</div>
              {suggestions.map((s, i) => {
                const itemIdx = i
                return (
                  <div
                    key={`sug-${i}`}
                    className={`gs-item gs-item--suggestion ${activeIndex === itemIdx ? 'gs-item--active' : ''}`}
                    role="option"
                    aria-selected={activeIndex === itemIdx}
                    onMouseEnter={() => setActiveIndex(itemIdx)}
                    onClick={() => selectItem({ kind: 'suggestion', data: s })}
                  >
                    <span className="gs-suggestion-icon">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="11" cy="11" r="8" />
                        <line x1="21" y1="21" x2="16.65" y2="16.65" />
                      </svg>
                    </span>
                    <span className="gs-suggestion-text">{s.text}</span>
                    <span className="gs-suggestion-cat">
                      {AUTOCOMPLETE_CATEGORY_LABELS[s.category] || s.category}
                    </span>
                  </div>
                )
              })}
            </div>
          )}

          {/* Search results by category */}
          {results.length > 0 && (
            <div className="gs-section">
              <div className="gs-section-label">
                Results
                {tookMs > 0 && (
                  <span className="gs-took">{tookMs}ms</span>
                )}
              </div>
              {results.map((r, i) => {
                const itemIdx = suggestions.length + i
                return (
                  <div
                    key={`res-${r.clip_id}-${i}`}
                    className={`gs-item gs-item--result ${activeIndex === itemIdx ? 'gs-item--active' : ''}`}
                    role="option"
                    aria-selected={activeIndex === itemIdx}
                    onMouseEnter={() => setActiveIndex(itemIdx)}
                    onClick={() => selectItem({ kind: 'result', data: r })}
                  >
                    {renderCategoryBadge(r.type)}
                    <div className="gs-result-content">
                      <div className="gs-result-title">
                        <HighlightedText html={r.title} />
                      </div>
                      {r.snippet && (
                        <div className="gs-result-snippet">
                          <HighlightedText html={r.snippet} />
                        </div>
                      )}
                      <div className="gs-result-meta">
                        {r.date && <span>{r.date}</span>}
                        {r.meeting_body && <span>{r.meeting_body}</span>}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* See all results link */}
          {query.trim() && totalResults > results.length && (
            <div
              className={`gs-item gs-item--see-all ${
                activeIndex === suggestions.length + results.length ? 'gs-item--active' : ''
              }`}
              role="option"
              aria-selected={activeIndex === suggestions.length + results.length}
              onMouseEnter={() => setActiveIndex(suggestions.length + results.length)}
              onClick={navigateToFullSearch}
            >
              See all {totalResults} results for "{query}"
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </div>
          )}

          {/* Recent searches (shown when input is empty and focused) */}
          {!query.trim() && getRecentSearches().length > 0 && (
            <div className="gs-section">
              <div className="gs-section-label">Recent Searches</div>
              {getRecentSearches().map((q, i) => {
                const itemIdx = i
                return (
                  <div
                    key={`rec-${i}`}
                    className={`gs-item gs-item--recent ${activeIndex === itemIdx ? 'gs-item--active' : ''}`}
                    role="option"
                    aria-selected={activeIndex === itemIdx}
                    onMouseEnter={() => setActiveIndex(itemIdx)}
                    onClick={() => selectItem({ kind: 'recent', data: { text: q } })}
                  >
                    <span className="gs-recent-icon">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10" />
                        <polyline points="12 6 12 12 16 14" />
                      </svg>
                    </span>
                    <span className="gs-suggestion-text">{q}</span>
                  </div>
                )
              })}
            </div>
          )}

          {/* Loading state */}
          {loading && results.length === 0 && suggestions.length === 0 && (
            <div className="gs-item gs-item--loading">
              Searching...
            </div>
          )}

          {/* No results */}
          {!loading && query.trim() && results.length === 0 && suggestions.length === 0 && (
            <div className="gs-item gs-item--empty">
              No results found for "{query}"
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default GlobalSearch
