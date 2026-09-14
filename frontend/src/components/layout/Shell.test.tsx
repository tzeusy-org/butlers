// @vitest-environment jsdom

import type { ReactNode } from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import Shell from "./Shell"

vi.mock("./Sidebar", () => ({ default: () => null }))
vi.mock("../ui/sheet", () => ({
  Sheet: ({ children }: { children: ReactNode }) => <>{children}</>,
  SheetContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SheetTitle: ({ children }: { children: ReactNode }) => <>{children}</>,
}))

afterEach(cleanup)

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

  // bu-8cdl1.13 (Viewport and Modality Contract, slice 3): the outer shell
  // and main-content region must track the dynamic viewport and stack the
  // page-gutter ramp with safe-area insets, not a fixed vh/padding value.
  it("sizes the shell to the dynamic viewport instead of a fixed 100vh", () => {
    const { container } = render(
      <Shell header={<span>Header</span>}>
        <p>Page content</p>
      </Shell>,
    )

    const shellRoot = container.firstElementChild as HTMLElement
    expect(shellRoot.className).toContain("h-dvh")
    expect(shellRoot.className).not.toContain("h-screen")
  })

  it("insets the outer shell from notch/home-indicator safe areas", () => {
    const { container } = render(
      <Shell header={<span>Header</span>}>
        <p>Page content</p>
      </Shell>,
    )

    const shellRoot = container.firstElementChild as HTMLElement
    expect(shellRoot.style.paddingTop).toBe("var(--safe-area-top)")
    expect(shellRoot.style.paddingLeft).toBe("var(--safe-area-left)")
    expect(shellRoot.style.paddingRight).toBe("var(--safe-area-right)")
  })

  it("gives main-content the page-gutter ramp with safe-area stacked into the bottom edge", () => {
    render(
      <Shell header={<span>Header</span>}>
        <p>Page content</p>
      </Shell>,
    )

    const main = screen.getByRole("main")
    expect(main.style.paddingTop).toBe("var(--page-gutter-y)")
    expect(main.style.paddingLeft).toBe("var(--page-gutter-x)")
    expect(main.style.paddingRight).toBe("var(--page-gutter-x)")
    expect(main.style.paddingBottom).toBe(
      "calc(var(--page-gutter-y) + var(--safe-area-bottom))",
    )
  })
})
