import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

// Fires a GA4 `page_view` on every React Router navigation (including the
// initial mount). The gtag loader + config live in index.html with
// send_page_view=false, so this component is the sole source of pageviews.
// Mounted inside <BrowserRouter> in App.jsx.
//
// Routes like /meeting/:id set document.title from an async fetch, so firing
// page_view on the location change alone reports the stale title (the
// pre-navigation one, or the index.html default). We watch <title> with a
// MutationObserver and fire once it settles, with a 1500ms failsafe for
// routes that don't change the title.
export default function RouteChangeTracker() {
  const location = useLocation()

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.gtag !== 'function') return
    const path = location.pathname + location.search
    const titleEl = document.querySelector('title')
    let fired = false
    let settleTimer = null
    let failsafeTimer = null

    const fire = () => {
      if (fired) return
      fired = true
      window.gtag('event', 'page_view', {
        page_path: path,
        page_location: window.location.href,
        page_title: document.title,
      })
    }

    const obs = titleEl
      ? new MutationObserver(() => {
          clearTimeout(settleTimer)
          settleTimer = setTimeout(fire, 150)
        })
      : null
    obs?.observe(titleEl, { childList: true, subtree: true, characterData: true })
    failsafeTimer = setTimeout(fire, 1500)

    return () => {
      obs?.disconnect()
      clearTimeout(settleTimer)
      clearTimeout(failsafeTimer)
    }
  }, [location.pathname, location.search])

  return null
}
