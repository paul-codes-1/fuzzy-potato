import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { SearchProvider } from './contexts/SearchContext'
import { BrandingProvider } from './contexts/BrandingContext'
import App from './App'
import './index.css'

// ---------------------------------------------------------------------------
// Service Worker registration (PWA)
// ---------------------------------------------------------------------------

function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return

  window.addEventListener('load', async () => {
    try {
      const registration = await navigator.serviceWorker.register('/sw.js', {
        scope: '/',
      })
      console.log('[SW] Registered with scope:', registration.scope)

      // Check for updates periodically (every 60 minutes)
      setInterval(() => {
        registration.update()
      }, 60 * 60 * 1000)
    } catch (err) {
      console.warn('[SW] Registration failed:', err.message)
    }
  })
}

registerServiceWorker()

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <BrandingProvider>
        <SearchProvider>
          <App />
        </SearchProvider>
      </BrandingProvider>
    </BrowserRouter>
  </React.StrictMode>
)
