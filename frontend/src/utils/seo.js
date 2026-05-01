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

export function setMarkdownAlternate(url) {
  if (typeof document === 'undefined') return
  let el = document.head.querySelector('link[rel="alternate"][type="text/markdown"]')
  if (!url) {
    if (el) el.remove()
    return
  }
  if (!el) {
    el = document.createElement('link')
    el.setAttribute('rel', 'alternate')
    el.setAttribute('type', 'text/markdown')
    document.head.appendChild(el)
  }
  el.setAttribute('href', url)
}

const SITE_URL = 'https://meetings.lexingtonky.news'
const ORG_ID = `${SITE_URL}#organization`
const SITE_ID = `${SITE_URL}#website`

// Stable Organization + WebSite nodes — referenced by per-page Article
// via @id so Google sees a coherent graph across the archive.
export const ORGANIZATION_NODE = {
  '@type': 'Organization',
  '@id': ORG_ID,
  name: 'LFUCG Meeting Archive',
  url: SITE_URL,
  description:
    'Searchable archive of Lexington-Fayette Urban County Government council and committee meetings.',
  founder: {
    '@type': 'Person',
    '@id': `${SITE_URL}/about/paul-oliva#person`,
    name: 'Paul Oliva',
    url: 'https://pauloliva.com',
    sameAs: [
      'https://pauloliva.com',
      'https://github.com/paul-codes-1',
      'https://lexingtonky.news/author/paulmoliva/',
    ],
  },
}

export const WEBSITE_NODE = {
  '@type': 'WebSite',
  '@id': SITE_ID,
  url: SITE_URL,
  name: 'LFUCG Meeting Archive',
  publisher: { '@id': ORG_ID },
  potentialAction: {
    '@type': 'SearchAction',
    target: {
      '@type': 'EntryPoint',
      urlTemplate: `${SITE_URL}/?q={search_term_string}`,
    },
    'query-input': 'required name=search_term_string',
  },
}

// Build a coherent JSON-LD @graph for a meeting detail page. Article
// carries the auto-transcription disclosure via creativeWorkStatus +
// producer; Event names the underlying civic proceeding so Google
// treats the page as both reporting and primary record.
export function buildMeetingGraph({
  clipId,
  title,
  date,
  meetingBody,
  description,
  granicusUrl,
  summaryUpdatedAt,
  processedAt,
  transcriptWords,
}) {
  if (!clipId) return null
  const url = `${SITE_URL}/meeting/${clipId}`
  const seoTitle = buildSeoTitle(title, date)
  const dateModified = summaryUpdatedAt || processedAt || date
  // 6 PM local Eastern is the standard council meeting time. Granicus
  // titles don't carry a reliable start time and a missing startDate
  // downgrades the Event signal — better to use the canonical default.
  const startDate = date ? `${date}T18:00:00-04:00` : undefined
  const cleaned = cleanTitle(title) || 'LFUCG Meeting'

  const article = {
    '@type': 'Article',
    '@id': `${url}#article`,
    headline: seoTitle,
    name: seoTitle,
    url,
    mainEntityOfPage: url,
    datePublished: date,
    dateModified,
    author: { '@id': ORG_ID },
    publisher: { '@id': ORG_ID },
    isPartOf: { '@id': SITE_ID },
    inLanguage: 'en-US',
    creativeWorkStatus: 'Auto-transcribed',
    producer: {
      '@type': 'Organization',
      name: 'OpenAI Whisper-1 (audio→text), GPT-4o (fact extraction), Anthropic Claude Sonnet (narrative summary)',
    },
    isBasedOn: granicusUrl || undefined,
    description: description || undefined,
  }
  if (transcriptWords && transcriptWords > 0) {
    article.wordCount = transcriptWords
  }

  const event = {
    '@type': 'Event',
    '@id': `${url}#event`,
    name: cleaned,
    startDate,
    eventAttendanceMode: 'https://schema.org/MixedEventAttendanceMode',
    eventStatus: 'https://schema.org/EventScheduled',
    location: {
      '@type': 'Place',
      name: 'Government Center, Lexington, KY',
      address: {
        '@type': 'PostalAddress',
        addressLocality: 'Lexington',
        addressRegion: 'KY',
        addressCountry: 'US',
      },
    },
    organizer: {
      '@type': 'GovernmentOrganization',
      name: 'Lexington-Fayette Urban County Government',
      url: 'https://www.lexingtonky.gov/',
    },
    about: meetingBody || undefined,
    subjectOf: { '@id': `${url}#article` },
  }
  if (granicusUrl) {
    event.recordedIn = { '@type': 'CreativeWork', url: granicusUrl }
  }

  return {
    '@context': 'https://schema.org',
    '@graph': [article, event, ORGANIZATION_NODE, WEBSITE_NODE],
  }
}

// Inject (or replace) a JSON-LD <script> tag keyed by id so other
// components can own their own graph blocks (e.g. /about emits a
// separate Person graph) without stepping on this one.
export function setJsonLdScript(id, graph) {
  if (typeof document === 'undefined') return
  let el = document.getElementById(id)
  if (!graph) {
    if (el) el.remove()
    return
  }
  if (!el) {
    el = document.createElement('script')
    el.type = 'application/ld+json'
    el.id = id
    document.head.appendChild(el)
  }
  el.textContent = JSON.stringify(graph)
}
