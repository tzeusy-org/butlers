// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// StoredFunctionsTile tests -- bu-uoctv
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Check unavailable (stored_function_check_available=false): unknown
//     notice, never rendered as a clean all-clear
//   - All matched (is_drifted=false, no not_deployed): green badge
//   - Drifted: red badge, one row per drifted function with its init-db.sql
//     line reference(s); no digest or body text reaches the DOM
//   - not_deployed: distinct (amber, non-red) styling from drifted, never
//     alarm-shaped on its own
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse, StoredFunctionFacts } from "@/api/types"
import { StoredFunctionsTile } from "./StoredFunctionsTile"

// ---------------------------------------------------------------------------
// Mock useStoredFunctionFacts
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<StoredFunctionFacts>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useStoredFunctionFacts: () => mockResult,
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeStoredFunctionFacts(
  overrides: Partial<StoredFunctionFacts> = {},
): ApiResponse<StoredFunctionFacts> {
  return {
    data: {
      checked_at: "2026-07-11T00:00:00Z",
      is_drifted: false,
      drifted: [],
      not_deployed: [],
      matched_count: 0,
      stored_function_check_available: true,
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<StoredFunctionsTile />)
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("StoredFunctionsTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("stored-functions-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    const html = render()
    expect(html).not.toContain("stored-functions-tile-clean")
    expect(html).not.toContain("stored-functions-tile-drifted")
    expect(html).not.toContain("stored-functions-tile-unavailable")
  })
})

// ---------------------------------------------------------------------------
// 2. Error state
// ---------------------------------------------------------------------------

describe("StoredFunctionsTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("stored-functions-tile-error")
  })

  it("does not render content or unavailable state when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    const html = render()
    expect(html).not.toContain("stored-functions-tile-clean")
    expect(html).not.toContain("stored-functions-tile-unavailable")
  })
})

// ---------------------------------------------------------------------------
// 3. Check unavailable (degraded, never a fabricated all-clear)
// ---------------------------------------------------------------------------

describe("StoredFunctionsTile -- check unavailable", () => {
  it("renders the unavailable state, not the clean state", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({ stored_function_check_available: false }),
    }
    const html = render()
    expect(html).toContain("stored-functions-tile-unavailable")
    expect(html).not.toContain("stored-functions-tile-clean")
  })

  it("shows 'Stored-function check unavailable' text", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({ stored_function_check_available: false }),
    }
    expect(render()).toContain("Stored-function check unavailable")
  })
})

// ---------------------------------------------------------------------------
// 4. All matched
// ---------------------------------------------------------------------------

describe("StoredFunctionsTile -- all matched", () => {
  it("renders the clean badge with the matched count", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({ matched_count: 7 }),
    }
    const html = render()
    expect(html).toContain("stored-functions-tile-clean")
    expect(html).toContain("stored-functions-tile-clean-badge")
    expect(html).toContain("All 7 matched")
  })

  it("does not render the drifted state", () => {
    mockResult = { isPending: false, data: makeStoredFunctionFacts() }
    expect(render()).not.toContain("stored-functions-tile-drifted")
  })
})

// ---------------------------------------------------------------------------
// 5. Drifted
// ---------------------------------------------------------------------------

describe("StoredFunctionsTile -- drifted", () => {
  it("renders the drifted badge with the correct count", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({
        is_drifted: true,
        matched_count: 4,
        drifted: [
          {
            function: "notify",
            committed_lines: [412],
            committed_digests: ["abc123def456"],
            deployed_digests: ["fedcba654321"],
          },
        ],
      }),
    }
    const html = render()
    expect(html).toContain("stored-functions-tile-drifted")
    expect(html).toContain("1 function drifted")
    expect(html).toContain("4 matched, 1 drifted, 0 not deployed")
  })

  it("names the drifted function with its init-db.sql line reference", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({
        is_drifted: true,
        drifted: [
          {
            function: "notify",
            committed_lines: [412],
            committed_digests: ["abc123def456"],
            deployed_digests: ["fedcba654321"],
          },
        ],
      }),
    }
    const html = render()
    expect(html).toContain("notify")
    expect(html).toContain("412")
  })

  it("joins multiple committed line references for one function", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({
        is_drifted: true,
        drifted: [
          {
            function: "resolve_entity",
            committed_lines: [88, 140],
            committed_digests: ["a1", "a2"],
            deployed_digests: ["b1"],
          },
        ],
      }),
    }
    const html = render()
    expect(html).toContain("resolve_entity")
    expect(html).toContain("88, 140")
  })

  it("never renders a committed or deployed digest", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({
        is_drifted: true,
        drifted: [
          {
            function: "notify",
            committed_lines: [412],
            committed_digests: ["abc123def456"],
            deployed_digests: ["fedcba654321"],
          },
        ],
      }),
    }
    const html = render()
    expect(html).not.toContain("abc123def456")
    expect(html).not.toContain("fedcba654321")
  })
})

// ---------------------------------------------------------------------------
// 6. not_deployed -- distinct from drifted, never alarm-shaped alone
// ---------------------------------------------------------------------------

describe("StoredFunctionsTile -- not_deployed", () => {
  it("renders a not-deployed badge distinct from the drifted badge", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({
        not_deployed: ["seed_defaults"],
      }),
    }
    const html = render()
    expect(html).toContain("stored-functions-tile-not-deployed-badge")
    expect(html).not.toContain("stored-functions-tile-drifted-badge")
    expect(html).toContain("seed_defaults")
  })

  it("does not mark is_drifted-driven state when only not_deployed is present", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({
        not_deployed: ["seed_defaults"],
      }),
    }
    const html = render()
    expect(html).not.toContain("border-[var(--red)]/40")
  })

  it("renders both drifted and not_deployed sections when both are present", () => {
    mockResult = {
      isPending: false,
      data: makeStoredFunctionFacts({
        is_drifted: true,
        drifted: [
          {
            function: "notify",
            committed_lines: [412],
            committed_digests: ["abc123"],
            deployed_digests: ["def456"],
          },
        ],
        not_deployed: ["seed_defaults"],
      }),
    }
    const html = render()
    expect(html).toContain("stored-functions-tile-drifted-badge")
    expect(html).toContain("stored-functions-tile-not-deployed-badge")
  })
})
