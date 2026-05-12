// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// ButlerDetailFooter.test.tsx — unit tests for the per-butler KPI band.
// (bu-ja5bt.4)
//
// Spec scenarios covered:
//   1. Four KPI cells render for the active butler
//   2. Partial-failure: one hook errors => affected cell(s) render placeholder
//   3. Last activity uses <Time> component (not raw date string)
//   4. KpiCell atom is reused (four KpiCell instances in the rendered band)
// ---------------------------------------------------------------------------

import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createElement } from "react"

// ---------------------------------------------------------------------------
// Mock hooks BEFORE importing the component under test
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
  useRuntimeConfig: vi.fn(),
}))

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(),
}))

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(),
}))

// Stub <Time> to avoid timezone / date-fns complexity in jsdom.
// The stub emits a <time> element with a data-testid so tests can assert its presence.
vi.mock("@/components/ui/time", () => ({
  Time: ({ value, className }: { value: string; className?: string }) =>
    createElement("time", { dateTime: value, "data-testid": "time-component", className }, value),
}))

import { useButlers, useRuntimeConfig } from "@/hooks/use-butlers"
import { useButlerHeartbeats } from "@/hooks/use-system"
import { useCostSummary } from "@/hooks/use-costs"
import { ButlerDetailFooter } from "./ButlerDetailFooter"

// ---------------------------------------------------------------------------
// Fixed clock
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-12T10:00:00.000Z"
const LAST_RUN_ISO = "2026-05-12T09:45:00.000Z"

beforeAll(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(FIXED_NOW_ISO))
})

