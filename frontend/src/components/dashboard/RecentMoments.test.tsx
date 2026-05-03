// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// RecentMoments tests — bu-2okpr.3
//
// Coverage:
//   - Loading state: skeleton rendered, no session rows
//   - Empty state: empty message, no skeleton
//   - Error state: error message rendered, no list or skeleton
//   - Single moment: row rendered with butler glyph, prompt, time, link
//   - Multi-moment: all rows rendered
//   - limit prop: correct number of skeleton rows; hook called with limit
// ---------------------------------------------------------------------------

import { beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter } from "react-router"

import type { SessionSummary } from "@/api/types"
import { RecentMoments } from "./RecentMoments"

// ---------------------------------------------------------------------------
// Mock useSessions — captures last call args so we can assert forwarding.
// ---------------------------------------------------------------------------

import type { UseQueryResult } from "@tanstack/react-query"
import type { PaginatedResponse } from "@/api/types"

type SessionsResult = Partial<UseQueryResult<PaginatedResponse<SessionSummary>, Error>>

let mockQueryResult: SessionsResult = { isPending: false, data: undefined }
let lastSessionsArgs: unknown[] = []

vi.mock("@/hooks/use-sessions", () => ({
  useSessions: (...args: unknown[]) => {
    lastSessionsArgs = args
    return mockQueryResult
  },
}))

// ---------------------------------------------------------------------------
// Mock <Time> to avoid ChroniclesTimezoneProvider / date-fns-tz in tests.
// Renders the ISO string so assertions on relative time content still work.
// ---------------------------------------------------------------------------

vi.mock("@/components/ui/time", () => ({
  Time: ({ value, className }: { value: string; className?: string }) => (
    <time dateTime={value} className={className}>{value}</time>
  ),
}))

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSession(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: "sess-00000001",
    butler: "general",
    prompt: "Summarize the day",
    trigger_source: "telegram",
    request_id: null,
    success: true,
    started_at: "2026-05-03T08:00:00Z",
    completed_at: "2026-05-03T08:00:10Z",
    duration_ms: 10_000,
    input_tokens: 100,
    output_tokens: 200,
    model: null,
    complexity: null,
    ...overrides,
  }
}

function makePageResult(sessions: SessionSummary[]): PaginatedResponse<SessionSummary> {
  return {
    data: sessions,
    meta: {
      total: sessions.length,
      offset: 0,
      limit: sessions.length,
      has_more: false,
    },
  }
}

function render(props: React.ComponentProps<typeof RecentMoments> = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <RecentMoments {...props} />
    </MemoryRouter>,
  )
}

// ---------------------------------------------------------------------------
// 1. Loading state
// ---------------------------------------------------------------------------

describe("RecentMoments — loading state", () => {
  it("renders skeleton container when isPending=true", () => {
    mockQueryResult = { isPending: true, data: undefined }
    const html = render()
    expect(html).toContain("recent-moments-skeleton")
  })

  it("does not render any session rows while loading", () => {
    mockQueryResult = { isPending: true, data: undefined }
    const html = render()
    expect(html).not.toContain("recent-moments-list")
    expect(html).not.toContain("Summarize")
  })

  it("renders as many skeleton rows as the limit prop (default 7)", () => {
    mockQueryResult = { isPending: true, data: undefined }
    const html = render({ limit: 7 })
    // Each skeleton row has aria-hidden="true"
    const matches = html.match(/aria-hidden="true"/g)
    expect(matches).toHaveLength(7)
  })

  it("renders custom limit skeleton rows when limit=3", () => {
    mockQueryResult = { isPending: true, data: undefined }
    const html = render({ limit: 3 })
    const matches = html.match(/aria-hidden="true"/g)
    expect(matches).toHaveLength(3)
  })
})

// ---------------------------------------------------------------------------
// 2. Empty state
// ---------------------------------------------------------------------------

describe("RecentMoments — empty state", () => {
  it("renders empty message when sessions list is empty", () => {
    mockQueryResult = { isPending: false, data: makePageResult([]) }
    const html = render()
    expect(html).toContain("No recent activity")
  })

  it("does not render list container when sessions list is empty", () => {
    mockQueryResult = { isPending: false, data: makePageResult([]) }
    const html = render()
    expect(html).not.toContain("recent-moments-list")
    expect(html).not.toContain("recent-moments-skeleton")
  })
})

// ---------------------------------------------------------------------------
// 3. Error state
// ---------------------------------------------------------------------------

