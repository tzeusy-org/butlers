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
import { ConnectorHistogram } from './ConnectorHistogram'

const ZEROS = Array(24).fill(0)
const WITH_DATA = Array(24)
  .fill(0)
  .map((_, i) => (i === 12 ? 50 : i === 13 ? 30 : 0))

describe('ConnectorHistogram', () => {
  it('renders empty state when all buckets are zero', () => {
    const html = renderToStaticMarkup(<ConnectorHistogram data={ZEROS} />)
    expect(html).toContain('data-testid="histogram-empty"')
    expect(html).not.toContain('data-testid="histogram-bars"')
    expect(html).toContain('no throughput recorded')
  })

  it('renders bars when at least one bucket is non-zero', () => {
    const html = renderToStaticMarkup(<ConnectorHistogram data={WITH_DATA} />)
    expect(html).toContain('data-testid="histogram-bars"')
    expect(html).not.toContain('data-testid="histogram-empty"')
    expect(html).not.toContain('no throughput recorded')
  })

  it('labels columns as relative rolling hours, never clock hours', () => {
    const emptyHtml = renderToStaticMarkup(<ConnectorHistogram data={ZEROS} />)
    const barsHtml = renderToStaticMarkup(<ConnectorHistogram data={WITH_DATA} />)
    for (const html of [emptyHtml, barsHtml]) {
      for (const label of ['-24h', '-12h', 'now']) expect(html).toContain(`>${label}<`)
      // The old positional strip claimed clock hours its bars did not represent.
      for (const label of ['00', '12', '23']) expect(html).not.toContain(`>${label}<`)
    }
  })

  it('draws no bar for an unreported slot, distinct from a measured zero', () => {
    // Slot 0 measured zero, slot 1 unreported, slot 2 volume; short input pads with null.
    const html = renderToStaticMarkup(<ConnectorHistogram data={[0, null, 4]} />)
    expect(html).toContain('data-testid="histogram-bars"')
    expect(html.match(/data-slot="unreported"/g)).toHaveLength(22)
    expect(html.match(/<rect/g)).toHaveLength(2)
    expect(html).toContain('fill-border')
  })

  it('hatches a not-listening slot, distinct in shape from a measured zero (bu-s11n0s.5)', () => {
    const listening = Array(24).fill('live')
    listening[5] = listening[6] = listening[7] = 'deaf'
    listening[23] = 'unknown'
    // A deaf window with no volume still draws the strip: deafness is not "no throughput".
    const html = renderToStaticMarkup(<ConnectorHistogram data={ZEROS} listening={listening} />)
    expect(html).not.toContain('histogram-empty')
    const slots = html.match(/<g[^>]*>/g) ?? []
    const deafSlots = slots.flatMap((g, i) => (g.includes('data-slot="deaf"') ? [i] : []))
    expect(deafSlots).toEqual([5, 6, 7])
    expect(html.match(/fill="url\(#/g)).toHaveLength(3)
    expect(html).toContain('not listening 3h')
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
    expect(html).toContain('fill-muted-foreground/25')
    expect(html).toContain('data-has-filtered="true"')
  })

  it('does not render an overlay when secondaryData is all zero', () => {
    const html = renderToStaticMarkup(
      <ConnectorHistogram data={WITH_DATA} secondaryData={ZEROS} />,
    )
    expect(html).not.toContain('fill-muted-foreground/25')
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
    expect(html).toContain('fill-muted-foreground/25')
  })
})
