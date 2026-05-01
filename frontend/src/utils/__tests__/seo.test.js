import { describe, it, expect } from 'vitest'
import {
  cleanTitle,
  formatLongDate,
  buildSeoTitle,
  buildSeoDescription,
} from '../seo'

describe('cleanTitle', () => {
  it('strips Granicus part-number suffix', () => {
    expect(cleanTitle('Urban County Council (1)')).toBe('Urban County Council')
  })

  it('strips multi-digit suffix', () => {
    expect(cleanTitle('Council Work Session (12)')).toBe('Council Work Session')
  })

  it('leaves titles without a suffix alone', () => {
    expect(cleanTitle('Urban County Council')).toBe('Urban County Council')
  })

  it('does not strip internal parens', () => {
    expect(cleanTitle('Budget (BFED) Committee (1)')).toBe('Budget (BFED) Committee')
  })

  it('handles null and empty', () => {
    expect(cleanTitle(null)).toBe('')
    expect(cleanTitle('')).toBe('')
  })
})

describe('formatLongDate', () => {
  it('formats ISO date as long form', () => {
    expect(formatLongDate('2026-04-30')).toBe('April 30, 2026')
  })

  it('strips leading zero from day', () => {
    expect(formatLongDate('2026-01-05')).toBe('January 5, 2026')
  })

  it('handles December', () => {
    expect(formatLongDate('2025-12-31')).toBe('December 31, 2025')
  })

  it('returns raw on invalid format', () => {
    expect(formatLongDate('not-a-date')).toBe('not-a-date')
  })

  it('returns empty on null', () => {
    expect(formatLongDate(null)).toBe('')
    expect(formatLongDate('')).toBe('')
  })
})

describe('buildSeoTitle', () => {
  it('combines cleaned title and long date', () => {
    expect(buildSeoTitle('Urban County Council (1)', '2026-04-30'))
      .toBe('Urban County Council - April 30, 2026')
  })

  it('falls back to title when no date', () => {
    expect(buildSeoTitle('Urban County Council (1)', null))
      .toBe('Urban County Council')
  })

  it('falls back to date when no title', () => {
    expect(buildSeoTitle(null, '2026-04-30')).toBe('April 30, 2026')
  })

  it('returns default when both missing', () => {
    expect(buildSeoTitle(null, null)).toBe('LFUCG Meeting')
  })
})

describe('buildSeoDescription', () => {
  it('pulls first substantive paragraph from summary', () => {
    const summary =
      '## Meeting Overview\n[timestamp: 00:00]\n\n' +
      'The Urban County Council convened on April 30, 2026, to consider ' +
      'two ordinances on second reading and several first-reading items.\n\n' +
      '## Attendance\n\nAll council members present.\n'
    const result = buildSeoDescription(summary)
    expect(result).toContain('Urban County Council convened')
    expect(result).not.toContain('##')
    expect(result).not.toContain('[timestamp:')
  })

  it('truncates at maxChars on word boundary', () => {
    const long = 'word '.repeat(200)
    const result = buildSeoDescription(long, '', 50)
    expect(result.length).toBeLessThanOrEqual(51)
    expect(result.endsWith('…')).toBe(true)
  })

  it('uses fallback when summary missing', () => {
    const result = buildSeoDescription(
      '',
      'The agenda includes ordinances and budget items for review by the council.',
    )
    expect(result).toContain('agenda includes ordinances')
  })

  it('returns empty when no summary or fallback', () => {
    expect(buildSeoDescription('', '')).toBe('')
    expect(buildSeoDescription(null, null)).toBe('')
  })

  it('skips short paragraphs', () => {
    const summary = '## Heading\n\nshort.\n\n' + 'Real content '.repeat(20)
    const result = buildSeoDescription(summary)
    expect(result).toContain('Real content')
    expect(result).not.toContain('short')
  })
})
