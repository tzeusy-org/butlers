// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StateLabel tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./StateLabel.tsx onto the shipping
// atoms.tsx export, which Spine.tsx actually renders. This pair had the
// starkest divergence found in this bead's audit: the dead copy asserted
// `state="ok"` renders the word "ok" in --dim (neutral). The shipping copy
// — driven by the real STATE_CATALOG (constants.ts), the single source of
// truth also used for spine sorting/severity — renders `state="ok"` as
// "healthy" in --green (toneColor("ok")). A green "ok" tests suite was
// pinning a lowercase-grey "ok" that the actual page has never rendered.
//
// The dead copy's local state set (`expiring_soon`) also doesn't match the
// real CredentialState union (`expiring`) or its two extra states (`warn`,
// `rotating`) — see types.ts / constants.ts STATE_CATALOG.
//
// Coverage:
//   - Each real credential state renders its STATE_CATALOG label
//   - Each state renders the colour toneColor() derives from its tone
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { StateLabel } from "./atoms.tsx"

// Mirrors constants.ts STATE_CATALOG.
const STATE_CASES = [
  { state: "expired",        label: "expired",        color: "var(--red"    },
  { state: "revoked",        label: "revoked",        color: "var(--red"    },
  { state: "scope_mismatch", label: "scope mismatch", color: "var(--amber"  },
  { state: "expiring",       label: "expiring",       color: "var(--amber"  },
  { state: "warn",           label: "unverified",     color: "var(--mfg"    },
  { state: "rotating",       label: "rotating…",      color: "var(--amber"  },
  { state: "ok",             label: "healthy",        color: "var(--green"  },
  { state: "failed",         label: "failed",         color: "var(--red"    },
  { state: "never_set",      label: "not set",        color: "var(--mfg"    },
] as const

describe("StateLabel: label text (from STATE_CATALOG)", () => {
  for (const { state, label } of STATE_CASES) {
    it(`state="${state}" renders "${label}"`, () => {
      const html = renderToStaticMarkup(<StateLabel state={state} />)
      expect(html).toContain(label)
    })
  }
})

describe("StateLabel: colour tokens", () => {
  for (const { state, color } of STATE_CASES) {
    it(`state="${state}" uses colour token starting with "${color}"`, () => {
      const html = renderToStaticMarkup(<StateLabel state={state} />)
      expect(html).toContain(color)
    })
  }
})

describe("StateLabel: uppercase rendering (CSS, not string transform)", () => {
  it("scope_mismatch label text itself has no underscore, styled uppercase", () => {
    const html = renderToStaticMarkup(<StateLabel state="scope_mismatch" />)
    expect(html).toContain("scope mismatch")
    expect(html).not.toContain("scope_mismatch")
    expect(html).toContain("uppercase")
  })
})

describe("StateLabel: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(<StateLabel state="ok" className="test-cls" />)
    expect(html).toContain("test-cls")
  })
})
