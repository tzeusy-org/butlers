// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Tip tests — bu-sywxz
//
// Tip is the focusable, SR-announced replacement for a load-bearing `title=`
// attribute. Coverage:
//   - nullish/empty content → child renders untouched, no wrapper, no title
//   - present content → the child element itself is the tooltip trigger
//     (asChild), carries no `title` attribute, and radix stamps data-state
//   - the tooltip opens on keyboard FOCUS (not hover alone), announcing the
//     content that used to live only in the native title
//   - no nested-interactive violation is introduced (axe clean) — the
//     interactive child IS the trigger
// ---------------------------------------------------------------------------

import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { axe, toHaveNoViolations } from "jest-axe"

import { Tip } from "./tip"

expect.extend(toHaveNoViolations)

afterEach(() => {
  cleanup()
})

describe("Tip: absent content", () => {
  it("renders the child untouched when content is undefined", () => {
    render(
      <Tip content={undefined}>
        <button type="button">Act</button>
      </Tip>,
    )
    const btn = screen.getByRole("button", { name: "Act" })
    expect(btn.getAttribute("data-state")).toBeNull()
    expect(btn.getAttribute("title")).toBeNull()
  })

  it("renders the child untouched when content is an empty string", () => {
    render(
      <Tip content="">
        <button type="button">Act</button>
      </Tip>,
    )
    expect(
      screen.getByRole("button", { name: "Act" }).getAttribute("data-state"),
    ).toBeNull()
  })
})

describe("Tip: present content", () => {
  it("makes the child the trigger (asChild) with no title attribute", () => {
    render(
      <Tip content="Full value">
        <button type="button">Trigger</button>
      </Tip>,
    )
    const btn = screen.getByRole("button", { name: "Trigger" })
    // radix stamps the trigger; the native title tooltip is gone.
    expect(btn.getAttribute("data-state")).toBe("closed")
    expect(btn.getAttribute("title")).toBeNull()
  })

  it("opens on keyboard focus, announcing the previously-title-only content", async () => {
    const user = userEvent.setup()
    render(
      <Tip content="Request 0xabc123">
        <button type="button">Details</button>
      </Tip>,
    )
    // Nothing shown before focus.
    expect(screen.queryByText("Request 0xabc123")).toBeNull()

    await user.tab()
    expect(screen.getByRole("button", { name: "Details" })).toBe(
      document.activeElement,
    )

    // Focus (not hover) surfaces the tooltip content.
    await waitFor(() => {
      expect(screen.getAllByText("Request 0xabc123").length).toBeGreaterThan(0)
    })
  })

  it("introduces no nested-interactive violation (axe clean)", async () => {
    const { container } = render(
      <Tip content="Delete this view">
        <button type="button" aria-label="Delete">
          ×
        </button>
      </Tip>,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
