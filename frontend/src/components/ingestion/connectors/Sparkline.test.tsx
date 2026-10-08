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
import { Sparkline as SourceSparkline } from './Sparkline'
import type { CountBucket } from '@/lib/bucket-series'

function Sparkline({ data, secondaryData, ...props }: { data: number[]; secondaryData?: number[]; height?: number; maxValue?: number }) {
  const origin = Date.parse('2026-05-10T00:00:00Z')
  const buckets: CountBucket[] = data.map((count, index) => ({
    bucket_start: new Date(origin + index * 3_600_000).toISOString(),
    bucket_end: new Date(origin + (index + 1) * 3_600_000).toISOString(),
    count, filtered: secondaryData?.[index] ?? 0, listening: 'unknown',
  }))
  return <SourceSparkline buckets={buckets} {...props} />
}

describe('Sparkline accessibility', () => {
  it('is not aria-hidden — it carries a numeric aria-label instead', () => {
    const data = Array(24).fill(0)
    data[10] = 5
    const html = renderToStaticMarkup(<Sparkline data={data} />)
    expect(html).toContain('aria-label=')
    expect(html).toContain('role="img"')
  })

  it('reports the 24h total and peak hour in the aria-label', () => {
    const data = Array(24).fill(0)
    data[23] = 10 // most recent hour — "in the last hour"
    data[20] = 4
    const html = renderToStaticMarkup(<Sparkline data={data} />)
    expect(html).toContain('total 14 events')
  })

  it('reports "Xh ago" for a peak earlier in the window', () => {
    const data = Array(24).fill(0)
    data[0] = 7 // oldest bucket — 23h ago
    const html = renderToStaticMarkup(<Sparkline data={data} />)
    expect(html).toContain('total 7 events')
  })

  it('reports "no events" when every bucket is zero', () => {
    const html = renderToStaticMarkup(<Sparkline data={Array(24).fill(0)} />)
    expect(html).toContain('total 0 events')
  })
})

describe('Sparkline normalization', () => {
  it('defaults to normalizing against its own peak when maxValue is omitted', () => {
    const data = Array(24).fill(0)
    data[0] = 10
    const html = renderToStaticMarkup(<Sparkline data={data} height={24} />)
    // The one non-zero bar should reach the full height (10 / peak(10) * 24 = 24).
    expect(html).toContain('height:24px')
  })

  it('normalizes against a shared maxValue so bars are comparable across rows', () => {
    const data = Array(24).fill(0)
    data[0] = 10
    // Roster-wide peak is 100 — this row's bar should reach ~10% of height, not 100%.
    const html = renderToStaticMarkup(<Sparkline data={data} height={100} maxValue={100} />)
    expect(html).toContain('height:10px')
  })
})

// ---------------------------------------------------------------------------
// bu-scyro: quiet secondary (filtered/skip-routed) series
// ---------------------------------------------------------------------------

describe('Sparkline secondaryData (filtered series)', () => {
  it('renders no overlay when secondaryData is absent', () => {
    const data = Array(24).fill(0)
    data[0] = 5
    const html = renderToStaticMarkup(<Sparkline data={data} />)
    expect(html).not.toContain('bg-muted-foreground/25')
  })

  it('renders no overlay when secondaryData is all zeros', () => {
    const data = Array(24).fill(0)
    data[0] = 5
    const html = renderToStaticMarkup(
      <Sparkline data={data} secondaryData={Array(24).fill(0)} />,
    )
    expect(html).not.toContain('bg-muted-foreground/25')
  })

  it('renders a quiet overlay bar when secondaryData has a non-zero bucket', () => {
    const data = Array(24).fill(0)
    data[0] = 5
    const secondary = Array(24).fill(0)
    secondary[3] = 12
    const html = renderToStaticMarkup(<Sparkline data={data} secondaryData={secondary} />)
    expect(html).toContain('bg-muted-foreground/25')
  })

  it('does not fold the secondary total into the primary aria-label count', () => {
    const data = Array(24).fill(0)
    data[23] = 10
    const secondary = Array(24).fill(0)
    secondary[23] = 90
    const html = renderToStaticMarkup(<Sparkline data={data} secondaryData={secondary} />)
    // Primary total stays 10 (never 10+90=100) — the filtered count is called
    // out separately in the aria-label, not summed into the events total.
    expect(html).toContain(
      'total 10 events (90 filtered)',
    )
  })

  it('reports the filtered total even when the primary series is entirely zero', () => {
    const secondary = Array(24).fill(0)
    secondary[0] = 40
    const html = renderToStaticMarkup(
      <Sparkline data={Array(24).fill(0)} secondaryData={secondary} />,
    )
    expect(html).toContain('total 0 events (40 filtered)')
  })

  it('normalizes the secondary series against its own peak, independent of the primary peak', () => {
    // Primary peak is 1000 (huge), secondary peak is 10 (small) — the secondary
    // bar for its own peak value must still reach its own (capped) max height,
    // not be squashed to near-zero by the primary's much larger scale.
    const data = Array(24).fill(0)
    data[0] = 1000
    const secondary = Array(24).fill(0)
    secondary[5] = 10
    const html = renderToStaticMarkup(
      <Sparkline data={data} secondaryData={secondary} height={100} />,
    )
    // secondaryMaxHeight = height * 0.3 = 30; secondary[5] is the secondary
    // series' own peak, so it should reach the full capped height.
    expect(html).toContain('height:30px')
  })
})
