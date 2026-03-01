import { createContext, useContext, useEffect } from 'react'
import { useFlexSearch } from '../hooks/useFlexSearch'

const SearchContext = createContext(null)

export function SearchProvider({ children }) {
  const flexSearch = useFlexSearch()

  // Start loading the index immediately on mount
  useEffect(() => {
    if (!flexSearch.isLoaded && !flexSearch.isLoading) {
      flexSearch.loadIndex()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <SearchContext.Provider value={flexSearch}>
      {children}
    </SearchContext.Provider>
  )
}

export function useSearchIndex() {
  const ctx = useContext(SearchContext)
  if (!ctx) throw new Error('useSearchIndex must be used within SearchProvider')
  return ctx
}
