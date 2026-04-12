import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const DEFAULTS = {
  display_name: 'CivicLens',
  logo_url: '',
  primary_color: '#1a56db',
  secondary_color: '#1e293b',
  accent_color: '#f59e0b',
  favicon_url: '',
  custom_css: '',
  welcome_message: 'Search and explore your local government meetings.',
  footer_text: '',
  support_email: '',
}

const BrandingContext = createContext(null)

/**
 * Map branding config values to CSS custom properties on :root.
 */
function applyCSSVariables(branding) {
  const root = document.documentElement
  root.style.setProperty('--brand-primary', branding.primary_color || DEFAULTS.primary_color)
  root.style.setProperty('--brand-secondary', branding.secondary_color || DEFAULTS.secondary_color)
  root.style.setProperty('--brand-accent', branding.accent_color || DEFAULTS.accent_color)
}

/**
 * Inject or update a <style> element for tenant custom CSS.
 */
function applyCustomCSS(css) {
  const STYLE_ID = 'brand-custom-css'
  let el = document.getElementById(STYLE_ID)
  if (!css) {
    if (el) el.remove()
    return
  }
  if (!el) {
    el = document.createElement('style')
    el.id = STYLE_ID
    document.head.appendChild(el)
  }
  el.textContent = css
}

/**
 * Update the page favicon if a custom one is provided.
 */
function applyFavicon(url) {
  if (!url) return
  let link = document.querySelector("link[rel~='icon']")
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.href = url
}

export function BrandingProvider({ children, apiBase }) {
  const [branding, setBranding] = useState(DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const base = apiBase || (import.meta.env.VITE_API_BASE || '')

  const fetchBranding = useCallback(async () => {
    try {
      setLoading(true)
      const headers = {}
      const apiKey = sessionStorage.getItem('api_key')
      if (apiKey) {
        headers['X-API-Key'] = apiKey
      }

      const res = await fetch(`${base}/api/v1/branding`, { headers })
      if (!res.ok) {
        // Non-critical -- fall back to defaults silently
        console.warn('Failed to load branding, using defaults')
        return
      }
      const data = await res.json()
      const merged = { ...DEFAULTS, ...data }
      setBranding(merged)
      applyCSSVariables(merged)
      applyCustomCSS(merged.custom_css)
      applyFavicon(merged.favicon_url)

      if (merged.display_name) {
        document.title = merged.display_name
      }
    } catch (err) {
      console.warn('Branding fetch error:', err.message)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [base])

  useEffect(() => {
    // Apply default CSS vars immediately so colours are never missing
    applyCSSVariables(DEFAULTS)
    fetchBranding()
  }, [fetchBranding])

  return (
    <BrandingContext.Provider value={{ branding, loading, error, refetch: fetchBranding }}>
      {children}
    </BrandingContext.Provider>
  )
}

export function useBranding() {
  const ctx = useContext(BrandingContext)
  if (!ctx) throw new Error('useBranding must be used within BrandingProvider')
  return ctx
}
