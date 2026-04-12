import { createContext, useContext, useState, useCallback, useRef } from 'react'

const AnnounceContext = createContext(null)

/**
 * Screen reader announcement provider + visually hidden aria-live region.
 * Usage:
 *   const announce = useAnnounce()
 *   announce('12 results loaded')          // polite
 *   announce('Error: request failed', 'assertive')
 */
export function AnnounceProvider({ children }) {
  const [message, setMessage] = useState('')
  const [politeness, setPoliteness] = useState('polite')
  const timeoutRef = useRef(null)

  const announce = useCallback((text, level = 'polite') => {
    // Clear then set to ensure repeated identical messages are announced
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    setMessage('')
    setPoliteness(level)
    timeoutRef.current = setTimeout(() => {
      setMessage(text)
    }, 100)
  }, [])

  return (
    <AnnounceContext.Provider value={announce}>
      {children}
      {/* Polite region */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {politeness === 'polite' ? message : ''}
      </div>
      {/* Assertive region */}
      <div
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        className="sr-only"
      >
        {politeness === 'assertive' ? message : ''}
      </div>
    </AnnounceContext.Provider>
  )
}

export function useAnnounce() {
  const ctx = useContext(AnnounceContext)
  if (!ctx) {
    // Fallback no-op when used outside provider (avoids crashes in tests)
    return () => {}
  }
  return ctx
}
