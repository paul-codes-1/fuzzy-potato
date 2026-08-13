import { useEffect, useRef } from 'react'

/**
 * Manually-placed AdSense unit. The loader script lives in index.html.
 * Guard on data-adsbygoogle-status so a remount never double-pushes the
 * same <ins> (AdSense throws on that).
 */
export default function AdSlot({ slot }) {
  const ref = useRef(null)

  useEffect(() => {
    const ins = ref.current
    if (!ins || ins.dataset.adsbygoogleStatus === 'done') return
    try {
      ;(window.adsbygoogle = window.adsbygoogle || []).push({})
    } catch {
      // loader blocked or absent — leave the slot empty
    }
  }, [])

  return (
    <div className="ad-slot">
      <span className="ad-slot-label">Advertisement</span>
      <ins
        ref={ref}
        className="adsbygoogle"
        style={{ display: 'block' }}
        data-ad-client="ca-pub-1303389657186007"
        data-ad-slot={slot}
        data-ad-format="auto"
        data-full-width-responsive="true"
      />
    </div>
  )
}
