import { useState, useRef, useEffect } from 'react'
import { useSuggestions } from '../hooks/useSuggestions'

const KIND_LABEL = {
  title: 'Meeting',
  body: 'Body',
  topic: 'Topic',
  speaker: 'Speaker',
}

function SearchBar({ query, setQuery, isSearching }) {
  const [focused, setFocused] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const containerRef = useRef(null)
  const suggestions = useSuggestions(query, { limit: 8 })

  // Hide dropdown on outside click. The autocomplete is mounted at
  // document level so a focus shift inside the input keeps it open.
  useEffect(() => {
    function onClick(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setFocused(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const showSuggestions = focused && suggestions.length > 0 && query.trim().length >= 2

  const apply = (term) => {
    setQuery(term)
    setFocused(false)
    setActiveIdx(-1)
  }

  const onKeyDown = (e) => {
    if (!showSuggestions) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIdx(i => Math.min(i + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(i - 1, -1))
    } else if (e.key === 'Enter' && activeIdx >= 0) {
      e.preventDefault()
      apply(suggestions[activeIdx].term)
    } else if (e.key === 'Escape') {
      setFocused(false)
      setActiveIdx(-1)
    }
  }

  return (
    <div className="search-bar-container" ref={containerRef}>
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
          placeholder='Search transcripts… (use "quotes" for exact phrases)'
          value={query}
          onChange={(e) => { setQuery(e.target.value); setActiveIdx(-1) }}
          onFocus={() => setFocused(true)}
          onKeyDown={onKeyDown}
          autoComplete="off"
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
        {isSearching && (
          <span className="search-loading-indicator" title="Searching...">
            <span className="spinner"></span>
          </span>
        )}
      </div>
      {showSuggestions && (
        <ul className="search-suggestions" role="listbox">
          {suggestions.map((s, i) => (
            <li
              key={`${s.kind}:${s.term}`}
              className={`search-suggestion ${i === activeIdx ? 'active' : ''}`}
              role="option"
              aria-selected={i === activeIdx}
              onMouseDown={() => apply(s.term)}
              onMouseEnter={() => setActiveIdx(i)}
            >
              <span className="search-suggestion-term">{s.term}</span>
              <span className="search-suggestion-kind">{KIND_LABEL[s.kind] || s.kind}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default SearchBar
