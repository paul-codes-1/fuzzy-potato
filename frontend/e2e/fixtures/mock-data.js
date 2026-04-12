/**
 * Shared mock data and route handlers for E2E tests.
 *
 * The frontend fetches from /data/index.json, /data/clips/{id}/metadata.json,
 * and various API endpoints. We intercept these via page.route() so the tests
 * work without a running backend.
 */

export const SAMPLE_CLIP_ID = '7001'

export const INDEX_DATA = {
  clips: [
    {
      clip_id: 7001,
      title: 'January 15 2026 Urban County Council meeting',
      date: '2026-01-15',
      meeting_body: 'Urban County Council',
      topics: ['Zoning', 'Budget', 'Public Comment'],
      transcript_words: 9500,
      files: {
        transcript: 'transcript_2026-01-15_audio.txt',
        transcript_segments: 'transcript_2026-01-15_audio_segments.json',
        summary_txt: 'summary.txt',
        extracted_facts: 'extracted_facts.json',
        agenda_txt: '2026-01-15_agenda.txt',
        minutes_txt: '2026-01-15_minutes.txt',
      },
    },
    {
      clip_id: 7002,
      title: 'January 22 2026 Planning Commission meeting',
      date: '2026-01-22',
      meeting_body: 'Planning Commission',
      topics: ['Land Use', 'Permits'],
      transcript_words: 4200,
      files: {
        transcript: 'transcript_2026-01-22_audio.txt',
        summary_txt: 'summary.txt',
        extracted_facts: 'extracted_facts.json',
      },
    },
    {
      clip_id: 7003,
      title: 'February 5 2026 Budget Committee meeting',
      date: '2026-02-05',
      meeting_body: 'Budget Committee',
      topics: ['Revenue', 'Capital Projects'],
      transcript_words: 3100,
      files: {
        transcript: 'transcript_2026-02-05_audio.txt',
        summary_txt: 'summary.txt',
      },
    },
  ],
}

export const METADATA_7001 = {
  clip_id: 7001,
  url: 'https://example.granicus.com/player/clip/7001',
  title: 'January 15 2026 Urban County Council meeting',
  date: '2026-01-15',
  meeting_body: 'Urban County Council',
  topics: ['Zoning', 'Budget', 'Public Comment'],
  transcript_words: 9500,
  files: {
    transcript: 'transcript_2026-01-15_audio.txt',
    transcript_segments: 'transcript_2026-01-15_audio_segments.json',
    summary_txt: 'summary.txt',
    extracted_facts: 'extracted_facts.json',
    agenda_txt: '2026-01-15_agenda.txt',
    minutes_txt: '2026-01-15_minutes.txt',
  },
  processed_at: '2026-01-16T08:00:00Z',
  processing_time_seconds: 145.3,
  audio_kept: false,
  models: {
    transcribe: 'whisper-1',
    summary: 'gpt-4o+claude-sonnet',
    topics: 'gpt-4o-mini',
  },
}

