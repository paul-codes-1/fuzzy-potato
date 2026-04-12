import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../../i18n/I18nProvider'
import { AnnounceProvider } from '../A11yAnnouncer'
import VoteTracker from '../VoteTracker'

const ROUTER_FUTURE_FLAGS = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
}

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

function renderVoteTracker() {
  return render(
    <MemoryRouter future={ROUTER_FUTURE_FLAGS}>
      <I18nProvider>
        <AnnounceProvider>
          <VoteTracker />
        </AnnounceProvider>
      </I18nProvider>
    </MemoryRouter>
  )
}

// Helper: build a successful votes API response
function votesResponse(votes = [], total = 0) {
  return {
    ok: true,
    json: async () => ({ votes, total }),
  }
}

// Helper: build a successful stats API response
function statsResponse(overrides = {}) {
  return {
    ok: true,
    json: async () => ({
      total_votes: 42,
      passed: 38,
      failed: 4,
      pass_rate: 90,
      most_active_movers: [],
      most_contested: [],
      ...overrides,
    }),
  }
}

describe('VoteTracker', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    // Default: return empty results for any fetch call
    mockFetch.mockImplementation((url) => {
      if (typeof url === 'string' && url.includes('/stats')) {
        return Promise.resolve(statsResponse())
      }
      return Promise.resolve(votesResponse())
    })
  })

  // ============================================================
  // Rendering tests
  // ============================================================

  it('renders tab buttons', async () => {
    renderVoteTracker()
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /votes/i })).toBeInTheDocument()
    })
    expect(screen.getByRole('tab', { name: /members/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /financial/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /alerts/i })).toBeInTheDocument()
  })

  it('renders vote filter inputs on the Votes tab', async () => {
    renderVoteTracker()
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/member name/i)).toBeInTheDocument()
    })
    expect(screen.getByLabelText(/filter by outcome/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/search description/i)).toBeInTheDocument()
  })

  // ============================================================
  // Loading state tests
  // ============================================================

  it('shows loading indicator while fetching votes', async () => {
    mockFetch.mockImplementation(() => new Promise(() => {})) // never resolves

    renderVoteTracker()
    expect(screen.getByText(/loading votes/i)).toBeInTheDocument()
  })

  // ============================================================
  // Success response tests
  // ============================================================

  it('renders vote records in a table after successful fetch', async () => {
    const votes = [
      {
        id: 1,
        meeting_date: '2026-01-08',
        identifier: 'ORD-001',
        description: 'Zoning amendment',
        motion_by: 'Smith',
        outcome: 'passed',
        ayes: 8,
        nays: 0,
        clip_id: 6669,
        votes_for: ['Smith', 'Jones'],
        votes_against: [],
      },
    ]

    mockFetch.mockImplementation((url) => {
      if (typeof url === 'string' && url.includes('/stats')) {
        return Promise.resolve(statsResponse())
      }
      return Promise.resolve(votesResponse(votes, 1))
    })

    renderVoteTracker()

    await waitFor(() => {
      expect(screen.getByText('Zoning amendment')).toBeInTheDocument()
    })
    expect(screen.getByText('ORD-001')).toBeInTheDocument()
    // "Smith" appears in both the heatmap SVG and the table, so use getAllByText
    expect(screen.getAllByText('Smith').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('passed')).toBeInTheDocument()
  })

  it('renders stats cards when stats are returned', async () => {
    mockFetch.mockImplementation((url) => {
      if (typeof url === 'string' && url.includes('/stats')) {
        return Promise.resolve(statsResponse({ total_votes: 100, passed: 85, failed: 15, pass_rate: 85 }))
      }
      return Promise.resolve(votesResponse())
    })

    renderVoteTracker()

    await waitFor(() => {
      expect(screen.getByText('100')).toBeInTheDocument()
    })
    expect(screen.getByText('85%')).toBeInTheDocument()
    expect(screen.getByText('Total Votes')).toBeInTheDocument()
    expect(screen.getByText('Pass Rate')).toBeInTheDocument()
  })

  it('shows empty state when no votes match filters', async () => {
    renderVoteTracker()

    await waitFor(() => {
      expect(screen.getByText(/no votes found/i)).toBeInTheDocument()
    })
  })

  // ============================================================
  // Error state tests
  // ============================================================

  it('renders error message on fetch failure', async () => {
    mockFetch.mockImplementation((url) => {
      if (typeof url === 'string' && url.includes('/stats')) {
        return Promise.resolve(statsResponse())
      }
      return Promise.resolve({
        ok: false,
        status: 500,
        json: async () => ({ detail: 'Internal server error' }),
      })
    })

    renderVoteTracker()

    await waitFor(() => {
      expect(screen.getByText(/internal server error/i)).toBeInTheDocument()
    })
  })

  // ============================================================
  // Tab switching
  // ============================================================

  it('switches to Financial tab when clicked', async () => {
    const user = userEvent.setup()

    mockFetch.mockImplementation(() =>
      Promise.resolve({ ok: true, json: async () => ({ items: [], total: 0 }) })
    )

    renderVoteTracker()
    await user.click(screen.getByRole('tab', { name: /financial/i }))

    await waitFor(() => {
      expect(screen.getByLabelText(/search financial item descriptions/i)).toBeInTheDocument()
    })
  })

  it('switches to Alerts tab when clicked', async () => {
    const user = userEvent.setup()

    mockFetch.mockImplementation(() =>
      Promise.resolve({ ok: true, json: async () => ({ alerts: [] }) })
    )

    renderVoteTracker()
    await user.click(screen.getByRole('tab', { name: /alerts/i }))

    await waitFor(() => {
      expect(screen.getByText(/policy alerts/i)).toBeInTheDocument()
    })
  })
})
