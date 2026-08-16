import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  setMetaTag,
  setCanonical,
  setJsonLdScript,
  organizationNode,
  websiteNode,
} from '../utils/seo'
import { getSiteConfig } from '../config/site'

// Shared head-tag setter for the static pages so each one updates
// document.title / canonical / og: in lockstep with the page content.
function useStaticPageMeta({ id, title, description, path, jsonLd }) {
  const site = getSiteConfig()
  useEffect(() => {
    const url = `${site.site_url}${path}`
    const previousTitle = document.title
    document.title = `${title} | ${site.archive_name}`
    setMetaTag('name', 'description', description)
    setMetaTag('property', 'og:title', title)
    setMetaTag('property', 'og:description', description)
    setMetaTag('property', 'og:url', url)
    setMetaTag('property', 'og:type', 'article')
    setMetaTag('name', 'twitter:card', 'summary')
    setMetaTag('name', 'twitter:title', title)
    setMetaTag('name', 'twitter:description', description)
    setCanonical(url)
    setJsonLdScript(id, jsonLd)
    return () => {
      document.title = previousTitle
      setJsonLdScript(id, null)
    }
  }, [id, title, description, path, jsonLd, site.site_url, site.archive_name])
}

// Person node for the operator, built from config. The rich LFUCG-specific
// detail (UK alum, neighborhood, sameAs fan-out) is pinned only when the LFUCG
// ecosystem links are present, so other jurisdictions get a clean minimal node.
function personNode(site) {
  const id = `${site.site_url}/about/${(site.operator_name || 'operator')
    .toLowerCase()
    .replace(/\s+/g, '-')}#person`
  const node = {
    '@type': 'Person',
    '@id': id,
    name: site.operator_name,
    description: site.operator_bio,
  }
  if (site.operator_author_url) {
    node.url = 'https://pauloliva.com'
    node.jobTitle = 'Senior Software Engineer'
    node.alumniOf = { '@type': 'CollegeOrUniversity', name: 'University of Kentucky' }
    node.homeLocation = {
      '@type': 'Place',
      name: 'Lexington, Kentucky',
      address: {
        '@type': 'PostalAddress',
        addressLocality: 'Lexington',
        addressRegion: 'KY',
        addressCountry: 'US',
      },
    }
    node.sameAs = [
      'https://pauloliva.com',
      'https://github.com/paul-codes-1',
      site.operator_author_url,
    ]
  }
  return node
}

