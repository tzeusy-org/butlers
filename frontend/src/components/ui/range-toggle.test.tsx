// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// RangeToggle tests — bu-iuol4.14
//
// Coverage:
//   - All three values (24h, 7d, 30d) render correctly with `value` prop.
//   - Click on each option calls onChange with the correct value.
//   - Controlled mode: external value prop change updates active button.
//   - aria-pressed reflects active state.
//   - Keyboard navigation: all three buttons are reachable via Tab.
// ---------------------------------------------------------------------------

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { renderToStaticMarkup } from "react-dom/server"

import { RangeToggle } from "./range-toggle"

afterEach(() => cleanup())

// ---------------------------------------------------------------------------
// 1. All three values render correctly
// ---------------------------------------------------------------------------

describe("RangeToggle: renders all three options", () => {
  it("renders 24H button", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    expect(screen.getByText("24H")).toBeDefined()
  })

  it("renders 7D button", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    expect(screen.getByText("7D")).toBeDefined()
  })

  it("renders 30D button", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    expect(screen.getByText("30D")).toBeDefined()
  })

  it("renders exactly three buttons", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    const buttons = screen.getAllByRole("button")
    expect(buttons).toHaveLength(3)
  })
})

// ---------------------------------------------------------------------------
// 2. aria-pressed reflects active state
// ---------------------------------------------------------------------------

describe("RangeToggle: aria-pressed reflects active state", () => {
  it("24H button has aria-pressed=true when value='24h'", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    const btn = screen.getByText("24H").closest("button")
    expect(btn?.getAttribute("aria-pressed")).toBe("true")
  })

  it("7D button has aria-pressed=false when value='24h'", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    const btn = screen.getByText("7D").closest("button")
    expect(btn?.getAttribute("aria-pressed")).toBe("false")
  })

  it("30D button has aria-pressed=false when value='24h'", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    const btn = screen.getByText("30D").closest("button")
    expect(btn?.getAttribute("aria-pressed")).toBe("false")
  })

  it("7D button has aria-pressed=true when value='7d'", () => {
    render(<RangeToggle value="7d" onChange={() => {}} />)
    const btn = screen.getByText("7D").closest("button")
    expect(btn?.getAttribute("aria-pressed")).toBe("true")
  })

  it("30D button has aria-pressed=true when value='30d'", () => {
    render(<RangeToggle value="30d" onChange={() => {}} />)
    const btn = screen.getByText("30D").closest("button")
    expect(btn?.getAttribute("aria-pressed")).toBe("true")
  })

  it("only one button has aria-pressed=true at a time", () => {
    render(<RangeToggle value="7d" onChange={() => {}} />)
    const buttons = screen.getAllByRole("button")
    const pressed = buttons.filter((b) => b.getAttribute("aria-pressed") === "true")
    expect(pressed).toHaveLength(1)
    expect(pressed[0].textContent).toBe("7D")
  })
})

// ---------------------------------------------------------------------------
// 3. Click on each option calls onChange with the correct value
// ---------------------------------------------------------------------------

describe("RangeToggle: click calls onChange with correct value", () => {
  it("clicking 24H calls onChange with '24h'", () => {
    const onChange = vi.fn()
    render(<RangeToggle value="7d" onChange={onChange} />)
    fireEvent.click(screen.getByText("24H"))
    expect(onChange).toHaveBeenCalledOnce()
    expect(onChange).toHaveBeenCalledWith("24h")
  })

  it("clicking 7D calls onChange with '7d'", () => {
    const onChange = vi.fn()
    render(<RangeToggle value="24h" onChange={onChange} />)
    fireEvent.click(screen.getByText("7D"))
    expect(onChange).toHaveBeenCalledOnce()
    expect(onChange).toHaveBeenCalledWith("7d")
  })

  it("clicking 30D calls onChange with '30d'", () => {
    const onChange = vi.fn()
    render(<RangeToggle value="24h" onChange={onChange} />)
    fireEvent.click(screen.getByText("30D"))
    expect(onChange).toHaveBeenCalledOnce()
    expect(onChange).toHaveBeenCalledWith("30d")
  })

  it("clicking the already-active button still calls onChange", () => {
    const onChange = vi.fn()
    render(<RangeToggle value="7d" onChange={onChange} />)
    fireEvent.click(screen.getByText("7D"))
    expect(onChange).toHaveBeenCalledOnce()
    expect(onChange).toHaveBeenCalledWith("7d")
  })
})

// ---------------------------------------------------------------------------
// 4. Controlled mode: external value prop change updates active button
// ---------------------------------------------------------------------------

describe("RangeToggle: controlled mode", () => {
  it("re-renders with a new value prop and updates aria-pressed accordingly", () => {
    const { rerender } = render(<RangeToggle value="24h" onChange={() => {}} />)

    // Initially 24H is active
    expect(screen.getByText("24H").closest("button")?.getAttribute("aria-pressed")).toBe("true")
    expect(screen.getByText("7D").closest("button")?.getAttribute("aria-pressed")).toBe("false")

    // Parent updates value to 7d
    rerender(<RangeToggle value="7d" onChange={() => {}} />)

    // Now 7D is active
    expect(screen.getByText("24H").closest("button")?.getAttribute("aria-pressed")).toBe("false")
    expect(screen.getByText("7D").closest("button")?.getAttribute("aria-pressed")).toBe("true")
  })

  it("switching from 7d to 30d updates active button", () => {
    const { rerender } = render(<RangeToggle value="7d" onChange={() => {}} />)

    rerender(<RangeToggle value="30d" onChange={() => {}} />)

    expect(screen.getByText("7D").closest("button")?.getAttribute("aria-pressed")).toBe("false")
    expect(screen.getByText("30D").closest("button")?.getAttribute("aria-pressed")).toBe("true")
  })
})

// ---------------------------------------------------------------------------
// 5. Keyboard accessibility
// ---------------------------------------------------------------------------

describe("RangeToggle: keyboard accessibility", () => {
  it("all three buttons are in the tab order (no tabIndex=-1)", () => {
    // Buttons have natural tabIndex=0 unless explicitly removed.
    // renderToStaticMarkup captures the initial HTML.
    const html = renderToStaticMarkup(<RangeToggle value="24h" onChange={() => {}} />)
    // There should be no tabindex="-1" — buttons are keyboard-reachable
    expect(html).not.toContain('tabindex="-1"')
  })

  it("buttons have type=button (no form submission on Enter)", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    const buttons = screen.getAllByRole("button")
    for (const btn of buttons) {
      expect(btn.getAttribute("type")).toBe("button")
    }
  })

  it("container has role=group for semantic button grouping", () => {
    render(<RangeToggle value="24h" onChange={() => {}} />)
    const group = screen.getByRole("group")
    expect(group).toBeDefined()
    expect(group.getAttribute("aria-label")).toBe("Time range")
  })
})

// ---------------------------------------------------------------------------
// 6. className prop is forwarded to the container
// ---------------------------------------------------------------------------

describe("RangeToggle: className forwarding", () => {
  it("forwards className to the root element", () => {
    const html = renderToStaticMarkup(
      <RangeToggle value="24h" onChange={() => {}} className="mt-4 ml-2" />,
    )
    expect(html).toContain("mt-4")
    expect(html).toContain("ml-2")
  })
})
