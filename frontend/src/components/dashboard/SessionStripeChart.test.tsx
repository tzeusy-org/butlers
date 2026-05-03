// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// SessionStripeChart tests — bu-2okpr.2
//
// Covers:
//   - Empty data state renders empty-state element
//   - Single-butler data produces correct pivot rows
//   - Multi-butler data distributes sessions across butler keys
//   - Time-bucket boundary: sessions exactly on the window boundary are included;
//     sessions outside are excluded
//   - Loading state renders skeleton
//   - Error state renders error element
//   - Recharts BarChart renders when data is present
// ---------------------------------------------------------------------------

import * as React from "react"
import { describe, expect, it, vi, beforeEach } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

// ---------------------------------------------------------------------------
// Mock TanStack Query — tests exercise pivot logic directly; network calls
// are tested via the hook under a separate integration harness.
// ---------------------------------------------------------------------------

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...original,
    useQuery: vi.fn(),
  }
})

// ---------------------------------------------------------------------------
// Mock recharts
// ---------------------------------------------------------------------------

vi.mock("recharts", () => {
  const BarChart = ({
    children,
  }: {
    data?: Array<Record<string, unknown>>
    children?: React.ReactNode
  }) => {
    return React.createElement("div", { "data-testid": "recharts-bar-chart" }, children)
  }

  const Bar = ({ dataKey }: { dataKey: string }) =>
    React.createElement("div", { "data-testid": `recharts-bar-${dataKey}` })

  const XAxis = () => null
  const YAxis = () => null
  const Tooltip = () => null
  const Legend = () => null
  const ResponsiveContainer = ({ children }: { children?: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "recharts-responsive-container" }, children)

  return { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer }
})

// ---------------------------------------------------------------------------
// Imports under test (after mock registration)
// ---------------------------------------------------------------------------

import { useQuery } from "@tanstack/react-query"
import { SessionStripeChart } from "./SessionStripeChart"
import { pivotSessionsIntoRows } from "./session-stripe-utils"
import type { ButlerSummary } from "@/api/types"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const mockUseQuery = vi.mocked(useQuery)

function makeButler(name: string): ButlerSummary {
  return { name, status: "ok", port: 41200, type: "butler" }
}

function renderChart(props: Parameters<typeof SessionStripeChart>[0]): string {
  return renderToStaticMarkup(<SessionStripeChart {...props} />)
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.resetAllMocks()
})

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("SessionStripeChart — loading state", () => {
  it("renders skeleton while loading", () => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as ReturnType<typeof useQuery>)

    const html = renderChart({ butlers: [] })
    expect(html).toContain("session-stripe-skeleton")
  })

  it("does not render the chart while loading", () => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as ReturnType<typeof useQuery>)

    const html = renderChart({ butlers: [] })
    expect(html).not.toContain("recharts-bar-chart")
  })
})

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("SessionStripeChart — error state", () => {
  it("renders error element on fetch failure", () => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as ReturnType<typeof useQuery>)

    const html = renderChart({ butlers: [] })
    expect(html).toContain("session-stripe-error")
  })
})

// ---------------------------------------------------------------------------
// Empty data state
// ---------------------------------------------------------------------------

