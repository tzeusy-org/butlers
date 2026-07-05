// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// EmptyState tests — bu-qvnce.7
//
// Dispatch Design Language § Voice Surface / § Interface Copy: empty states
// are one serif-italic sentence with no trailing explanation and no
// illustration.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { EmptyState } from "./empty-state"

describe("EmptyState: renders the title as the one visible serif-italic sentence", () => {
  it("renders title text in a serif italic element", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="No contacts found." description="Contacts appear as they are added." />,
    )
    expect(html).toContain("No contacts found.")
    expect(html).toContain("font-serif")
    expect(html).toContain("italic")
  })

  it("does not render a separate heading element for the title", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="Nothing waiting." description="Detail." />,
    )
    expect(html).not.toContain("<h1")
    expect(html).not.toContain("<h2")
  })
})

describe("EmptyState: no illustration", () => {
  it("never renders the icon prop, even when supplied", () => {
    const html = renderToStaticMarkup(
      <EmptyState
        title="No items."
        description="Detail."
        icon={<span data-testid="forbidden-icon">*</span>}
      />,
    )
    expect(html).not.toContain("forbidden-icon")
  })
})

describe("EmptyState: description is preserved for assistive tech, not shown visually", () => {
  it("renders description in an sr-only element", () => {
    const html = renderToStaticMarkup(
      <EmptyState title="No items." description="Items appear once the butler ingests them." />,
    )
    expect(html).toContain("sr-only")
    expect(html).toContain("Items appear once the butler ingests them.")
  })

  it("omits the sr-only span entirely when description is an empty string", () => {
    const html = renderToStaticMarkup(<EmptyState title="No items." description="" />)
    expect(html).not.toContain("sr-only")
  })
})

describe("EmptyState: action", () => {
  it("renders the action affordance when supplied", () => {
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
