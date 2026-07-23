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
})
