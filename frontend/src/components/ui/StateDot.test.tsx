// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StateDot tests — bu-ec2wb, extended bu-rixan
//
// Coverage:
//   - Each entity state renders the correct color token
//   - Each Dispatch §4e system state renders the correct color token
//   - Default size is 6px
//   - Custom size prop is respected
//   - ARIA: role="img" + aria-label per state
//   - className and style forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { StateDot } from "./StateDot"

// ---------------------------------------------------------------------------
// Color tokens per state
// ---------------------------------------------------------------------------

describe("StateDot: state-to-color mapping", () => {
  const STATE_CASES = [
    { state: "unidentified" as const, token: "var(--state-unidentified)" },
    { state: "duplicate-candidate" as const, token: "var(--amber)" },
    { state: "stale" as const, token: "var(--red)" },
    { state: "healthy" as const, token: "var(--green)" },
    { state: "archived" as const, token: "var(--muted-foreground)" },
  ] as const

  for (const { state, token } of STATE_CASES) {
    it(`state="${state}" uses color token ${token}`, () => {
      const html = renderToStaticMarkup(<StateDot state={state} />)
      expect(html).toContain(token)
    })
  }
})

// ---------------------------------------------------------------------------
// Size
// ---------------------------------------------------------------------------

describe("StateDot: size prop", () => {
  it("defaults to 6px (per Brief §2 spec)", () => {
    const html = renderToStaticMarkup(<StateDot state="healthy" />)
    expect(html).toContain("width:6px")
    expect(html).toContain("height:6px")
  })

  it("renders at a custom size", () => {
    const html = renderToStaticMarkup(<StateDot state="healthy" size={10} />)
    expect(html).toContain("width:10px")
    expect(html).toContain("height:10px")
  })
})

// ---------------------------------------------------------------------------
// ARIA
// ---------------------------------------------------------------------------

describe("StateDot: accessibility", () => {
  it("has role=img", () => {
    const html = renderToStaticMarkup(<StateDot state="unidentified" />)
    expect(html).toContain('role="img"')
  })

  it('state="unidentified" has aria-label="Unidentified"', () => {
    const html = renderToStaticMarkup(<StateDot state="unidentified" />)
    expect(html).toContain('aria-label="Unidentified"')
  })

  it('state="duplicate-candidate" has aria-label="Duplicate candidate"', () => {
    const html = renderToStaticMarkup(<StateDot state="duplicate-candidate" />)
    expect(html).toContain('aria-label="Duplicate candidate"')
  })

  it('state="stale" has aria-label="Stale"', () => {
    const html = renderToStaticMarkup(<StateDot state="stale" />)
    expect(html).toContain('aria-label="Stale"')
  })

  it('state="healthy" has aria-label="Healthy"', () => {
    const html = renderToStaticMarkup(<StateDot state="healthy" />)
    expect(html).toContain('aria-label="Healthy"')
  })

  it('state="archived" has aria-label="Archived"', () => {
    const html = renderToStaticMarkup(<StateDot state="archived" />)
    expect(html).toContain('aria-label="Archived"')
  })
})

// ---------------------------------------------------------------------------
// className and style forwarding
// ---------------------------------------------------------------------------

describe("StateDot: prop forwarding", () => {
  it("forwards className to the root span", () => {
    const html = renderToStaticMarkup(
      <StateDot state="healthy" className="my-dot-class" />,
    )
    expect(html).toContain("my-dot-class")
  })

  it("merges additional style props", () => {
    const html = renderToStaticMarkup(
      <StateDot state="healthy" style={{ opacity: 0.5 }} />,
    )
    expect(html).toContain("opacity:0.5")
  })
})

// ---------------------------------------------------------------------------
// Shape
// ---------------------------------------------------------------------------

describe("StateDot: circular shape", () => {
  it("is a rounded element (rounded-full / 50% border-radius class)", () => {
    const html = renderToStaticMarkup(<StateDot state="healthy" />)
    expect(html).toContain("rounded-full")
  })
})

// ---------------------------------------------------------------------------
// Dispatch §4e system states (bu-rixan extension)
// ---------------------------------------------------------------------------

describe("StateDot: Dispatch system states", () => {
  const DISPATCH_CASES = [
    { state: "ok" as const, token: "var(--green)" },
    { state: "degraded" as const, token: "var(--amber)" },
    { state: "error" as const, token: "var(--red)" },
    { state: "waiting" as const, token: "var(--dim" },
  ] as const

  for (const { state, token } of DISPATCH_CASES) {
    it(`state="${state}" uses color token containing ${token}`, () => {
      const html = renderToStaticMarkup(<StateDot state={state} />)
      expect(html).toContain(token)
    })
  }

  it('state="ok" has aria-label="OK"', () => {
    const html = renderToStaticMarkup(<StateDot state="ok" />)
    expect(html).toContain('aria-label="OK"')
  })

  it('state="degraded" has aria-label="Degraded"', () => {
    const html = renderToStaticMarkup(<StateDot state="degraded" />)
    expect(html).toContain('aria-label="Degraded"')
  })

  it('state="error" has aria-label="Error"', () => {
    const html = renderToStaticMarkup(<StateDot state="error" />)
    expect(html).toContain('aria-label="Error"')
  })

  it('state="waiting" has aria-label="Waiting"', () => {
    const html = renderToStaticMarkup(<StateDot state="waiting" />)
    expect(html).toContain('aria-label="Waiting"')
  })
})