afterAll(() => {
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function renderFooter(butlerName = "relationship") {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <ButlerDetailFooter butler={butlerName} />
    </QueryClientProvider>,
  )
}

/** Configure all four data sources with happy-path data for "relationship". */
function setupAllHappy() {
  vi.mocked(useButlers).mockReturnValue({
    data: {
      data: [
        {
          name: "relationship",
          sessions_24h: 7,
          status: "healthy",
          type: "butler",
          description: "Relationship butler",
        },
      ],
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlers>)

  vi.mocked(useCostSummary).mockReturnValue({
    data: {
      data: {
        total_cost_usd: 3.5,
        total_sessions: 10,
        total_input_tokens: 1000,
        total_output_tokens: 500,
        by_butler: { relationship: 1.23 },
        by_model: {},
      },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useCostSummary>)

  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: {
      data: {
        butlers: [
          {
            name: "relationship",
            last_session_at: LAST_RUN_ISO,
            active_session_count: 1,
            heartbeat_age_seconds: 30,
          },
        ],
      },
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerHeartbeats>)

  vi.mocked(useRuntimeConfig).mockReturnValue({
    data: { max_concurrent: 2, model: "claude-sonnet-4-6" },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useRuntimeConfig>)
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  setupAllHappy()
})

// ---------------------------------------------------------------------------
// Scenario 1: Four KPI cells render for the active butler
// ---------------------------------------------------------------------------

describe("Scenario 1: Four KPI cells render for the active butler", () => {
  it("renders exactly four KpiCell label texts", () => {
    renderFooter("relationship")
    // KpiCell renders its label via MonoLabel; each label is unique text.
    expect(screen.getByText("Sessions 24h")).toBeDefined()
    expect(screen.getByText("Spend today")).toBeDefined()
    expect(screen.getByText("Load")).toBeDefined()
    expect(screen.getByText("Last activity")).toBeDefined()
  })

  it("sessions 24h cell is scoped to the active butler (not fleet total)", () => {
    renderFooter("relationship")
    // value = "7" (from relationship row), not sum of all butlers
    expect(screen.getByText("7")).toBeDefined()
  })

  it("spend today cell shows butler-scoped cost", () => {
    renderFooter("relationship")
    expect(screen.getByText("$1.23")).toBeDefined()
  })

  it("load% cell shows derived percentage", () => {
    // active_session_count=1, max_concurrent=2 => 50%
    renderFooter("relationship")
    expect(screen.getByText("50%")).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// Scenario 2: Partial-failure data renders a placeholder glyph
// ---------------------------------------------------------------------------

describe("Scenario 2: Partial-failure renders placeholder glyphs", () => {
  it("load% cell renders placeholder when max_concurrent is zero", () => {
    vi.mocked(useRuntimeConfig).mockReturnValue({
      data: { max_concurrent: 0, model: "claude-sonnet-4-6" },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRuntimeConfig>)

    renderFooter("relationship")
    // At least one "--" placeholder should appear
    const placeholders = screen.getAllByText("--")
    expect(placeholders.length).toBeGreaterThanOrEqual(1)
  })

  it("load% cell renders placeholder when max_concurrent is null", () => {
    vi.mocked(useRuntimeConfig).mockReturnValue({
      data: { max_concurrent: null, model: "claude-sonnet-4-6" },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRuntimeConfig>)

    renderFooter("relationship")
    const placeholders = screen.getAllByText("--")
    expect(placeholders.length).toBeGreaterThanOrEqual(1)
  })

  it("spend cell renders placeholder when cost data is unavailable", () => {
    vi.mocked(useCostSummary).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("network error"),
    } as unknown as ReturnType<typeof useCostSummary>)

    renderFooter("relationship")
    // spend placeholder; sessions + load may also render correctly
    const placeholders = screen.getAllByText("--")
    expect(placeholders.length).toBeGreaterThanOrEqual(1)
  })

  it("spend cell renders placeholder when butler has no cost entry", () => {
    vi.mocked(useCostSummary).mockReturnValue({
      data: {
        data: {
          total_cost_usd: 0,
          total_sessions: 0,
          total_input_tokens: 0,
          total_output_tokens: 0,
          by_butler: {},
          by_model: {},
        },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useCostSummary>)

    renderFooter("relationship")
    const placeholders = screen.getAllByText("--")
    expect(placeholders.length).toBeGreaterThanOrEqual(1)
  })

  it("last activity cell renders placeholder when heartbeats error", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("heartbeat error"),
    } as unknown as ReturnType<typeof useButlerHeartbeats>)

    renderFooter("relationship")
    // Last activity placeholder; other cells remain valid
    const placeholders = screen.getAllByText("--")
    expect(placeholders.length).toBeGreaterThanOrEqual(1)
  })

  it("other cells remain when only the heartbeats hook errors", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("heartbeat error"),
    } as unknown as ReturnType<typeof useButlerHeartbeats>)

    renderFooter("relationship")
    // sessions and spend should still render their values
    expect(screen.getByText("7")).toBeDefined()
    expect(screen.getByText("$1.23")).toBeDefined()
    // Labels are still present
    expect(screen.getByText("Sessions 24h")).toBeDefined()
    expect(screen.getByText("Spend today")).toBeDefined()
    expect(screen.getByText("Load")).toBeDefined()
    expect(screen.getByText("Last activity")).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// Scenario 3: Last activity uses the <Time> component
// ---------------------------------------------------------------------------

describe("Scenario 3: Last activity uses Time component", () => {
  it("renders a <time> element (from the stubbed Time component)", () => {
    renderFooter("relationship")
    const timeEl = screen.getByTestId("time-component")
    expect(timeEl).toBeDefined()
  })

  it("time element has dateTime attribute equal to the last_session_at ISO value", () => {
    renderFooter("relationship")
    const timeEl = screen.getByTestId("time-component") as HTMLTimeElement
    expect(timeEl.getAttribute("dateTime")).toBe(LAST_RUN_ISO)
  })

  it("does NOT render raw toLocaleString or toISOString output in last activity cell", () => {
    renderFooter("relationship")
    // The stub renders value as-is (the ISO string itself), not a locale string.
    // The key assertion: no Date.prototype.toLocaleString() variant leaks out.
    // We verify by checking the <time> element's text matches the ISO string,
    // meaning the component delegated to <Time> and not to toLocaleString().
    const timeEl = screen.getByTestId("time-component")
    expect(timeEl.textContent).toBe(LAST_RUN_ISO)
  })

  it("renders placeholder (not a <time> element) when last_session_at is null", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: {
        data: {
          butlers: [
            {
              name: "relationship",
              last_session_at: null,
              active_session_count: 0,
              heartbeat_age_seconds: null,
            },
          ],
        },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerHeartbeats>)

    renderFooter("relationship")
    expect(screen.queryByTestId("time-component")).toBeNull()
    const placeholders = screen.getAllByText("--")
    expect(placeholders.length).toBeGreaterThanOrEqual(1)
  })
})

// ---------------------------------------------------------------------------
// Scenario 4: KpiCell atom is reused
// ---------------------------------------------------------------------------

describe("Scenario 4: KpiCell atom is reused", () => {
  it("renders exactly four MonoLabel eyebrow elements (one per KpiCell)", () => {
    const { container } = renderFooter("relationship")
    // KpiCell renders a MonoLabel which carries the classes: font-mono uppercase tracking-wider tnum
    // The label text for each of our 4 cells is distinct — count via label texts.
    const labels = ["Sessions 24h", "Spend today", "Load", "Last activity"]
    for (const label of labels) {
      expect(screen.getByText(label)).toBeDefined()
    }
    expect(labels.length).toBe(4)
    // All four label spans are within the footer element
    const footer = container.querySelector("footer")
    expect(footer).not.toBeNull()
    // Verify 4 MonoLabel spans by their shared classes
    const monoLabels = footer!.querySelectorAll("span.font-mono.uppercase.tracking-wider")
    expect(monoLabels.length).toBe(4)
  })

  it("all four cells use tnum (font-variant-numeric) on the value", () => {
    const { container } = renderFooter("relationship")
    // KpiCell renders the value in a span with classes including "tnum"
    // We expect 4 value spans + MonoLabels also have tnum — filter by font-medium
    const valueSpans = container.querySelectorAll("span.tnum.font-medium")
    expect(valueSpans.length).toBe(4)
  })
})
