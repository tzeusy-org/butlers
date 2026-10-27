import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import {
  Tile,
  TileAction,
  TileContent,
  TileDescription,
  TileFooter,
  TileHeader,
  TileTitle,
} from "./Tile"

describe("Tile", () => {
  it("renders a semantic tile boundary", () => {
    const html = renderToStaticMarkup(
      <Tile>
        <p>Health</p>
      </Tile>,
    )

    expect(html).toContain("<section")
    expect(html).toContain('data-slot="tile"')
    expect(html).not.toContain('data-slot="card"')
  })

  it("marks an independently loading module as busy", () => {
    const html = renderToStaticMarkup(<Tile loading>Loading</Tile>)

    expect(html).toContain('aria-busy="true"')
    expect(html).not.toContain('data-degraded="true"')
  })

  it("marks an independently degraded module without changing sibling content", () => {
    const html = renderToStaticMarkup(
      <Tile degraded>
        <p data-testid="tile-content">Unavailable</p>
      </Tile>,
    )

    expect(html).toContain('data-degraded="true"')
    expect(html).toContain('data-testid="tile-content"')
  })

  it("uses an eyebrow for the tile title", () => {
    const html = renderToStaticMarkup(
      <Tile>
        <TileHeader>
          <TileTitle>Uptime</TileTitle>
          <TileDescription>Process health</TileDescription>
        </TileHeader>
      </Tile>,
    )

    expect(html).toContain('<h2 data-slot="tile-title"')
    expect(html).toContain('data-slot="tile-title"')
    expect(html).toContain('data-slot="tile-description"')
  })

  it("keeps tile anatomy flat and slot-addressable", () => {
    const html = renderToStaticMarkup(
      <Tile>
        <TileHeader>
          <TileAction>Refresh</TileAction>
        </TileHeader>
        <TileContent>Facts</TileContent>
        <TileFooter>Updated</TileFooter>
      </Tile>,
    )

    expect(html).toContain('data-slot="tile-header"')
    expect(html).toContain('data-slot="tile-action"')
    expect(html).toContain('data-slot="tile-content"')
    expect(html).toContain('data-slot="tile-footer"')
    expect(html).not.toContain("rounded-xl")
  })
})
