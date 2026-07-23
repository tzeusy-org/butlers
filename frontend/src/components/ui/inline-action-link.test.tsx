// @vitest-environment jsdom

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { InlineActionLink } from "./inline-action-link"

describe("InlineActionLink", () => {
  it("renders a semantic button with the shared action, link, and focus treatments", () => {
    const html = renderToStaticMarkup(<InlineActionLink>Review</InlineActionLink>)

    expect(html).toContain("<button")
    expect(html).toContain('type="button"')
    expect(html).toContain("Review")
    expect(html).toContain('data-slot="inline-action-link"')
    expect(html).toContain("font-mono")
    expect(html).toContain("uppercase")
    expect(html).toContain("underline-offset-4")
    expect(html).toContain("decoration-[var(--border-strong)]")
    expect(html).toContain("focus-visible:outline")
    expect(html).toContain("focus-visible:outline-2")
    expect(html).toContain("focus-visible:outline-offset-2")
    expect(html).toContain("focus-visible:outline-fg")
    expect(html).not.toContain("focus-visible:ring")
  })

  it("provides a 44px minimum target and native disabled semantics", () => {
    const html = renderToStaticMarkup(<InlineActionLink disabled>Retry</InlineActionLink>)

    expect(html).toContain("min-h-11")
    expect(html).toContain("min-w-11")
    expect(html).toContain("disabled")
  })

  it("preserves native link and summary semantics when requested", () => {
    const link = renderToStaticMarkup(
      <InlineActionLink as="a" href="/audit-log">
        Audit log
      </InlineActionLink>,
    )
    const summary = renderToStaticMarkup(
      <InlineActionLink as="summary">Configuration</InlineActionLink>,
    )

    expect(link).toContain("<a")
    expect(link).toContain('href="/audit-log"')
    expect(link).toContain("</a>")
    expect(summary).toContain("<summary")
  })

  it("requires a destination for anchor variants", () => {
    // @ts-expect-error Anchor actions without href are non-focusable pseudo-links.
    const missingHref = <InlineActionLink as="a">Audit log</InlineActionLink>

    expect(missingHref).toBeDefined()
  })
})
