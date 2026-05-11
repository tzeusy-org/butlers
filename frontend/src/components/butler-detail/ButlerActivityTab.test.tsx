// @vitest-environment jsdom
/**
 * ButlerActivityTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders
 *  - Loading state: skeletons appear, KPI cells show "…"
 *  - Empty state: kind breakdown shows empty message
 *  - All 3 ranges: 24h (ActivityStripe), 7d (DayBars), 30d (DayBars)
 *  - Error from one of the 4 data sources shows ErrorLine
 *  - KPI quartet renders sessions count, p50, p95, errors
 *  - RangeToggle is present and interactive
 *
 * bead: bu-iuol4.16
 */

import {
  afterEach,
  beforeAll,
  afterAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createElement } from "react"

import ButlerActivityTab from "./ButlerActivityTab"

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerHourlyActivity: vi.fn(),
  useButlerDailyActivity: vi.fn(),
  useButlerSessionKinds: vi.fn(),
  useButlerLatencyStats: vi.fn(),
}))

// Stub ActivityStripe and DayBars to avoid SVG/canvas complexity
vi.mock("@/components/butlers/ActivityStripe", () => ({
  ActivityStripe: ({ counts }: { counts: number[] }) =>
    createElement("div", { "data-testid": "activity-stripe", "aria-label": `stripe-${counts.length}` }),
}))

vi.mock("@/components/butlers/DayBars", () => ({
  DayBars: ({ data }: { data: number[] }) =>
    createElement("div", { "data-testid": "day-bars", "aria-label": `bars-${data.length}` }),
}))

import {
  useButlerHourlyActivity,
  useButlerDailyActivity,
  useButlerSessionKinds,
  useButlerLatencyStats,
} from "@/hooks/use-butler-analytics"

// ---------------------------------------------------------------------------
// Fixed clock
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-11T12:00:00.000Z"

beforeAll(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(FIXED_NOW_ISO))
})

