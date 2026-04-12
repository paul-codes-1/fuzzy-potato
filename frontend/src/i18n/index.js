/**
 * Lightweight i18n system for CivicLens.
 *
 * No heavy library -- just a context, translation files, and a t() helper.
 *
 * Language detection order:
 *   1. ?lang= URL parameter
 *   2. localStorage('civiclens-lang')
 *   3. navigator.language / navigator.languages
 *   4. 'en' (fallback)
 *
 * Supports: en, es, zh, vi, ko
 */

import en from './locales/en.json'
import es from './locales/es.json'
import zh from './locales/zh.json'
import vi from './locales/vi.json'
import ko from './locales/ko.json'

// Supported locales. Non-en locales may contain a small number of English
// fallback strings for specialized legal/civic terminology -- see the
// corresponding `{locale}.todo.md` files for the list.
export const SUPPORTED_LOCALES = {
  en: { label: 'English', flag: '\uD83C\uDDFA\uD83C\uDDF8', nativeName: 'English', messages: en },
  es: { label: 'Espa\u00f1ol', flag: '\uD83C\uDDEA\uD83C\uDDF8', nativeName: 'Espa\u00f1ol', messages: es },
  zh: { label: '\u4E2D\u6587', flag: '\uD83C\uDDE8\uD83C\uDDF3', nativeName: '\u4E2D\u6587', messages: zh },
  vi: { label: 'Ti\u1EBFng Vi\u1EC7t', flag: '\uD83C\uDDFB\uD83C\uDDF3', nativeName: 'Ti\u1EBFng Vi\u1EC7t', messages: vi },
  ko: { label: '\uD55C\uAD6D\uC5B4', flag: '\uD83C\uDDF0\uD83C\uDDF7', nativeName: '\uD55C\uAD6D\uC5B4', messages: ko },
}

export const DEFAULT_LOCALE = 'en'

const STORAGE_KEY = 'civiclens-lang'

// ---------------------------------------------------------------------------
// Locale detection
// ---------------------------------------------------------------------------

/**
 * Detect the best locale from available signals.
 * Order: URL ?lang= > localStorage > browser > default
 */
export function detectLocale() {
  // 1. URL parameter
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search)
    const urlLang = params.get('lang')
    if (urlLang && SUPPORTED_LOCALES[urlLang]) {
      return urlLang
    }
  }

  // 2. localStorage
  if (typeof localStorage !== 'undefined') {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && SUPPORTED_LOCALES[stored]) {
      return stored
    }
  }

  // 3. Browser language(s)
  if (typeof navigator !== 'undefined') {
    const langs = navigator.languages || [navigator.language]
    for (const lang of langs) {
      // Try exact match first (e.g. "es")
      const code = lang.toLowerCase().split('-')[0]
      if (SUPPORTED_LOCALES[code]) {
        return code
      }
    }
  }

  // 4. Default
  return DEFAULT_LOCALE
}

/**
 * Persist locale choice to localStorage.
 */
export function persistLocale(locale) {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, locale)
  }
}

// ---------------------------------------------------------------------------
// Translation helper
// ---------------------------------------------------------------------------

/**
 * Resolve a nested key like "nav.browse" from a messages object.
 */
function resolveKey(messages, key) {
  const parts = key.split('.')
  let current = messages
  for (const part of parts) {
    if (current == null || typeof current !== 'object') return undefined
    current = current[part]
  }
  return current
}

/**
 * Simple ICU-like plural resolver.
 * Pattern: "{count, plural, one {meeting} other {meetings}}"
 * Only handles "one" vs "other" (covers English/Spanish).
 */
function resolvePlural(template, params) {
  return template.replace(
    /\{(\w+),\s*plural,\s*one\s*\{([^}]*)\}\s*other\s*\{([^}]*)\}\}/g,
    (_, paramName, oneForm, otherForm) => {
      const count = params[paramName]
      return count === 1 ? oneForm : otherForm
    }
  )
}

/**
 * Interpolate {param} placeholders in a string.
 */
function interpolate(template, params) {
  if (!params) return template
  // First resolve plurals
  let result = resolvePlural(template, params)
  // Then simple interpolation
  result = result.replace(/\{(\w+)\}/g, (_, key) => {
    return params[key] !== undefined ? String(params[key]) : `{${key}}`
  })
  return result
}

/**
 * Create a t() function bound to a specific locale.
 *
 * Usage:
 *   t('nav.browse')                          -> "Browse"
 *   t('meetingList.meetingCount', { count: 5 }) -> "5 meetings"
 *
 * Falls back to English if the key is missing in the current locale,
 * then falls back to the key itself.
 */
export function createT(locale) {
  const messages = SUPPORTED_LOCALES[locale]?.messages || en
  const fallback = en

  return function t(key, params) {
    let value = resolveKey(messages, key)
    if (value === undefined) {
      value = resolveKey(fallback, key)
    }
    if (value === undefined) {
      return key // return key as-is if not found anywhere
    }
    if (typeof value !== 'string') {
      return key
    }
    return interpolate(value, params)
  }
}

// ---------------------------------------------------------------------------
// Date / number formatting per locale
// ---------------------------------------------------------------------------

const LOCALE_MAP = {
  en: 'en-US',
  es: 'es-US',
  zh: 'zh-CN',
  vi: 'vi-VN',
  ko: 'ko-KR',
}

/**
 * Format a date string (YYYY-MM-DD) according to the locale.
 * Returns the long date (e.g. "Monday, January 8, 2026" in English).
 */
export function formatDate(dateStr, locale = DEFAULT_LOCALE) {
  if (!dateStr) return ''
  const bcp47 = LOCALE_MAP[locale] || 'en-US'
  const date = new Date(dateStr + 'T00:00:00')
  return date.toLocaleDateString(bcp47, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/**
 * Format a number with locale-appropriate grouping (e.g. 1,234 vs 1.234).
 */
export function formatNumber(num, locale = DEFAULT_LOCALE) {
  if (num == null) return ''
  const bcp47 = LOCALE_MAP[locale] || 'en-US'
  return new Intl.NumberFormat(bcp47).format(num)
}
