// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// EgressCatalogTile tests -- bu-ngfzz.6
//
// Coverage:
//   - Loading state: skeleton rendered, no content
//   - isForbidden: owner-only notice rendered
//   - Error state: error message rendered, no content
//   - Empty actors list: "No external egress recorded yet"
//   - Populated actors: display name, call count, last_seen_at rendered
//   - catalog_covers_from: footer rendered with Time in absolute mode
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import type { ApiResponse, EgressCatalog } from "@/api/types"
import { EgressCatalogTile } from "./EgressCatalogTile"

// ---------------------------------------------------------------------------
// Mock useEgressFacts
// ---------------------------------------------------------------------------

type HookResult = Partial<{
  isPending: boolean
  isError: boolean
  isForbidden: boolean
  data: ApiResponse<EgressCatalog>
}>

let mockResult: HookResult = { isPending: false }

vi.mock("@/hooks/use-system", () => ({
  useEgressFacts: () => mockResult,
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

function makeCatalog(overrides: Partial<EgressCatalog> = {}): ApiResponse<EgressCatalog> {
  return {
    data: {
      actors: [],
      catalog_covers_from: null,
      ...overrides,
    },
    meta: {},
  }
}

function render(): string {
  return renderToStaticMarkup(<EgressCatalogTile />)
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("EgressCatalogTile -- loading state", () => {
  it("renders skeleton when isPending=true", () => {
    mockResult = { isPending: true }
    expect(render()).toContain("egress-tile-skeleton")
  })

  it("does not render content while loading", () => {
    mockResult = { isPending: true }
    const html = render()
    expect(html).not.toContain("egress-tile-content")
    expect(html).not.toContain("egress-tile-forbidden")
    expect(html).not.toContain("egress-tile-empty")
  })
})

// ---------------------------------------------------------------------------
// 2. isForbidden state
// ---------------------------------------------------------------------------

describe("EgressCatalogTile -- isForbidden", () => {
  it("renders forbidden notice when isForbidden=true", () => {
    mockResult = { isPending: false, isForbidden: true }
    expect(render()).toContain("egress-tile-forbidden")
  })

  it("renders owner-only message", () => {
    mockResult = { isPending: false, isForbidden: true }
    expect(render()).toContain("Owner only")
  })

  it("does not render error when isForbidden=true", () => {
    mockResult = { isPending: false, isForbidden: true }
    expect(render()).not.toContain("egress-tile-error")
  })

  it("does not render content when isForbidden=true", () => {
    mockResult = { isPending: false, isForbidden: true }
    expect(render()).not.toContain("egress-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 3. Error state
// ---------------------------------------------------------------------------

describe("EgressCatalogTile -- error state", () => {
  it("renders error message when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("egress-tile-error")
  })

  it("renders error text when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).toContain("Could not load egress catalog")
  })

  it("does not render content when isError=true", () => {
    mockResult = { isPending: false, isError: true }
    expect(render()).not.toContain("egress-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 4. Empty actors list
// ---------------------------------------------------------------------------

describe("EgressCatalogTile -- empty actors", () => {
  it("renders empty-state when actors list is empty", () => {
    mockResult = { isPending: false, data: makeCatalog({ actors: [] }) }
    expect(render()).toContain("egress-tile-empty")
  })

  it("renders 'No external egress recorded yet' text", () => {
    mockResult = { isPending: false, data: makeCatalog({ actors: [] }) }
    expect(render()).toContain("No external egress recorded yet")
  })

  it("does not render content tile for empty list", () => {
    mockResult = { isPending: false, data: makeCatalog({ actors: [] }) }
    expect(render()).not.toContain("egress-tile-content")
  })
})

// ---------------------------------------------------------------------------
// 5. Populated actors
// ---------------------------------------------------------------------------

describe("EgressCatalogTile -- populated actors", () => {
  const actors = [
    {
      actor_id: "anthropic.claude",
      display_name: "Anthropic Claude API",
      last_seen_at: "2026-05-03T10:00:00Z",
      total_calls: 42,
      data_types: ["session_prompt"],
    },
    {
      actor_id: "telegram.api",
      display_name: "Telegram Bot API",
      last_seen_at: "2026-05-02T08:00:00Z",
      total_calls: 1,
      data_types: ["message_text"],
    },
  ]

  it("renders content container", () => {
    mockResult = { isPending: false, data: makeCatalog({ actors }) }
    expect(render()).toContain("egress-tile-content")
  })

  it("renders each actor display name", () => {
    mockResult = { isPending: false, data: makeCatalog({ actors }) }
    const html = render()
    expect(html).toContain("Anthropic Claude API")
    expect(html).toContain("Telegram Bot API")
  })

  it("renders last_seen_at timestamps via <Time>", () => {
    mockResult = { isPending: false, data: makeCatalog({ actors }) }
    const html = render()
    expect(html).toContain("2026-05-03T10:00:00Z")
    expect(html).toContain("2026-05-02T08:00:00Z")
  })

  it("renders call counts", () => {
    mockResult = { isPending: false, data: makeCatalog({ actors }) }
    const html = render()
    expect(html).toContain("42")
  })

  it("uses singular 'call' when total_calls is 1", () => {
    mockResult = {
      isPending: false,
      data: makeCatalog({
        actors: actors.filter((a) => a.actor_id === "telegram.api"),
      }),
    }
    expect(render()).toContain("1 call")
  })

  it("uses plural 'calls' when total_calls > 1", () => {
    mockResult = {
      isPending: false,
      data: makeCatalog({
        actors: actors.filter((a) => a.actor_id === "anthropic.claude"),
      }),
    }
    expect(render()).toContain("calls")
  })
})

// ---------------------------------------------------------------------------
// 6. catalog_covers_from
// ---------------------------------------------------------------------------

describe("EgressCatalogTile -- catalog_covers_from", () => {
  const actor = {
    actor_id: "anthropic.claude",
    display_name: "Anthropic Claude API",
    last_seen_at: "2026-05-03T10:00:00Z",
    total_calls: 5,
    data_types: ["session_prompt"],
  }

  it("renders covers-from footer when catalog_covers_from is set", () => {
    mockResult = {
      isPending: false,
      data: makeCatalog({
        actors: [actor],
        catalog_covers_from: "2026-01-01T00:00:00Z",
      }),
    }
    expect(render()).toContain("egress-tile-covers-from")
  })

  it("renders catalog_covers_from timestamp via <Time>", () => {
    mockResult = {
      isPending: false,
      data: makeCatalog({
        actors: [actor],
        catalog_covers_from: "2026-01-01T00:00:00Z",
      }),
    }
    expect(render()).toContain("2026-01-01T00:00:00Z")
  })

  it("renders 'Records since' label", () => {
    mockResult = {
      isPending: false,
      data: makeCatalog({
        actors: [actor],
        catalog_covers_from: "2026-01-01T00:00:00Z",
      }),
    }
    expect(render()).toContain("Records since")
  })

  it("does not render covers-from footer when catalog_covers_from is null", () => {
    mockResult = {
      isPending: false,
      data: makeCatalog({
        actors: [actor],
        catalog_covers_from: null,
      }),
    }
    expect(render()).not.toContain("egress-tile-covers-from")
  })
})
