// @vitest-environment jsdom
/**
 * ConnectorHistogram unit tests.
 *
 * AC: All-zero windows render "no throughput recorded" empty state
 *     (data-testid="histogram-empty") instead of a fake min-height baseline.
 * AC: Non-zero data renders SVG bars (data-testid="histogram-bars").
 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ConnectorHistogram as SourceConnectorHistogram } from './ConnectorHistogram'
import type { CountBucket } from '@/lib/bucket-series'
import { AppTimezoneProvider } from '@/components/ui/timezone-context'

const ZEROS = Array(24).fill(0)
const WITH_DATA = Array(24)
  .fill(0)
  .map((_, i) => (i === 12 ? 50 : i === 13 ? 30 : 0))

function ConnectorHistogram({ data, secondaryData, ...props }: { data: number[]; secondaryData?: number[]; height?: number; maxValue?: number }) {
  const origin = Date.parse('2026-05-10T00:00:00Z')
  const buckets: CountBucket[] = data.map((count, index) => ({
    bucket_start: new Date(origin + index * 3_600_000).toISOString(),
    bucket_end: new Date(origin + (index + 1) * 3_600_000).toISOString(),
    count, filtered: secondaryData?.[index] ?? 0, listening: 'unknown',
  }))
  return <AppTimezoneProvider timezone="UTC"><SourceConnectorHistogram buckets={buckets} {...props} /></AppTimezoneProvider>
}

describe('ConnectorHistogram', () => {
  it('renders empty state when all buckets are zero', () => {
    const html = renderToStaticMarkup(<ConnectorHistogram data={ZEROS} />)
    expect(html).toContain('data-testid="histogram-empty"')
    expect(html).not.toContain('data-testid="histogram-bars"')
    expect(html).toContain('total 0 events')
    expect(html).toContain('liveness unknown')
  })

  it('renders bars when at least one bucket is non-zero', () => {
    const html = renderToStaticMarkup(<ConnectorHistogram data={WITH_DATA} />)
    expect(html).toContain('data-testid="histogram-bars"')
    expect(html).not.toContain('data-testid="histogram-empty"')
    expect(html).not.toContain('no throughput recorded')
  })

  it('still shows hour labels in both empty and non-empty states', () => {
    const emptyHtml = renderToStaticMarkup(<ConnectorHistogram data={ZEROS} />)
    const barsHtml = renderToStaticMarkup(<ConnectorHistogram data={WITH_DATA} />)
    // Actual source hours in the explicit owner timezone, via canonical time formatting.
    for (const label of ['12:00 AM UTC', '3:00 AM UTC', '12:00 PM UTC', '11:00 PM UTC']) {
      expect(emptyHtml).toContain(label)
      expect(barsHtml).toContain(label)
    }
  })

  it('retains short keyed windows without fabricating extra buckets', () => {
    // Only these ten source cells exist; rendering preserves all ten.
    const short = Array(10).fill(5)
    const html = renderToStaticMarkup(<ConnectorHistogram data={short} />)
    expect(html).toContain('data-testid="histogram-bars"')
    expect(html.match(/class="relative flex-1/g)).toHaveLength(10)
    const origin = Date.parse('2026-05-10T00:00:00Z')
    const sparse: CountBucket[] = [2, 20].map(hour => ({
      bucket_start: new Date(origin + hour * 3_600_000).toISOString(),
      bucket_end: new Date(origin + (hour + 1) * 3_600_000).toISOString(),
      count: 5, listening: 'unknown',
    }))
    const render = (buckets: CountBucket[], window?: { window_start: string; window_end: string; bucket_width_s: number; counts_available: boolean }) =>
      renderToStaticMarkup(<AppTimezoneProvider timezone="UTC"><SourceConnectorHistogram buckets={buckets} window={window} /></AppTimezoneProvider>)
    const ambiguous = render(sparse)
    expect(ambiguous).toContain('Count window unavailable')
    expect(ambiguous).not.toContain('histogram-bars')
    expect(ambiguous).not.toContain('total 10 events')
    const declared = render([...sparse].reverse(), {
      window_start: new Date(origin).toISOString(), window_end: new Date(origin + 24 * 3_600_000).toISOString(),
      bucket_width_s: 3600, counts_available: true,
    })
    expect(declared.match(/class="relative flex-1/g)).toHaveLength(24)
    expect(declared).toContain('total 10 events')
    const contiguous = Array.from({ length: 10 }, (_, hour) => ({ ...sparse[0],
      bucket_start: new Date(origin + hour * 3_600_000).toISOString(),
      bucket_end: new Date(origin + (hour + 1) * 3_600_000).toISOString(),
    }))
    expect(render(contiguous.reverse()).match(/class="relative flex-1/g)).toHaveLength(10)
    expect(render([sparse[0]])).toContain('total 5 events')
    const irregular = [{ ...sparse[0], bucket_end: sparse[1].bucket_start }, sparse[1]]
    expect(render(irregular)).toContain('Count window unavailable')
  })

  it('handles a single non-zero bucket (no all-zero false positive)', () => {
    const single = Array(24).fill(0)
    single[0] = 1
    const html = renderToStaticMarkup(<ConnectorHistogram data={single} />)
    expect(html).toContain('data-testid="histogram-bars"')
    expect(html).not.toContain('histogram-empty')
  })

  // Skip-aware filtered overlay (bu-c48im)

  it('renders a filtered overlay tick when secondaryData has volume', () => {
    const filtered = Array(24)
      .fill(0)
      .map((_, i) => (i === 12 ? 8 : 0))
    const html = renderToStaticMarkup(
      <ConnectorHistogram data={WITH_DATA} secondaryData={filtered} />,
    )
    // The quiet overlay uses the muted-foreground/25 fill, distinct from the
    // ingested bars' foreground fills.
    expect(html).toContain('bg-muted-foreground/25')
    expect(html).toContain('data-has-filtered="true"')
  })

  it('does not render an overlay when secondaryData is all zero', () => {
    const html = renderToStaticMarkup(
      <ConnectorHistogram data={WITH_DATA} secondaryData={ZEROS} />,
    )
    expect(html).not.toContain('bg-muted-foreground/25')
    expect(html).not.toContain('data-has-filtered')
  })

  it('surfaces filtered volume even when ingested is all zero (100% skip-routed)', () => {
    // A fully skip-routed connector has zero ingested but real filtered volume —
    // it must NOT collapse to the "no throughput recorded" empty state, or the
    // skip volume the histogram exists to surface would be hidden.
    const filtered = Array(24)
      .fill(0)
      .map((_, i) => (i === 5 ? 3 : 0))
    const html = renderToStaticMarkup(
      <ConnectorHistogram data={ZEROS} secondaryData={filtered} />,
    )
    expect(html).not.toContain('histogram-empty')
    expect(html).not.toContain('no throughput recorded')
    expect(html).toContain('data-testid="histogram-bars"')
    expect(html).toContain('bg-muted-foreground/25')
  })
})