describe("RecentMoments — error state", () => {
  it("renders error message when isError=true", () => {
    mockQueryResult = { isPending: false, isError: true, data: undefined }
    const html = render()
    expect(html).toContain("Could not load recent activity")
  })

  it("does not render list or skeleton when isError=true", () => {
    mockQueryResult = { isPending: false, isError: true, data: undefined }
    const html = render()
    expect(html).not.toContain("recent-moments-list")
    expect(html).not.toContain("recent-moments-skeleton")
    expect(html).not.toContain("No recent activity")
  })
})

// ---------------------------------------------------------------------------
// 4. Single moment
// ---------------------------------------------------------------------------

describe("RecentMoments — single moment", () => {
  const session = makeSession({
    id: "sess-abc",
    butler: "general",
    prompt: "Summarize the day",
    started_at: "2026-05-03T08:00:00Z",
  })

  beforeEach(() => {
    mockQueryResult = { isPending: false, data: makePageResult([session]) }
  })

  it("renders the list container", () => {
    expect(render()).toContain("recent-moments-list")
  })

  it("renders the butler initial glyph", () => {
    const html = render()
    expect(html).toContain(">G<") // "general" -> "G"
  })

  it("renders the butler name in the glyph title attribute", () => {
    const html = render()
    expect(html).toContain('title="general"')
  })

  it("renders the prompt text", () => {
    const html = render()
    expect(html).toContain("Summarize the day")
  })

  it("renders a <time> element with the session start timestamp", () => {
    const html = render()
    expect(html).toContain("2026-05-03T08:00:00Z")
  })

  it("renders a link to the session detail page with butler query param", () => {
    const html = render()
    expect(html).toContain(`href="/sessions/sess-abc?butler=general"`)
  })
})

// ---------------------------------------------------------------------------
// 5. Multi-moment
// ---------------------------------------------------------------------------

describe("RecentMoments — multi-moment", () => {
  const sessions = [
    makeSession({ id: "sess-1", butler: "general", prompt: "First task" }),
    makeSession({ id: "sess-2", butler: "health", prompt: "Second task" }),
    makeSession({ id: "sess-3", butler: "switchboard", prompt: "Third task" }),
  ]

  beforeEach(() => {
    mockQueryResult = { isPending: false, data: makePageResult(sessions) }
  })

  it("renders all three session rows", () => {
    const html = render()
    expect(html).toContain("First task")
    expect(html).toContain("Second task")
    expect(html).toContain("Third task")
  })

  it("renders distinct butler initials for each session", () => {
    const html = render()
    expect(html).toContain(">G<") // general
    expect(html).toContain(">H<") // health
    expect(html).toContain(">S<") // switchboard
  })

  it("renders a detail link with butler query param for each session", () => {
    const html = render()
    expect(html).toContain('href="/sessions/sess-1?butler=general"')
    expect(html).toContain('href="/sessions/sess-2?butler=health"')
    expect(html).toContain('href="/sessions/sess-3?butler=switchboard"')
  })
})

// ---------------------------------------------------------------------------
// 6. limit prop — forwarding to useSessions
// ---------------------------------------------------------------------------

describe("RecentMoments — limit prop", () => {
  it("default limit is 7 (skeleton count without explicit prop)", () => {
    mockQueryResult = { isPending: true, data: undefined }
    const html = render()
    const matches = html.match(/aria-hidden="true"/g)
    expect(matches).toHaveLength(7)
  })

  it("respects custom limit=5 for skeleton count", () => {
    mockQueryResult = { isPending: true, data: undefined }
    const html = render({ limit: 5 })
    const matches = html.match(/aria-hidden="true"/g)
    expect(matches).toHaveLength(5)
  })

  it("respects custom limit=10 for skeleton count", () => {
    mockQueryResult = { isPending: true, data: undefined }
    const html = render({ limit: 10 })
    const matches = html.match(/aria-hidden="true"/g)
    expect(matches).toHaveLength(10)
  })

  it("forwards limit prop to useSessions as the query param", () => {
    mockQueryResult = { isPending: true, data: undefined }
    render({ limit: 5 })
    expect(lastSessionsArgs[0]).toMatchObject({ limit: 5 })
  })

  it("forwards default limit=7 to useSessions when no prop given", () => {
    mockQueryResult = { isPending: true, data: undefined }
    render()
    expect(lastSessionsArgs[0]).toMatchObject({ limit: 7 })
  })
})
