// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// EmptyState tests — bu-qvnce.7, reclassified bu-eyo56
//
// Two tiers per `about/heart-and-soul/design-language.md` § Empty states:
// page-level empty states (default) show a heading plus one short visible
// sentence of context; Voice-surface-inline empty states (`variant="voice"`)
// are the strict "one serif-italic sentence, no trailing explanation" case.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { EmptyState } from "./empty-state"

describe("EmptyState: default (page) variant shows visible context", () => {
  it("renders the title as a heading, not italic serif", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="No contacts found." description="Contacts appear as they are added." />,
    )
    expect(html).toContain("No contacts found.")
    expect(html).toContain("<h2")
    expect(html).not.toContain("italic")
  })

  it("renders description visibly (not sr-only) exactly once", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="No items." description="Items appear once the butler ingests them." />,
    )
    expect(html).toContain("Items appear once the butler ingests them.")
    expect(html).not.toContain("sr-only")
  })

  it("omits the description paragraph entirely when description is an empty string", () => {
    const html = renderToStaticMarkup(<EmptyState title="No items." description="" />)
    expect(html).not.toContain("<p")
  })

  it("is the default when variant is omitted", () => {
    const withDefault = renderToStaticMarkup(
      <EmptyState title="No items." description="Detail." />,
    )
    const withExplicitPage = renderToStaticMarkup(
      <EmptyState title="No items." description="Detail." variant="page" />,
    )
    expect(withDefault).toBe(withExplicitPage)
  })
})

describe("EmptyState: voice variant is strict (bu-qvnce.7 behavior)", () => {
  it("renders the title as one serif italic sentence", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="Nothing waiting." description="Detail." variant="voice" />,
    )
    expect(html).toContain("Nothing waiting.")
    expect(html).toContain("font-serif")
    expect(html).toContain("italic")
  })

  it("does not render a separate heading element for the title", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="Nothing waiting." description="Detail." variant="voice" />,
    )
    expect(html).not.toContain("<h1")
    expect(html).not.toContain("<h2")
  })

  it("renders description in an sr-only element, not visibly", () => {
    const html = renderToStaticMarkup(
      <EmptyState
        title="Nothing waiting."
        description="Items appear once the butler ingests them."
        variant="voice"
      />,
    )
    expect(html).toContain("sr-only")
    expect(html).toContain("Items appear once the butler ingests them.")
  })

  it("omits the sr-only span entirely when description is an empty string", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="Nothing waiting." description="" variant="voice" />,
    )
    expect(html).not.toContain("sr-only")
  })
})

describe("EmptyState: no illustration in either variant", () => {
  it("never renders the icon prop in the page variant", () => {
    const html = renderToStaticMarkup(
      <EmptyState
        title="No items."
        description="Detail."
        icon={<span data-testid="forbidden-icon">*</span>}
      />,
    )
    expect(html).not.toContain("forbidden-icon")
  })

  it("never renders the icon prop in the voice variant", () => {
    const html = renderToStaticMarkup(
      <EmptyState
        title="No items."
        description="Detail."
        icon={<span data-testid="forbidden-icon">*</span>}
        variant="voice"
      />,
    )
    expect(html).not.toContain("forbidden-icon")
  })
})

describe("EmptyState: action", () => {
  it("renders the action affordance when supplied (page variant)", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="No items." description="Detail." action={<button>Add item</button>} />,
    )
    expect(html).toContain("Add item")
  })

  it("renders nothing extra when action is omitted", () => {
    const html = renderToStaticMarkup(<EmptyState title="No items." description="Detail." />)
    expect(html).not.toContain("<button")
  })
})
