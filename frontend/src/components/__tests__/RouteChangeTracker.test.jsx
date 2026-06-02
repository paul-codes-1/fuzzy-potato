import { render } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import RouteChangeTracker from '../RouteChangeTracker.jsx'

// Real timers — fake timers do not flush jsdom's MutationObserver microtask
// queue, which is the whole point of this component.

function Harness({ navRef }) {
  const navigate = useNavigate()
  if (navRef) navRef.current = navigate
  return null
}

function mount(initialPath = '/', navRef = null) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <RouteChangeTracker />
      <Harness navRef={navRef} />
    </MemoryRouter>,
  )
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

describe('RouteChangeTracker', () => {
  beforeEach(() => {
    window.gtag = vi.fn()
    document.title = 'LFUCG Meeting Archive'
  })

  afterEach(() => {
    delete window.gtag
  })

  it('does not fire page_view synchronously on mount', () => {
    mount('/')
    expect(window.gtag).not.toHaveBeenCalled()
  })

  it('fires page_view with updated title once <title> mutates (settles ~150ms)', async () => {
    mount('/meeting/6669')
    document.title = 'January 8 2026 WQFB meeting | LFUCG Meeting Archive'
    await wait(250)
    expect(window.gtag).toHaveBeenCalledTimes(1)
    const [event, name, payload] = window.gtag.mock.calls[0]
    expect(event).toBe('event')
    expect(name).toBe('page_view')
    expect(payload.page_path).toBe('/meeting/6669')
    expect(payload.page_title).toBe('January 8 2026 WQFB meeting | LFUCG Meeting Archive')
  })

  it('debounces multiple title mutations and fires once with final title', async () => {
    mount('/meeting/6669')
    document.title = 'A'
    await wait(50)
    document.title = 'B'
    await wait(50)
    document.title = 'Final title'
    await wait(250)
    expect(window.gtag).toHaveBeenCalledTimes(1)
    expect(window.gtag.mock.calls[0][2].page_title).toBe('Final title')
  })

  it('fires failsafe page_view ~1500ms even if title never changes', async () => {
    mount('/static-page')
    await wait(200)
    expect(window.gtag).not.toHaveBeenCalled() // still waiting on failsafe
    await wait(1500)
    expect(window.gtag).toHaveBeenCalledTimes(1)
    expect(window.gtag.mock.calls[0][2].page_path).toBe('/static-page')
    expect(window.gtag.mock.calls[0][2].page_title).toBe('LFUCG Meeting Archive')
  }, 5000)

  it('fires only once per route even if title mutates again after settle', async () => {
    mount('/meeting/6669')
    document.title = 'Loaded title'
    await wait(250)
    expect(window.gtag).toHaveBeenCalledTimes(1)
    document.title = 'Some later change'
    await wait(250)
    expect(window.gtag).toHaveBeenCalledTimes(1)
  })

  it('fires once per route after a navigation', async () => {
    const { act } = await import('react')
    const navRef = { current: null }
    mount('/a', navRef)
    document.title = 'A page'
    await wait(250)
    expect(window.gtag).toHaveBeenCalledTimes(1)
    expect(window.gtag.mock.calls[0][2].page_path).toBe('/a')

    await act(async () => { navRef.current('/b') })
    document.title = 'B page'
    await wait(250)
    expect(window.gtag).toHaveBeenCalledTimes(2)
    expect(window.gtag.mock.calls[1][2].page_path).toBe('/b')
    expect(window.gtag.mock.calls[1][2].page_title).toBe('B page')
  })
})
