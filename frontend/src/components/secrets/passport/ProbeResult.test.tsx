// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ProbeResult tests — bu-qo3sf
//
// Coverage:
//   - outcome="ok" renders "ok" in --green
//   - outcome="fail" renders "fail" in --red
//   - HTTP code is rendered when provided, absent when omitted
//   - Latency is rendered in ms
//   - Timestamp is rendered
//   - Optional message renders in serif italic (Voice component)
//   - No message element when message is omitted
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ProbeResult } from "./ProbeResult"

describe("ProbeResult: outcome rendering", () => {
  it('outcome="ok" renders "ok"', () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="ok" latencyMs={142} timestamp="14:21 today" />,
    )
    expect(html).toContain("ok")
  })

  it('outcome="ok" uses --green colour', () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="ok" latencyMs={142} timestamp="14:21 today" />,
    )
    expect(html).toContain("var(--green)")
  })

  it('outcome="fail" renders "fail"', () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="fail" latencyMs={89} timestamp="09:03 today" />,
    )
    expect(html).toContain("fail")
  })

  it('outcome="fail" uses --red colour', () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="fail" latencyMs={89} timestamp="09:03 today" />,
    )
    expect(html).toContain("var(--red)")
  })
})

describe("ProbeResult: HTTP code", () => {
  it("renders the HTTP code when provided", () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="fail" httpCode={401} latencyMs={89} timestamp="09:03 today" />,
    )
    expect(html).toContain("401")
  })

  it("omits HTTP code element when not provided", () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="ok" latencyMs={100} timestamp="14:21 today" />,
    )
    expect(html).not.toContain("401")
    expect(html).not.toContain("200")
  })
})

describe("ProbeResult: latency and timestamp", () => {
  it("renders latency with 'ms' suffix", () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="ok" latencyMs={142} timestamp="14:21 today" />,
    )
    expect(html).toContain("142ms")
  })

  it("renders the timestamp", () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="ok" latencyMs={50} timestamp="3 May 09:03" />,
    )
    expect(html).toContain("3 May 09:03")
  })
})

describe("ProbeResult: message", () => {
  it("renders the message when provided", () => {
    const html = renderToStaticMarkup(
      <ProbeResult
        outcome="fail"
        latencyMs={89}
        timestamp="09:03 today"
        message="Token expired: 401 Unauthorized"
      />,
    )
    expect(html).toContain("Token expired: 401 Unauthorized")
  })

  it("renders message using serif font (Voice component)", () => {
    const html = renderToStaticMarkup(
      <ProbeResult
        outcome="fail"
        latencyMs={89}
        timestamp="09:03 today"
        message="Token expired"
      />,
    )
    expect(html).toContain("font-serif")
  })

  it("renders message as italic", () => {
    const html = renderToStaticMarkup(
      <ProbeResult
        outcome="fail"
        latencyMs={89}
        timestamp="09:03 today"
        message="Token expired"
      />,
    )
    expect(html).toContain("italic")
  })

  it("omits message element when message is not provided", () => {
    const html = renderToStaticMarkup(
      <ProbeResult outcome="ok" latencyMs={100} timestamp="14:21 today" />,
    )
    expect(html).not.toContain("font-serif")
  })
})