describe("SessionStripeChart — empty data state", () => {
  it("renders the empty-state element when sessions is empty", () => {
    mockUseQuery.mockReturnValue({
      data: { data: [], meta: { total: 0, offset: 0, limit: 2000, has_more: false } },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useQuery>)

    const html = renderChart({ butlers: [] })
    expect(html).toContain("session-stripe-empty")
  })

  it("does NOT render the chart when sessions is empty", () => {
    mockUseQuery.mockReturnValue({
      data: { data: [], meta: { total: 0, offset: 0, limit: 2000, has_more: false } },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useQuery>)

    const html = renderChart({ butlers: [] })
    expect(html).not.toContain("recharts-bar-chart")
  })
})

// ---------------------------------------------------------------------------
// Chart renders with data
// ---------------------------------------------------------------------------

describe("SessionStripeChart — renders with data", () => {
  it("renders the bar chart container when sessions are non-empty", () => {
    mockUseQuery.mockReturnValue({
      data: {
        data: [{ id: "s1", butler: "home", started_at: "2024-06-15T10:30:00.000Z" }],
        meta: { total: 1, offset: 0, limit: 2000, has_more: false },
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useQuery>)

    const html = renderChart({ butlers: [makeButler("home")] })
    expect(html).toContain("session-stripe-chart")
    expect(html).toContain("recharts-bar-chart")
  })

  it("renders a Bar element per present butler", () => {
    mockUseQuery.mockReturnValue({
      data: {
        data: [
          { id: "s1", butler: "home", started_at: "2024-06-15T10:30:00.000Z" },
          { id: "s2", butler: "email", started_at: "2024-06-15T11:00:00.000Z" },
        ],
        meta: { total: 2, offset: 0, limit: 2000, has_more: false },
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useQuery>)

    const html = renderChart({
      butlers: [makeButler("home"), makeButler("email")],
    })
    expect(html).toContain("recharts-bar-home")
    expect(html).toContain("recharts-bar-email")
  })
})

// ---------------------------------------------------------------------------
// pivotSessionsIntoRows — unit tests (pure function, no mocks needed)
// ---------------------------------------------------------------------------

describe("pivotSessionsIntoRows — single-butler", () => {
  it("places a session in the correct hourly bucket", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T12:00:00.000Z")
    const sessions = [{ butler: "home", started_at: "2024-06-15T09:45:00.000Z" }]

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")

    // Bucket for 09:00 UTC
    const row = rows.find((r) => r.bucket === "2024-06-15T09")
    expect(row).toBeDefined()
    expect(row!["home"]).toBe(1)
  })

  it("accumulates multiple sessions in the same bucket", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T12:00:00.000Z")
    const sessions = [
      { butler: "home", started_at: "2024-06-15T10:00:00.000Z" },
      { butler: "home", started_at: "2024-06-15T10:30:00.000Z" },
      { butler: "home", started_at: "2024-06-15T10:55:00.000Z" },
    ]

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")
    const row = rows.find((r) => r.bucket === "2024-06-15T10")
    expect(row!["home"]).toBe(3)
  })

  it("generates all expected bucket rows for the window", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T10:00:00.000Z")
    const sessions: Array<{ butler: string; started_at: string }> = []

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")
    // Expect buckets: 08, 09, 10 = 3 rows
    expect(rows).toHaveLength(3)
    expect(rows.map((r) => r.bucket)).toEqual([
      "2024-06-15T08",
      "2024-06-15T09",
      "2024-06-15T10",
    ])
  })
})

describe("pivotSessionsIntoRows — multi-butler", () => {
  it("distributes sessions across butler keys in the same bucket", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T10:00:00.000Z")
    const sessions = [
      { butler: "home", started_at: "2024-06-15T08:10:00.000Z" },
      { butler: "email", started_at: "2024-06-15T08:50:00.000Z" },
      { butler: "home", started_at: "2024-06-15T09:20:00.000Z" },
    ]

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")

    const row08 = rows.find((r) => r.bucket === "2024-06-15T08")
    expect(row08!["home"]).toBe(1)
    expect(row08!["email"]).toBe(1)

    const row09 = rows.find((r) => r.bucket === "2024-06-15T09")
    expect(row09!["home"]).toBe(1)
    expect(row09!["email"]).toBeUndefined()
  })
})

describe("pivotSessionsIntoRows — time-bucket boundary", () => {
  it("includes a session exactly on the from boundary", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T10:00:00.000Z")
    const sessions = [{ butler: "home", started_at: "2024-06-15T08:00:00.000Z" }]

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")
    const row = rows.find((r) => r.bucket === "2024-06-15T08")
    expect(row!["home"]).toBe(1)
  })

  it("includes a session exactly on the to boundary", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T10:00:00.000Z")
    const sessions = [{ butler: "home", started_at: "2024-06-15T10:00:00.000Z" }]

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")
    const row = rows.find((r) => r.bucket === "2024-06-15T10")
    expect(row!["home"]).toBe(1)
  })

  it("excludes a session before the from boundary", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T10:00:00.000Z")
    // Session starts at 07:59 — rounds to hour 07, which is outside the window
    const sessions = [{ butler: "home", started_at: "2024-06-15T07:59:00.000Z" }]

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")
    // Row for bucket 07 does not exist in the range
    const row07 = rows.find((r) => r.bucket === "2024-06-15T07")
    expect(row07).toBeUndefined()
    // And the 08 bucket has no count from that session
    const row08 = rows.find((r) => r.bucket === "2024-06-15T08")
    expect(row08!["home"]).toBeUndefined()
  })

  it("excludes a session after the to boundary", () => {
    const from = new Date("2024-06-15T08:00:00.000Z")
    const to = new Date("2024-06-15T10:00:00.000Z")
    const sessions = [{ butler: "home", started_at: "2024-06-15T11:00:00.000Z" }]

    const rows = pivotSessionsIntoRows(sessions, from, to, "hour")
    const row11 = rows.find((r) => r.bucket === "2024-06-15T11")
    expect(row11).toBeUndefined()
  })

  it("uses daily buckets for windows longer than 48 hours", () => {
    const from = new Date("2024-06-10T00:00:00.000Z")
    const to = new Date("2024-06-15T00:00:00.000Z")
    const sessions = [{ butler: "home", started_at: "2024-06-12T14:00:00.000Z" }]

    const rows = pivotSessionsIntoRows(sessions, from, to, "day")
    const row = rows.find((r) => r.bucket === "2024-06-12")
    expect(row!["home"]).toBe(1)
  })
})
