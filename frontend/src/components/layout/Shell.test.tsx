// @vitest-environment jsdom

import type { ReactNode } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import Shell from "./Shell"

vi.mock("./Sidebar", () => ({ default: () => null }))
vi.mock("../ui/sheet", () => ({
  Sheet: ({ children }: { children: ReactNode }) => <>{children}</>,
  SheetContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SheetTitle: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

describe("Shell", () => {
  it("exposes a programmatically focusable main-content target for the skip link", () => {
    render(
      <Shell header={<span>Header</span>}>
        <p>Page content</p>
      </Shell>,
    )

    const main = screen.getByRole("main")
    expect(main.id).toBe("main-content")
    expect(main.getAttribute("tabindex")).toBe("-1")
  })

  it("omits the chat dock landmark when no chatDock is passed", () => {
    render(
      <Shell header={<span>Header</span>}>
        <p>Page content</p>
      </Shell>,
    )

    expect(screen.queryByRole("complementary", { name: "Chat" })).toBeNull()
  })

  it("renders a passed chatDock as a hairline-bordered sibling of main, never a shadowed overlay", () => {
    render(
      <Shell header={<span>Header</span>} chatDock={<div data-testid="dock-content">Dock</div>}>
        <p>Page content</p>
      </Shell>,
    )

    // `<aside>` is implicitly role="complementary" -- the desktop sidebar
    // (also an <aside>) matches the bare role too, so scope by accessible name.
    const dock = screen.getByRole("complementary", { name: "Chat" })
    expect(dock.tagName).toBe("ASIDE")
    expect(dock.className).toContain("border-l")
    expect(dock.className).toContain("border-border")
    expect(dock.className).not.toMatch(/shadow/)
    expect(screen.getByTestId("dock-content").textContent).toBe("Dock")
  })
})
