// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// VersionTile tests -- bu-ngfzz.5
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Happy path: version string and started_at time rendered
//   - Happy path: null version field shows "unknown"
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse } from "@/api/types"
import type { InstanceFacts } from "@/api/types"
import { VersionTile } from "./VersionTile"

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
// Mock <Time> to sidestep date-fns-tz / ChroniclesTimezoneProvider
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
      version: "1.2.3",
      uptime_seconds: 3661,
      started_at: "2026-05-01T10:00:00Z",
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<VersionTile />)
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("VersionTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("version-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    expect(render()).not.toContain("version-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 2. Error state
// ---------------------------------------------------------------------------

describe("VersionTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("version-tile-error")
  })

  it("renders error text when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("Could not load version info")
  })

  it("does not render content when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).not.toContain("version-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 3. Happy path
// ---------------------------------------------------------------------------

describe("VersionTile -- happy path", () => {
  it("renders the content container", () => {
    mockResult = { isPending: false, data: makeInstanceFacts() }
    expect(render()).toContain("version-tile-content")
  })

  it("renders the version string", () => {
    mockResult = { isPending: false, data: makeInstanceFacts({ version: "1.2.3" }) }
    expect(render()).toContain("1.2.3")
  })

  it("renders the started_at timestamp via <Time>", () => {
    mockResult = {
      isPending: false,
      data: makeInstanceFacts({ started_at: "2026-05-01T10:00:00Z" }),
    }
    expect(render()).toContain("2026-05-01T10:00:00Z")
  })

  it("renders 'unknown' when version is empty string", () => {
    mockResult = { isPending: false, data: makeInstanceFacts({ version: "" }) }
    const html = render()
    expect(html).toContain("unknown")
    expect(html).not.toContain("1.2.3")
  })
})
