// Parse a clock string ("MM:SS" or "H:MM:SS") into total seconds.
// Returns null for anything that isn't a parseable clock so callers
// can skip the seek instead of jumping to a garbage offset.
export function parseClock(str) {
  if (typeof str !== 'string') return null
  const parts = str.trim().split(':')
  if (parts.length < 2 || parts.length > 3) return null
  if (parts.some(p => !/^\d+$/.test(p))) return null
  const nums = parts.map(Number)
  if (nums.length === 3) {
    const [h, m, s] = nums
    return h * 3600 + m * 60 + s
  }
  const [m, s] = nums
  return m * 60 + s
}
