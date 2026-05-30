// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StampRow tests — bu-qo3sf
//
// Coverage:
//   - Renders the action glyph
//   - Renders the datetime
//   - Renders the action label text
//   - Renders the actor
//   - Optional note renders in serif italic when provided
//   - Note is absent when not provided
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { StampRow } from "./StampRow"

describe("StampRow: required fields", () => {
  it("renders the glyph for the action", () => {
    const html = renderToStaticMarkup(
      <StampRow action="verified" datetime="14:21 today" actor="owner" />,
    )
    // verified glyph is ✓
    expect(html).toContain("✓")
  })

  it("renders the datetime", () => {
    const html = renderToStaticMarkup(
      <StampRow action="verified" datetime="14:21 today" actor="owner" />,
    )
    expect(html).toContain("14:21 today")
  })

  it("renders the action label", () => {
    const html = renderToStaticMarkup(
      <StampRow action="rotated" datetime="09:00 today" actor="owner" />,
    )
    expect(html).toContain("rotated")
  })

  it("renders the actor", () => {
    const html = renderToStaticMarkup(
      <StampRow action="failed" datetime="08:55 today" actor="butler:health" />,
    )
    expect(html).toContain("butler:health")
  })
})

describe("StampRow: note", () => {
  it("renders the note when provided", () => {
    const html = renderToStaticMarkup(
      <StampRow
        action="failed"
        datetime="09:03 today"
        actor="butler:health"
        note="Token expired: 401 Unauthorized"
      />,
    )
    expect(html).toContain("Token expired: 401 Unauthorized")
  })

  it("renders note using serif font (Voice component)", () => {
    const html = renderToStaticMarkup(
      <StampRow
        action="failed"
        datetime="09:03 today"
        actor="butler:health"
        note="Token expired"
      />,
    )
    // Voice uses font-serif class
    expect(html).toContain("font-serif")
  })

  it("renders note as italic (empty-state variant)", () => {
    const html = renderToStaticMarkup(
      <StampRow
        action="failed"
        datetime="09:03 today"
        actor="butler:health"
        note="Token expired"
      />,
    )
    // Voice variant="italic" applies italic class
    expect(html).toContain("italic")
  })

  it("does not render note element when note is omitted", () => {
    const html = renderToStaticMarkup(
      <StampRow action="set" datetime="10:00 today" actor="owner" />,
    )
    expect(html).not.toContain("font-serif")
  })
})
