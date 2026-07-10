// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// DriftTile tests -- bu-9r3hd.1
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Check unavailable (drift_check_available=false): unknown notice, never
//     rendered as a clean all-clear
//   - Clean (is_drifted=false): green "In sync" badge
//   - Drifted: red badge, one row per drifted chain, first-detected time,
//     escalated marker only when escalated=true
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse, DriftFacts } from "@/api/types"
import { DriftTile } from "./DriftTile"

// ---------------------------------------------------------------------------
// Mock useDriftFacts
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<DriftFacts>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useDriftFacts: () => mockResult,
}))

// ---------------------------------------------------------------------------
// Mock <Time> to sidestep date-fns-tz / ChroniclesTimezoneProvider
// ---------------------------------------------------------------------------

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <time dateTime={value}>{value}</time>,
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeDriftFacts(overrides: Partial<DriftFacts> = {}): ApiResponse<DriftFacts> {
  return {
    data: {
      checked_at: "2026-07-11T00:00:00Z",
      is_drifted: false,
      drifted: [],
      first_detected_at: null,
      escalated: false,
      drift_check_available: true,
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<DriftTile />)
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("DriftTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("drift-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    const html = render()
    expect(html).not.toContain("drift-tile-clean")
    expect(html).not.toContain("drift-tile-drifted")
    expect(html).not.toContain("drift-tile-unavailable")
  })
})

// ---------------------------------------------------------------------------
// 2. Error state
// ---------------------------------------------------------------------------

describe("DriftTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("drift-tile-error")
  })

  it("does not render content or unavailable state when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    const html = render()
    expect(html).not.toContain("drift-tile-clean")
    expect(html).not.toContain("drift-tile-unavailable")
  })
})

// ---------------------------------------------------------------------------
// 3. Check unavailable (degraded, never a fabricated all-clear)
// ---------------------------------------------------------------------------

describe("DriftTile -- drift check unavailable", () => {
  it("renders the unavailable state, not the clean state", () => {
    mockResult = {
      isPending: false,
      data: makeDriftFacts({ drift_check_available: false }),
    }
    const html = render()
    expect(html).toContain("drift-tile-unavailable")
    expect(html).not.toContain("drift-tile-clean")
  })

  it("shows 'Drift check unavailable' text", () => {
    mockResult = {
      isPending: false,
      data: makeDriftFacts({ drift_check_available: false }),
    }
    expect(render()).toContain("Drift check unavailable")
  })
})

// ---------------------------------------------------------------------------
// 4. Clean (not drifted)
// ---------------------------------------------------------------------------

describe("DriftTile -- in sync", () => {
  it("renders the clean badge", () => {
    mockResult = { isPending: false, data: makeDriftFacts() }
    const html = render()
    expect(html).toContain("drift-tile-clean")
    expect(html).toContain("drift-tile-clean-badge")
  })

  it("does not render the drifted state", () => {
    mockResult = { isPending: false, data: makeDriftFacts() }
    expect(render()).not.toContain("drift-tile-drifted")
  })
})

// ---------------------------------------------------------------------------
// 5. Drifted
// ---------------------------------------------------------------------------

describe("DriftTile -- drifted", () => {
  it("renders the drifted badge with the correct count", () => {
    mockResult = {
      isPending: false,
      data: makeDriftFacts({
        is_drifted: true,
        drifted: [
          {
            schema_name: "finance",
            chain: "core",
            expected_head: "core_163",
            actual_revision: "core_155",
          },
        ],
      }),
    }
    const html = render()
    expect(html).toContain("drift-tile-drifted")
    expect(html).toContain("1 chain drifted")
  })

  it("renders each drifted chain's schema/chain/expected/actual", () => {
    mockResult = {
      isPending: false,
      data: makeDriftFacts({
        is_drifted: true,
        drifted: [
          {
            schema_name: "finance",
            chain: "core",
            expected_head: "core_163",
            actual_revision: "core_155",
          },
        ],
      }),
    }
    const html = render()
    expect(html).toContain("finance/core")
    expect(html).toContain("core_163")
    expect(html).toContain("core_155")
  })

  it("renders 'none' for a chain that was never applied", () => {
    mockResult = {
      isPending: false,
      data: makeDriftFacts({
        is_drifted: true,
        drifted: [
          {
            schema_name: "finance",
            chain: "memory",
            expected_head: "memory_002",
            actual_revision: null,
          },
        ],
      }),
    }
    expect(render()).toContain("has none")
  })

  it("shows first-detected time without an escalation marker before 24h", () => {
    mockResult = {
      isPending: false,
      data: makeDriftFacts({
        is_drifted: true,
        drifted: [
          {
            schema_name: "finance",
            chain: "core",
            expected_head: "core_163",
            actual_revision: "core_155",
          },
        ],
        first_detected_at: "2026-07-10T12:00:00Z",
        escalated: false,
      }),
    }
    const html = render()
    expect(html).toContain("First detected")
    expect(html).not.toContain("escalated to QA")
  })

  it("shows the escalation marker once escalated=true", () => {
    mockResult = {
      isPending: false,
      data: makeDriftFacts({
        is_drifted: true,
        drifted: [
          {
            schema_name: "finance",
            chain: "core",
            expected_head: "core_163",
            actual_revision: "core_155",
          },
        ],
        first_detected_at: "2026-07-09T12:00:00Z",
        escalated: true,
      }),
    }
    expect(render()).toContain("escalated to QA")
  })
})
