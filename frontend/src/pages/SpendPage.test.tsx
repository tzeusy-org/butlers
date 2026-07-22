/**
 * SpendPage — /spend  [bu-86c4c.11, JARVIS audit move 8]
 *
 * Covers the merged surface (was /costs + /settings/spend):
 *   - Posture: KPI strip renders MTD / projected EOM / ceiling / days-in-month
 *   - Ceiling-update flow: PUT /spend/ceiling, KPI strip re-renders
 *   - Movers strip: ranks butler deltas between the current and prior window
 *     honestly (new butlers, stopped butlers, no fabricated $0 rows)
 *   - Honest per-butler-per-day chart: CostStripeChart is mounted with the
 *     real by_butler-bearing daily series (recharts internals covered by
 *     CostStripeChart.test.tsx directly)
 *   - Evidence layer: Top Sessions and By Schedule sections render
 *   - Routing-rules create-rule flow (ported from the former
 *     SettingsSpendPage.test.tsx — same evaluator-shaped payload contract)
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, cleanup, fireEvent, screen, act, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const apiFetchMock = vi.fn()

vi.mock("@/api/client", () => ({
  apiFetch: (...args: Parameters<typeof import("@/api/client").apiFetch>) => apiFetchMock(...args),
}))

const mockUseSpendTicker = vi.fn()

vi.mock("@/hooks/use-spend-ticker", () => ({
  useSpendTicker: () => mockUseSpendTicker(),
}))

const mockUseFleetHaltStatus = vi.fn()

vi.mock("@/hooks/use-fleet-halt", () => ({
  useFleetHaltStatus: () => mockUseFleetHaltStatus(),
}))

// BreakdownSection/SpendRulesSection/the posture forecast query now call
// useBusAwarePollInterval directly (bu-01r64.4), which reads the real
// EventBusProvider context via useContext -- invalid without a provider in
// the render tree. Stub the bus as always "open" (same pattern as
// use-issues.test.ts / SessionStripeChart.test.tsx), giving every test here
// the reconciliation cadence.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", lastEventAt: null, subscribe: vi.fn() }),
}))

vi.mock("@/hooks/use-model-catalog", () => ({
  useModelCatalog: () => ({
    data: {
      data: [
        { id: "1", model_id: "claude-haiku", complexity_tier: "cheap" },
        { id: "2", model_id: "claude-sonnet", complexity_tier: "workhorse" },
      ],
    },
  }),
}))

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}))

// Avoid recharts/ResponsiveContainer SSR complexity — CostStripeChart's own
// stacking/tooltip/legend behavior is covered directly by
// CostStripeChart.test.tsx. Here we only assert SpendPage wires the real
// daily series into it.
vi.mock("@/components/costs/CostStripeChart", () => ({
  CostStripeChart: (props: {
    data: Array<{ date: string; by_butler?: Record<string, number> }>
    unavailableButlers?: readonly string[]
    sourceError?: boolean
  }) => (
    <div
      data-testid="cost-stripe-chart-mock"
      data-unavailable={JSON.stringify(props.unavailableButlers ?? [])}
      data-source-error={String(props.sourceError ?? false)}
    >
      {JSON.stringify(props.data)}
    </div>
  ),
}))

const mockUseSpendSummary = vi.fn()
const mockUseDailySpend = vi.fn()
const mockUseTopSessions = vi.fn()
const mockUseCostsBySchedule = vi.fn()

vi.mock("@/hooks/use-spend", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-spend")>()
  return {
    ...actual,
    useSpendSummary: (...args: unknown[]) => mockUseSpendSummary(...args),
    useDailySpend: (...args: unknown[]) => mockUseDailySpend(...args),
    useTopSessions: (...args: unknown[]) => mockUseTopSessions(...args),
    useCostsBySchedule: (...args: unknown[]) => mockUseCostsBySchedule(...args),
  }
})

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import SpendPage from "@/pages/SpendPage"

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const DAYS_ELAPSED = 17
const DAYS_IN_MONTH = 31

function buildForecastDays() {
  const days = []
  for (let i = 1; i <= DAYS_ELAPSED; i++) {
    days.push({ date: `2026-05-${String(i).padStart(2, "0")}`, cost_usd: 0.5 + i * 0.1, projected: false })
  }
  for (let i = DAYS_ELAPSED + 1; i <= DAYS_IN_MONTH; i++) {
    days.push({ date: `2026-05-${String(i).padStart(2, "0")}`, cost_usd: 0.5 + i * 0.1, projected: true })
  }
  return days
}

const MOCK_FORECAST = {
  data: {
    days: buildForecastDays(),
    projected_eom_usd: 5.42,
    days_in_month: DAYS_IN_MONTH,
    days_elapsed: DAYS_ELAPSED,
    mtd_usd: 2.2,
    ceiling_usd: null,
  },
  meta: {},
}

const MOCK_FORECAST_WITH_CEILING = {
  ...MOCK_FORECAST,
  data: { ...MOCK_FORECAST.data, ceiling_usd: 10.0 },
}

const MOCK_BREAKDOWN = {
  data: { by: "butler", breakdown: { inbox: 1.5, calendar: 0.7 } },
  meta: {},
}

const MOCK_RULES = { data: [], meta: {} }

const DAILY_DATA = [
  {
    date: "2026-05-16",
    cost_usd: 0.6,
    sessions: 20,
    input_tokens: 50_000,
    output_tokens: 25_000,
    by_butler: { general: 0.4, memory: 0.2 },
  },
  {
    date: "2026-05-17",
    cost_usd: 0.63,
    sessions: 22,
    input_tokens: 50_000,
    output_tokens: 25_000,
    by_butler: { general: 0.63 },
  },
]

function defaultApiFetch(path: string) {
  if (path === "/spend/forecast") return Promise.resolve(MOCK_FORECAST)
  if (path.startsWith("/spend/breakdown")) return Promise.resolve(MOCK_BREAKDOWN)
  if (path === "/spend/rules") return Promise.resolve(MOCK_RULES)
  return Promise.resolve({ data: {} })
}

function setHooks({
  currentByButler = {},
  priorByButler = {},
  currentUnavailable = [],
  priorUnavailable = [],
  currentUnpriced = [],
  priorUnpriced = [],
  currentError = false,
  priorError = false,
}: {
  currentByButler?: Record<string, number>
  priorByButler?: Record<string, number>
  currentUnavailable?: string[]
  priorUnavailable?: string[]
  currentUnpriced?: Array<{
    model: string
    calls: number
    input_tokens: number
    output_tokens: number
    cached_input_tokens: number
    cache_creation_tokens: number
  }>
  priorUnpriced?: Array<{
    model: string
    calls: number
    input_tokens: number
    output_tokens: number
    cached_input_tokens: number
    cache_creation_tokens: number
  }>
  currentError?: boolean
  priorError?: boolean
} = {}) {
  mockUseSpendTicker.mockReturnValue({ streamedCostUsd: 0, streamedUnpricedEvents: [] })
  // Fleet-halt inactive by default -- individual fleet-halt tests below
  // override this per-case (bu-7o89u.3).
  mockUseFleetHaltStatus.mockReturnValue({
    active: false,
    deniedToday: 0,
    deniedTotal: 0,
    since: null,
    recentAttempts: [],
    isLoading: false,
    isError: false,
  })

  // useSpendSummary is called twice per render: current window, prior window.
  let call = 0
  mockUseSpendSummary.mockImplementation(() => {
    call += 1
    const isCurrent = call % 2 === 1
    const byButler = isCurrent ? currentByButler : priorByButler
    const unavailableButlers = isCurrent ? currentUnavailable : priorUnavailable
    const unpricedModels = isCurrent ? currentUnpriced : priorUnpriced
    const isError = isCurrent ? currentError : priorError
    return {
      data: isError
        ? undefined
        : {
            data: {
              total_cost_usd: 1,
              by_butler: byButler,
              unavailable_butlers: unavailableButlers,
              unpriced_models: unpricedModels,
            },
            meta: {},
          },
      isLoading: false,
      isError,
    }
  })
  mockUseDailySpend.mockReturnValue({
    data: { data: DAILY_DATA, meta: {} },
    isLoading: false,
    isError: false,
  })
  mockUseTopSessions.mockReturnValue({
    data: {
      data: [
        {
          session_id: "sess-1",
          butler: "general",
          cost_usd: 1.2345,
          input_tokens: 5000,
          output_tokens: 2500,
          model: "claude-sonnet",
          started_at: "2026-05-17T10:00:00Z",
        },
      ],
      meta: {},
    },
    isLoading: false,
    isError: false,
  })
  mockUseCostsBySchedule.mockReturnValue({
    data: {
      data: [
        {
          schedule_name: "morning-briefing",
          butler: "general",
          cron: "0 7 * * *",
          total_runs: 30,
          total_cost_usd: 3.0,
          avg_cost_per_run: 0.1,
          runs_per_day: 1,
          projected_monthly_usd: 3.0,
        },
      ],
      meta: {},
    },
    isLoading: false,
    isError: false,
  })
}

function renderPage(initialEntries: string[] = ["/"]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <SpendPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SpendPage — posture", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("renders the KPI strip with MTD, projected EOM, ceiling, and days-in-month", async () => {
    await act(async () => {
      renderPage()
    })

    const mtdCell = await screen.findByTestId("kpi-mtd")
    expect(mtdCell.textContent).toContain("MTD Spend")
    expect(mtdCell.textContent).toContain("$2.20")

    const projCell = screen.getByTestId("kpi-projected-eom")
    expect(projCell.textContent).toContain("$5.42")

    const ceilingCell = screen.getByTestId("kpi-ceiling")
    expect(ceilingCell.textContent).toContain("—")

    const daysCell = screen.getByTestId("kpi-days-in-month")
    expect(daysCell.textContent).toContain(String(DAYS_IN_MONTH))
  })

  it("names unpriced ledger usage and makes the monthly-ceiling blind spot explicit", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        return Promise.resolve({
          data: {
            ...MOCK_FORECAST.data,
            ceiling_usd: 10,
            unpriced_models: [
              {
                model: "unpriced-codex",
                calls: 1988,
                input_tokens: 100,
                output_tokens: 50,
                cached_input_tokens: 0,
                cache_creation_tokens: 0,
              },
            ],
            ceiling_blind_to_unpriced_models: 1,
            divergences: [
              {
                date: "2026-05-17",
                butler: "general",
                ledger_tokens: 100,
                session_tokens: 80,
                difference_ratio: 0.2,
              },
            ],
            historical_attribution_note: "Legacy labels use requested models.",
          },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    expect((await screen.findByTestId("kpi-mtd")).textContent).toContain(
      "excludes 1,988 unpriced calls",
    )
    expect(screen.getByTestId("kpi-ceiling").textContent).toContain(
      "blind to 1 unpriced model",
    )
    expect((await screen.findByTestId("forecast-unpriced")).textContent).toContain(
      "unpriced-codex",
    )
    expect(screen.getByTestId("forecast-divergence").textContent).toContain(
      "ledger/session token drift",
    )
    expect(screen.getByTestId("forecast-historical-attribution").textContent).toContain(
      "Legacy labels",
    )
  })

  it("ceiling-update flow submits PUT and re-renders with the new ceiling", async () => {
    let ceilingSet = false
    apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/spend/ceiling" && init?.method === "PUT") {
        ceilingSet = true
        return Promise.resolve({ data: null, meta: {} })
      }
      if (path === "/spend/forecast") {
        return Promise.resolve(ceilingSet ? MOCK_FORECAST_WITH_CEILING : MOCK_FORECAST)
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    const setCeilingBtn = await screen.findByRole("button", { name: /set ceiling/i })
    await act(async () => {
      fireEvent.click(setCeilingBtn)
    })

    const ceilingInput = screen.getByRole("spinbutton")
    await act(async () => {
      fireEvent.change(ceilingInput, { target: { value: "10" } })
    })

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^save$/i }))
    })

    await waitFor(() => {
      const putCall = apiFetchMock.mock.calls.find(
        (c) => c[0] === "/spend/ceiling" && c[1]?.method === "PUT",
      )
      expect(putCall).toBeTruthy()
      expect(JSON.parse(putCall![1].body as string)).toMatchObject({ monthly_usd: 10 })
    })

    await waitFor(() => {
      expect(screen.getByTestId("kpi-ceiling").textContent).toContain("$10.00")
    })
  })
})

// ---------------------------------------------------------------------------
// Live MTD stream merge [bu-qvnce.2] — the stream counter is monotonic and
// never resets on its own, but every 120s poll refreshes the MTD baseline
// with a number that already includes any spend that streamed in before the
// poll landed. The merge must not add that same spend twice.
// ---------------------------------------------------------------------------

describe("SpendPage — live MTD stream merge", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("adds live streamed spend on top of the polled MTD baseline", async () => {
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
    mockUseSpendTicker.mockReturnValue({ streamedCostUsd: 0, streamedUnpricedEvents: [] })

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <SpendPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("kpi-mtd").textContent).toContain("$2.20")
    })

    // $3 of live spend streams in before the next poll.
    mockUseSpendTicker.mockReturnValue({ streamedCostUsd: 3, streamedUnpricedEvents: [] })
    rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <SpendPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(screen.getByTestId("kpi-mtd").textContent).toContain("$5.20")
    })
  })

  it("does not double-count streamed spend or unpriced usage once the next poll baseline reflects both", async () => {
    let forecastCalls = 0
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        forecastCalls += 1
        // The 2nd poll's baseline already includes the $3 that streamed
        // between the 1st poll and now -- ground truth is $5.20, not $8.20.
        const mtd = forecastCalls === 1 ? 2.2 : 5.2
        return Promise.resolve({
          data: {
            ...MOCK_FORECAST.data,
            mtd_usd: mtd,
            ...(forecastCalls === 2
              ? {
                  unpriced_models: [
                    {
                      model: "unpriced-live-model",
                      calls: 1,
                      input_tokens: 1000,
                      output_tokens: 500,
                      cached_input_tokens: 125,
                      cache_creation_tokens: 25,
                    },
                  ],
                  ceiling_blind_to_unpriced_models: 1,
                }
              : {}),
          },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })
    mockUseSpendTicker.mockReturnValue({ streamedCostUsd: 0, streamedUnpricedEvents: [] })

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <SpendPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("kpi-mtd").textContent).toContain("$2.20")
    })

    // $3 streams in live, ahead of the next poll.
    mockUseSpendTicker.mockReturnValue({
      streamedCostUsd: 3,
      streamedUnpricedEvents: [
        {
          model: "unpriced-live-model",
          input_tokens: 1000,
          output_tokens: 500,
          cached_input_tokens: 125,
          cache_creation_tokens: 25,
        },
      ],
    })
    rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <SpendPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("kpi-mtd").textContent).toContain("$5.20")
    })
    expect(screen.getByTestId("kpi-mtd").textContent).toContain("excludes 1 unpriced call")

    // The interval fires; the next poll lands with a baseline that already
    // reflects that $3 (simulate it directly rather than fake-timing 120s,
    // which deadlocks against RTL's own waitFor polling).
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["spend-forecast"] })
    })
    await waitFor(() => {
      expect(forecastCalls).toBe(2)
    })

    expect(screen.getByTestId("kpi-mtd").textContent).toContain("$5.20")
    expect(screen.getByTestId("kpi-mtd").textContent).not.toContain("$8.20")
    expect(screen.getByTestId("kpi-mtd").textContent).toContain("excludes 1 unpriced call")
    expect(screen.getByTestId("kpi-mtd").textContent).not.toContain("excludes 2 unpriced calls")
  })

  it("surfaces an unpriced live call instead of treating it as a complete zero-dollar total", async () => {
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
    mockUseSpendTicker.mockReturnValue({ streamedCostUsd: 0, streamedUnpricedEvents: [] })

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <SpendPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await waitFor(() => {
      expect(screen.getByTestId("kpi-mtd").textContent).toContain("$2.20")
    })

    mockUseSpendTicker.mockReturnValue({
      streamedCostUsd: 0,
      streamedUnpricedEvents: [
        {
          model: "unpriced-live-model",
          input_tokens: 1000,
          output_tokens: 500,
          cached_input_tokens: 125,
          cache_creation_tokens: 25,
        },
      ],
    })
    rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <SpendPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )

    await waitFor(() => {
      expect(screen.getByTestId("kpi-mtd").textContent).toContain("excludes 1 unpriced call")
    })
    expect(screen.getByTestId("forecast-unpriced").textContent).toContain(
      "blind to 1 unpriced model",
    )
    expect(screen.getByTestId("forecast-unpriced").textContent).toContain("unpriced-live-model")
  })
})

describe("SpendPage — what changed", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
  })

  afterEach(() => {
    cleanup()
  })

  it("renders the honest per-butler stacked chart with real by_butler data", async () => {
    setHooks()
    await act(async () => {
      renderPage()
    })

    const chart = await screen.findByTestId("cost-stripe-chart-mock")
    expect(chart.textContent).toContain("general")
    expect(chart.textContent).toContain("memory")
  })

  it("wires daily meta.unavailable_butlers into the stacked chart (bu-jad4j.3)", async () => {
    // A failed butler is dropped from GET /api/spend/daily's fan-out; the page
    // must forward meta.unavailable_butlers so the chart can footnote it rather
    // than let it silently vanish. (The footnote render itself is covered by
    // CostStripeChart.test.tsx.)
    setHooks()
    mockUseDailySpend.mockReturnValue({
      data: { data: DAILY_DATA, meta: { unavailable_butlers: ["finance"] } },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    const chart = await screen.findByTestId("cost-stripe-chart-mock")
    expect(JSON.parse(chart.getAttribute("data-unavailable") ?? "[]")).toEqual(["finance"])
  })

  it("passes no unavailable butlers to the chart on the happy path (bu-jad4j.3)", async () => {
    // Mutation guard: the wiring must reflect the flag. An all-clear daily
    // response forwards an empty list.
    setHooks()
    await act(async () => {
      renderPage()
    })
    const chart = await screen.findByTestId("cost-stripe-chart-mock")
    expect(JSON.parse(chart.getAttribute("data-unavailable") ?? "[]")).toEqual([])
  })

  it("passes a daily compatibility source_error to the chart rather than allowing its empty data state", async () => {
    setHooks()
    mockUseDailySpend.mockReturnValue({
      data: { data: [], meta: { source_error: true } },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    const chart = await screen.findByTestId("cost-stripe-chart-mock")
    expect(chart.getAttribute("data-source-error")).toBe("true")
  })

  it("footnotes unpriced daily ledger usage rather than rendering it as free", async () => {
    setHooks()
    mockUseDailySpend.mockReturnValue({
      data: {
        data: DAILY_DATA,
        meta: {
          unpriced_models: [
            {
              model: "unpriced-codex",
              calls: 3,
              input_tokens: 100,
              output_tokens: 50,
              cached_input_tokens: 0,
              cache_creation_tokens: 0,
            },
          ],
          divergences: [
            {
              date: "2026-05-17",
              butler: "general",
              ledger_tokens: 100,
              session_tokens: 80,
              difference_ratio: 0.2,
            },
          ],
        },
      },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    expect((await screen.findByTestId("daily-spend-unpriced")).textContent).toContain(
      "excludes 3 unpriced calls",
    )
    expect(screen.getByTestId("daily-spend-divergence").textContent).toContain(
      "ledger/session token drift",
    )
  })

  it("ranks movers by delta vs the prior window, marking new butlers honestly", async () => {
    setHooks({
      currentByButler: { general: 1.0, memory: 0.5 },
      priorByButler: { general: 0.2 },
    })
    await act(async () => {
      renderPage()
    })

    const strip = await screen.findByTestId("movers-strip")
    const chips = strip.querySelectorAll('[data-testid="mover-chip"]')
    // general: delta = 0.8 (largest); memory: delta = 0.5, prior=0 -> "new"
    expect(chips.length).toBe(2)
    expect(chips[0].textContent).toContain("general")
    expect(chips[1].textContent).toContain("memory")
    expect(chips[1].textContent).toContain("new")
  })

  it("shows an honest empty state when nothing changed vs the prior window", async () => {
    setHooks({ currentByButler: { general: 1.0 }, priorByButler: { general: 1.0 } })
    await act(async () => {
      renderPage()
    })

    const strip = await screen.findByTestId("movers-strip")
    expect(strip.textContent).toContain("No spend change vs the prior window")
  })

  it("shows a degraded note instead of a false all-clear when a comparison window fails (bu-qvnce.1)", async () => {
    setHooks({ currentByButler: { general: 1.0 }, priorError: true })
    await act(async () => {
      renderPage()
    })

    const strip = await screen.findByTestId("movers-strip")
    expect(strip.textContent).not.toContain("No spend change vs the prior window")
    expect(strip.textContent).toContain("spend comparison unavailable")
    expect(strip.querySelectorAll('[data-testid="mover-chip"]').length).toBe(0)
  })

  it("shows a degraded movers state when summary compatibility envelopes carry source_error", async () => {
    setHooks()
    mockUseSpendSummary.mockImplementation(() => ({
      data: {
        data: {
          total_cost_usd: 0,
          by_butler: {},
          unavailable_butlers: [],
          source_error: true,
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
    }))
    await act(async () => {
      renderPage()
    })

    const strip = await screen.findByTestId("movers-strip")
    expect(strip.textContent).toContain("spend comparison unavailable")
    expect(strip.textContent).not.toContain("No spend change vs the prior window")
  })

  it("suppresses movers and the calm spend verdict when either comparison window is unpriced", async () => {
    setHooks({
      currentByButler: { general: 1.0 },
      priorByButler: { general: 0.2 },
      currentUnpriced: [
        {
          model: "unknown-executed-model",
          calls: 2,
          input_tokens: 1_000,
          output_tokens: 100,
          cached_input_tokens: 0,
          cache_creation_tokens: 0,
        },
      ],
    })
    await act(async () => {
      renderPage()
    })

    const strip = await screen.findByTestId("movers-strip")
    expect(strip.textContent).toContain("comparison incomplete")
    expect(strip.textContent).toContain("unknown-executed-model")
    expect(strip.querySelectorAll('[data-testid="mover-chip"]').length).toBe(0)
    expect(screen.queryByTestId("spend-verdict-all-clear")).toBeNull()
    expect(screen.getByTestId("spend-verdict-clauses").textContent).toContain(
      "comparison incomplete",
    )
  })

  it("excludes a butler with unavailable cost data instead of fabricating a '+$X · new' delta (bu-qvnce.1)", async () => {
    // "finance" has real current spend but its prior-window cost was
    // unavailable server-side -- a naive current-vs-0 comparison would
    // fabricate "finance +$2.00 · new", which isn't true.
    setHooks({
      currentByButler: { general: 1.0, finance: 2.0 },
      priorByButler: { general: 0.2 },
      priorUnavailable: ["finance"],
    })
    await act(async () => {
      renderPage()
    })

    const strip = await screen.findByTestId("movers-strip")
    const chips = Array.from(strip.querySelectorAll('[data-testid="mover-chip"]'))
    expect(chips.map((c) => c.textContent)).not.toEqual(
      expect.arrayContaining([expect.stringContaining("finance")]),
    )
    expect(strip.textContent).toContain("excluded from comparison")
    expect(strip.textContent).toContain("finance")
  })
})

describe("SpendPage — spend breakdown", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("fetches and renders the purpose dimension when its button is clicked", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/breakdown?by=purpose") {
        return Promise.resolve({
          data: {
            by: "purpose",
            breakdown: { classification: 1.2, discretion: 0.4 },
            source_error: false,
          },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "purpose" }))
    })

    await waitFor(() => {
      expect(
        apiFetchMock.mock.calls.some((c) => c[0] === "/spend/breakdown?by=purpose"),
      ).toBe(true)
    })
    expect(await screen.findByText("classification")).toBeTruthy()
  })

  it("renders absent model pricing as —/unpriced and keeps subscription zeroes distinct", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/breakdown?by=model") {
        return Promise.resolve({
          data: {
            by: "model",
            breakdown: { "gpt-5.6-luna": 0 },
            billing_classes: { "gpt-5.6-luna": "subscription" },
            unpriced_models: [
              {
                model: "unpriced-codex",
                calls: 3,
                input_tokens: 100,
                output_tokens: 50,
                cached_input_tokens: 0,
                cache_creation_tokens: 0,
              },
            ],
          },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "model" }))
    })

    expect(await screen.findByText("gpt-5.6-luna")).toBeTruthy()
    expect(screen.getByText(/subscription/)).toBeTruthy()
    expect(screen.getByText("—/unpriced")).toBeTruthy()
    expect((await screen.findByTestId("breakdown-unpriced")).textContent).toContain(
      "unpriced-codex",
    )
  })

  it("gates the empty state when butlers drop out of the butler breakdown fan-out (bu-jad4j.3)", async () => {
    // An empty breakdown WITH unavailable_butlers is an outage, not a genuine
    // "$0 month" — it must name the missing butlers, never the calm "No spend
    // has been recorded yet." line.
    apiFetchMock.mockImplementation((path: string) => {
      if (path.startsWith("/spend/breakdown")) {
        return Promise.resolve({
          data: { by: "butler", breakdown: {}, unavailable_butlers: ["finance", "home"] },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    const note = await screen.findByTestId("breakdown-unavailable")
    expect(note.getAttribute("role")).toBe("alert")
    expect(note.textContent).toContain("finance, home")
    expect(screen.queryByText("No spend has been recorded yet.")).toBeNull()
  })

  it("footnotes dropped butlers alongside a populated butler breakdown (bu-jad4j.3)", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path.startsWith("/spend/breakdown")) {
        return Promise.resolve({
          data: { by: "butler", breakdown: { inbox: 1.5 }, unavailable_butlers: ["finance"] },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    expect(await screen.findByText("inbox")).toBeTruthy()
    const note = await screen.findByTestId("breakdown-unavailable")
    expect(note.textContent).toContain("finance")
  })

  it("keeps the honest empty state when the breakdown is genuinely empty (bu-jad4j.3)", async () => {
    // Mutation guard: with no unavailable butlers, an empty breakdown is a real
    // "$0 month" and keeps its calm empty copy.
    apiFetchMock.mockImplementation((path: string) => {
      if (path.startsWith("/spend/breakdown")) {
        return Promise.resolve({
          data: { by: "butler", breakdown: {}, unavailable_butlers: [] },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    expect(await screen.findByText("No spend has been recorded yet.")).toBeTruthy()
    expect(screen.queryByTestId("breakdown-unavailable")).toBeNull()
  })

  it("shows a degraded-source note when the purpose breakdown source errors", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/breakdown?by=purpose") {
        return Promise.resolve({
          data: { by: "purpose", breakdown: {}, source_error: true },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "purpose" }))
    })

    const note = await screen.findByRole("alert")
    expect(note.textContent).toContain("Purpose breakdown")
  })
})

describe("SpendPage — why (evidence layer)", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("renders the Top Sessions evidence table", async () => {
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("top-sessions-section")
    expect(section.textContent).toContain("Most Expensive Sessions")
    expect(section.textContent).toContain("general")
    expect(section.textContent).toContain("$1.23")
  })

  it("footnotes dropped butlers alongside a populated Top Sessions table (bu-jad4j.3)", async () => {
    // A butler dropped from the top-sessions fan-out (meta.unavailable_butlers)
    // must be named beneath the ranking rather than silently omitted.
    mockUseTopSessions.mockReturnValue({
      data: {
        data: [
          {
            session_id: "sess-1",
            butler: "general",
            cost_usd: 1.2345,
            input_tokens: 5000,
            output_tokens: 2500,
            model: "claude-sonnet",
            started_at: "2026-05-17T10:00:00Z",
          },
        ],
        meta: { unavailable_butlers: ["finance"] },
      },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("top-sessions-section")
    expect(section.textContent).toContain("general")
    const note = await screen.findByTestId("top-sessions-unavailable")
    expect(note.textContent).toContain("finance")
  })

  it("gates the empty Top Sessions state on the unavailable-butlers outage (bu-jad4j.3)", async () => {
    // Empty ranking WITH unavailable_butlers is an outage, not a genuine absence
    // of expensive sessions — it must name the missing butlers, not the calm
    // "No session data available." line.
    mockUseTopSessions.mockReturnValue({
      data: { data: [], meta: { unavailable_butlers: ["finance", "home"] } },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    const note = await screen.findByTestId("top-sessions-unavailable")
    expect(note.getAttribute("role")).toBe("alert")
    expect(note.textContent).toContain("finance, home")
    expect(screen.queryByText("No session data available.")).toBeNull()
  })

  it("renders the By Schedule evidence table", async () => {
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("by-schedule-section")
    expect(section.textContent).toContain("morning-briefing")
    expect(section.textContent).toContain("$3.00")
  })

  it("footnotes dropped butlers alongside a populated By Schedule table (bu-h3ej9)", async () => {
    // A butler dropped from the by-schedule fan-out (meta.unavailable_butlers)
    // must be named beneath the ranking rather than silently omitted.
    mockUseCostsBySchedule.mockReturnValue({
      data: {
        data: [
          {
            schedule_name: "morning-briefing",
            butler: "general",
            cron: "0 7 * * *",
            total_runs: 30,
            total_cost_usd: 3.0,
            avg_cost_per_run: 0.1,
            runs_per_day: 1,
            projected_monthly_usd: 3.0,
          },
        ],
        meta: { unavailable_butlers: ["finance"] },
      },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("by-schedule-section")
    expect(section.textContent).toContain("morning-briefing")
    const note = await screen.findByTestId("by-schedule-unavailable")
    expect(note.getAttribute("role")).toBe("alert")
    expect(note.textContent).toContain("finance")
  })

  it("gates the empty By Schedule state on the unavailable-butlers outage (bu-h3ej9)", async () => {
    // Empty ranking WITH unavailable_butlers is an outage, not a genuine absence
    // of scheduled-task cost data — it must name the missing butlers, not the
    // calm "No scheduled-task cost data available." line.
    mockUseCostsBySchedule.mockReturnValue({
      data: { data: [], meta: { unavailable_butlers: ["finance", "home"] } },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    const note = await screen.findByTestId("by-schedule-unavailable")
    expect(note.getAttribute("role")).toBe("alert")
    expect(note.textContent).toContain("finance, home")
    expect(screen.queryByText("No scheduled-task cost data available.")).toBeNull()
  })

  it("keeps the honest empty By Schedule state when the fan-out is genuinely empty (bu-h3ej9)", async () => {
    // Mutation guard: with no unavailable butlers, an empty ranking is a real
    // "nothing scheduled" result and keeps its calm empty copy.
    mockUseCostsBySchedule.mockReturnValue({
      data: { data: [], meta: { unavailable_butlers: [] } },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    expect(await screen.findByText("No scheduled-task cost data available.")).toBeTruthy()
    expect(screen.queryByTestId("by-schedule-unavailable")).toBeNull()
  })

  it("scopes both evidence sections to the TimeWindowPicker window, not all-time [bu-oaiiw]", async () => {
    await act(async () => {
      renderPage()
    })

    await screen.findByTestId("top-sessions-section")

    // Both hooks are called with (limit-or-nothing, from, to) — from/to must be
    // real Date instances (the active TimeWindowPicker window), and neither
    // section's label should claim to be "all-time" anymore.
    expect(mockUseTopSessions).toHaveBeenCalled()
    const topSessionsArgs = mockUseTopSessions.mock.calls[0]
    expect(topSessionsArgs[0]).toBe(10)
    expect(topSessionsArgs[1]).toBeInstanceOf(Date)
    expect(topSessionsArgs[2]).toBeInstanceOf(Date)

    expect(mockUseCostsBySchedule).toHaveBeenCalled()
    const byScheduleArgs = mockUseCostsBySchedule.mock.calls[0]
    expect(byScheduleArgs[0]).toBeInstanceOf(Date)
    expect(byScheduleArgs[1]).toBeInstanceOf(Date)

    // Same window is shared across both evidence sections and the daily chart.
    expect((byScheduleArgs[0] as Date).getTime()).toBe((topSessionsArgs[1] as Date).getTime())
    expect((byScheduleArgs[1] as Date).getTime()).toBe((topSessionsArgs[2] as Date).getTime())

    const topSection = screen.getByTestId("top-sessions-section")
    const scheduleSection = screen.getByTestId("by-schedule-section")
    expect(topSection.textContent).not.toContain("All-time")
    expect(scheduleSection.textContent).not.toContain("all-time")
  })
})

describe("SpendPage — routing rules", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("shows an Add rule button that opens the create form", async () => {
    await act(async () => {
      renderPage()
    })

    const addBtn = await screen.findByTestId("add-rule-button")
    expect(screen.queryByTestId("create-rule-form")).toBeNull()

    await act(async () => {
      fireEvent.click(addBtn)
    })

    expect(screen.getByTestId("create-rule-form")).toBeTruthy()
  })

  it("POSTs an evaluator-shaped payload to /spend/rules and refreshes the list", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Butler condition"), { target: { value: "general" } })
      fireEvent.change(screen.getByLabelText("Complexity condition"), { target: { value: "workhorse" } })
      fireEvent.change(screen.getByLabelText("Target model"), { target: { value: "claude-sonnet" } })
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    await waitFor(() => {
      const postCall = apiFetchMock.mock.calls.find(
        (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
      )
      expect(postCall).toBeTruthy()
      expect(JSON.parse(postCall![1].body as string)).toEqual({
        condition: { butler: "general", complexity: "workhorse" },
        action: { model: "claude-sonnet" },
      })
    })
  })

  it("includes the purpose condition dim in the created rule (bu-og0j2)", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    await act(async () => {
      fireEvent.change(screen.getByLabelText("Purpose condition"), {
        target: { value: "discretion" },
      })
      fireEvent.change(screen.getByLabelText("Target model"), { target: { value: "claude-sonnet" } })
    })

    await act(async () => {
      fireEvent.submit(screen.getByTestId("create-rule-form"))
    })

    await waitFor(() => {
      const postCall = apiFetchMock.mock.calls.find(
        (c) => c[0] === "/spend/rules" && c[1]?.method === "POST",
      )
      expect(postCall).toBeTruthy()
      expect(JSON.parse(postCall![1].body as string)).toEqual({
        condition: { purpose: "discretion" },
        action: { model: "claude-sonnet" },
      })
    })
  })

  it("keeps Trigger and Purpose mutually exclusive with an accessible explanation", async () => {
    await act(async () => {
      renderPage()
    })

    await act(async () => {
      fireEvent.click(await screen.findByTestId("add-rule-button"))
    })

    const trigger = screen.getByLabelText("Trigger condition") as HTMLSelectElement
    const purpose = screen.getByLabelText("Purpose condition") as HTMLSelectElement
    const hint = screen.getByTestId("trigger-purpose-alias-hint")

    expect(trigger.disabled).toBe(false)
    expect(purpose.disabled).toBe(false)
    expect(trigger.getAttribute("aria-describedby")).toBe("trigger-purpose-alias-hint")
    expect(purpose.getAttribute("aria-describedby")).toBe("trigger-purpose-alias-hint")
    expect(hint.getAttribute("role")).toBe("status")
    expect(hint.textContent).toContain("Choose either Trigger or Purpose")

    await act(async () => {
      fireEvent.change(trigger, { target: { value: "route" } })
    })

    expect(purpose.disabled).toBe(true)
    expect(hint.textContent).toContain("Trigger selected")

    await act(async () => {
      fireEvent.change(trigger, { target: { value: "" } })
      fireEvent.change(purpose, { target: { value: "discretion" } })
    })

    expect(trigger.disabled).toBe(true)
    expect(hint.textContent).toContain("Purpose selected")
  })
})

// ---------------------------------------------------------------------------
// Degraded states — an errored inline query must render an honest degraded
// note, never a calm empty (bu-mkd5r, three-way state contract). QueryClient
// runs with retry:false so a rejected apiFetch surfaces isError on first pass.
// ---------------------------------------------------------------------------

describe("SpendPage — degraded states (bu-mkd5r)", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("forecast outage: posture slot names the outage, not a blank $0 strip", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") return Promise.reject(new Error("boom"))
      return defaultApiFetch(path)
    })
    await act(async () => {
      renderPage()
    })
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert").map((el) => el.textContent ?? "")
      expect(alerts.some((t) => t.includes("Spend forecast"))).toBe(true)
    })
    // The genuinely-empty forecast copy must NOT appear on an outage.
    expect(screen.queryByText("No forecast data is available yet.")).toBeNull()
  })

  it("forecast ceiling_source_error: KPI strip shows a degraded note, not a fabricated $0 MTD (bu-7o89u.1)", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        return Promise.resolve({
          data: { ...MOCK_FORECAST.data, mtd_usd: 0, projected_eom_usd: 0, ceiling_source_error: true },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })
    await act(async () => {
      renderPage()
    })
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert").map((el) => el.textContent ?? "")
      expect(alerts.some((t) => t.includes("Spend forecast") && t.includes("ceiling source"))).toBe(true)
    })
    // A degraded ledger source must never render the KPI strip's numbers --
    // those would be the fabricated $0 this bead exists to stop.
    expect(screen.queryByTestId("kpi-strip")).toBeNull()
  })

  it("forecast ceiling_source_error: chart drops the (fabricated $0) projected segment but keeps real solid actuals", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        return Promise.resolve({
          data: { ...MOCK_FORECAST.data, mtd_usd: 0, projected_eom_usd: 0, ceiling_source_error: true },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })
    await act(async () => {
      renderPage()
    })
    await waitFor(() => {
      expect(screen.getByLabelText("Spend forecast chart")).toBeTruthy()
    })
    // The genuinely-empty forecast copy must not appear either -- there IS
    // real (solid-actuals) chart content, just no projection.
    expect(screen.queryByText("No forecast data is available yet.")).toBeNull()
  })

  it("forecast unavailable_butlers: footnotes excluded butlers under the chart, independent of ceiling_source_error", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        return Promise.resolve({
          data: { ...MOCK_FORECAST.data, unavailable_butlers: ["broken"] },
          meta: {},
        })
      }
      return defaultApiFetch(path)
    })
    await act(async () => {
      renderPage()
    })
    const note = await screen.findByTestId("forecast-unavailable-butlers")
    expect(note.textContent ?? "").toContain("broken")
    // The KPI strip still renders -- ceiling_source_error is false here.
    expect(screen.getByTestId("kpi-strip")).toBeTruthy()
  })

  it("breakdown outage: renders a degraded note, not 'No spend has been recorded yet.'", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path.startsWith("/spend/breakdown")) return Promise.reject(new Error("boom"))
      return defaultApiFetch(path)
    })
    await act(async () => {
      renderPage()
    })
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert").map((el) => el.textContent ?? "")
      expect(alerts.some((t) => t.includes("Spend breakdown"))).toBe(true)
    })
    expect(screen.queryByText("No spend has been recorded yet.")).toBeNull()
  })

  it("rules outage: renders a degraded note, not the empty-ruleset line", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/rules") return Promise.reject(new Error("boom"))
      return defaultApiFetch(path)
    })
    await act(async () => {
      renderPage()
    })
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert").map((el) => el.textContent ?? "")
      expect(alerts.some((t) => t.includes("Routing rules"))).toBe(true)
    })
    expect(
      screen.queryByText(/No routing rules are configured/),
    ).toBeNull()
  })
})

describe("SpendPage — fleet-halt banner (bu-7o89u.3)", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation(defaultApiFetch)
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("renders nothing when the fleet halt is not active", async () => {
    await act(async () => {
      renderPage()
    })
    expect(screen.queryByTestId("fleet-halt-banner")).toBeNull()
    expect(screen.queryByTestId("fleet-halt-source-error")).toBeNull()
  })

  it("renders a red banner naming the denied-today/total counts and since-timestamp when active", async () => {
    mockUseFleetHaltStatus.mockReturnValue({
      active: true,
      deniedToday: 4,
      deniedTotal: 12,
      since: "2026-05-10T08:00:00.000Z",
      recentAttempts: [],
      isLoading: false,
      isError: false,
    })

    await act(async () => {
      renderPage()
    })

    const banner = await screen.findByTestId("fleet-halt-banner")
    expect(banner.textContent ?? "").toContain("Monthly ceiling reached")
    expect(banner.textContent ?? "").toContain("12")
    expect(banner.textContent ?? "").toContain("4")
    expect(screen.getByRole("alert")).toBeTruthy()
  })

  it("degraded: a failed attempts source renders a note, never a silent 'no halt'", async () => {
    mockUseFleetHaltStatus.mockReturnValue({
      active: false,
      deniedToday: 0,
      deniedTotal: 0,
      since: null,
      recentAttempts: [],
      isLoading: false,
      isError: true,
    })

    await act(async () => {
      renderPage()
    })

    const note = await screen.findByTestId("fleet-halt-source-error")
    expect(note.textContent ?? "").toContain("Fleet-halt status")
    // The (potentially false) "no halt" banner must not also render.
    expect(screen.queryByTestId("fleet-halt-banner")).toBeNull()
  })

  it("attempts drawer: expands to list recent denied attempts with session doors", async () => {
    mockUseFleetHaltStatus.mockReturnValue({
      active: true,
      deniedToday: 2,
      deniedTotal: 2,
      since: "2026-05-10T08:00:00.000Z",
      recentAttempts: [
        {
          ts: "2026-05-10T09:00:00.000Z",
          butler: "finance",
          outcome: "quota_skip",
          attempt_index: 0,
          failure_reason: "Monthly spend ceiling reached: month-to-date $50.00 >= ceiling $50.00",
          error_code: null,
          error_message: null,
          tool_call_count: 0,
          session_id: "sess-halt-1",
          logical_session_id: "req-halt-1",
        },
        {
          ts: "2026-05-10T08:00:00.000Z",
          butler: "general",
          outcome: "quota_skip",
          attempt_index: 0,
          failure_reason: "Monthly spend ceiling reached: month-to-date $50.00 >= ceiling $50.00",
          error_code: null,
          error_message: null,
          tool_call_count: 0,
          session_id: null,
          logical_session_id: "req-halt-2",
        },
      ],
      isLoading: false,
      isError: false,
    })

    await act(async () => {
      renderPage()
    })

    await screen.findByTestId("fleet-halt-banner")
    expect(screen.queryByTestId("fleet-halt-drawer")).toBeNull()

    fireEvent.click(screen.getByTestId("fleet-halt-drawer-toggle"))

    const drawer = await screen.findByTestId("fleet-halt-drawer")
    const rows = screen.getAllByTestId("fleet-halt-attempt-row")
    expect(rows).toHaveLength(2)
    expect(drawer.textContent ?? "").toContain("finance")
    expect(drawer.textContent ?? "").toContain("general")

    // The row with a session_id gets a session door; the pre-session row does not.
    const sessionLink = screen.getByRole("link", { name: "View session" })
    expect(sessionLink.getAttribute("href")).toBe("/sessions/sess-halt-1")
    expect(screen.getAllByRole("link", { name: "View session" })).toHaveLength(1)
  })

  it("bu-7o89u.4: a ?openDrawer=fleet-halt door lands with the drawer already expanded", async () => {
    mockUseFleetHaltStatus.mockReturnValue({
      active: true,
      deniedToday: 1,
      deniedTotal: 1,
      since: "2026-07-12T09:00:00.000Z",
      recentAttempts: [
        {
          ts: "2026-07-12T09:00:00.000Z",
          butler: "finance",
          outcome: "quota_skip",
          attempt_index: 0,
          failure_reason: "Monthly spend ceiling reached: month-to-date $50.00 >= ceiling $50.00",
          error_code: null,
          error_message: null,
          tool_call_count: 0,
          session_id: null,
          logical_session_id: "req-halt-1",
        },
      ],
      isLoading: false,
      isError: false,
    })

    await act(async () => {
      renderPage(["/spend?openDrawer=fleet-halt"])
    })

    await screen.findByTestId("fleet-halt-banner")
    // No click needed -- the owner-push door opens the drawer directly.
    expect(await screen.findByTestId("fleet-halt-drawer")).toBeTruthy()
  })

  it("a plain visit to /spend (no openDrawer param) keeps the drawer collapsed", async () => {
    mockUseFleetHaltStatus.mockReturnValue({
      active: true,
      deniedToday: 1,
      deniedTotal: 1,
      since: "2026-07-12T09:00:00.000Z",
      recentAttempts: [],
      isLoading: false,
      isError: false,
    })

    await act(async () => {
      renderPage(["/spend"])
    })

    await screen.findByTestId("fleet-halt-banner")
    expect(screen.queryByTestId("fleet-halt-drawer")).toBeNull()
  })
})
