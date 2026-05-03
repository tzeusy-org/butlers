// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// UptimeTile tests -- bu-ngfzz.5
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Happy path: uptime d/h/m string rendered; started_at time rendered
//   - formatUptimeParts: unit tests for the helper function
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse } from "@/api/types"
import type { InstanceFacts } from "@/api/types"
import { UptimeTile } from "./UptimeTile"
import { formatUptimeParts } from "./uptime-utils"

// ---------------------------------------------------------------------------
// Mock useInstanceFacts
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<InstanceFacts>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useInstanceFacts: () => mockResult,
  useDatabaseFacts: () => ({ isPending: false }),
}))

// ---------------------------------------------------------------------------
// Mock <Time>
// ---------------------------------------------------------------------------

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => (
    <time dateTime={value}>{value}</time>
  ),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeInstanceFacts(overrides: Partial<InstanceFacts> = {}): ApiResponse<InstanceFacts> {
  return {
    data: {
      version: "1.0.0",
      uptime_seconds: 90061, // 1d 1h 1m 1s
      started_at: "2026-05-01T00:00:00Z",
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<UptimeTile />)
}

// ---------------------------------------------------------------------------
// 1. formatUptimeParts helper
// ---------------------------------------------------------------------------

describe("formatUptimeParts", () => {
  it("returns '0m' for zero seconds", () => {
    expect(formatUptimeParts(0)).toBe("0m")
  })

  it("returns '0m' for sub-minute values", () => {
    expect(formatUptimeParts(59)).toBe("0m")
  })

  it("returns minutes only when under one hour", () => {
    expect(formatUptimeParts(3600 - 60)).toBe("59m")
  })

  it("returns hours and minutes for 1h 30m", () => {
    expect(formatUptimeParts(90 * 60)).toBe("1h 30m")
  })

  it("includes trailing 0m for exact hours", () => {
    expect(formatUptimeParts(2 * 3600)).toBe("2h 0m")
  })

  it("returns days, hours, and minutes for multi-day uptime", () => {
    // 2d 3h 4m = (2*24*60 + 3*60 + 4) minutes = 3064 minutes = 183840 seconds
    expect(formatUptimeParts(183840)).toBe("2d 3h 4m")
  })

  it("omits days when less than 1 day", () => {
    const result = formatUptimeParts(3661) // 1h 1m 1s
    expect(result).toBe("1h 1m")
    expect(result).not.toContain("d")
  })

  it("handles negative values as zero uptime", () => {
    expect(formatUptimeParts(-100)).toBe("0m")
  })
})

// ---------------------------------------------------------------------------
// 2. Loading state
// ---------------------------------------------------------------------------

describe("UptimeTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("uptime-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    expect(render()).not.toContain("uptime-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 3. Error state
// ---------------------------------------------------------------------------

describe("UptimeTile -- error state", () => {
  it("renders error element when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("uptime-tile-error")
  })

  it("renders error text when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("Could not load uptime info")
  })

  it("does not render content when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).not.toContain("uptime-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 4. Happy path
// ---------------------------------------------------------------------------

describe("UptimeTile -- happy path", () => {
  it("renders the content container", () => {
    mockResult = { isPending: false, data: makeInstanceFacts() }
    expect(render()).toContain("uptime-tile-content")
  })

  it("renders the d/h/m uptime string for multi-day uptime", () => {
    // 90061 seconds = 1d 1h 1m
    mockResult = { isPending: false, data: makeInstanceFacts({ uptime_seconds: 90061 }) }
    expect(render()).toContain("1d 1h 1m")
  })

  it("renders hours+minutes for sub-day uptime", () => {
    // 3661 = 1h 1m 1s -> "1h 1m"
    mockResult = { isPending: false, data: makeInstanceFacts({ uptime_seconds: 3661 }) }
    expect(render()).toContain("1h 1m")
  })

  it("renders the started_at timestamp via <Time>", () => {
    mockResult = {
      isPending: false,
      data: makeInstanceFacts({ started_at: "2026-05-01T00:00:00Z" }),
    }
    expect(render()).toContain("2026-05-01T00:00:00Z")
  })
})