export const EXTRACTED_FACTS_7001 = {
  meeting_info: {
    date: '2026-01-15',
    time: '6:00 PM',
    body: 'Urban County Council',
    presiding_officer: 'Mayor Linda Gorton',
    location: 'Council Chambers',
  },
  attendance: {
    present: ['Beasley', 'Boone', 'Brown', 'Curtis', 'Ellinger', 'Evans', 'Farmer', 'Plomin'],
    absent: ['McCurn'],
    late: [],
  },
  motions_and_votes: [
    {
      identifier: 'Ordinance 0016-26',
      description: 'Zoning change from Agricultural-Rural to Medium Density Residential for property on Newtown Pike',
      motion_by: 'Brown',
      second_by: 'Curtis',
      outcome: 'passed',
      vote_type: 'roll_call',
      ayes: 8,
      nays: 0,
      abstentions: 0,
      votes_for: ['Beasley', 'Boone', 'Brown', 'Curtis', 'Ellinger', 'Evans', 'Farmer', 'Plomin'],
      votes_against: [],
      conditions: null,
      transcript_approx_time: '25:15',
    },
  ],
  financial_items: [
    {
      description: 'General Obligation Bonds for infrastructure improvements',
      amount: '$18,040,000',
      type: 'appropriation',
      identifier: 'Resolution 0044-26',
      vendor_or_recipient: null,
    },
  ],
  public_comments: [
    {
      speaker: 'John Smith',
      topic: 'Zoning concerns on Newtown Pike',
      summary: 'Expressed concerns about increased traffic from the proposed rezoning.',
      transcript_approx_time: '15:30',
    },
  ],
  agenda_items: [
    {
      identifier: 'Ordinance 0016-26',
      title: 'Zoning Change - Agricultural to Residential',
      type: 'ordinance',
      summary: 'Changed zone from Agricultural-Rural to Medium Density Residential for 12.5 acres on Newtown Pike.',
      key_speakers: ['Brown', 'Curtis'],
      outcome: 'approved',
      transcript_approx_time: '25:15',
    },
  ],
  appointments: [],
  contentious_items: [],
}

export const TRANSCRIPT_SEGMENTS_7001 = [
  { start: 0, end: 30, text: 'Good evening everyone. The Urban County Council meeting is called to order.' },
  { start: 30, end: 65, text: 'The clerk will call the roll. Council Member Beasley, present.' },
  { start: 65, end: 120, text: 'We will now move to public comment. First speaker is John Smith.' },
  { start: 120, end: 180, text: 'Thank you Madam Mayor. I want to talk about the zoning change on Newtown Pike.' },
  { start: 180, end: 240, text: 'The traffic impact study shows a significant increase in vehicles during peak hours.' },
  { start: 240, end: 300, text: 'Thank you Mr Smith. Are there any other speakers for public comment?' },
  { start: 900, end: 960, text: 'We now move to item six, Ordinance 0016-26 for the zoning change.' },
  { start: 960, end: 1020, text: 'Council Member Brown moves to approve. Council Member Curtis seconds.' },
  { start: 1020, end: 1080, text: 'The vote is eight to zero. The ordinance passes.' },
]

export const TRANSCRIPT_TEXT_7001 = TRANSCRIPT_SEGMENTS_7001.map((s) => s.text).join(' ')

export const AGENDA_TEXT_7001 = `URBAN COUNTY COUNCIL
REGULAR SESSION
January 15, 2026 - 6:00 PM

1. Call to Order
2. Roll Call
3. Public Comment
4. Consent Calendar
5. Reports
6. Ordinance 0016-26: Zoning Change - Agricultural to Residential
7. Resolution 0044-26: General Obligation Bonds
8. New Business
9. Adjournment`

export const MINUTES_TEXT_7001 = `OFFICIAL MINUTES
Urban County Council
January 15, 2026

The Urban County Council met in regular session on January 15, 2026 at 6:00 PM.

ROLL CALL: Present - Beasley, Boone, Brown, Curtis, Ellinger, Evans, Farmer, Plomin. Absent - McCurn.

PUBLIC COMMENT:
John Smith spoke regarding concerns about the zoning change on Newtown Pike.

ORDINANCE 0016-26:
Council Member Brown moved to approve the zoning change from Agricultural-Rural to Medium Density Residential.
Second by Curtis. Vote: 8-0. PASSED.

RESOLUTION 0044-26:
Approved General Obligation Bonds in the amount of $18,040,000.

Meeting adjourned at 8:15 PM.`

export const ASK_RESPONSE = {
  answer: 'The city council **approved** the zoning change from Agricultural-Rural to Medium Density Residential on Newtown Pike with an 8-0 vote (Ordinance 0016-26). John Smith spoke during public comment expressing concerns about increased traffic.',
  sources: [
    {
      clip_id: 7001,
      date: '2026-01-15',
      title: 'January 15 2026 Urban County Council meeting',
      meeting_body: 'Urban County Council',
      timestamp: 1515,
      excerpt: 'Ordinance 0016-26 zoning change approved 8-0...',
      granicus_url: 'https://example.granicus.com/player/clip/7001?view_id=14&entrytime=1515',
    },
  ],
  chunks_retrieved: 8,
}

