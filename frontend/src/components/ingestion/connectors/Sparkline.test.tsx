// @vitest-environment jsdom
/**
 * Sparkline unit tests.
 *
 * AC (bu-4utdw.10 item 8): the sparkline is no longer aria-hidden with no
 * numeric fallback — it carries an aria-label with the 24h total and peak
 * hour, and normalizes against a shared `maxValue` when the caller passes
 * the roster-wide peak (so bar heights are comparable across rows).
 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Sparkline } from './Sparkline'

describe('Sparkline accessibility', () => {
  it('is not aria-hidden — it carries a numeric aria-label instead', () => {
    const data = Array(24).fill(0)
    data[10] = 5
    const html = renderToStaticMarkup(<Sparkline data={data} />)
    expect(html).not.toContain('aria-hidden')
    expect(html).toContain('role="img"')
  })

  it('reports the 24h total and peak hour in the aria-label', () => {
    const data = Array(24).fill(0)
    data[23] = 10 // most recent hour — "in the last hour"
    data[20] = 4
    const html = renderToStaticMarkup(<Sparkline data={data} />)
    expect(html).toContain('aria-label="24h activity: 14 events total, peak in the last hour"')
  })

  it('reports "Xh ago" for a peak earlier in the window', () => {
    const data = Array(24).fill(0)
    data[0] = 7 // oldest bucket — 23h ago
    const html = renderToStaticMarkup(<Sparkline data={data} />)
    expect(html).toContain('aria-label="24h activity: 7 events total, peak 23h ago"')
  })

  it('reports "no events" when every bucket is zero', () => {
    const html = renderToStaticMarkup(<Sparkline data={Array(24).fill(0)} />)
    expect(html).toContain('aria-label="24h activity: no events"')
  })
})

describe('Sparkline normalization', () => {
  it('defaults to normalizing against its own peak when maxValue is omitted', () => {
    const data = Array(24).fill(0)
    data[0] = 10
    const html = renderToStaticMarkup(<Sparkline data={data} height={24} />)
    // The one non-zero bar should reach the full height (10 / peak(10) * 24 = 24).
    expect(html).toContain('height="24"')
  })

  it('normalizes against a shared maxValue so bars are comparable across rows', () => {
    const data = Array(24).fill(0)
    data[0] = 10
    // Roster-wide peak is 100 — this row's bar should reach ~10% of height, not 100%.
    const html = renderToStaticMarkup(<Sparkline data={data} height={100} maxValue={100} />)
    expect(html).toContain('height="10"')
  })
})
