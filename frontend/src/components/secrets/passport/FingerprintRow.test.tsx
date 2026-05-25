// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// FingerprintRow tests — bu-qo3sf
//
// Coverage:
//   - Renders fingerprint by default
//   - showVerifyCmd=false (default) hides the verify command
//   - showVerifyCmd=true renders the exact hard-coded command literal
//   - Verify command contains '<value>' placeholder (never the real secret)
//   - Verify command contains sha256sum and cut -c1-8
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { FingerprintRow } from "./FingerprintRow"

const FP = "sha256:7a3f8e9b"

describe("FingerprintRow: default rendering", () => {
  it("renders the fingerprint", () => {
    const html = renderToStaticMarkup(<FingerprintRow fingerprint={FP} />)
    expect(html).toContain("sha256")
    expect(html).toContain("7a3f8e9b")
  })

  it("does not render the verify command by default", () => {
    const html = renderToStaticMarkup(<FingerprintRow fingerprint={FP} />)
    expect(html).not.toContain("sha256sum")
  })
})

describe("FingerprintRow: verify command", () => {
  it("renders the verify command when showVerifyCmd=true", () => {
    const html = renderToStaticMarkup(
      <FingerprintRow fingerprint={FP} showVerifyCmd />,
    )
    expect(html).toContain("sha256sum")
  })

  it("verify command contains the <value> placeholder (not real secret)", () => {
    const html = renderToStaticMarkup(
      <FingerprintRow fingerprint={FP} showVerifyCmd />,
    )
    expect(html).toContain("&lt;value&gt;")
  })

  it("verify command contains cut -c1-8", () => {
    const html = renderToStaticMarkup(
      <FingerprintRow fingerprint={FP} showVerifyCmd />,
    )
    expect(html).toContain("cut -c1-8")
  })

  it("verify command is rendered as <code> element", () => {
    const html = renderToStaticMarkup(
      <FingerprintRow fingerprint={FP} showVerifyCmd />,
    )
    expect(html).toContain("<code")
  })
})

describe("FingerprintRow: className forwarding", () => {
  it("merges additional className", () => {
    const html = renderToStaticMarkup(
      <FingerprintRow fingerprint={FP} className="fp-row-cls" />,
    )
    expect(html).toContain("fp-row-cls")
  })
})
