// Mirrors the Python helpers in `seo.py` so server-generated artifacts
// (news-sitemap, llms.txt) and client-side titles agree on the format.
// Keep this file in sync with `seo.py::clean_title / format_long_date /
// build_seo_title / build_seo_description`.

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const GRANICUS_SUFFIX_RE = /\s*\(\d+\)\s*$/
const TIMESTAMP_RE = /\[timestamp:\s*\d+:\d+\]/g
const HEADER_RE = /^##\s.*$/gm

export function cleanTitle(title) {
  return (title || '').trim().replace(GRANICUS_SUFFIX_RE, '')
}

export function formatLongDate(isoDate) {
  if (!isoDate) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate)
  if (!m) return isoDate
  const year = parseInt(m[1], 10)
  const month = parseInt(m[2], 10)
  const day = parseInt(m[3], 10)
  if (month < 1 || month > 12) return isoDate
  return `${MONTHS[month - 1]} ${day}, ${year}`
}

export function buildSeoTitle(title, isoDate) {
  const cleaned = cleanTitle(title)
  const formatted = formatLongDate(isoDate)
  if (cleaned && formatted) return `${cleaned} - ${formatted}`
  return cleaned || formatted || 'LFUCG Meeting'
}

export function buildSeoDescription(summaryText, fallback = '', maxChars = 200) {
  let text = (summaryText || fallback || '').replace(HEADER_RE, '').replace(TIMESTAMP_RE, '')
  const paragraphs = text.split(/\n\s*\n/)
  for (const para of paragraphs) {
    const cleaned = para.replace(/\s+/g, ' ').trim()
    if (cleaned.length >= 50) {
      if (cleaned.length > maxChars) {
        const cut = cleaned.slice(0, maxChars - 1)
        const lastSpace = cut.lastIndexOf(' ')
        const truncated = lastSpace > 0 ? cut.slice(0, lastSpace) : cut
        return truncated.replace(/[,.;:]+$/, '') + '…'
      }
      return cleaned
    }
  }
  return ''
}

// Set or replace a <meta> tag in document.head. Used for description /
// og: / twitter: tags that need to update on every clip-page mount.
export function setMetaTag(attrName, attrValue, content) {
  if (typeof document === 'undefined') return
  let el = document.head.querySelector(`meta[${attrName}="${attrValue}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attrName, attrValue)
    document.head.appendChild(el)
  }
  el.setAttribute('content', content)
}

export function setCanonical(url) {
  if (typeof document === 'undefined') return
  let el = document.head.querySelector('link[rel="canonical"]')
  if (!el) {
    el = document.createElement('link')
    el.setAttribute('rel', 'canonical')
    document.head.appendChild(el)
  }
  el.setAttribute('href', url)
}
