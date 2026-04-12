import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { I18nProvider } from '../../i18n/I18nProvider'
import { AnnounceProvider } from '../A11yAnnouncer'
import MeetingList from '../MeetingList'

const ROUTER_FUTURE_FLAGS = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
}

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

// Mock the useMeetings hook
const mockUseMeetings = vi.fn()
vi.mock('../../hooks/useMeetings', () => ({
  useMeetings: (...args) => mockUseMeetings(...args),
}))

// Mock the SearchContext to avoid FlexSearch dependency
vi.mock('../../contexts/SearchContext', () => ({
  useSearchIndex: () => ({
    search: () => [],
    isLoading: false,
    isLoaded: true,
    loadProgress: 100,
    error: null,
  }),
  SearchProvider: ({ children }) => children,
}))

function renderMeetingList() {
  return render(
    <MemoryRouter future={ROUTER_FUTURE_FLAGS}>
      <I18nProvider>
        <AnnounceProvider>
          <MeetingList />
        </AnnounceProvider>
      </I18nProvider>
    </MemoryRouter>
  )
}

const sampleMeetings = [
  {
    clip_id: 6669,
    title: 'City Council Regular Meeting',
    date: '2026-01-22',
    meeting_body: 'Council',
    transcript_words: 9000,
    topics: ['Budget', 'Zoning'],
  },
  {
    clip_id: 6670,
    title: 'Planning Commission Meeting',
    date: '2026-01-15',
    meeting_body: 'Commission',
    transcript_words: 4500,
    topics: ['Development'],
  },
  {
    clip_id: 6671,
    title: 'Parks Board Meeting',
    date: '2026-01-08',
    meeting_body: 'Board',
    transcript_words: 3000,
    topics: ['Parks'],
  },
]

describe('MeetingList', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    mockUseMeetings.mockReset()
  })

  // ============================================================
  // Loading state tests
  // ============================================================

  it('shows loading indicator while meetings are being fetched', () => {
    mockUseMeetings.mockReturnValue({
      meetings: [],
      loading: true,
      error: null,
    })

    renderMeetingList()
    expect(screen.getByText(/loading meetings/i)).toBeInTheDocument()
  })

  // ============================================================
  // Success response tests
  // ============================================================

  it('renders meeting cards after successful load', () => {
    mockUseMeetings.mockReturnValue({
      meetings: sampleMeetings,
      loading: false,
      error: null,
    })

    renderMeetingList()
    expect(screen.getByText('City Council Regular Meeting')).toBeInTheDocument()
    expect(screen.getByText('Planning Commission Meeting')).toBeInTheDocument()
    expect(screen.getByText('Parks Board Meeting')).toBeInTheDocument()
  })

  it('renders meeting body badges on cards', () => {
    mockUseMeetings.mockReturnValue({
      meetings: sampleMeetings,
      loading: false,
      error: null,
    })

    renderMeetingList()
    // Meeting body names appear both as card badges and in the filter dropdown,
    // so use getAllByText to confirm they are present
    expect(screen.getAllByText('Council').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Commission').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Board').length).toBeGreaterThanOrEqual(1)
  })

  it('renders estimated durations from transcript word count', () => {
    mockUseMeetings.mockReturnValue({
      meetings: sampleMeetings,
      loading: false,
      error: null,
    })

    renderMeetingList()
    // 9000 words / 150 wpm = 60 min = ~1h
    expect(screen.getByText('~1h')).toBeInTheDocument()
    // 4500 / 150 = 30 min
    expect(screen.getByText('~30 min')).toBeInTheDocument()
    // 3000 / 150 = 20 min
    expect(screen.getByText('~20 min')).toBeInTheDocument()
  })

  it('renders meeting count summary', () => {
    mockUseMeetings.mockReturnValue({
      meetings: sampleMeetings,
      loading: false,
      error: null,
    })

    renderMeetingList()
    // Should show the count of meetings
    expect(screen.getByText(/3 meetings/i)).toBeInTheDocument()
  })

  it('renders the meeting list as a list role', () => {
    mockUseMeetings.mockReturnValue({
      meetings: sampleMeetings,
      loading: false,
      error: null,
    })

    renderMeetingList()
    expect(screen.getByRole('list', { name: /meeting results/i })).toBeInTheDocument()
  })

  it('shows empty state when there are no meetings', () => {
    mockUseMeetings.mockReturnValue({
      meetings: [],
      loading: false,
      error: null,
    })

    renderMeetingList()
    expect(screen.getByText(/no meetings/i)).toBeInTheDocument()
  })

  // ============================================================
  // Error state tests
  // ============================================================

  it('renders error message when loading fails', () => {
    mockUseMeetings.mockReturnValue({
      meetings: [],
      loading: false,
      error: 'Failed to fetch meeting index',
    })

    renderMeetingList()
    // The error state renders a div with role="alert" containing the error message
    expect(screen.getByText(/failed to fetch meeting index/i)).toBeInTheDocument()
    // Verify the error container has role="alert" (use getAllByRole since A11yAnnouncer also has one)
    const alerts = screen.getAllByRole('alert')
    const errorAlert = alerts.find(el => el.textContent.includes('Failed to fetch meeting index'))
    expect(errorAlert).toBeTruthy()
  })
})
