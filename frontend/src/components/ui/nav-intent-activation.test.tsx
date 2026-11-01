// @vitest-environment jsdom
/**
 * Caller wiring for the unified navigation-intent hook: activation warms the
 * destination before the caller's own callback runs (openspec/changes/
 * unify-dashboard-navigation-intent, "Predecessor hooks are retired
 * atomically"). The real hook runs; only the chunk registry is recorded.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"

const events: string[] = []

vi.mock("@/lib/route-chunk-registry", () => ({
  resolveRouteChunkLoader: () => () => {
    events.push("warm")
    return Promise.resolve({})
  },
}))
vi.mock("@/lib/prefetch-registry", () => ({ resolvePrefetchTarget: () => null }))

import { DisclosureRow } from "./DisclosureRow"
import { RowLink } from "./RowLink"

beforeEach(() => {
  events.length = 0
})

afterEach(cleanup)

function renderDisclosure() {
  render(
    <DisclosureRow
      expanded={false}
      onToggle={() => events.push("toggle")}
      prefetchTo="/sessions/abc"
      data-testid="row"
    >
      row
    </DisclosureRow>,
  )
  return screen.getByTestId("row")
}

describe("DisclosureRow activation warmup", () => {
  it("focus then Space warms once, before onToggle", () => {
    const row = renderDisclosure()
    fireEvent.focus(row)
    fireEvent.keyDown(row, { key: " " })
    expect(events).toEqual(["warm", "toggle"])
  })

  it("Enter without prior focus warms before onToggle", () => {
    const row = renderDisclosure()
    fireEvent.keyDown(row, { key: "Enter" })
    expect(events).toEqual(["warm", "toggle"])
  })

  it("click warms before onToggle", () => {
    const row = renderDisclosure()
    fireEvent.click(row)
    expect(events).toEqual(["warm", "toggle"])
  })
})

describe("RowLink activation warmup", () => {
  function renderNested() {
    render(
      <MemoryRouter>
        <RowLink
          to="/sessions/abc"
          hasNestedInteractive
          onClick={() => events.push("click")}
          onActivate={() => events.push("activate")}
          data-testid="row"
        >
          row
        </RowLink>
      </MemoryRouter>,
    )
    return screen.getByTestId("row")
  }

  it("nested fallback click warms before onClick and onActivate", () => {
    fireEvent.click(renderNested())
    expect(events).toEqual(["warm", "click", "activate"])
  })

  it.each(["Enter", " "])("nested fallback %j key warms before onActivate", (key) => {
    const row = renderNested()
    fireEvent.focus(row)
    fireEvent.keyDown(row, { key })
    expect(events).toEqual(["warm", "activate"])
  })

  it.each(["Enter", " "])("nested fallback %j key without prior focus warms before onActivate", (key) => {
    // No focus first: the key press alone must start the warmup, not a focus-started cycle.
    fireEvent.keyDown(renderNested(), { key })
    expect(events).toEqual(["warm", "activate"])
  })

  it("anchor click warms before the caller's onClick", () => {
    render(
      <MemoryRouter>
        <RowLink to="/sessions/abc" onClick={() => events.push("click")} data-testid="row">
          row
        </RowLink>
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByTestId("row"))
    expect(events).toEqual(["warm", "click"])
  })
})
