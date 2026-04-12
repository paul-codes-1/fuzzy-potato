import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../../i18n/I18nProvider'
import { AnnounceProvider } from '../A11yAnnouncer'
import AskQuestion from '../AskQuestion'

const ROUTER_FUTURE_FLAGS = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
}

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

function renderAskQuestion() {
  return render(
    <MemoryRouter future={ROUTER_FUTURE_FLAGS}>
      <I18nProvider>
        <AnnounceProvider>
          <AskQuestion />
        </AnnounceProvider>
      </I18nProvider>
    </MemoryRouter>
  )
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

  it('renders filter dropdowns', () => {
    renderAskQuestion()
    expect(screen.getByLabelText(/meeting body/i)).toBeInTheDocument()
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
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        answer: 'The zoning ordinance was **passed** 8-0.',
        sources: [],
        chunks_retrieved: 5,
      }),
    })

    renderAskQuestion()
    await userEvent.type(screen.getByPlaceholderText(/ask a question/i), 'What about zoning?')
    await userEvent.click(screen.getByRole('button', { name: /ask/i }))

    await waitFor(() => {
      expect(screen.getByText(/zoning ordinance/i)).toBeInTheDocument()
    })
  })

  it('renders source cards with meeting links', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        answer: 'Zoning was discussed.',
        sources: [
          {
            clip_id: 6669,
            date: '2026-01-22',
            title: 'Urban County Council (1)',
            meeting_body: 'Council',
            timestamp: 120,
            excerpt: 'Zoning ordinance passed...',
            granicus_url: 'https://example.granicus.com/player/clip/6669?view_id=14&entrytime=120',
          },
        ],
        chunks_retrieved: 5,
      }),
    })

    renderAskQuestion()
    await userEvent.type(screen.getByPlaceholderText(/ask a question/i), 'zoning')
    await userEvent.click(screen.getByRole('button', { name: /ask/i }))

    await waitFor(() => {
      expect(screen.getByText(/Urban County Council/i)).toBeInTheDocument()
    })

    // Check meeting link exists
    const meetingLink = screen.getByRole('link', { name: /Urban County Council/i })
    expect(meetingLink).toHaveAttribute('href', '/meeting/6669')
  })

  it('renders timestamp badges linking to Granicus', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        answer: 'Discussion happened.',
        sources: [
          {
            clip_id: 6669,
            date: '2026-01-22',
            title: 'Council Meeting',
            meeting_body: 'Council',
            timestamp: 120,
            excerpt: 'Some excerpt...',
            granicus_url: 'https://example.granicus.com/player/clip/6669?view_id=14&entrytime=120',
          },
        ],
        chunks_retrieved: 3,
      }),
    })

    renderAskQuestion()
    await userEvent.type(screen.getByPlaceholderText(/ask a question/i), 'test')
    await userEvent.click(screen.getByRole('button', { name: /ask/i }))

    await waitFor(() => {
      const timestampLink = screen.getByRole('link', { name: /2:00/i })
      expect(timestampLink).toHaveAttribute('href', 'https://example.granicus.com/player/clip/6669?view_id=14&entrytime=120')
    })
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
