import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useFeedsLink } from '../useFeedsLink'

const mockFetch = vi.fn()
global.fetch = mockFetch

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }
}

describe('useFeedsLink', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('returns null while the lookup is in flight, then the payload', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({
        slug: 'example-post',
        title: 'Example post',
        link: 'https://feeds.example/article/example-post',
        publishedAt: '2026-05-01T00:00:00Z',
      }),
    )

    const { result } = renderHook(() => useFeedsLink(6760))
    expect(result.current).toBeNull()

    await waitFor(() => {
      expect(result.current?.slug).toBe('example-post')
      expect(result.current?.link).toMatch(/example-post$/)
    })

    const url = mockFetch.mock.calls[0][0]
    expect(url).toMatch(/by-source\?source=lfucg-meeting-archive&clipId=6760/)
  })

  it('treats 404 as null without throwing', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ error: 'not found' }, 404))

    // Use a different clipId so the cache from earlier tests doesn't match.
    const { result } = renderHook(() => useFeedsLink(99999))

    // Wait for at least one fetch to settle. Hook is fire-and-forget,
    // so we just need the network call to have occurred.
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1))
    expect(result.current).toBeNull()
  })

  it('caches results across remounts (no second fetch)', async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ slug: 'cached', title: 'Cached', link: 'https://x', publishedAt: 'now' }),
    )

    const { unmount } = renderHook(() => useFeedsLink(8888))
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1))
    unmount()

    const { result: result2 } = renderHook(() => useFeedsLink(8888))
    // No new fetch — value should appear synchronously from the cache.
    expect(result2.current?.slug).toBe('cached')
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})
