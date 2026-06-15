// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Provenance primitives tests — bu-ovq7t
//
// Note: the ConfBar component was removed (bu-8j0ir) because conf is
// hardcoded 1.0 at every write site — no calibration path exists — making the
// bar always 100% and the amber branch unreachable. Tests removed accordingly.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import {
  ProvenanceMarks,
  StalenessBand,
  stalenessBandForTimestamp,
} from "./Provenance"

// ---------------------------------------------------------------------------
// Axis 1 — StalenessBand
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
