import { useEffect, useState } from 'react'
import { getSiteConfig } from '../config/site'

// Module-level cache so navigating between meeting pages doesn't
// re-fetch what we already know. Keyed by clipId; values are either
// the resolved {slug, title, link, publishedAt} payload or null when
// the lookup returned 404 (clip not yet ingested into feeds).
const _cache = new Map()

/**
 * Look up the feeds.lexingtonky.news article that corresponds to this
 * meeting clip, via /api/by-source. Returns null when no article
 * exists yet (or the lookup failed); consumers should hide the UI
 * silently rather than treat that as an error.
 *
 * @param {number|string} clipId
 */
export function useFeedsLink(clipId) {
  const [link, setLink] = useState(() => _cache.get(String(clipId)) ?? null)

  useEffect(() => {
    const feeds = getSiteConfig().feeds || {}
    // The feeds cross-link is an LFUCG-only integration; off elsewhere.
    if (!clipId || !feeds.enabled || !feeds.base_url) {
      setLink(null)
      return
    }

    const key = String(clipId)
    if (_cache.has(key)) {
      setLink(_cache.get(key))
      return
    }

    let cancelled = false
    const controller = new AbortController()
    const url = `${feeds.base_url}/api/by-source?source=${encodeURIComponent(feeds.source_id || '')}&clipId=${encodeURIComponent(key)}`

    fetch(url, { signal: controller.signal })
      .then((r) => {
        if (r.status === 404) return null
        if (!r.ok) throw new Error(`feeds by-source ${r.status}`)
        return r.json()
      })
      .then((data) => {
        if (cancelled) return
        // Cache both hits and 404s so we don't re-fetch for the
        // session — the cron is on a 6h cadence, a 404 isn't going to
        // become a 200 mid-pageview.
        _cache.set(key, data)
        setLink(data)
      })
      .catch((err) => {
        if (err?.name === 'AbortError') return
        // Surface only in dev tools — a feeds outage shouldn't break
        // the meeting page.
        console.warn('useFeedsLink: lookup failed', err)
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [clipId])

  return link
}
