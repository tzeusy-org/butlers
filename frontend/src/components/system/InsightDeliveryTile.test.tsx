// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// InsightDeliveryTile tests -- bu-dl98i.3.3
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - Error state: error message rendered, no content
//   - Empty state: all zeros, "No deliveries yet"
//   - Happy path: queued/delivered/failed counts and last_delivery_at rendered
//   - No placeholder/hardcoded values (values come from data)
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse, InsightDeliveryState } from "@/api/types"
import { InsightDeliveryTile } from "./InsightDeliveryTile"

// ---------------------------------------------------------------------------
// Mock useInsightDeliveryState
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  data: ApiResponse<InsightDeliveryState>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useInsightDeliveryState: () => mockResult,
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeResponse(state: Partial<InsightDeliveryState>): ApiResponse<InsightDeliveryState> {
  return {
    data: {
      queued: 0,
      delivered: 0,
      failed: 0,
      last_delivery_at: null,
      ...state,
    },
    meta: {},
  } as ApiResponse<InsightDeliveryState>
}

function render(): string {
  return renderToStaticMarkup(<InsightDeliveryTile />)
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("InsightDeliveryTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("insight-delivery-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    expect(render()).not.toContain("insight-delivery-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 2. Error state
// ---------------------------------------------------------------------------

describe("InsightDeliveryTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("insight-delivery-tile-error")
  })

  it("renders error text", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("Could not load insight delivery state")
  })

  it("does not render content on error", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).not.toContain("insight-delivery-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 3. Empty state (all zeros, no deliveries yet)
// ---------------------------------------------------------------------------

describe("InsightDeliveryTile -- empty state", () => {
  it("renders content container", () => {
    mockResult = { isPending: false, data: makeResponse({}) }
    expect(render()).toContain("insight-delivery-tile-content")
  })

  it("shows 'No deliveries yet' when last_delivery_at is null", () => {
    mockResult = { isPending: false, data: makeResponse({ last_delivery_at: null }) }
    expect(render()).toContain("No deliveries yet")
  })

  it("shows zero for queued", () => {
    mockResult = { isPending: false, data: makeResponse({ queued: 0 }) }
    const html = render()
    expect(html).toContain("insight-delivery-queued")
    expect(html).toContain(">0<")
  })

  it("shows zero for delivered", () => {
    mockResult = { isPending: false, data: makeResponse({ delivered: 0 }) }
    expect(render()).toContain("insight-delivery-delivered")
  })

  it("shows zero for failed", () => {
    mockResult = { isPending: false, data: makeResponse({ failed: 0 }) }
    expect(render()).toContain("insight-delivery-failed")
  })
})

// ---------------------------------------------------------------------------
// 4. Happy path — real delivery data
// ---------------------------------------------------------------------------

describe("InsightDeliveryTile -- with delivery data", () => {
  const STATE: Partial<InsightDeliveryState> = {
    queued: 3,
    delivered: 10,
    failed: 2,
    last_delivery_at: "2026-06-17T10:00:00Z",
  }

  it("renders content container", () => {
    mockResult = { isPending: false, data: makeResponse(STATE) }
    expect(render()).toContain("insight-delivery-tile-content")
  })

  it("renders the queued count", () => {
    mockResult = { isPending: false, data: makeResponse(STATE) }
    const html = render()
    expect(html).toContain("insight-delivery-queued")
    expect(html).toContain(">3<")
  })

  it("renders the delivered count", () => {
    mockResult = { isPending: false, data: makeResponse(STATE) }
    const html = render()
    expect(html).toContain("insight-delivery-delivered")
    expect(html).toContain(">10<")
  })

  it("renders the failed count", () => {
    mockResult = { isPending: false, data: makeResponse(STATE) }
    const html = render()
    expect(html).toContain("insight-delivery-failed")
    expect(html).toContain(">2<")
  })

  it("renders a last_delivery_at timestamp (not 'No deliveries yet')", () => {
    mockResult = { isPending: false, data: makeResponse(STATE) }
    const html = render()
    expect(html).not.toContain("No deliveries yet")
    // The rendered timestamp is locale-formatted, so just check the test-id
    expect(html).toContain("insight-delivery-last-at")
  })
})

// ---------------------------------------------------------------------------
// 5. No placeholder values
// ---------------------------------------------------------------------------

describe("InsightDeliveryTile -- no placeholder values", () => {
  it("does not render hardcoded placeholder counts when data has different values", () => {
    // If the component ever hardcodes e.g. "42", this test catches it
    mockResult = { isPending: false, data: makeResponse({ queued: 7, delivered: 99, failed: 1 }) }
    const html = render()
    expect(html).toContain(">7<")
    expect(html).toContain(">99<")
    expect(html).toContain(">1<")
  })
})
