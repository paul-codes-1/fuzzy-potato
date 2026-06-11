import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import AskQuestion from '../AskQuestion'

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

// Mock useFacets so the component doesn't issue a /api/facets fetch that
// would consume the mockResolvedValueOnce queued for /api/ask.
vi.mock('../../hooks/useFacets', () => ({
  useFacets: () => ({
    facets: {
      bodies: ['Urban County Council', 'Planning Commission'],
      speakers: [],
      date_min: null,
      date_max: null,
    },
    error: null,
  }),
}))

function renderAskQuestion() {
  return render(
    <MemoryRouter>
      <AskQuestion />
    </MemoryRouter>
  )
}

function makeSource(overrides = {}) {
  return {
    clip_id: 6669,
    date: '2026-01-22',
    title: 'Urban County Council (1)',
    meeting_body: 'Council',
    timestamp: 120,
    excerpt: 'Zoning ordinance passed...',
    granicus_url: 'https://lfucg.granicus.com/player/clip/6669?view_id=14&entrytime=120',
    ...overrides,
  }
}

async function askWithResponse(body) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => body,
  })
  renderAskQuestion()
  await userEvent.type(screen.getByPlaceholderText(/ask a question/i), 'test question')
  await userEvent.click(screen.getByRole('button', { name: /ask/i }))
}

