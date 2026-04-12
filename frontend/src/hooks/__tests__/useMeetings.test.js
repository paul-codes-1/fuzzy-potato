import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

// Mock fetch globally
const mockFetch = vi.fn()
global.fetch = mockFetch

import { useMeetings, useMeeting } from '../useMeetings'

describe('useMeetings', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  // ============================================================
  // Initial / loading state
  // ============================================================

  it('starts in loading state with empty meetings', () => {
    mockFetch.mockImplementation(() => new Promise(() => {}))

    const { result } = renderHook(() => useMeetings())

    expect(result.current.meetings).toEqual([])
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeNull()
  })

  // ============================================================
  // Successful fetch
  // ============================================================

  it('fetches meetings from /data/index.json on mount', async () => {
    const mockClips = [
      { clip_id: 6669, title: 'Council Meeting', date: '2026-01-08', meeting_body: 'Council' },
      { clip_id: 6670, title: 'Planning Commission', date: '2026-01-09', meeting_body: 'Planning' },
    ]

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ clips: mockClips }),
    })

    const { result } = renderHook(() => useMeetings())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.meetings).toEqual(mockClips)
    expect(result.current.error).toBeNull()
    expect(mockFetch).toHaveBeenCalledWith('/data/index.json')
  })

  it('returns empty array when clips field is missing', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    })

    const { result } = renderHook(() => useMeetings())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.meetings).toEqual([])
    expect(result.current.error).toBeNull()
  })

  // ============================================================
  // Error handling
  // ============================================================

  it('sets error on server error response', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    })

    const { result } = renderHook(() => useMeetings())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Failed to fetch meeting index')
    expect(result.current.meetings).toEqual([])
    expect(errorSpy).toHaveBeenCalled()
    errorSpy.mockRestore()
  })

  it('sets error on network failure', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetch.mockRejectedValueOnce(new Error('Network error'))

    const { result } = renderHook(() => useMeetings())

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Network error')
    expect(result.current.meetings).toEqual([])
    expect(errorSpy).toHaveBeenCalled()
    errorSpy.mockRestore()
  })
})

describe('useMeeting', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  // ============================================================
  // Initial state
  // ============================================================

  it('starts in loading state with null data', () => {
    mockFetch.mockImplementation(() => new Promise(() => {}))

    const { result } = renderHook(() => useMeeting(6669))

    expect(result.current.meeting).toBeNull()
    expect(result.current.extractedFacts).toBeNull()
    expect(result.current.transcript).toBeNull()
    expect(result.current.agenda).toBeNull()
    expect(result.current.minutes).toBeNull()
    expect(result.current.loading).toBe(true)
    expect(result.current.error).toBeNull()
  })

  it('does not fetch when clipId is falsy', () => {
    const { result } = renderHook(() => useMeeting(null))

    // Should not call fetch at all
    expect(mockFetch).not.toHaveBeenCalled()
    expect(result.current.loading).toBe(true)
  })

  // ============================================================
  // Successful fetch - metadata only
  // ============================================================

  it('fetches meeting metadata', async () => {
    const metadata = {
      clip_id: 6669,
      title: 'Council Meeting',
      date: '2026-01-08',
      files: {},
    }

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => metadata,
    })

    const { result } = renderHook(() => useMeeting(6669))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.meeting).toEqual(metadata)
    expect(result.current.error).toBeNull()
    expect(mockFetch).toHaveBeenCalledWith('/data/clips/6669/metadata.json')
  })

  // ============================================================
  // Successful fetch - with associated files
  // ============================================================

  it('fetches associated files when present in metadata', async () => {
    const metadata = {
      clip_id: 6669,
      title: 'Council Meeting',
      files: {
        extracted_facts: 'extracted_facts.json',
        transcript: 'transcript_6669.txt',
        transcript_segments: 'transcript_6669_segments.json',
        agenda_txt: 'agenda.txt',
        minutes_txt: 'minutes.txt',
      },
    }

    const extractedFacts = {
      meeting_info: { date: '2026-01-08', body: 'Council' },
      motions_and_votes: [],
    }
    const transcriptSegments = [{ start: 0, end: 10, text: 'Hello' }]

    mockFetch
      // metadata
      .mockResolvedValueOnce({
        ok: true,
        json: async () => metadata,
      })
      // extracted_facts
      .mockResolvedValueOnce({
        ok: true,
        json: async () => extractedFacts,
      })
      // transcript
      .mockResolvedValueOnce({
        ok: true,
        text: async () => 'Full transcript text here.',
      })
      // transcript_segments
      .mockResolvedValueOnce({
        ok: true,
        json: async () => transcriptSegments,
      })
      // agenda
      .mockResolvedValueOnce({
        ok: true,
        text: async () => 'Agenda item 1\nAgenda item 2',
      })
      // minutes
      .mockResolvedValueOnce({
        ok: true,
        text: async () => 'Minutes of the meeting.',
      })

    const { result } = renderHook(() => useMeeting(6669))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.meeting).toEqual(metadata)
    expect(result.current.extractedFacts).toEqual(extractedFacts)
    expect(result.current.transcript).toBe('Full transcript text here.')
    expect(result.current.transcriptSegments).toEqual(transcriptSegments)
    expect(result.current.agenda).toBe('Agenda item 1\nAgenda item 2')
    expect(result.current.minutes).toBe('Minutes of the meeting.')
  })

  // ============================================================
  // Error handling
  // ============================================================

  it('sets error when meeting metadata fetch fails', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
    })

    const { result } = renderHook(() => useMeeting(9999))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Meeting not found')
    expect(result.current.meeting).toBeNull()
    expect(errorSpy).toHaveBeenCalled()
    errorSpy.mockRestore()
  })

  it('gracefully handles failed associated file fetches', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const metadata = {
      clip_id: 6669,
      title: 'Council Meeting',
      files: {
        extracted_facts: 'extracted_facts.json',
        transcript: 'transcript_6669.txt',
      },
    }

    mockFetch
      // metadata succeeds
      .mockResolvedValueOnce({
        ok: true,
        json: async () => metadata,
      })
      // extracted_facts fails
      .mockResolvedValueOnce({ ok: false, status: 404 })
      // transcript fails
      .mockRejectedValueOnce(new Error('Network error'))

    const { result } = renderHook(() => useMeeting(6669))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    // Meeting metadata still loaded despite file failures
    expect(result.current.meeting).toEqual(metadata)
    expect(result.current.extractedFacts).toBeNull()
    expect(result.current.transcript).toBeNull()
    expect(result.current.error).toBeNull()
    expect(warnSpy).toHaveBeenCalled()
    warnSpy.mockRestore()
  })
})
