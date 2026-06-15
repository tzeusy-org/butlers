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

  it('still shows hour labels in both empty and non-empty states', () => {
    const emptyHtml = renderToStaticMarkup(<ConnectorHistogram data={ZEROS} />)
    const barsHtml = renderToStaticMarkup(<ConnectorHistogram data={WITH_DATA} />)
    // HOUR_LABELS = ['00', '03', '06', '09', '12', '15', '18', '21', '23']
    for (const label of ['00', '03', '12', '23']) {
      expect(emptyHtml).toContain(label)
      expect(barsHtml).toContain(label)
    }
  })

  it('pads short data arrays to 24 buckets', () => {
    // 10-element input — should not throw and should render 24 bars
    const short = Array(10).fill(5)
    const html = renderToStaticMarkup(<ConnectorHistogram data={short} />)
    expect(html).toContain('data-testid="histogram-bars"')
  })

  it('handles a single non-zero bucket (no all-zero false positive)', () => {
    const single = Array(24).fill(0)
    single[0] = 1
    const html = renderToStaticMarkup(<ConnectorHistogram data={single} />)
    expect(html).toContain('data-testid="histogram-bars"')
    expect(html).not.toContain('histogram-empty')
  })
})
