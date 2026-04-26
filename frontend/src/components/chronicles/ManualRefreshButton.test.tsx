// @vitest-environment jsdom
/**
 * Tests for ManualRefreshButton (bu-hzqr0).
 *
 * Verifies:
 *   - Renders a "Refresh" button with the correct aria-label.
 *   - Clicking the button calls queryClient.invalidateQueries with chroniclesKeys.all.
 *   - Button shows a loading state ("Refreshing…") while the invalidation promise resolves.
 *   - Button returns to the default label after the invalidation settles.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act } from "react"
import { createRoot, type Root } from "react-dom/client"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockInvalidateQueries = vi.fn()

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
}))

// chroniclesKeys.all is ["chronicles"] — we just need to verify invalidation is called
// with the matching key.
vi.mock("@/hooks/use-chronicles", () => ({
  chroniclesKeys: {
    all: ["chronicles"] as const,
  },
}))

import { ManualRefreshButton } from "./ManualRefreshButton"

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement("div")
  document.body.appendChild(container)
  root = createRoot(container)
  mockInvalidateQueries.mockReset()
})

afterEach(() => {
  act(() => {
    root.unmount()
  })
  container.remove()
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderButton() {
  act(() => {
    root.render(<ManualRefreshButton />)
  })
}

function getButton(): HTMLButtonElement {
  const btn = container.querySelector<HTMLButtonElement>("button[aria-label='Refresh chronicles data']")
  if (!btn) throw new Error("ManualRefreshButton not found in DOM")
  return btn
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ManualRefreshButton", () => {
  it("renders a Refresh button with aria-label", () => {
    renderButton()
    const btn = getButton()
    expect(btn).toBeTruthy()
    expect(btn.textContent).toContain("Refresh")
  })

  it("calls queryClient.invalidateQueries with chroniclesKeys.all on click", async () => {
    // Return a resolved promise immediately so the async flow completes.
    mockInvalidateQueries.mockResolvedValue(undefined)
    renderButton()
    const btn = getButton()
    await act(async () => {
      btn.click()
    })
    expect(mockInvalidateQueries).toHaveBeenCalledOnce()
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["chronicles"] })
  })

  it("shows Refreshing state while invalidation is in flight", async () => {
    // Use a manually controlled promise to freeze the loading state.
    let resolveInvalidate!: () => void
    const pending = new Promise<void>((resolve) => { resolveInvalidate = resolve })
    mockInvalidateQueries.mockReturnValue(pending)

    renderButton()
    const btn = getButton()

    // Trigger click — button enters loading state.
    act(() => {
      btn.click()
    })

    // Should show "Refreshing…" and be disabled.
    expect(btn.textContent).toContain("Refreshing")
    expect(btn.disabled).toBe(true)

    // Resolve the pending promise.
    await act(async () => {
      resolveInvalidate()
      await pending
    })

    // After resolution, button returns to the default label and is enabled.
    expect(btn.textContent).toContain("Refresh")
    expect(btn.disabled).toBe(false)
  })

  it("sets aria-busy=true while refreshing and aria-busy=false after", async () => {
    let resolveInvalidate!: () => void
    const pending = new Promise<void>((resolve) => { resolveInvalidate = resolve })
    mockInvalidateQueries.mockReturnValue(pending)

    renderButton()
    const btn = getButton()

    // Before click — aria-busy should be absent or false.
    expect(btn.getAttribute("aria-busy")).not.toBe("true")

    act(() => {
      btn.click()
    })

    // While in-flight — aria-busy must be true.
    expect(btn.getAttribute("aria-busy")).toBe("true")

    await act(async () => {
      resolveInvalidate()
      await pending
    })

    // After completion — aria-busy must be false.
    expect(btn.getAttribute("aria-busy")).toBe("false")
  })
})
