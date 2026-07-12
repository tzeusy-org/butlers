// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StampRow tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./StampRow.tsx onto the shipping
// atoms.tsx export, which pages.tsx renders directly
// (`<StampRow event={e} .../>` in the Audit section, e from
// `credential.audit: AuditEvent[]`). Real divergences:
//   - The dead copy took flat props (action/datetime/actor/note); the
//     shipping copy takes a single `event: {ts, actor, action, note}`
//     object matching the AuditEvent shape (types.ts) — `ts` is a combined
//     "YYYY-MM-DD HH:MM" string (see mock-data.ts) that the atom itself
//     splits into a date line + time line at the first space.
//   - `note` is a required string field on `event`, not an optional prop —
//     the shipping copy always renders the note's serif span structurally,
//     even for an empty string (no conditional `{note && ...}` omission).
//   - The note renders in plain (non-italic) Source Serif 4 via an inline
//     `fontFamily` style, not Tailwind's `font-serif`/`italic` classes —
//     the dead copy's assertions on those two class names don't apply.
//
// Coverage:
//   - Renders the action glyph
//   - Renders the date and time (split from event.ts)
//   - Renders the action label
//   - Renders the actor
//   - Renders the note in Source Serif 4
//   - `last=true` omits the row's bottom border
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { StampRow } from "./atoms.tsx"

describe("StampRow: required fields", () => {
  it("renders the glyph for the action", () => {
    const html = renderToStaticMarkup(
      <StampRow event={{ ts: "2026-05-23 14:21", actor: "owner", action: "verified", note: "" }} />,
    )
    // verified glyph is ✓
    expect(html).toContain("✓")
  })

  it("renders the date and time split from event.ts", () => {
    const html = renderToStaticMarkup(
      <StampRow event={{ ts: "2026-05-23 14:21", actor: "owner", action: "verified", note: "" }} />,
    )
    expect(html).toContain("2026-05-23")
    expect(html).toContain("14:21")
  })

  it("renders the action label", () => {
    const html = renderToStaticMarkup(
      <StampRow event={{ ts: "2026-05-21 09:00", actor: "owner", action: "rotated", note: "" }} />,
    )
    expect(html).toContain("rotated")
  })

  it("renders the actor", () => {
    const html = renderToStaticMarkup(
      <StampRow event={{ ts: "2026-05-21 08:55", actor: "butler:health", action: "failed", note: "" }} />,
    )
    expect(html).toContain("butler:health")
  })
})

describe("StampRow: note", () => {
  it("renders the note text", () => {
    const html = renderToStaticMarkup(
      <StampRow
        event={{
          ts: "2026-05-21 09:03",
          actor: "butler:health",
          action: "failed",
          note: "Token expired: 401 Unauthorized",
        }}
      />,
    )
    expect(html).toContain("Token expired: 401 Unauthorized")
  })

  it("renders the note using Source Serif 4", () => {
    const html = renderToStaticMarkup(
      <StampRow
        event={{ ts: "2026-05-21 09:03", actor: "butler:health", action: "failed", note: "Token expired" }}
      />,
    )
    expect(html).toContain("Source Serif 4")
  })
})

describe("StampRow: last row", () => {
  it("last=true omits the bottom border class", () => {
    const html = renderToStaticMarkup(
      <StampRow event={{ ts: "2026-05-21 10:00", actor: "owner", action: "set", note: "" }} last />,
    )
    expect(html).not.toContain("border-b")
  })

  it("last=false (default) renders the bottom border class", () => {
    const html = renderToStaticMarkup(
      <StampRow event={{ ts: "2026-05-21 10:00", actor: "owner", action: "set", note: "" }} />,
    )
    expect(html).toContain("border-b")
  })
})
