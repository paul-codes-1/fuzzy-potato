/**
 * Locale coverage guardrail.
 *
 * Ensures every non-en locale defines the same keys as en.json. This test
 * exists so future PRs cannot silently drift -- if a new key is added to
 * en.json, it must be added (or explicitly English-fallback'd) to every
 * other locale file at the same time.
 *
 * Rules:
 *   1. Every locale file must be valid JSON with a top-level object shape.
 *   2. Every non-en locale must contain the EXACT same set of keys as en.
 *   3. No value anywhere may be an empty string.
 *   4. Every locale must be registered in SUPPORTED_LOCALES.
 *
 * Note: A non-en locale is allowed to reuse the English string as its value
 * (see the `{locale}.todo.md` files for the list of intentional fallbacks),
 * but it must NOT simply omit the key.
 */

import { describe, it, expect } from 'vitest'
import en from '../locales/en.json'
import es from '../locales/es.json'
import zh from '../locales/zh.json'
import vi from '../locales/vi.json'
import ko from '../locales/ko.json'
import { SUPPORTED_LOCALES } from '../index'

const LOCALES = { en, es, zh, vi, ko }

/**
 * Recursively collect all leaf key paths in an object, e.g.
 * { a: { b: 'x' } } -> ['a.b']
 */
function collectKeys(obj, prefix = '') {
  const keys = []
  for (const k of Object.keys(obj)) {
    const v = obj[k]
    const path = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      keys.push(...collectKeys(v, path))
    } else {
      keys.push(path)
    }
  }
  return keys
}

/** Read a nested value by dot path. */
function getAtPath(obj, path) {
  return path.split('.').reduce((acc, part) => (acc == null ? acc : acc[part]), obj)
}

describe('locale coverage', () => {
  const enKeys = collectKeys(en).sort()

  it('en.json has a non-trivial key set', () => {
    expect(enKeys.length).toBeGreaterThan(50)
  })

  it.each(Object.keys(LOCALES))('%s is valid JSON with a top-level object shape', (code) => {
    const data = LOCALES[code]
    expect(data).toBeTypeOf('object')
    expect(data).not.toBeNull()
    expect(Array.isArray(data)).toBe(false)
    // Spot-check top-level shape: every locale should at least define nav + header.
    expect(data).toHaveProperty('nav')
    expect(data).toHaveProperty('header')
  })

  it.each(Object.keys(LOCALES).filter((c) => c !== 'en'))(
    '%s contains every key present in en.json',
    (code) => {
      const localeKeys = collectKeys(LOCALES[code]).sort()
      const missing = enKeys.filter((k) => !localeKeys.includes(k))
      const extra = localeKeys.filter((k) => !enKeys.includes(k))
      expect(missing, `${code} is missing keys: ${missing.join(', ')}`).toEqual([])
      expect(extra, `${code} has extra keys not in en: ${extra.join(', ')}`).toEqual([])
    }
  )

  it.each(Object.keys(LOCALES))('%s has no empty-string values', (code) => {
    const data = LOCALES[code]
    const keys = collectKeys(data)
    const empties = keys.filter((k) => getAtPath(data, k) === '')
    expect(empties, `${code} has empty values for keys: ${empties.join(', ')}`).toEqual([])
  })

  it.each(Object.keys(LOCALES))('%s is registered in SUPPORTED_LOCALES', (code) => {
    expect(SUPPORTED_LOCALES).toHaveProperty(code)
    expect(SUPPORTED_LOCALES[code]).toHaveProperty('messages')
    expect(SUPPORTED_LOCALES[code]).toHaveProperty('nativeName')
    expect(SUPPORTED_LOCALES[code]).toHaveProperty('flag')
  })
})