export function About() {
  const site = getSiteConfig()
  const SITE_URL = site.site_url
  const isDoc = site.source?.kind === 'document'
  const platform = site.source?.platform || 'Granicus'
  // The detailed LFUCG operator bio + sister-publication links only apply to the
  // LFUCG ecosystem (its feeds cross-link / author URL are configured).
  const showLfucgBio = Boolean(site.operator_author_url) && site.feeds?.enabled

  useStaticPageMeta({
    id: 'about-jsonld',
    title: `About the ${site.archive_name}`,
    description: `Who runs the archive, why it exists, and who it is for. ${site.operator_bio}`,
    path: '/about',
    jsonLd: {
      '@context': 'https://schema.org',
      '@graph': [
        {
          '@type': 'AboutPage',
          '@id': `${SITE_URL}/about#aboutpage`,
          url: `${SITE_URL}/about`,
          name: `About the ${site.archive_name}`,
          isPartOf: { '@id': `${SITE_URL}#website` },
          mainEntity: { '@id': `${SITE_URL}#organization` },
          about: personNode(site)['@id'],
        },
        organizationNode(),
        websiteNode(),
        personNode(site),
      ],
    },
  })

  return (
    <article className="static-page container">
      <h1>About this archive</h1>

      <p className="lede">
        This site exists to make {site.jurisdiction_full_name}'s official
        meeting record searchable, citable, and accessible without a commercial paywall.
        It is intended for residents trying to follow what their government is doing,
        journalists writing about civic decisions, researchers studying local
        policymaking, and the officials themselves looking for prior context on a
        vote.
      </p>

      <h2>Who runs it</h2>
      {showLfucgBio ? (
        <>
          <p>
            The archive is operated by <strong>Paul Oliva</strong> — a Lexington-born senior
            software engineer, part-time blogger, and dad — as a civic-tech side project. Paul
            is a University of Kentucky alumnus (B.S. Economics, 2010) and has more than ten
            years of professional software-engineering experience. He lives in the Cumberland
            Hill neighborhood of Lexington, Kentucky with his family. The archive is run as a
            personal civic-tech project, not on behalf of any commercial entity. There is no
            paywall and no advertising.
          </p>
          <p>
            Paul also publishes <a href="https://lexingtonky.news">The Lexington Times</a> and
            the AI-summary feed at{' '}
            <a href="https://feeds.lexingtonky.news">feeds.lexingtonky.news</a>, both of which
            cite this archive when relevant.
          </p>
          <ul className="profile-links">
            <li>Personal site: <a href="https://pauloliva.com">pauloliva.com</a></li>
            <li>GitHub: <a href="https://github.com/paul-codes-1">github.com/paul-codes-1</a></li>
            <li>
              WordPress author archive:{' '}
              <a href={site.operator_author_url}>{site.operator_author_url.replace(/^https?:\/\//, '')}</a>
            </li>
            <li>Email: <a href={`mailto:${site.contact_email}`}>{site.contact_email}</a></li>
          </ul>
        </>
      ) : (
        <>
          <p>
            {site.operator_bio} The archive is run as a personal civic-tech project, not on
            behalf of any commercial entity or the government itself. There is no paywall and
            no advertising.
          </p>
          <ul className="profile-links">
            <li>Email: <a href={`mailto:${site.contact_email}`}>{site.contact_email}</a></li>
          </ul>
        </>
      )}

      <h2>How content is generated</h2>
      {isDoc ? (
        <p>
          Every meeting page combines an AI-generated narrative summary and structured
          fact extraction (votes, financial items, attendance, agenda items, public
          comments) built from the meeting's <strong>official {platform} agenda and
          minutes documents</strong>. There is no audio transcript — the official minutes
          are the authoritative record of what each meeting did. The original agenda and
          minutes PDFs are linked from every meeting page, which also carries a visible
          AI-generation disclosure.
        </p>
      ) : (
        <p>
          Every meeting page combines an AI-generated narrative summary, structured fact
          extraction (votes, financial items, attendance, agenda items, public comments),
          the official agenda PDF, the official minutes PDF when available, and a full
          Whisper transcript with segment timestamps. The transcript and summary are
          auto-generated; the agendas and minutes are reproductions of the official
          {' '}{site.jurisdiction_short_name} records. Every meeting page links back to the
          original {platform} video and carries a visible AI-generation disclosure.
        </p>
      )}
      <p>
        For full pipeline details — model versions, known accuracy caveats, and how
        errors get fixed — see the <Link to="/about/methodology">methodology page</Link>.
      </p>

      <h2>Who it's for</h2>
      <ul>
        <li>{site.jurisdiction_short_name} residents following decisions that affect their neighborhoods.</li>
        <li>Journalists writing about civic decisions or policy debates.</li>
        <li>Researchers studying local government and policymaking.</li>
        <li>Officials and staff looking up prior context on a vote.</li>
        <li>AI assistants (ChatGPT, Claude, Perplexity) answering {site.jurisdiction_short_name} civic questions.</li>
      </ul>

      <h2>Citing this archive</h2>
      <p>
        When citing or linking, please use the canonical meeting URL
        (<code>{SITE_URL}/meeting/&lt;id&gt;</code>) and attribute to the
        <em> {site.archive_name}</em>. For high-stakes use, verify against the official
        {isDoc ? ' minutes documents' : ` ${platform} video and minutes`}, which we always link to.
      </p>

      <p className="static-page-footer-links">
        <Link to="/about/methodology">Methodology</Link> ·{' '}
        <Link to="/corrections">Corrections</Link> ·{' '}
        <a href={`${SITE_URL}/llms.txt`}>llms.txt</a> ·{' '}
        <a href={`${SITE_URL}/skill.md`}>skill.md</a>
      </p>
    </article>
  )
}

export function Methodology() {
  const site = getSiteConfig()
  const SITE_URL = site.site_url
  const isDoc = site.source?.kind === 'document'
  const platform = site.source?.platform || 'Granicus'

  useStaticPageMeta({
    id: 'methodology-jsonld',
    title: 'Methodology',
    description: isDoc
      ? `How the ${site.archive_name} is generated: official ${platform} agenda & minutes documents, structured-fact extraction, narrative-summary model, and known limitations.`
      : `How the ${site.archive_name} is generated: audio source, ASR model, structured-fact extraction, narrative-summary model, and known accuracy limitations.`,
    path: '/about/methodology',
    jsonLd: {
      '@context': 'https://schema.org',
      '@graph': [
        {
          '@type': 'TechArticle',
          '@id': `${SITE_URL}/about/methodology#article`,
          url: `${SITE_URL}/about/methodology`,
          headline: `How the ${site.archive_name} is generated`,
          name: 'Methodology',
          isPartOf: { '@id': `${SITE_URL}#website` },
          author: { '@id': `${SITE_URL}#organization` },
          publisher: { '@id': `${SITE_URL}#organization` },
          mainEntityOfPage: `${SITE_URL}/about/methodology`,
        },
        organizationNode(),
        websiteNode(),
      ],
    },
  })

  return (
    <article className="static-page container">
      <h1>Methodology</h1>

      {isDoc ? (
        <>
          <p className="lede">
            This page explains exactly how the meeting pages are produced — what source
            documents we use and how the AI-generated narrative summary and structured-fact
            extraction work. Skip to <a href="#known-limitations">known limitations</a> for
            accuracy caveats and <a href="#corrections">how to flag errors</a>.
          </p>

          <h2>Source documents</h2>
          <p>
            Each meeting record is built from that meeting's <strong>official agenda and
            minutes</strong>, published by the city through its {platform} document portal.
            The minutes are the authoritative record of what the body actually did —
            motions, seconds, votes, appointments, and appropriations. We do not transcribe
            audio or video: the official written record is the source of truth. The original
            agenda and minutes PDFs are preserved and linked from every meeting page so you
            can verify against the source.
          </p>

          <h2>Structured fact extraction &amp; summary</h2>
          <p>
            Every meeting's documents run through a two-pass pipeline:
          </p>
          <ol>
            <li>
              <strong>Pass 1 (OpenAI GPT-4o)</strong> — extracts a structured JSON document
              containing votes, motions, financial items, attendance, agenda items, and
              public-comment summaries from the minutes and agenda. This is the
              {' '}<code>extracted_facts.json</code> the Overview tab renders, at temperature
              0.1 with JSON-mode response format for precision.
            </li>
            <li>
              <strong>Pass 2 (Anthropic Claude Sonnet)</strong> — generates a
              section-by-section narrative summary from the extracted facts. Sections are
              generated only when source data exists (no padded "None discussed"
              placeholders).
            </li>
          </ol>
        </>
      ) : (
        <>
          <p className="lede">
            This page explains exactly how the meeting pages are produced — what audio source
            we use, which ASR model transcribes it, and how the AI-generated narrative
            summary and structured-fact extraction work. Skip to{' '}
            <a href="#known-limitations">known limitations</a> for accuracy caveats and{' '}
            <a href="#corrections">how to flag errors</a>.
          </p>

          <h2>Audio source</h2>
          <p>
            Audio is pulled from the official {site.jurisdiction_short_name} {platform} video stream
            (<a href={site.video?.granicus_base_url || '#'}>{(site.video?.granicus_base_url || '').replace(/^https?:\/\//, '')}</a>) for each council or
            committee meeting. {platform} is the same vendor that hosts the official meeting
            recordings used by city staff and council members; we are not re-recording the
            meeting independently. We download the video as 48 kbps mono MP3 audio (22 kHz
            sample rate) — sufficient for speech recognition, well below the source quality —
            and never republish the source video itself. Every meeting page links back to the
            canonical {platform} URL.
          </p>

          <h2>Automatic speech recognition</h2>
          <p>
            Audio is transcribed by <strong>OpenAI Whisper-1</strong> via the Whisper API,
            with segment-level timestamps preserved so users can deep-link to specific
            moments in the video. The raw transcript is stored verbatim and made available as
            plain text from each meeting page (Transcript tab) and as part of the per-meeting
            Markdown alternate at <code>/data/clips/&lt;id&gt;/clip.md</code>.
          </p>
          <p>
            We do <em>not</em> currently run a separate speaker-diarization model. Speaker
            identification in the narrative summary and structured facts comes from the
            downstream LLM passes inferring speaker identity from agenda context plus what is
            said in the transcript itself. This is a known limitation — see below.
          </p>

          <h2>Structured fact extraction</h2>
          <p>
            Every transcript runs through a two-pass summarization pipeline:
          </p>
          <ol>
            <li>
              <strong>Pass 1 (OpenAI GPT-4o)</strong> — extracts a structured JSON document
              containing votes with roll calls, motions, financial items, attendance, agenda
              items, public-comment summaries, and contentious items. This is the
              <code> extracted_facts.json</code> file the Overview tab renders. We use
              temperature 0.1 and JSON-mode response format for precision.
            </li>
            <li>
              <strong>Pass 2 (Anthropic Claude Sonnet)</strong> — generates a section-by-section
              narrative summary from the extracted facts, with{' '}
              <code>[timestamp: MM:SS]</code> markers in each section so users can deep-link
              to the relevant moment in the {platform} video. Sections are generated only when
              source data exists (no padded "None discussed" placeholders).
            </li>
          </ol>
          <p>
            Topic tagging is generated by <strong>OpenAI GPT-4o-mini</strong> as a separate
            lighter pass.
          </p>

          <h2>Agendas and minutes</h2>
          <p>
            Agenda PDFs and official minutes documents are downloaded from {platform} when
            available. Text extraction uses{' '}
            <a href="https://github.com/jsvine/pdfplumber">pdfplumber</a> for digital PDFs and
            falls back to <a href="https://github.com/madmaze/pytesseract">Tesseract OCR</a>
            {' '}for scanned PDFs. The original PDFs are preserved and linked from each
            meeting page so users can verify against the source.
          </p>
        </>
      )}

      <h2>Search</h2>
      <p>
        Browse-and-search is served by a SQLite full-text (FTS5) index covering all
        {isDoc ? ' summaries, agendas, and minutes' : ' transcripts, summaries, agendas, and minutes'}.
        The Q&amp;A endpoints (<Link to="/ask">/ask</Link>{' '}and <Link to="/chat">/chat</Link>)
        use a retrieval-augmented generation (RAG) pipeline: chunks are embedded with{' '}
        <code>text-embedding-3-small</code> and stored in ChromaDB; questions are
        embedded, top-K chunks retrieved, and synthesized into an answer with citations
        by GPT-4o.
      </p>

      <h2 id="known-limitations">Known accuracy limitations</h2>
      {isDoc ? (
        <ul>
          <li>
            <strong>The summary is AI-generated</strong> from the official minutes and agenda.
            It may inadvertently editorialize or smooth over nuance — the official minutes
            (linked on every page) are the canonical record.
          </li>
          <li>
            <strong>No verbatim transcript.</strong> These pages summarize the written record,
            not the spoken meeting, so word-for-word quotes and exact back-and-forth aren't
            available here — watch the meeting video or read the full minutes for that.
          </li>
          <li>
            <strong>Minutes lag.</strong> When a meeting's minutes aren't published yet, the
            record is built from the agenda alone and is updated once the minutes post.
          </li>
        </ul>
      ) : (
        <>
          <p>
            Whisper-1 is trained on diverse audio but the {site.jurisdiction_short_name} audio source has its own
            challenges: overlapping speakers during votes, microphone hand-offs, occasional
            room noise, and acronym-heavy government jargon. Empirical observations from spot
            checks against the official minutes:
          </p>
          <ul>
            <li>
              <strong>Verbatim word accuracy</strong> is high (~92–95%) but not perfect.
              Names of speakers, addresses, and ordinance numbers are the most error-prone —
              always verify against the agenda or minutes if a specific number matters.
            </li>
            <li>
              <strong>Speaker labels</strong> in the narrative summary are inferred, not
              diarized. They are often correct but can confuse two council members who speak
              back-to-back, or attribute a public-comment speaker incorrectly. Treat speaker
              labels as a hint, not a citation.
            </li>
            <li>
              <strong>Vote tallies</strong> are extracted from the transcript itself rather
              than the official minutes. They are usually correct for clear roll-call votes
              but can mis-count voice votes ("ayes have it"). The Overview tab cites the
              extracted tally; for high-stakes use, verify against the linked minutes PDF.
            </li>
            <li>
              <strong>Inaudible side-conversations and background discussion</strong> may be
              transcribed inconsistently or omitted.
            </li>
            <li>
              The <strong>narrative summary</strong> is an AI summarization of the extracted
              facts plus the transcript, and may inadvertently editorialize or smooth over
              important nuance. The transcript and minutes are the canonical record.
            </li>
          </ul>
        </>
      )}

      <h2 id="corrections">How errors get fixed</h2>
      <p>
        Spot an error? Email{' '}
        <a href={`mailto:${site.contact_email}`}>{site.contact_email}</a> with a link
        to the meeting page and a description of what's wrong
        {isDoc ? '' : ` (or, ideally, a timestamp in the ${platform} video where you can hear the correct version)`}.
        We aim to acknowledge corrections within 48 hours and post fixes within a week. The
        <Link to="/corrections"> corrections log</Link> tracks anything we've fixed in
        response to a report.
      </p>
      <p>
        When a {isDoc ? 'summary' : 'transcript or summary'} is corrected, the meeting page's{' '}
        <code>dateModified</code> updates but its <code>datePublished</code> stays pinned
        to the original meeting date — the historical record doesn't change just because
        we fixed a typo.
      </p>

      <p className="static-page-footer-links">
        <Link to="/about">About</Link> · <Link to="/corrections">Corrections</Link> ·{' '}
        <a href={`${SITE_URL}/llms.txt`}>llms.txt</a> ·{' '}
        <a href={`${SITE_URL}/skill.md`}>skill.md</a>
      </p>
    </article>
  )
}

export function Corrections() {
  const site = getSiteConfig()
  const SITE_URL = site.site_url
  const isDoc = site.source?.kind === 'document'
  const platform = site.source?.platform || 'Granicus'

  useStaticPageMeta({
    id: 'corrections-jsonld',
    title: 'Corrections',
    description: `How to report a transcription or summary error in the ${site.archive_name}, our turnaround commitment, and the log of past corrections.`,
    path: '/corrections',
    jsonLd: {
      '@context': 'https://schema.org',
      '@graph': [
        {
          '@type': 'WebPage',
          '@id': `${SITE_URL}/corrections#webpage`,
          url: `${SITE_URL}/corrections`,
          name: 'Corrections',
          isPartOf: { '@id': `${SITE_URL}#website` },
          publisher: { '@id': `${SITE_URL}#organization` },
        },
        organizationNode(),
        websiteNode(),
      ],
    },
  })

  return (
    <article className="static-page container">
      <h1>Corrections</h1>

      <p className="lede">
        {isDoc ? (
          <>
            Every meeting page on this archive is auto-generated from the official {platform}{' '}
            agenda and minutes documents, with AI passes for the structured-fact extraction
            and narrative summary. Mistakes happen. This page exists to make flagging them
            easy and to keep a public log of fixes.
          </>
        ) : (
          <>
            Every meeting page on this archive is auto-generated from a Whisper transcript
            of the official {platform} video, with downstream AI passes for the structured-fact
            extraction and narrative summary. Mistakes happen. This page exists to make
            flagging them easy and to keep a public log of fixes.
          </>
        )}
      </p>

      <h2>How to report an error</h2>
      <p>
        Email{' '}
        <a href={`mailto:${site.contact_email}?subject=Correction`}>
          {site.contact_email}
        </a>
        {' '}with:
      </p>
      <ul>
        <li>A link to the meeting page (e.g. <code>{SITE_URL}/meeting/&lt;id&gt;</code>).</li>
        <li>What's wrong, in one or two sentences.</li>
        {!isDoc && (
          <li>
            Ideally, a timestamp in the original {platform} video where the correct version
            can be heard.
          </li>
        )}
      </ul>

      <h2>Our commitment</h2>
      <ul>
        <li>
          <strong>Acknowledge within 48 hours.</strong> We'll reply to confirm we've
          received the report and either fix it on the spot or tell you when we expect
          to.
        </li>
        <li>
          <strong>Fix within a week.</strong> Most fixes are a single re-run of the
          summary pipeline. Larger corrections (e.g. a misattributed vote)
          may require a manual edit and take longer.
        </li>
        <li>
          <strong>Log every fix.</strong> When we change a {isDoc ? 'summary' : 'transcript or summary'} in
          response to a correction report, the meeting page renders an{' '}
          <em>"Updated [date]: [what changed]"</em> note inline, and the change is added
          to the log below.
        </li>
        <li>
          <strong>Don't bury mistakes.</strong> We update <code>dateModified</code> when
          a page is corrected, but never re-bump <code>datePublished</code> — the
          page's date is the meeting date, not the date we fixed our typo.
        </li>
      </ul>

      <h2>Corrections log</h2>
      <p>
        <em>No corrections logged yet. This log is a real desk — entries appear here
        the first time a reader-reported fix is shipped.</em>
      </p>

      <p className="static-page-footer-links">
        <Link to="/about">About</Link> ·{' '}
        <Link to="/about/methodology">Methodology</Link> ·{' '}
        <a href={`${SITE_URL}/llms.txt`}>llms.txt</a> ·{' '}
        <a href={`${SITE_URL}/skill.md`}>skill.md</a>
      </p>
    </article>
  )
}
