import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useServerSearch } from '../useServerSearch'

const mockFetch = vi.fn()
global.fetch = mockFetch

function jsonResponse(body, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
  }
}

describe('useServerSearch', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('skips fetch when query is empty', async () => {
    const { result } = renderHook(() => useServerSearch(''))
    // Wait past the 250ms debounce window
    await new Promise(r => setTimeout(r, 350))
    expect(mockFetch).not.toHaveBeenCalled()
    expect(result.current.results).toEqual([])
    expect(result.current.isSearching).toBe(false)
  })

  it('fires a debounced POST to /api/search', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      results: [{ clip_id: 100, title: 'Test', date: '2025-01-01', snippet: '<mark>budget</mark>' }],
      count: 1,
    }))

    const { result } = renderHook(() => useServerSearch('budget'))

    expect(mockFetch).not.toHaveBeenCalled()

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1), { timeout: 1000 })

    const [url, opts] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/search')
    expect(opts.method).toBe('POST')
    const body = JSON.parse(opts.body)
    expect(body.q).toBe('budget')

    await waitFor(() => {
      expect(result.current.results).toHaveLength(1)
      expect(result.current.results[0].clip_id).toBe(100)
    })
  })

  it('passes filter params through to the server', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ results: [], count: 0 }))

    renderHook(() => useServerSearch('rezoning', {
      meeting_body: 'Council',
      speaker: 'Mayor Gorton',
      date_after: '2024-01-01',
      date_before: '2025-12-31',
      limit: 25,
    }))

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1), { timeout: 1000 })

    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.meeting_body).toBe('Council')
    expect(body.speaker).toBe('Mayor Gorton')
    expect(body.date_after).toBe('2024-01-01')
    expect(body.date_before).toBe('2025-12-31')
    expect(body.limit).toBe(25)
  })

  it('surfaces server errors', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({}, false, 500))

    const { result } = renderHook(() => useServerSearch('budget'))
    await waitFor(() => expect(result.current.error).toBeTruthy(), { timeout: 1000 })
    expect(result.current.results).toEqual([])
  })

  it('debounces rapid query changes into a single fetch', async () => {
    mockFetch.mockResolvedValue(jsonResponse({ results: [], count: 0 }))

    const { rerender } = renderHook(
      ({ q }) => useServerSearch(q),
      { initialProps: { q: 'b' } },
    )

    // Rapid keystrokes within the 250ms debounce window — should
    // collapse into one fetch with the final value.
    rerender({ q: 'bu' })
    rerender({ q: 'bud' })
    rerender({ q: 'budget' })

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1), { timeout: 1000 })

    const body = JSON.parse(mockFetch.mock.calls[0][1].body)
    expect(body.q).toBe('budget')
  })
})
