// Turn the RAG answer's bracket citations into links.
//
// The backend cites as [Clip 6669], [Clip 6669, 12:34], [Clip 6669, 1:02:03-1:05:00]
// and multi-ID forms like [Clip 6865, 5695, 6757] / [Clips 6865 and 5695] /
// [Clip 6865; Clip 5695, 12:34]. rag/query.py::verify_citations has already
// stripped any ID that was NOT retrieved, so every ID left in the text is
// expected to be in `sources` — but we still only link IDs we can find there.
//
// Each ID links to the in-app meeting page (/meeting/<id>); a timestamp that
// follows an ID links to the same page with ?t=<seconds>, which MeetingDetail
// uses to start the embedded video at that moment. Both are plain <a> tags
// (the answer is rendered via dangerouslySetInnerHTML) carrying
// data-citation-link so the container's click handler can route them through
// React Router instead of a full reload.

const BRACKET_RE = /\[Clips?\s+([^\]]*)\]/g
// A clip ID: 1–8 digits not touching a colon (so "12:34" never yields "12").
const ID_RE = /(?<![\d:])\d{1,8}(?![\d:])/g
// MM:SS or H:MM:SS, optionally a range "A-B" (only the start is linked).
const TS_RE = /(\d+):(\d{2})(?::(\d{2}))?/

export function timestampToSeconds(ts) {
  const m = TS_RE.exec(ts || '')
  if (!m) return null
  if (m[3] !== undefined) {
    return parseInt(m[1], 10) * 3600 + parseInt(m[2], 10) * 60 + parseInt(m[3], 10)
  }
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10)
}

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}

/**
 * Link every citation bracket in an ALREADY-ESCAPED HTML string.
 *
 * @param {string} html   answer HTML produced by simpleMarkdown (escaped text)
 * @param {Array<{clip_id: number|string}>} sources  the answer's sources
 * @returns {string} html with <a> tags inside the brackets
 */
export function linkifyCitations(html, sources) {
  if (!html) return ''
  const known = new Set((sources || []).map(s => String(s.clip_id)))
  if (known.size === 0) return html

  return html.replace(BRACKET_RE, (whole, body) => {
    // Split the body into ID-led segments so each timestamp attaches to the
    // ID that precedes it: "6865, 12:34; Clip 5695" -> ["6865, 12:34", "5695"].
    let out = ''
    let lastIndex = 0
    let currentId = null
    const re = new RegExp(ID_RE.source, 'g')
    let m
    while ((m = re.exec(body)) !== null) {
      const id = m[0]
      if (!known.has(id)) continue
      // Text between the previous ID and this one may hold the previous
      // ID's timestamp(s).
      out += linkTimestamps(body.slice(lastIndex, m.index), currentId)
      out += `<a href="/meeting/${id}" class="citation-link" data-citation-link="1" data-clip="${escapeAttr(id)}">${id}</a>`
      lastIndex = m.index + id.length
      currentId = id
    }
    out += linkTimestamps(body.slice(lastIndex), currentId)
    return `[Clip ${out}]`.replace('[Clip Clip ', '[Clip ')
  })
}

function linkTimestamps(fragment, clipId) {
  if (!fragment || !clipId) return fragment || ''
  return fragment.replace(new RegExp(TS_RE.source, 'g'), ts => {
    const secs = timestampToSeconds(ts)
    if (secs === null) return ts
    return `<a href="/meeting/${clipId}?t=${secs}" class="citation-link citation-timestamp" data-citation-link="1" data-clip="${escapeAttr(clipId)}" data-t="${secs}">${ts}</a>`
  })
}

/**
 * Click handler for a container rendered with dangerouslySetInnerHTML: routes
 * citation links through React Router so the SPA doesn't reload. Pass the
 * `navigate` from useNavigate().
 */
export function handleCitationClick(event, navigate) {
  const a = event.target && event.target.closest ? event.target.closest('a[data-citation-link]') : null
  if (!a) return
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1) return // new tab
  event.preventDefault()
  navigate(a.getAttribute('href'))
}
