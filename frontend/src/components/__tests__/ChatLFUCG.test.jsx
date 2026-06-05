import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import ChatLFUCG from '../ChatLFUCG'

// jsdom doesn't implement scrollIntoView
Element.prototype.scrollIntoView = vi.fn()

const mockFetch = vi.fn()
global.fetch = mockFetch

function renderChat() {
  return render(
    <MemoryRouter>
      <ChatLFUCG />
    </MemoryRouter>
  )
}

describe('ChatLFUCG', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  // ============================================================
  // Empty state tests
  // ============================================================

  it('renders empty state with suggested questions', () => {
    renderChat()
    expect(screen.getByText(/ask questions about lexington/i)).toBeInTheDocument()
    // Should have suggested question buttons
    expect(screen.getByText(/budget changes/i)).toBeInTheDocument()
    expect(screen.getByText(/zoning approvals/i)).toBeInTheDocument()
  })

  it('renders send button', () => {
    renderChat()
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument()
  })

  // ============================================================
  // Message flow tests
  // ============================================================

  it('sends message and displays response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        role: 'assistant',
        content: 'The zoning ordinance was passed 8-0.',
        sources: [],
        model_used: 'gpt-4o',
        filters_applied: {},
        chunks_retrieved: 3,
      }),
    })

    renderChat()
    const input = screen.getByPlaceholderText(/ask a question about/i)
    await userEvent.type(input, 'What about zoning?')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getByText(/zoning ordinance was passed/i)).toBeInTheDocument()
    })
  })

  it('renders model badge on assistant message', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        role: 'assistant',
        content: 'Test answer.',
        sources: [],
        model_used: 'gpt-4o',
        filters_applied: {},
        chunks_retrieved: 1,
      }),
    })

    renderChat()
    await userEvent.type(screen.getByPlaceholderText(/ask a question about/i), 'test')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(document.querySelector('.chat-model-badge')).toBeInTheDocument()
      expect(document.querySelector('.chat-model-badge').textContent).toBe('GPT-4o')
    })
  })

  // ============================================================
  // Clear chat tests
  // ============================================================

  it('clears chat on new chat button click', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        role: 'assistant',
        content: 'Some answer about parks.',
        sources: [],
        model_used: 'gpt-4o',
        filters_applied: {},
        chunks_retrieved: 1,
      }),
    })

    renderChat()
    await userEvent.type(screen.getByPlaceholderText(/ask a question about/i), 'parks')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getByText(/some answer about parks/i)).toBeInTheDocument()
    })

    // Click New Chat
    await userEvent.click(screen.getByRole('button', { name: /new chat/i }))

    // Messages should be cleared, empty state should return
    await waitFor(() => {
      expect(screen.getByText(/ask questions about lexington/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/some answer about parks/i)).not.toBeInTheDocument()
  })

  // ============================================================
  // Loading state tests
  // ============================================================

  it('shows typing indicator while loading', async () => {
    mockFetch.mockImplementation(() => new Promise(() => {})) // never resolves

    renderChat()
    await userEvent.type(screen.getByPlaceholderText(/ask a question about/i), 'test')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(document.querySelector('.chat-typing')).toBeInTheDocument()
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

    renderChat()
    await userEvent.type(screen.getByPlaceholderText(/ask a question about/i), 'test')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getByText(/error|something went wrong/i)).toBeInTheDocument()
    })
  })

  // ============================================================
  // Conversation history tests
  // ============================================================

  it('sends conversation history in request', async () => {
    // First message
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        role: 'assistant',
        content: 'First answer.',
        sources: [],
        model_used: 'gpt-4o',
        filters_applied: {},
        chunks_retrieved: 1,
      }),
    })

    renderChat()
    await userEvent.type(screen.getByPlaceholderText(/ask a question about/i), 'first question')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getByText(/first answer/i)).toBeInTheDocument()
    })

    // Second message
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        role: 'assistant',
        content: 'Second answer.',
        sources: [],
        model_used: 'gpt-4o',
        filters_applied: {},
        chunks_retrieved: 1,
      }),
    })

    await userEvent.type(screen.getByPlaceholderText(/ask a question about/i), 'second question')
    await userEvent.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => {
      expect(screen.getByText(/second answer/i)).toBeInTheDocument()
    })

    // Verify the second fetch call includes conversation history
    const secondCallBody = JSON.parse(mockFetch.mock.calls[1][1].body)
    expect(secondCallBody.messages.length).toBeGreaterThanOrEqual(3) // user + assistant + user
    expect(secondCallBody.messages[0].role).toBe('user')
    expect(secondCallBody.messages[0].content).toBe('first question')
  })
})