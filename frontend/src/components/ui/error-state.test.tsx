// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ErrorState tests — bu-eyo56
//
// The error-tier sibling of EmptyState: a data-fetch failure must announce
// itself (role="alert") and read as an error (destructive color), never as
// calm emptiness.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { ErrorState } from "./error-state"

describe("ErrorState: announces failure", () => {
  it("renders role=alert", () => {
    const html = renderToStaticMarkup(<ErrorState title="Could not load issues." />)
    expect(html).toContain('role="alert"')
  })

  it("renders the title in destructive color", () => {
    const html = renderToStaticMarkup(<ErrorState title="Could not load issues." />)
    expect(html).toContain("Could not load issues.")
    expect(html).toContain("text-destructive")
  })
})

describe("ErrorState: description", () => {
  it("renders description visibly when supplied", () => {
    const html = renderToStaticMarkup(
      <ErrorState
        title="Could not load concentration data"
        description="Owner access is required, or no relational predicates are registered."
      />,
    )
    expect(html).toContain("Owner access is required, or no relational predicates are registered.")
  })

  it("omits the description paragraph when not supplied", () => {
    const html = renderToStaticMarkup(<ErrorState title="Could not load issues." />)
    expect(html).not.toContain("<p")
  })
})

describe("ErrorState: action", () => {
  it("renders the retry action when supplied", () => {
    const html = renderToStaticMarkup(
      <ErrorState title="Could not load issues." action={<button>Retry</button>} />,
    )
    expect(html).toContain("Retry")
  })

  it("renders nothing extra when action is omitted", () => {
    const html = renderToStaticMarkup(<ErrorState title="Could not load issues." />)
    expect(html).not.toContain("<button")
  })
})
