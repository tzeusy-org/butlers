// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// FingerprintRow tests — bu-qo3sf, repointed bu-sd0l7.2
//
// bu-sd0l7.2: repointed from the orphan ./FingerprintRow.tsx (never imported
// by any page — pages.tsx renders the atoms.tsx FingerprintRow directly)
// onto the shipping export. Prop is `value` (nullable) not `fingerprint`
// (required). Real behaviour divergence: the dead copy showed the verify
// command unconditionally whenever `showVerifyCmd` was true; the shipping
// copy renders a "+ verify cmd" TOGGLE button and only reveals the command
// after a click (internal `open` state, default closed) — which is what the
// spec text quoted in the old file's own docstring actually described
// ("WHEN the '+ verify cmd' expander is toggled open…"). Exercising that
// now requires simulating the click via @testing-library/react rather than
// static markup.
//
// Coverage:
//   - Renders fingerprint by default
//   - showVerifyCmd=false (default) renders no toggle button at all
//   - showVerifyCmd=true renders a closed "+ verify cmd" toggle by default
//   - Clicking the toggle reveals the exact hard-coded command literal
//   - Verify command contains '<value>' placeholder (never the real secret)
//   - Verify command contains sha256sum and cut -c1-8
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it, afterEach } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { renderToStaticMarkup } from "react-dom/server"

import { FingerprintRow } from "./atoms.tsx"

const FP = "sha256:7a3f8e9b"

afterEach(() => {
  cleanup()
})

describe("FingerprintRow: default rendering", () => {
  it("renders the fingerprint", () => {
    const html = renderToStaticMarkup(<FingerprintRow value={FP} />)
    expect(html).toContain("sha256")
    expect(html).toContain("7a3f8e9b")
  })

  it("does not render the verify-cmd toggle when showVerifyCmd is omitted", () => {
    const html = renderToStaticMarkup(<FingerprintRow value={FP} />)
    expect(html).not.toContain("verify cmd")
  })
})

describe("FingerprintRow: verify command toggle", () => {
  it("renders a closed '+ verify cmd' toggle when showVerifyCmd=true", () => {
    const html = renderToStaticMarkup(<FingerprintRow value={FP} showVerifyCmd />)
    expect(html).toContain("+ verify cmd")
    expect(html).not.toContain("sha256sum")
  })

  it("reveals the verify command after clicking the toggle", () => {
    render(<FingerprintRow value={FP} showVerifyCmd />)
    fireEvent.click(screen.getByText("+ verify cmd"))
    expect(screen.getByText(/sha256sum/)).toBeTruthy()
  })

  it("verify command contains the <value> placeholder (not real secret)", () => {
    render(<FingerprintRow value={FP} showVerifyCmd />)
    fireEvent.click(screen.getByText("+ verify cmd"))
    expect(screen.getByText(/<value>/)).toBeTruthy()
  })

  it("verify command contains cut -c1-8", () => {
    render(<FingerprintRow value={FP} showVerifyCmd />)
    fireEvent.click(screen.getByText("+ verify cmd"))
    expect(screen.getByText(/cut -c1-8/)).toBeTruthy()
  })

  it("toggling again hides the verify command", () => {
    render(<FingerprintRow value={FP} showVerifyCmd />)
    const toggle = screen.getByText("+ verify cmd")
    fireEvent.click(toggle)
    fireEvent.click(screen.getByText("− hide verify cmd"))
    expect(screen.queryByText(/sha256sum/)).toBeNull()
  })
})

describe("FingerprintRow: no value", () => {
  it("omits the verify-cmd toggle entirely when value is null, even if showVerifyCmd is true", () => {
    const html = renderToStaticMarkup(<FingerprintRow value={null} showVerifyCmd />)
    expect(html).not.toContain("verify cmd")
  })
})

describe("FingerprintRow: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <FingerprintRow value={FP} className="fp-row-cls" />,
    )
    expect(html).toContain("fp-row-cls")
  })
})
