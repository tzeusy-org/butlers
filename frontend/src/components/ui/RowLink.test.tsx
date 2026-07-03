// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// RowLink tests — bu-86c4c.16
//
// Coverage:
//   - default: renders a real <a> (react-router Link) to `to`
//   - hasNestedInteractive: renders <div role="link"> instead of <a>
//   - hasNestedInteractive: tabIndex=0, focusable
//   - hasNestedInteractive: Enter/Space (row itself focused) call onActivate
//   - hasNestedInteractive: a nested button's own click does not also fire
//     onActivate when it stops propagation (matches StatusBoardCell's
//     restore-chip convention)
//   - className forwarding on both branches
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { afterEach } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router"

import { RowLink } from "./RowLink"

afterEach(() => {
  cleanup()
})

describe("RowLink: default (plain navigating row)", () => {
  it("renders a real <a> element", () => {
    render(
      <MemoryRouter>
        <RowLink to="/butlers/general" data-testid="row">
          general
        </RowLink>
      </MemoryRouter>,
    )
    const el = screen.getByTestId("row")
    expect(el.tagName).toBe("A")
    expect(el.getAttribute("href")).toBe("/butlers/general")
  })

  it("forwards className", () => {
    render(
      <MemoryRouter>
        <RowLink to="/butlers/general" className="cell-class" data-testid="row">
          general
        </RowLink>
      </MemoryRouter>,
    )
    expect(screen.getByTestId("row").className).toContain("cell-class")
  })

  it("forwards aria-label", () => {
    render(
      <MemoryRouter>
        <RowLink to="/butlers/general" aria-label="general, idle" data-testid="row">
          general
        </RowLink>
      </MemoryRouter>,
    )
    expect(screen.getByTestId("row").getAttribute("aria-label")).toBe("general, idle")
  })
})

describe("RowLink: hasNestedInteractive (restore-chip fallback)", () => {
  it('renders a <div role="link"> instead of <a>', () => {
    render(
      <MemoryRouter>
        <RowLink to="/butlers/quarant" hasNestedInteractive onActivate={() => {}} data-testid="row">
          quarant
        </RowLink>
      </MemoryRouter>,
    )
    const el = screen.getByTestId("row")
    expect(el.tagName).toBe("DIV")
    expect(el.getAttribute("role")).toBe("link")
  })

  it("is focusable via tabIndex=0", () => {
    render(
      <MemoryRouter>
        <RowLink to="/butlers/quarant" hasNestedInteractive onActivate={() => {}} data-testid="row">
          quarant
        </RowLink>
      </MemoryRouter>,
    )
    expect(screen.getByTestId("row").getAttribute("tabindex")).toBe("0")
  })

  it("calls onActivate on click", async () => {
    const onActivate = vi.fn()
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <RowLink to="/butlers/quarant" hasNestedInteractive onActivate={onActivate} data-testid="row">
          quarant
        </RowLink>
      </MemoryRouter>,
    )
    await user.click(screen.getByTestId("row"))
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it("calls onActivate on Enter when the row itself is focused", async () => {
    const onActivate = vi.fn()
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <RowLink to="/butlers/quarant" hasNestedInteractive onActivate={onActivate} data-testid="row">
          quarant
        </RowLink>
      </MemoryRouter>,
    )
    screen.getByTestId("row").focus()
    await user.keyboard("{Enter}")
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it("calls onActivate on Space when the row itself is focused", async () => {
    const onActivate = vi.fn()
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <RowLink to="/butlers/quarant" hasNestedInteractive onActivate={onActivate} data-testid="row">
          quarant
        </RowLink>
      </MemoryRouter>,
    )
    screen.getByTestId("row").focus()
    await user.keyboard(" ")
    expect(onActivate).toHaveBeenCalledTimes(1)
  })

  it("does not fire onActivate when a nested control stops click propagation", async () => {
    const onActivate = vi.fn()
    const onRestore = vi.fn()
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <RowLink to="/butlers/quarant" hasNestedInteractive onActivate={onActivate} data-testid="row">
          <button
            type="button"
            data-testid="restore"
            onClick={(e) => {
              e.stopPropagation()
              onRestore()
            }}
          >
            restore
          </button>
        </RowLink>
      </MemoryRouter>,
    )
    await user.click(screen.getByTestId("restore"))
    expect(onRestore).toHaveBeenCalledTimes(1)
    expect(onActivate).not.toHaveBeenCalled()
  })

  it("forwards className", () => {
    render(
      <MemoryRouter>
        <RowLink
          to="/butlers/quarant"
          hasNestedInteractive
          onActivate={() => {}}
          className="cell-class"
          data-testid="row"
        >
          quarant
        </RowLink>
      </MemoryRouter>,
    )
    expect(screen.getByTestId("row").className).toContain("cell-class")
  })
})