afterAll(() => {
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const HOURLY_BUCKETS = Array.from({ length: 24 }, (_, i) => ({
  hour_start: new Date(Date.now() - i * 3600 * 1000).toISOString(),
  sessions_count: i % 3 === 0 ? 2 : 0,
  hour_index: i,
}))

const DAILY_BUCKETS_7D = Array.from({ length: 5 }, (_, i) => ({
  date: new Date(Date.now() - i * 86400 * 1000).toISOString().slice(0, 10),
  sessions_count: i + 1,
}))

const DAILY_BUCKETS_30D = Array.from({ length: 20 }, (_, i) => ({
  date: new Date(Date.now() - i * 86400 * 1000).toISOString().slice(0, 10),
  sessions_count: i + 1,
}))

const SESSION_KINDS_DATA = [
  { kind: "cron", count: 12 },
  { kind: "manual", count: 3 },
  { kind: "webhook", count: 5 },
]

const SESSION_KINDS_WITH_ERRORS = [
  { kind: "cron", count: 10 },
  { kind: "error", count: 2 },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function renderTab(butlerName = "test-butler") {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerActivityTab butlerName={butlerName} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

// ---------------------------------------------------------------------------
// Default mock: all data loaded, 24h range
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useButlerHourlyActivity).mockReturnValue({
    data: { data: { buckets: HOURLY_BUCKETS } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerHourlyActivity>)

  vi.mocked(useButlerDailyActivity).mockReturnValue({
    data: { data: { buckets: DAILY_BUCKETS_7D } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerDailyActivity>)

  vi.mocked(useButlerSessionKinds).mockReturnValue({
    data: { data: { kinds: SESSION_KINDS_DATA } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerSessionKinds>)

  vi.mocked(useButlerLatencyStats).mockReturnValue({
    data: null,
    isLoading: false,
    isError: false,
    isAvailable: false,
  })
}

function setupLoading() {
  vi.mocked(useButlerHourlyActivity).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useButlerHourlyActivity>)

  vi.mocked(useButlerDailyActivity).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useButlerDailyActivity>)

  vi.mocked(useButlerSessionKinds).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useButlerSessionKinds>)

  vi.mocked(useButlerLatencyStats).mockReturnValue({
    data: null,
    isLoading: false,
    isError: false,
    isAvailable: false,
  })
}

function setupEmpty() {
  vi.mocked(useButlerHourlyActivity).mockReturnValue({
    data: { data: { buckets: [] } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerHourlyActivity>)

  vi.mocked(useButlerDailyActivity).mockReturnValue({
    data: { data: { buckets: [] } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerDailyActivity>)

  vi.mocked(useButlerSessionKinds).mockReturnValue({
    data: { data: { kinds: [] } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerSessionKinds>)

  vi.mocked(useButlerLatencyStats).mockReturnValue({
    data: null,
    isLoading: false,
    isError: false,
    isAvailable: false,
  })
}

// ---------------------------------------------------------------------------
// Tests: Root container + section presence
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — all sections present", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupWithData()
  })
  afterEach(() => cleanup())

  it("renders the root tab container", () => {
    renderTab()
    expect(screen.getByTestId("butler-activity-tab")).toBeDefined()
  })

  it("renders the KPI panel", () => {
    renderTab()
    expect(screen.getByTestId("activity-kpi-panel")).toBeDefined()
  })

  it("renders the activity chart panel", () => {
    renderTab()
    expect(screen.getByTestId("activity-chart-panel")).toBeDefined()
  })

  it("renders the kind breakdown panel", () => {
    renderTab()
    expect(screen.getByTestId("activity-kind-panel")).toBeDefined()
  })

  it("renders kind breakdown rows in loaded state", () => {
    renderTab()
    const rows = screen.getAllByTestId("kind-breakdown-row")
    expect(rows.length).toBe(3)
  })

  it("renders the RangeToggle", () => {
    renderTab()
    expect(screen.getByRole("group", { name: "Time range" })).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// Tests: Loading state
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupLoading()
  })
  afterEach(() => cleanup())

  it("shows loading skeleton in activity chart panel", () => {
    renderTab()
    const loadingLines = screen.getAllByTestId("loading-line")
    expect(loadingLines.length).toBeGreaterThanOrEqual(1)
  })

  it("shows '…' in sessions KPI cell while loading", () => {
    renderTab()
    expect(screen.getByTestId("kpi-sessions").textContent).toBe("…")
  })

  it("shows '…' in errors KPI cell while loading", () => {
    renderTab()
    expect(screen.getByTestId("kpi-errors").textContent).toBe("…")
  })
})

// ---------------------------------------------------------------------------
// Tests: Empty state
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — empty state", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupEmpty()
  })
  afterEach(() => cleanup())

  it("shows empty state text in kind breakdown", () => {
    renderTab()
    expect(screen.getByTestId("empty-state-line")).toBeDefined()
    expect(screen.getByText("No sessions in this window.")).toBeDefined()
  })

  it("does not render kind breakdown rows", () => {
    renderTab()
    expect(screen.queryAllByTestId("kind-breakdown-row").length).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Tests: 24h range (ActivityStripe)
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — 24h range", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupWithData()
  })
  afterEach(() => cleanup())

  it("renders ActivityStripe in 24h mode", () => {
    renderTab()
    expect(screen.getByTestId("activity-stripe")).toBeDefined()
    expect(screen.queryByTestId("day-bars")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Tests: 7d range (DayBars)
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — 7d range", () => {
  beforeEach(() => {
    vi.resetAllMocks()

    vi.mocked(useButlerHourlyActivity).mockReturnValue({
      data: { data: { buckets: HOURLY_BUCKETS } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerHourlyActivity>)

    vi.mocked(useButlerDailyActivity).mockReturnValue({
      data: { data: { buckets: DAILY_BUCKETS_7D } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerDailyActivity>)

    vi.mocked(useButlerSessionKinds).mockReturnValue({
      data: { data: { kinds: SESSION_KINDS_DATA } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerSessionKinds>)

    vi.mocked(useButlerLatencyStats).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      isAvailable: false,
    })
  })
  afterEach(() => cleanup())

  it("switches to DayBars when 7D is selected", () => {
    renderTab()
    const btn = screen.getByRole("button", { name: "7D" })
    fireEvent.click(btn)
    expect(screen.getByTestId("day-bars")).toBeDefined()
    expect(screen.queryByTestId("activity-stripe")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Tests: 30d range (DayBars)
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — 30d range", () => {
  beforeEach(() => {
    vi.resetAllMocks()

    vi.mocked(useButlerHourlyActivity).mockReturnValue({
      data: { data: { buckets: HOURLY_BUCKETS } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerHourlyActivity>)

    vi.mocked(useButlerDailyActivity).mockReturnValue({
      data: { data: { buckets: DAILY_BUCKETS_30D } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerDailyActivity>)

    vi.mocked(useButlerSessionKinds).mockReturnValue({
      data: { data: { kinds: SESSION_KINDS_DATA } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerSessionKinds>)

    vi.mocked(useButlerLatencyStats).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      isAvailable: false,
    })
  })
  afterEach(() => cleanup())

  it("switches to DayBars when 30D is selected", () => {
    renderTab()
    const btn = screen.getByRole("button", { name: "30D" })
    fireEvent.click(btn)
    expect(screen.getByTestId("day-bars")).toBeDefined()
    expect(screen.queryByTestId("activity-stripe")).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Tests: Error from one endpoint
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — error state (session-kinds fails)", () => {
  afterEach(() => cleanup())

  it("shows ErrorLine in KPI panel when session-kinds errors", () => {
    vi.resetAllMocks()

    vi.mocked(useButlerHourlyActivity).mockReturnValue({
      data: { data: { buckets: HOURLY_BUCKETS } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerHourlyActivity>)

    vi.mocked(useButlerDailyActivity).mockReturnValue({
      data: { data: { buckets: DAILY_BUCKETS_7D } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerDailyActivity>)

    vi.mocked(useButlerSessionKinds).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useButlerSessionKinds>)

    vi.mocked(useButlerLatencyStats).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      isAvailable: false,
    })

    renderTab()
    const errorLines = screen.getAllByTestId("error-state-line")
    expect(errorLines.length).toBeGreaterThanOrEqual(1)
  })
})

describe("ButlerActivityTab — error state (hourly-activity fails)", () => {
  afterEach(() => cleanup())

  it("shows ErrorLine in activity panel when hourly-activity errors", () => {
    vi.resetAllMocks()

    vi.mocked(useButlerHourlyActivity).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useButlerHourlyActivity>)

    vi.mocked(useButlerDailyActivity).mockReturnValue({
      data: { data: { buckets: DAILY_BUCKETS_7D } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerDailyActivity>)

    vi.mocked(useButlerSessionKinds).mockReturnValue({
      data: { data: { kinds: SESSION_KINDS_DATA } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerSessionKinds>)

    vi.mocked(useButlerLatencyStats).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      isAvailable: false,
    })

    renderTab()
    // Should show the ErrorLine for activity panel
    const errorLines = screen.getAllByTestId("error-state-line")
    expect(errorLines.length).toBeGreaterThanOrEqual(1)
  })
})

// ---------------------------------------------------------------------------
// Tests: KPI values
// ---------------------------------------------------------------------------

describe("ButlerActivityTab — KPI values", () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setupWithData()
  })
  afterEach(() => cleanup())

  it("shows correct sessions count (sum of all kinds)", () => {
    renderTab()
    // 12 + 3 + 5 = 20
    expect(screen.getByTestId("kpi-sessions").textContent).toBe("20")
  })

  it("shows '—' for p50 when latency-stats endpoint is unavailable", () => {
    renderTab()
    expect(screen.getByTestId("kpi-p50").textContent).toBe("—")
  })

  it("shows '—' for p95 when latency-stats endpoint is unavailable", () => {
    renderTab()
    expect(screen.getByTestId("kpi-p95").textContent).toBe("—")
  })

  it("shows '—' for errors count when no error kind in session-kinds", () => {
    renderTab()
    // SESSION_KINDS_DATA has no 'error' kind
    expect(screen.getByTestId("kpi-errors").textContent).toBe("—")
  })

  it("shows errors count when error kind is present in session-kinds", () => {
    vi.mocked(useButlerSessionKinds).mockReturnValue({
      data: { data: { kinds: SESSION_KINDS_WITH_ERRORS } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useButlerSessionKinds>)

    renderTab()
    expect(screen.getByTestId("kpi-errors").textContent).toBe("2")
  })
})
