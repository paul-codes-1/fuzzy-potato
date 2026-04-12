import { createContext, useContext, useState, useCallback, useMemo } from 'react'
import {
  SUPPORTED_LOCALES,
  DEFAULT_LOCALE,
  detectLocale,
  persistLocale,
  createT,
  formatDate as fmtDate,
  formatNumber as fmtNumber,
} from './index'

const I18nContext = createContext(null)

/**
 * I18nProvider -- wraps the app with locale state and translation helpers.
 *
 * Provides:
 *   locale         -- current locale code (e.g. 'en', 'es')
 *   setLocale(code) -- change locale (persists to localStorage)
 *   t(key, params) -- translate a key
 *   formatDate(dateStr) -- locale-aware date formatting
 *   formatNumber(num)   -- locale-aware number formatting
 *   locales        -- SUPPORTED_LOCALES map for building switchers
 */
export function I18nProvider({ children }) {
  const [locale, setLocaleState] = useState(() => detectLocale())

  const setLocale = useCallback((code) => {
    if (SUPPORTED_LOCALES[code]) {
      setLocaleState(code)
      persistLocale(code)
    }
  }, [])

  const t = useMemo(() => createT(locale), [locale])

  const formatDate = useCallback(
    (dateStr) => fmtDate(dateStr, locale),
    [locale]
  )

  const formatNumber = useCallback(
    (num) => fmtNumber(num, locale),
    [locale]
  )

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      t,
      formatDate,
      formatNumber,
      locales: SUPPORTED_LOCALES,
    }),
    [locale, setLocale, t, formatDate, formatNumber]
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

/**
 * useI18n hook -- access translation helpers from any component.
 *
 * const { t, locale, setLocale, formatDate, formatNumber } = useI18n()
 */
export function useI18n() {
  const ctx = useContext(I18nContext)
  if (!ctx) {
    throw new Error('useI18n must be used within an I18nProvider')
  }
  return ctx
}

export default I18nProvider
