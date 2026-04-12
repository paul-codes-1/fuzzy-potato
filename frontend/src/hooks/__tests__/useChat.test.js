import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { I18nProvider } from '../../i18n/I18nProvider'

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

// Wrapper that provides I18nProvider (required by useChat)
function wrapper({ children }) {
  return createElement(I18nProvider, null, children)
}

// Import after mocks are set up
import { useChat } from '../useChat'

describe('useChat', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  // ============================================================
  // Initial state
  // ============================================================

  it('has correct initial state', () => {
    const { result } = renderHook(() => useChat(), { wrapper })

    expect(result.current.messages).toEqual([])
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.modelProvider).toBe('openai')
    expect(result.current.filters).toEqual({
      meeting_body: '',
      date_after: '',
      date_before: '',
    })
  })

  // ============================================================
  // sendMessage
  // ============================================================

  it('adds user message and sets loading when sending', async () => {
    // Never-resolving fetch to freeze in loading state
    mockFetch.mockImplementation(() => new Promise(() => {}))

    const { result } = renderHook(() => useChat(), { wrapper })

    act(() => {
      result.current.sendMessage('What about zoning?')
    })

    // User message should be added immediately
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].role).toBe('user')
    expect(result.current.messages[0].content).toBe('What about zoning?')
    expect(result.current.messages[0].timestamp).toBeInstanceOf(Date)
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeNull()
  })

  it('adds assistant message on successful response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        content: 'The zoning ordinance was passed 8-0.',
        sources: [{ clip_id: 6669, title: 'Council Meeting' }],
        model_used: 'gpt-4o',
      }),
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('What about zoning?')
    })

    expect(result.current.messages).toHaveLength(2)
    expect(result.current.messages[0].role).toBe('user')
    expect(result.current.messages[1].role).toBe('assistant')
    expect(result.current.messages[1].content).toBe('The zoning ordinance was passed 8-0.')
    expect(result.current.messages[1].sources).toEqual([{ clip_id: 6669, title: 'Council Meeting' }])
    expect(result.current.messages[1].model).toBe('gpt-4o')
    expect(result.current.messages[1].timestamp).toBeInstanceOf(Date)
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('sends correct request body to /api/chat', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ content: 'Response', sources: [] }),
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('budget question')
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, options] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/chat')
    expect(options.method).toBe('POST')
    expect(options.headers['Content-Type']).toBe('application/json')

    const body = JSON.parse(options.body)
    expect(body.messages).toEqual([{ role: 'user', content: 'budget question' }])
    expect(body.model_provider).toBe('openai')
  })

  it('includes filters in request body when set', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ content: 'Response', sources: [] }),
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    act(() => {
      result.current.setFilters({
        meeting_body: 'Council',
        date_after: '2025-01-01',
        date_before: '2025-12-31',
      })
    })

    await act(async () => {
      await result.current.sendMessage('budget question')
    })

    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.meeting_body).toBe('Council')
    expect(body.date_after).toBe('2025-01-01')
    expect(body.date_before).toBe('2025-12-31')
  })

  it('accumulates messages across multiple exchanges', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ content: 'First response', sources: [] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ content: 'Second response', sources: [] }),
      })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('First question')
    })

    await act(async () => {
      await result.current.sendMessage('Follow up')
    })

    expect(result.current.messages).toHaveLength(4)
    expect(result.current.messages[0].content).toBe('First question')
    expect(result.current.messages[1].content).toBe('First response')
    expect(result.current.messages[2].content).toBe('Follow up')
    expect(result.current.messages[3].content).toBe('Second response')

    // Second request should include full conversation history
    const secondBody = JSON.parse(mockFetch.mock.calls[1][1].body)
    expect(secondBody.messages).toHaveLength(3) // first user + first assistant + second user
  })

  // ============================================================
  // Error handling
  // ============================================================

  it('sets error on server error response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('test')
    })

    expect(result.current.error).toBe('Server error: 500')
    expect(result.current.loading).toBe(false)
    // User message is still added
    expect(result.current.messages).toHaveLength(1)
    expect(result.current.messages[0].role).toBe('user')
  })

  it('sets error on network failure', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network error'))

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('test')
    })

    expect(result.current.error).toBe('Network error')
    expect(result.current.loading).toBe(false)
  })

  it('clears previous error when sending a new message', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: false, status: 500 })
      .mockImplementation(() => new Promise(() => {}))

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('fail')
    })
    expect(result.current.error).toBeTruthy()

    act(() => {
      result.current.sendMessage('retry')
    })
    expect(result.current.error).toBeNull()
  })

  // ============================================================
  // clearChat
  // ============================================================

  it('clears messages and error', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ content: 'Response', sources: [] }),
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('hello')
    })
    expect(result.current.messages).toHaveLength(2)

    act(() => {
      result.current.clearChat()
    })

    expect(result.current.messages).toEqual([])
    expect(result.current.error).toBeNull()
  })

  // ============================================================
  // Loading state
  // ============================================================

  it('sets loading false after successful response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ content: 'done', sources: [] }),
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('test')
    })

    expect(result.current.loading).toBe(false)
  })

  it('sets loading false after error', async () => {
    mockFetch.mockRejectedValueOnce(new Error('fail'))

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('test')
    })

    expect(result.current.loading).toBe(false)
  })

  // ============================================================
  // Model provider
  // ============================================================

  it('uses selected model provider in request', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ content: 'Response', sources: [] }),
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    act(() => {
      result.current.setModelProvider('anthropic')
    })

    await act(async () => {
      await result.current.sendMessage('test')
    })

    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.model_provider).toBe('anthropic')
  })

  // ============================================================
  // retry
  // ============================================================

  it('retry resends the last user message', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: false, status: 500 })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ content: 'Retry success', sources: [] }),
      })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('important question')
    })
    expect(result.current.error).toBeTruthy()

    await act(async () => {
      result.current.retry()
      // retry uses setTimeout(0), wait for it
      await new Promise(r => setTimeout(r, 10))
    })

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    // Should have re-sent with the same content
    expect(mockFetch).toHaveBeenCalledTimes(2)
    const retryBody = JSON.parse(mockFetch.mock.calls[1][1].body)
    expect(retryBody.messages[0].content).toBe('important question')
  })

  // ============================================================
  // Sources default to empty array
  // ============================================================

  it('defaults sources to empty array if not in response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ content: 'No sources' }),
    })

    const { result } = renderHook(() => useChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('test')
    })

    expect(result.current.messages[1].sources).toEqual([])
  })
})
