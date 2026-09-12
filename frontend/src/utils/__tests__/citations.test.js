import { describe, it, expect, vi } from 'vitest'
import { linkifyCitations, timestampToSeconds, handleCitationClick } from '../citations'

const SOURCES = [{ clip_id: 6865 }, { clip_id: 5695 }, { clip_id: '6757' }]

describe('timestampToSeconds', () => {
  it('parses MM:SS and H:MM:SS', () => {
    expect(timestampToSeconds('12:34')).toBe(754)
    expect(timestampToSeconds('1:02:03')).toBe(3723)
    expect(timestampToSeconds('nope')).toBeNull()
  })
})

describe('linkifyCitations', () => {
  it('links a single clip id', () => {
    const out = linkifyCitations('<p>Passed [Clip 6865].</p>', SOURCES)
    expect(out).toContain('<a href="/meeting/6865" class="citation-link" data-citation-link="1" data-clip="6865">6865</a>')
    expect(out).toMatch(/\[Clip <a[^>]*>6865<\/a>\]/)
  })

  it('links every id in a multi-id bracket', () => {
    const out = linkifyCitations('[Clip 6865, 5695, 6757]', SOURCES)
    expect(out).toContain('href="/meeting/6865"')
    expect(out).toContain('href="/meeting/5695"')
    expect(out).toContain('href="/meeting/6757"')
    expect(out.startsWith('[Clip <a')).toBe(true)
  })

  it('handles "Clips X and Y" and "; Clip Y" forms', () => {
    expect(linkifyCitations('[Clips 6865 and 5695]', SOURCES)).toMatch(/\[Clip <a[^>]*>6865<\/a> and <a[^>]*>5695<\/a>\]/)
    const out = linkifyCitations('[Clip 6865; Clip 5695, 12:34]', SOURCES)
    expect(out).toContain('href="/meeting/5695?t=754"')
    expect(out).toContain('href="/meeting/6865"')
  })

  it('attaches a timestamp to the id that precedes it', () => {
    const out = linkifyCitations('[Clip 6865, 12:34]', SOURCES)
    expect(out).toContain('href="/meeting/6865?t=754"')
    expect(out).toContain('data-t="754"')
    // range: only the start is linked, but the text is preserved
    const range = linkifyCitations('[Clip 6865, 1:07:06-1:09:11]', SOURCES)
    expect(range).toContain('href="/meeting/6865?t=4026"')
    expect(range).toContain('1:09:11')
  })

  it('never turns a timestamp into a clip id', () => {
    const out = linkifyCitations('[Clip 6865, 12:34]', [{ clip_id: 12 }, { clip_id: 6865 }])
    expect(out).not.toContain('href="/meeting/12"')
  })

  it('leaves unknown ids as plain text', () => {
    const out = linkifyCitations('[Clip 9999]', SOURCES)
    expect(out).toBe('[Clip 9999]')
  })

  it('is a no-op without sources', () => {
    expect(linkifyCitations('[Clip 6865]', [])).toBe('[Clip 6865]')
    expect(linkifyCitations('', SOURCES)).toBe('')
  })
})

describe('handleCitationClick', () => {
  it('routes citation links through navigate', () => {
    document.body.innerHTML = '<div id="c"><a href="/meeting/6865?t=5" data-citation-link="1">6865</a></div>'
    const a = document.querySelector('a')
    const navigate = vi.fn()
    const evt = { target: a, preventDefault: vi.fn(), metaKey: false, ctrlKey: false, shiftKey: false, button: 0 }
    handleCitationClick(evt, navigate)
    expect(evt.preventDefault).toHaveBeenCalled()
    expect(navigate).toHaveBeenCalledWith('/meeting/6865?t=5')
  })

  it('ignores modifier clicks and non-citation targets', () => {
    document.body.innerHTML = '<div id="c"><a href="/meeting/6865" data-citation-link="1">6865</a><span>x</span></div>'
    const navigate = vi.fn()
    const a = document.querySelector('a')
    handleCitationClick({ target: a, preventDefault: vi.fn(), metaKey: true, button: 0 }, navigate)
    handleCitationClick({ target: document.querySelector('span'), preventDefault: vi.fn(), button: 0 }, navigate)
    expect(navigate).not.toHaveBeenCalled()
  })
})
