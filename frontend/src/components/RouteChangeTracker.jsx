import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

// Fires a GA4 `page_view` on every React Router navigation (including the
// initial mount). The gtag loader + config live in index.html with
// send_page_view=false, so this component is the sole source of pageviews.
// Mounted inside <BrowserRouter> in App.jsx.
export default function RouteChangeTracker() {
  const location = useLocation()

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.gtag !== 'function') return
    const path = location.pathname + location.search
    window.gtag('event', 'page_view', {
      page_path: path,
      page_location: window.location.href,
      page_title: document.title,
    })
  }, [location.pathname, location.search])

  return null
}
