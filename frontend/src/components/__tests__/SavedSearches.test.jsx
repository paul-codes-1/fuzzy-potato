import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import SavedSearches from '../SavedSearches'

const ROUTER_FUTURE_FLAGS = {
  v7_startTransition: true,
  v7_relativeSplatPath: true,
}

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

function renderPage() {
  return render(
    <MemoryRouter future={ROUTER_FUTURE_FLAGS}>
      <SavedSearches />
    </MemoryRouter>
  )
}

function okResponse(body) {
  return { ok: true, json: async () => body }
}

function errorResponse(status, detail) {
  return {
    ok: false,
    status,
    json: async () => ({ detail }),
  }
}

describe('SavedSearches', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('shows empty state when no saved searches exist', async () => {
    mockFetch.mockResolvedValue(okResponse({ saved_searches: [] }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/no saved searches yet/i)).toBeInTheDocument()
    })
    expect(
      screen.getByRole('button', { name: /create your first saved search/i })
    ).toBeInTheDocument()
  })

  it('renders the list when saved searches load', async () => {
    const items = [
      {
        id: 'a1',
        name: 'Zoning votes',
        search_type: 'vote_search',
        query_text: 'zoning',
        alert_enabled: true,
        alert_frequency: 'weekly',
        last_run_at: null,
        filters: {},
      },
      {
        id: 'b2',
        name: 'Budget questions',
        search_type: 'rag_ask',
        query_text: 'what is the budget for parks',
        alert_enabled: false,
        alert_frequency: 'off',
        last_run_at: '2026-04-01T12:00:00+00:00',
        filters: {},
      },
    ]
    mockFetch.mockResolvedValue(okResponse({ saved_searches: items }))
    renderPage()

    await waitFor(() => {
      expect(screen.getByText('Zoning votes')).toBeInTheDocument()
    })
    expect(screen.getByText('Budget questions')).toBeInTheDocument()
    expect(screen.getByText(/alert: weekly/i)).toBeInTheDocument()
    // Type badges render
    expect(screen.getByText('Vote search')).toBeInTheDocument()
    expect(screen.getByText('Ask a question')).toBeInTheDocument()
  })

  it('renders error state when fetch fails', async () => {
    mockFetch.mockResolvedValue(errorResponse(500, 'Boom'))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/boom/i)
    })
  })

  it('opens the new saved search modal and creates an item', async () => {
    const user = userEvent.setup()
    // Initial empty list, then after POST reload returns one item
    mockFetch
      .mockResolvedValueOnce(okResponse({ saved_searches: [] })) // initial load
      .mockResolvedValueOnce(okResponse({ // create
        id: 'new-1',
        name: 'New one',
        search_type: 'meeting_search',
        query_text: 'parks',
        alert_enabled: false,
        alert_frequency: 'off',
        last_run_at: null,
        filters: {},
      }))
      .mockResolvedValueOnce(okResponse({ // reload
        saved_searches: [{
          id: 'new-1',
          name: 'New one',
          search_type: 'meeting_search',
          query_text: 'parks',
          alert_enabled: false,
          alert_frequency: 'off',
          last_run_at: null,
          filters: {},
        }],
      }))

    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/no saved searches yet/i)).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /new saved search/i }))

    // Modal appears
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.type(screen.getByLabelText(/^name$/i), 'New one')
    await user.type(screen.getByLabelText(/^query$/i), 'parks')

    // There may be other "Create..." buttons in the page (empty state CTA),
    // so target the submit button inside the dialog explicitly.
    const dialog = screen.getByRole('dialog')
    const submitBtn = dialog.querySelector('button[type="submit"]')
    await user.click(submitBtn)

    await waitFor(() => {
      expect(screen.getByText('New one')).toBeInTheDocument()
    })

    // Verify the create call went out
    const createCalls = mockFetch.mock.calls.filter(
      (call) => call[1]?.method === 'POST'
    )
    expect(createCalls.length).toBeGreaterThanOrEqual(1)
  })

  it('confirms and deletes a saved search', async () => {
    const user = userEvent.setup()
    const initialItem = {
      id: 'del-1',
      name: 'To Delete',
      search_type: 'rag_ask',
      query_text: 'goodbye',
      alert_enabled: false,
      alert_frequency: 'off',
      last_run_at: null,
      filters: {},
    }
    mockFetch
      .mockResolvedValueOnce(okResponse({ saved_searches: [initialItem] })) // load
      .mockResolvedValueOnce(okResponse({ deleted: 'del-1' })) // delete
      .mockResolvedValueOnce(okResponse({ saved_searches: [] })) // reload

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('To Delete')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /delete to delete/i }))

    // Confirmation dialog
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/delete saved search/i)).toBeInTheDocument()

    // Click the confirm button in the dialog (second "Delete" button)
    const dialogs = screen.getAllByRole('dialog')
    const confirmBtn = dialogs[0].querySelector('button.danger')
    await user.click(confirmBtn)

    await waitFor(() => {
      expect(screen.getByText(/no saved searches yet/i)).toBeInTheDocument()
    })

    // Verify delete call
    const deleteCalls = mockFetch.mock.calls.filter(
      (call) => call[1]?.method === 'DELETE'
    )
    expect(deleteCalls.length).toBeGreaterThanOrEqual(1)
  })

  it('runs a saved search and calls the run endpoint', async () => {
    const user = userEvent.setup()
    const item = {
      id: 'run-1',
      name: 'Runnable',
      search_type: 'meeting_search',
      query_text: 'budget',
      alert_enabled: false,
      alert_frequency: 'off',
      last_run_at: null,
      filters: {},
    }
    mockFetch
      .mockResolvedValueOnce(okResponse({ saved_searches: [item] }))
      .mockResolvedValueOnce(okResponse({
        saved_search: { ...item, last_run_at: '2026-04-02T00:00:00+00:00' },
        result_type: 'meeting_search',
        results: { results: [], total: 0 },
      }))
      .mockResolvedValueOnce(okResponse({
        saved_searches: [{ ...item, last_run_at: '2026-04-02T00:00:00+00:00' }],
      }))

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('Runnable')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /run runnable/i }))

    await waitFor(() => {
      const runCalls = mockFetch.mock.calls.filter(
        (call) => typeof call[0] === 'string' && call[0].includes('/run')
      )
      expect(runCalls.length).toBeGreaterThanOrEqual(1)
    })
  })
})