export const SEARCH_RESULTS = {
  results: [
    {
      clip_id: 7001,
      type: 'meeting',
      title: 'January 15 2026 <mark>Urban County Council</mark> meeting',
      snippet: '...discussed the <mark>zoning</mark> change on Newtown Pike...',
      date: '2026-01-15',
      meeting_body: 'Urban County Council',
    },
    {
      clip_id: 7001,
      type: 'vote',
      title: 'Ordinance 0016-26 - <mark>Zoning</mark> Change',
      snippet: 'Passed 8-0. Changed zone from Agricultural-Rural...',
      date: '2026-01-15',
      meeting_body: 'Urban County Council',
    },
  ],
  total: 2,
  took_ms: 12,
}

export const AUTOCOMPLETE_SUGGESTIONS = {
  suggestions: [
    { text: 'zoning changes', category: 'topic' },
    { text: 'Urban County Council', category: 'meeting_body', clip_id: 7001 },
  ],
}

/**
 * Install all mock API routes on a Playwright page.
 *
 * Call this in beforeEach() to intercept network requests so the app renders
 * with realistic data and no backend is needed.
 */
export async function installMockRoutes(page) {
  // Index data (meeting list)
  await page.route('**/data/index.json', (route) =>
    route.fulfill({ json: INDEX_DATA })
  )

  // Meeting metadata
  await page.route('**/data/clips/7001/metadata.json', (route) =>
    route.fulfill({ json: METADATA_7001 })
  )

  // Extracted facts
  await page.route('**/data/clips/7001/extracted_facts.json', (route) =>
    route.fulfill({ json: EXTRACTED_FACTS_7001 })
  )

  // Transcript segments
  await page.route('**/data/clips/7001/*_segments.json', (route) =>
    route.fulfill({ json: TRANSCRIPT_SEGMENTS_7001 })
  )

  // Transcript text
  await page.route('**/data/clips/7001/transcript_*.txt', (route) =>
    route.fulfill({ body: TRANSCRIPT_TEXT_7001, contentType: 'text/plain' })
  )

  // Agenda text
  await page.route('**/data/clips/7001/*_agenda*.txt', (route) =>
    route.fulfill({ body: AGENDA_TEXT_7001, contentType: 'text/plain' })
  )

  // Minutes text
  await page.route('**/data/clips/7001/*_minutes*.txt', (route) =>
    route.fulfill({ body: MINUTES_TEXT_7001, contentType: 'text/plain' })
  )

  // RAG ask endpoint
  await page.route('**/api/ask*', (route) =>
    route.fulfill({ json: ASK_RESPONSE })
  )

  // Global search API
  await page.route('**/api/v1/search?*', (route) =>
    route.fulfill({ json: SEARCH_RESULTS })
  )

  // Autocomplete API
  await page.route('**/api/v1/search/autocomplete?*', (route) =>
    route.fulfill({ json: AUTOCOMPLETE_SUGGESTIONS })
  )

  // Tenant creation (onboarding step 3)
  await page.route('**/api/v1/admin/tenants', (route) =>
    route.fulfill({
      json: { id: 'tenant_abc123', api_key: 'cl_test_abc123def456ghi789' },
    })
  )

  // Branding endpoint
  await page.route('**/api/v1/branding', (route) =>
    route.fulfill({ json: {} })
  )

  // Diffs endpoint (Changes tab)
  await page.route('**/api/v1/diffs*', (route) =>
    route.fulfill({ json: { diffs: [] } })
  )

  // Catch-all for unknown API calls -- return empty to prevent hangs
  await page.route('**/api/**', (route) => {
    if (!route.request().url().includes('/api/v1/search') && !route.request().url().includes('/api/ask')) {
      return route.fulfill({ json: {} })
    }
    return route.continue()
  })
}
