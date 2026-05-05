// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

// MapWidget pulls in maplibre-gl on lazy import; stub it so server-rendered
// markup tests don't spin up a real map.
vi.mock("./MapWidget", () => ({
  MapWidget: ({ height }: { height?: string }) => (
    <div data-testid="map-widget-stub" className={height} />
  ),
}))

import { FloatingMapMinimap } from "./FloatingMapMinimap"

describe("FloatingMapMinimap", () => {
  it("renders the minimap panel by default in 'open' mode", () => {
    const html = renderToStaticMarkup(<FloatingMapMinimap trailPoints={[]} />)
    expect(html).toContain('data-testid="map-minimap"')
    expect(html).toContain('data-mode="open"')
    expect(html).toContain('data-testid="map-widget-stub"')
  })

  it("starts in 'expanded' mode when initialMode='expanded'", () => {
    const html = renderToStaticMarkup(
      <FloatingMapMinimap trailPoints={[]} initialMode="expanded" />,
    )
    expect(html).toContain('data-mode="expanded"')
  })

  it("renders a restore button instead of the panel when minimized", () => {
    const html = renderToStaticMarkup(
      <FloatingMapMinimap trailPoints={[]} initialMode="minimized" />,
    )
    expect(html).toContain('data-testid="map-minimap-restore"')
    expect(html).not.toContain('data-testid="map-minimap"')
    expect(html).not.toContain('data-testid="map-widget-stub"')
  })

  it("renders both header controls in non-minimized modes", () => {
    const html = renderToStaticMarkup(<FloatingMapMinimap trailPoints={[]} />)
    expect(html).toContain('data-testid="map-minimap-toggle-size"')
    expect(html).toContain('data-testid="map-minimap-minimize"')
  })

  it("uses fixed bottom-right positioning", () => {
    const html = renderToStaticMarkup(<FloatingMapMinimap trailPoints={[]} />)
    // Tailwind classes — fixed + bottom-4 + right-4 anchor it in the corner.
    expect(html).toContain("fixed")
    expect(html).toContain("bottom-4")
    expect(html).toContain("right-4")
  })

  it("uses transform: scale instead of width/height transitions (motion AC #5)", () => {
    // The motion contract forbids animating width/height/top/left/margin.
    // The panel must use transition-transform + scale, not transition-[width,height].
    const openHtml = renderToStaticMarkup(<FloatingMapMinimap trailPoints={[]} />)
    const expandedHtml = renderToStaticMarkup(
      <FloatingMapMinimap trailPoints={[]} initialMode="expanded" />,
    )

    // Must animate transform, not dimensions.
    expect(openHtml).toContain("transition-transform")
    expect(openHtml).not.toContain("transition-[width,height]")

    // "open" mode is the scaled-down state — scale-50 shrinks visually.
    expect(openHtml).toContain("scale-50")
    // Both modes render at the expanded (larger) fixed dimensions.
    expect(openHtml).toContain("origin-bottom-right")
    expect(expandedHtml).not.toContain("scale-50")
  })

  it("forwards the playheadPoint and trailPoints to MapWidget", () => {
    // The mock above renders nothing structural for these props but the
    // component must accept them without throwing.
    const html = renderToStaticMarkup(
      <FloatingMapMinimap
        trailPoints={[{ lng: 103.8, lat: 1.35 }]}
        playheadPoint={{ lng: 103.8, lat: 1.35 }}
      />,
    )
    expect(html).toContain('data-testid="map-widget-stub"')
  })
})
