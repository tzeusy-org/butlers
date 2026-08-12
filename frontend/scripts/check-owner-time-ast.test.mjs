import { describe, expect, it } from "vitest"

import { findOwnerTimeViolations } from "./check-owner-time-ast.mjs"

describe("owner-time AST gate", () => {
  it("rejects locale formatting through a Date variable", () => {
    const violations = findOwnerTimeViolations(
      "const eventDate = new Date(value); eventDate.toLocaleString();",
      "components/example.tsx",
    )

    expect(violations).toHaveLength(1)
    expect(violations[0]).toContain("toLocaleString()")
  })

  it("does not reject numeric locale grouping", () => {
    const violations = findOwnerTimeViolations(
      "const count = 42; count.toLocaleString();",
      "components/example.tsx",
    )

    expect(violations).toEqual([])
  })
})