describe('AskQuestion', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  // ============================================================
  // Rendering tests
  // ============================================================

  it('renders input and submit button', () => {
    renderAskQuestion()
    expect(screen.getByPlaceholderText(/ask a question/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /ask/i })).toBeInTheDocument()
  })

  it('gives the question input an accessible name', () => {
    renderAskQuestion()
    expect(
      screen.getByRole('textbox', { name: /ask a question about the meeting archive/i })
    ).toBeInTheDocument()
  })

  it('renders filter dropdowns', () => {
    renderAskQuestion()
    expect(screen.getByLabelText(/meeting body/i)).toBeInTheDocument()
  })

  it('sources meeting-body options from the facets endpoint', () => {
    renderAskQuestion()
    const select = screen.getByLabelText(/meeting body/i)
    const values = Array.from(select.querySelectorAll('option')).map(o => o.value)
    expect(values).toEqual(['', 'Urban County Council', 'Planning Commission'])
  })

  // ============================================================
  // Loading state tests
  // ============================================================

  it('shows loading state when submitting', async () => {
    mockFetch.mockImplementation(() => new Promise(() => {})) // never resolves

    renderAskQuestion()
    const input = screen.getByPlaceholderText(/ask a question/i)
    const button = screen.getByRole('button', { name: /ask/i })

    await userEvent.type(input, 'What about zoning?')
    await userEvent.click(button)

    expect(screen.getAllByText(/searching/i).length).toBeGreaterThanOrEqual(1)
  })

  // ============================================================
  // Success response tests
  // ============================================================

  it('renders answer markdown after successful response', async () => {
    await askWithResponse({
      answer: 'The zoning ordinance was **passed** 8-0.',
      sources: [],
      chunks_retrieved: 5,
    })

    await waitFor(() => {
      expect(screen.getByText(/zoning ordinance/i)).toBeInTheDocument()
    })
  })

  it('renders source cards with meeting links', async () => {
    await askWithResponse({
      answer: 'Zoning was discussed.',
      sources: [makeSource()],
      chunks_retrieved: 5,
    })

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Urban County Council/i })).toBeInTheDocument()
    })

    // Check meeting link exists
    const meetingLink = screen.getByRole('link', { name: /Urban County Council/i })
    expect(meetingLink).toHaveAttribute('href', '/meeting/6669')
  })

  it('renders timestamp badges linking to Granicus', async () => {
    await askWithResponse({
      answer: 'Discussion happened.',
      sources: [makeSource({ title: 'Council Meeting', excerpt: 'Some excerpt...' })],
      chunks_retrieved: 3,
    })

    await waitFor(() => {
      const timestampLink = screen.getByRole('link', { name: /2:00/i })
      expect(timestampLink).toHaveAttribute('href', 'https://lfucg.granicus.com/player/clip/6669?view_id=14&entrytime=120')
    })
  })

  // ============================================================
  // Cited / uncited source split
  // ============================================================

  it('renders cited sources prominently and collapses uncited ones', async () => {
    await askWithResponse({
      answer: 'Zoning was discussed.',
      sources: [
        makeSource({ clip_id: 6669, title: 'Cited Meeting A', cited: true }),
        makeSource({ clip_id: 7000, title: 'Uncited Meeting B', cited: false }),
        makeSource({ clip_id: 7001, title: 'Uncited Meeting C', cited: false }),
      ],
      chunks_retrieved: 3,
    })

    await waitFor(() => {
      expect(screen.getByText(/Cited in this answer \(1\)/i)).toBeInTheDocument()
    })

    // Cited source card visible
    expect(screen.getByRole('link', { name: /Cited Meeting A/i })).toBeInTheDocument()

    // Uncited sources hidden behind the collapsed toggle
    expect(screen.queryByText(/Uncited Meeting B/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Uncited Meeting C/i)).not.toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: /other retrieved excerpts \(2\)/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    // Expanding the toggle reveals the uncited cards
    await userEvent.click(toggle)
    expect(screen.getByRole('link', { name: /Uncited Meeting B/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Uncited Meeting C/i })).toBeInTheDocument()
  })

  it('shows the synthesis note above cited sources', async () => {
    await askWithResponse({
      answer: 'Budget was discussed.',
      sources: [
        makeSource({ clip_id: 6669, title: 'Meeting A', cited: true }),
        makeSource({ clip_id: 6669, title: 'Meeting A', cited: true }),
        makeSource({ clip_id: 7000, title: 'Meeting B', cited: true }),
      ],
      chunks_retrieved: 3,
    })

    await waitFor(() => {
      expect(
        screen.getByText(/answer synthesized from 3 excerpts across 2 meetings/i)
      ).toBeInTheDocument()
    })
  })

  it('shows no source cards by default for refusal answers (nothing cited)', async () => {
    await askWithResponse({
      answer: "The archive doesn't cover that topic.",
      sources: [
        makeSource({ clip_id: 7000, title: 'Irrelevant Meeting B', cited: false }),
        makeSource({ clip_id: 7001, title: 'Irrelevant Meeting C', cited: false }),
      ],
      chunks_retrieved: 2,
    })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /other retrieved excerpts \(2\)/i })).toBeInTheDocument()
    })

    // No prominent source section, no cards
    expect(screen.queryByText(/Cited in this answer/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Sources \(/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Irrelevant Meeting B/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/answer synthesized from/i)).not.toBeInTheDocument()
  })

  it('keeps the old render-everything behavior when no source has a cited flag', async () => {
    await askWithResponse({
      answer: 'Old backend answer.',
      sources: [
        makeSource({ clip_id: 6669, title: 'Legacy Meeting A' }),
        makeSource({ clip_id: 7000, title: 'Legacy Meeting B' }),
      ],
      chunks_retrieved: 2,
    })

    await waitFor(() => {
      expect(screen.getByText(/Sources \(2\)/i)).toBeInTheDocument()
    })

    // All cards visible, no collapse toggle
    expect(screen.getByRole('link', { name: /Legacy Meeting A/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Legacy Meeting B/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /other retrieved excerpts/i })).not.toBeInTheDocument()
  })

  // ============================================================
  // Error state tests
  // ============================================================

  it('renders error message on fetch failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    })

    renderAskQuestion()
    await userEvent.type(screen.getByPlaceholderText(/ask a question/i), 'test')
    await userEvent.click(screen.getByRole('button', { name: /ask/i }))

    await waitFor(() => {
      expect(screen.getByText(/error|something went wrong/i)).toBeInTheDocument()
    })
  })

  it('renders error message on network failure', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network error'))

    renderAskQuestion()
    await userEvent.type(screen.getByPlaceholderText(/ask a question/i), 'test')
    await userEvent.click(screen.getByRole('button', { name: /ask/i }))

    await waitFor(() => {
      expect(screen.getByText(/error|something went wrong/i)).toBeInTheDocument()
    })
  })
})
