// Runtime, jurisdiction-aware site config.
//
// The SPA is a SINGLE bundle served to every jurisdiction (LFUCG, Paris, …).
// All jurisdiction-specific copy — header, tagline, footer, video provider,
// chat text, contact email — lives in /data/site.json, generated per-county by
// the Python pipeline (config.build_site_config). main.jsx fetches it ONCE
// before the first render, so every component can read getSiteConfig()
// synchronously.
//
// DEFAULT_SITE_CONFIG mirrors the historical hard-coded LFUCG strings, so the
// LFUCG site stays byte-identical and dev / tests / a failed fetch all fall
// back to LFUCG. NOTE: read config INSIDE components/functions (render time),
// never at module top-level — module constants evaluate before the boot fetch
// resolves and would freeze the defaults.

export const DEFAULT_SITE_CONFIG = {
  archive_name: 'LFUCG Meeting Archive',
  jurisdiction_full_name: 'Lexington-Fayette Urban County Government',
  jurisdiction_short_name: 'LFUCG',
  site_url: 'https://meetings.lexingtonky.news',
  contact_email: 'editor@lexingtonky.news',
  operator_name: 'Paul Oliva',
  operator_bio: 'Operated by Paul Oliva as a civic-tech side project.',
  operator_author_url: 'https://lexingtonky.news/author/paulmoliva/',
  tagline: 'Lexington-Fayette Urban County Government Meeting Transcripts & Summaries',
  description:
    'Searchable archive of Lexington-Fayette Urban County Government council and committee meetings — transcripts, summaries, agendas, and minutes.',
  default_seo_title: 'LFUCG Meeting',
  records_table_name: 'Table of Motions',
  show_coverage_note: true,
  source: { kind: 'video', platform: 'Granicus', has_transcript: true },
  video: {
    provider: 'granicus',
    granicus_base_url: 'https://lfucg.granicus.com',
    granicus_view_id: 14,
  },
  chat: {
    title: 'ChatLFUCG',
    description: 'Ask questions about Lexington city council meetings, votes, budgets, and more.',
    placeholder: 'Ask a question about Lexington city meetings...',
  },
  feeds: {
    enabled: true,
    base_url: 'https://feeds.lexingtonky.news',
    source_id: 'lfucg-meeting-archive',
    publication_name: 'Lexington Times',
  },
}

function deepMerge(base, override) {
  if (!override || typeof override !== 'object') return base
  const out = Array.isArray(base) ? [...base] : { ...base }
  for (const [k, v] of Object.entries(override)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && base && typeof base[k] === 'object') {
      out[k] = deepMerge(base[k], v)
    } else {
      out[k] = v
    }
  }
  return out
}

let _config = DEFAULT_SITE_CONFIG

export function getSiteConfig() {
  return _config
}

// Test/SSR seam — set the active config directly.
export function setSiteConfig(partial) {
  _config = deepMerge(DEFAULT_SITE_CONFIG, partial || {})
  return _config
}

// Fetch /data/site.json and merge over the defaults. Never throws — a missing
// or malformed file leaves the LFUCG defaults in place.
export async function loadSiteConfig() {
  try {
    const res = await fetch('/data/site.json', { cache: 'no-cache' })
    if (res.ok) {
      _config = deepMerge(DEFAULT_SITE_CONFIG, await res.json())
    }
  } catch {
    // keep defaults
  }
  return _config
}
