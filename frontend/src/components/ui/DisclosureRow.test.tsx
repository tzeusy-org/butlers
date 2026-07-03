// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// DisclosureRow tests — bu-86c4c.16
//
// Coverage:
//   - renders children content
//   - role="button", aria-expanded reflects the `expanded` prop
//   - aria-controls forwards `controlsId`
//   - tabIndex=0 by default, focusable
//   - click on the row surface calls onToggle
//   - Enter and Space (when the row itself has focus) both call onToggle
//   - Enter/Space on a NESTED interactive child does not double-fire (the
//     row only reacts when e.target === e.currentTarget)
//   - disabled: tabIndex=-1, aria-disabled, onToggle not called
//   - className forwarding
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach } from "vitest"

import { DisclosureRow } from "./DisclosureRow"

afterEach(() => {
  cleanup()
})

describe("DisclosureRow: content", () => {
  it("renders children", () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}}>
        row content
      </DisclosureRow>,
    )
    expect(screen.getByText("row content")).toBeTruthy()
  })
})

describe("DisclosureRow: ARIA", () => {
  it('has role="button"', () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}} data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").getAttribute("role")).toBe("button")
  })

  it("aria-expanded=false when collapsed", () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}} data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").getAttribute("aria-expanded")).toBe("false")
  })

  it("aria-expanded=true when expanded", () => {
    render(
      <DisclosureRow expanded onToggle={() => {}} data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").getAttribute("aria-expanded")).toBe("true")
  })

  it("forwards controlsId to aria-controls", () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}} controlsId="drawer-1" data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").getAttribute("aria-controls")).toBe("drawer-1")
  })

  it("is focusable via tabIndex=0 by default", () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}} data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").getAttribute("tabindex")).toBe("0")
  })
})

describe("DisclosureRow: activation", () => {
  it("calls onToggle on click", async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(
      <DisclosureRow expanded={false} onToggle={onToggle} data-testid="row">
        x
      </DisclosureRow>,
    )
    await user.click(screen.getByTestId("row"))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it("calls onToggle on Enter when the row itself is focused", async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(
      <DisclosureRow expanded={false} onToggle={onToggle} data-testid="row">
        x
      </DisclosureRow>,
    )
    screen.getByTestId("row").focus()
    await user.keyboard("{Enter}")
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it("calls onToggle on Space when the row itself is focused", async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(
      <DisclosureRow expanded={false} onToggle={onToggle} data-testid="row">
        x
      </DisclosureRow>,
    )
    screen.getByTestId("row").focus()
    await user.keyboard(" ")
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it("ignores a bare keydown on a nested child (only the row's own focus triggers toggle-on-key)", () => {
    const onToggle = vi.fn()
    render(
      <DisclosureRow expanded={false} onToggle={onToggle} data-testid="row">
        <button type="button" data-testid="nested">nested</button>
      </DisclosureRow>,
    )
    // Dispatch Enter directly on the nested child without letting it bubble
    // as a native click (jsdom/user-event would otherwise convert a real
    // button's Enter into a click that legitimately bubbles to the row,
    // same as any other nested-button click — see the stopPropagation test
    // below for how consumers opt out of that).
    const nested = screen.getByTestId("nested")
    nested.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }))
    expect(onToggle).toHaveBeenCalledTimes(0)
  })

  it("does not toggle when a nested interactive child stops click propagation", async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(
      <DisclosureRow expanded={false} onToggle={onToggle} data-testid="row">
        <button
          type="button"
          data-testid="nested"
          onClick={(e) => e.stopPropagation()}
        >
          nested
        </button>
      </DisclosureRow>,
    )
    await user.click(screen.getByTestId("nested"))
    // Matches the established TimelineTab/StatusBoardCell convention: nested
    // interactive controls stopPropagation on their own click so activating
    // them does not also toggle the row.
    expect(onToggle).toHaveBeenCalledTimes(0)
  })
})

describe("DisclosureRow: disabled", () => {
  it("tabIndex=-1 when disabled", () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}} disabled data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").getAttribute("tabindex")).toBe("-1")
  })

  it("aria-disabled=true when disabled", () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}} disabled data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").getAttribute("aria-disabled")).toBe("true")
  })

  it("does not call onToggle when disabled and clicked", async () => {
    const onToggle = vi.fn()
    const user = userEvent.setup()
    render(
      <DisclosureRow expanded={false} onToggle={onToggle} disabled data-testid="row">
        x
      </DisclosureRow>,
    )
    await user.click(screen.getByTestId("row"))
    expect(onToggle).not.toHaveBeenCalled()
  })
})

describe("DisclosureRow: className forwarding", () => {
  it("forwards className to the root", () => {
    render(
      <DisclosureRow expanded={false} onToggle={() => {}} className="my-row" data-testid="row">
        x
      </DisclosureRow>,
    )
    expect(screen.getByTestId("row").className).toContain("my-row")
  })
})
