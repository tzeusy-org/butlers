// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Provenance primitives tests — bu-ovq7t
//
// The binding invariant under test: confidence and staleness are TWO distinct
// axes that NEVER blend into one score. The headline case
// ("conf=1.0 + stale simultaneously") proves a full confidence bar and a stale
// dim treatment coexist on the same fact.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import {
  CONF_AMBER_THRESHOLD,
  ConfBar,
  ProvenanceMarks,
  StalenessBand,
  stalenessBandForTimestamp,
} from "./Provenance"

// ---------------------------------------------------------------------------
// Axis 1 — ConfBar
// ---------------------------------------------------------------------------

describe("ConfBar: confidence axis", () => {
  it("renders a meter with the confidence value", () => {
    const html = renderToStaticMarkup(<ConfBar conf={0.9} />)
    expect(html).toContain('role="meter"')
    expect(html).toContain('aria-valuenow="0.9"')
  })

  it("is neutral (--mfg) at or above the amber threshold", () => {
    const html = renderToStaticMarkup(<ConfBar conf={CONF_AMBER_THRESHOLD} />)
    expect(html).toContain("var(--mfg)")
    expect(html).not.toContain("var(--amber)")
    expect(html).not.toContain('data-low-confidence')
  })

  it("turns amber below the threshold", () => {
    const html = renderToStaticMarkup(<ConfBar conf={0.4} />)
    expect(html).toContain("var(--amber)")
    expect(html).toContain('data-low-confidence="true"')
  })

  it("is 4px tall (spec: conf bar 4px)", () => {
    const html = renderToStaticMarkup(<ConfBar conf={0.9} />)
    expect(html).toContain("height:4px")
  })

  it("fill width is proportional to conf", () => {
    const html = renderToStaticMarkup(<ConfBar conf={0.5} />)
    expect(html).toContain("width:50%")
  })

  it("clamps out-of-range values", () => {
    expect(renderToStaticMarkup(<ConfBar conf={1.5} />)).toContain("width:100%")
    expect(renderToStaticMarkup(<ConfBar conf={-0.2} />)).toContain("width:0%")
  })

  it("uses no hex literals (token discipline)", () => {
    const html = renderToStaticMarkup(<ConfBar conf={0.4} />)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}\b/)
  })
})

// ---------------------------------------------------------------------------
// Axis 2 — StalenessBand
// ---------------------------------------------------------------------------

describe("StalenessBand: staleness axis", () => {
  it("dims when stale", () => {
    const html = renderToStaticMarkup(<StalenessBand band="stale" />)
    expect(html).toContain("opacity-40")
    expect(html).toContain('data-stale="true"')
    expect(html).toContain("Stale")
  })

  it("is not dimmed when fresh", () => {
    const html = renderToStaticMarkup(<StalenessBand band="fresh" />)
    expect(html).not.toContain("opacity-40")
    expect(html).toContain("Fresh")
  })

  it("renders aging muted", () => {
    const html = renderToStaticMarkup(<StalenessBand band="aging" />)
    expect(html).toContain("Aging")
    expect(html).not.toContain("opacity-40")
  })
})

// ---------------------------------------------------------------------------
// The binding invariant: two axes, never blended
// ---------------------------------------------------------------------------

describe("Provenance: confidence and staleness are separate axes", () => {
  it("conf=1.0 + stale renders a FULL confidence bar AND a stale dim simultaneously", () => {
    // Spec scenario: a fact with conf = 1.0 observed 300 days ago renders the
    // confidence bar full and the staleness indicator stale — no blended score.
    const conf = renderToStaticMarkup(<ConfBar conf={1.0} />)
    const stale = renderToStaticMarkup(<StalenessBand band="stale" />)

    // Full bar, neutral (high confidence is NOT amber).
    expect(conf).toContain("width:100%")
    expect(conf).toContain("var(--mfg)")
    expect(conf).not.toContain("var(--amber)")

    // Stale band is dimmed at the same time.
    expect(stale).toContain("opacity-40")
    expect(stale).toContain("Stale")

    // The two primitives are independent: neither emits a combined numeric score.
    expect(conf).not.toContain("Stale")
    expect(stale).not.toContain("role=\"meter\"")
  })
})

// ---------------------------------------------------------------------------
// Source + verification marks
// ---------------------------------------------------------------------------

describe("ProvenanceMarks: src + verified", () => {
  it("renders the src tag", () => {
    const html = renderToStaticMarkup(<ProvenanceMarks src="relationship" />)
    expect(html).toContain("relationship")
    expect(html).toContain("Source: relationship")
  })

  it("renders a green check when verified", () => {
    const html = renderToStaticMarkup(<ProvenanceMarks src="relationship" verified />)
    expect(html).toContain("var(--green)")
    expect(html).toContain('data-verified="true"')
  })

  it("renders dim when unverified", () => {
    const html = renderToStaticMarkup(<ProvenanceMarks src="relationship" verified={false} />)
    expect(html).toContain("var(--dim)")
    expect(html).toContain('data-verified="false"')
  })

  it("omits the src tag when src is empty", () => {
    const html = renderToStaticMarkup(<ProvenanceMarks src="" verified />)
    expect(html).not.toContain("Source:")
  })

  it("uses no hex literals (token discipline)", () => {
    const html = renderToStaticMarkup(<ProvenanceMarks src="relationship" verified />)
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}\b/)
  })
})

describe("stalenessBandForTimestamp: server-aligned thresholds", () => {
  const now = new Date("2026-06-13T00:00:00Z")
  const daysAgo = (n: number) =>
    new Date(now.getTime() - n * 86_400_000).toISOString()

  it("is fresh at the 30-day boundary (inclusive)", () => {
    expect(stalenessBandForTimestamp(daysAgo(0), now)).toBe("fresh")
    expect(stalenessBandForTimestamp(daysAgo(30), now)).toBe("fresh")
  })

  it("is aging between 30 and 180 days", () => {
    expect(stalenessBandForTimestamp(daysAgo(31), now)).toBe("aging")
    expect(stalenessBandForTimestamp(daysAgo(180), now)).toBe("aging")
  })

  it("is stale above 180 days", () => {
    expect(stalenessBandForTimestamp(daysAgo(181), now)).toBe("stale")
    expect(stalenessBandForTimestamp(daysAgo(1000), now)).toBe("stale")
  })

  it("treats null / unparseable timestamps as stale", () => {
    expect(stalenessBandForTimestamp(null, now)).toBe("stale")
    expect(stalenessBandForTimestamp("not-a-date", now)).toBe("stale")
  })
})
