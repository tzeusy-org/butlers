// @vitest-environment jsdom
/**
 * ButlerFrictionPanel -- RTL tests (bu-8cdl1.9 S3).
 *
 * Tests:
 *  - Renders zero-filled friction kinds and outcome stats
 *  - Empty-episode state renders distinct copy from a genuine zero-fill
 *  - Loading state renders neither empty nor error copy
 *  - Error state renders a degraded note, never a fabricated empty list
 *  - The period toggle switches which period the hook is called with
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import type { FrictionSummary } from "@/api/types"
import { ButlerFrictionPanel } from "./ButlerFrictionPanel"

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerFrictionSummary: vi.fn(),
}))

import { useButlerFrictionSummary } from "@/hooks/use-butler-analytics"

const mockUseFrictionSummary = useButlerFrictionSummary as unknown as ReturnType<typeof vi.fn>

function makeSummary(overrides: Partial<FrictionSummary> = {}): FrictionSummary {
  return {
    period: "7d",
    total: 0,
    by_kind: {
      degenerate_tool_loop: 0,
      guardrail_termination: 0,
      classification_timeout: 0,
      recovered_error: 0,
      dead_end: 0,
    },
    succeeded: 0,
    failed: 0,
    by_error_marker: {},
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ButlerFrictionPanel", () => {
  it("renders zero-filled kinds and outcome stats when no friction occurred", () => {
    mockUseFrictionSummary.mockReturnValue({
      data: makeSummary({ succeeded: 12, failed: 0 }),
      isLoading: false,
      isError: false,
    })

    render(<ButlerFrictionPanel butlerName="atlas" />)

    expect(screen.getByTestId("panel-friction")).toBeDefined()
    expect(screen.getByText("no friction episodes this period")).toBeDefined()
    expect(screen.getByText("no failures this period")).toBeDefined()
    expect(screen.getByText(/12 succeeded \/ 0 failed/)).toBeDefined()
  })

  it("renders each friction kind's count, zero-filling kinds with no episodes", () => {
    mockUseFrictionSummary.mockReturnValue({
      data: makeSummary({
        total: 4,
        by_kind: {
          degenerate_tool_loop: 0,
          guardrail_termination: 3,
          classification_timeout: 0,
          recovered_error: 0,
          dead_end: 1,
        },
        succeeded: 16,
        failed: 4,
        by_error_marker: { token_budget_exceeded: 3 },
      }),
      isLoading: false,
      isError: false,
    })

    render(<ButlerFrictionPanel butlerName="atlas" />)

    const rows = screen.getAllByTestId("friction-episode-row")
    expect(rows).toHaveLength(5)
    expect(screen.getByText("guardrail termination")).toBeDefined()
    expect(screen.getByText("dead end")).toBeDefined()
    expect(screen.getByText("token_budget_exceeded")).toBeDefined()
    expect(screen.getByText(/16 succeeded \/ 4 failed/)).toBeDefined()
  })

  it("shows a loading state without empty or error copy", () => {
    mockUseFrictionSummary.mockReturnValue({ data: undefined, isLoading: true, isError: false })

    render(<ButlerFrictionPanel butlerName="atlas" />)

    expect(screen.queryByText("no friction episodes this period")).toBeNull()
    expect(screen.queryByTestId("friction-summary-error")).toBeNull()
  })

  it("shows a degraded note on error, never a fabricated empty list", () => {
    mockUseFrictionSummary.mockReturnValue({ data: undefined, isLoading: false, isError: true })

    render(<ButlerFrictionPanel butlerName="atlas" />)

    expect(screen.getByTestId("friction-summary-error")).toBeDefined()
    expect(screen.queryByText("no friction episodes this period")).toBeNull()
  })

  it("switches the requested period when a toggle button is clicked", async () => {
    mockUseFrictionSummary.mockReturnValue({
      data: makeSummary(),
      isLoading: false,
      isError: false,
    })

    render(<ButlerFrictionPanel butlerName="atlas" />)

    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "30D" }))

    expect(mockUseFrictionSummary).toHaveBeenLastCalledWith("atlas", "30d")
  })
})
