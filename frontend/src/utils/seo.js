// Mirrors the Python helpers in `seo.py` so server-generated artifacts
// (news-sitemap, llms.txt) and client-side titles agree on the format.
// Keep this file in sync with `seo.py::clean_title / format_long_date /
// build_seo_title / build_seo_description`.

import { getSiteConfig } from '../config/site'

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
  return cleaned || formatted || getSiteConfig().default_seo_title
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

// Stable Organization + WebSite nodes — referenced by per-page Article via @id
// so Google sees a coherent graph across the archive. Built from the runtime
// site config (functions, not constants, so they read the config that loaded at
// boot rather than freezing the import-time default).
export function organizationNode() {
  const site = getSiteConfig()
  const orgId = `${site.site_url}#organization`
  const founder = {
    '@type': 'Person',
    '@id': `${site.site_url}/about/${(site.operator_name || 'operator').toLowerCase().replace(/\s+/g, '-')}#person`,
    name: site.operator_name,
  }
  if (site.operator_author_url) {
    founder.sameAs = [site.operator_author_url]
  }
  return {
    '@type': 'Organization',
    '@id': orgId,
    name: site.archive_name,
    url: site.site_url,
    description: site.description,
    founder,
  }
}

export function websiteNode() {
  const site = getSiteConfig()
  return {
    '@type': 'WebSite',
    '@id': `${site.site_url}#website`,
    url: site.site_url,
    name: site.archive_name,
    publisher: { '@id': `${site.site_url}#organization` },
    potentialAction: {
      '@type': 'SearchAction',
      target: {
        '@type': 'EntryPoint',
        urlTemplate: `${site.site_url}/?q={search_term_string}`,
      },
      'query-input': 'required name=search_term_string',
    },
  }
}

// Build a coherent JSON-LD @graph for a meeting detail page. Article
// carries the auto-transcription disclosure via creativeWorkStatus +
// producer. Earlier versions also emitted an Event node, but these
// pages are archival recordings of past proceedings — Event is for
// upcoming attendable things and was triggering Search Console
// "missing required field" reports.
export function buildMeetingGraph({
  clipId,
  title,
  date,
  meetingBody: _meetingBody,
  description,
  granicusUrl,
  summaryUpdatedAt,
  processedAt,
  transcriptWords,
}) {
  if (!clipId) return null
  const site = getSiteConfig()
  const orgId = `${site.site_url}#organization`
  const siteId = `${site.site_url}#website`
  const url = `${site.site_url}/meeting/${clipId}`
  const seoTitle = buildSeoTitle(title, date)
  const dateModified = summaryUpdatedAt || processedAt || date
  const producerName =
    site.source?.kind === 'document'
      ? 'GPT-4o (fact extraction), Anthropic Claude Sonnet (narrative summary)'
      : 'OpenAI Whisper-1 (audio→text), GPT-4o (fact extraction), Anthropic Claude Sonnet (narrative summary)'

  const article = {
    '@type': 'Article',
    '@id': `${url}#article`,
    headline: seoTitle,
    name: seoTitle,
    url,
    mainEntityOfPage: url,
    datePublished: date,
    dateModified,
    author: { '@id': orgId },
    publisher: { '@id': orgId },
    isPartOf: { '@id': siteId },
    inLanguage: 'en-US',
    creativeWorkStatus: site.source?.kind === 'document' ? 'Auto-summarized' : 'Auto-transcribed',
    producer: {
      '@type': 'Organization',
      name: producerName,
    },
    isBasedOn: granicusUrl || undefined,
    description: description || undefined,
  }
  if (transcriptWords && transcriptWords > 0) {
    article.wordCount = transcriptWords
  }

  return {
    '@context': 'https://schema.org',
    '@graph': [article, organizationNode(), websiteNode()],
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
