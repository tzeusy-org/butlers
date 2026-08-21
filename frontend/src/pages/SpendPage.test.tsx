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

import { formatCostDate } from "@/hooks/use-spend"
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

// Mirrors butlers.core.sessions.CADENCE_BASIS_DESCRIPTION — the forecast basis
// the API states alongside every projection (bu-6jv4m.2).
const FORECAST_BASIS =
  "Projected runs are the cron expression's own cadence over an average " +
  "Gregorian calendar month (30.436875 days), sampled from a fixed anchor so " +
  "the forecast does not change with the time of the request."

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
          projected_monthly_runs: 30.4369,
          projected_monthly_usd: 3.0437,
          forecast_basis: FORECAST_BASIS,
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

describe("SpendPage — UTC implicit spend windows", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    // This instant is already Aug 1 in the owner timezone (Asia/Singapore),
    // while the ledger's UTC day and ceiling remain July 31.
    vi.setSystemTime(new Date("2026-07-31T18:00:00.000Z"))
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
    setHooks()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
  })

  it("aligns implicit daily, mover, and evidence queries with the ledger UTC day", async () => {
    await act(async () => {
      renderPage()
    })

    const [dailyFrom, dailyTo, dailyOptions] = mockUseDailySpend.mock.calls.at(-1)!
    expect(formatCostDate(dailyFrom as Date, "UTC")).toBe("2026-07-25")
    expect(formatCostDate(dailyTo as Date, "UTC")).toBe("2026-07-31")
    expect(dailyOptions).toMatchObject({ dateKeyTimezone: "UTC" })
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-07-25")
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("2026-07-31")
    expect(screen.getByRole("button", { name: "Last 7 days" }).getAttribute("aria-pressed")).toBe("true")
    expect(screen.getByRole("button", { name: "Today" }).getAttribute("aria-pressed")).toBe("false")

    const [currentSummaryCall, priorSummaryCall] = mockUseSpendSummary.mock.calls.slice(-2)
    const [currentPeriod, currentFrom, currentTo, currentButler, currentTimezone] = currentSummaryCall
    expect(currentPeriod).toBeUndefined()
    expect(currentButler).toBeUndefined()
    expect(formatCostDate(currentFrom as Date, "UTC")).toBe("2026-07-25")
    expect(formatCostDate(currentTo as Date, "UTC")).toBe("2026-07-31")
    expect(currentTimezone).toBe("UTC")

    const [, priorFrom, priorTo, priorButler, priorTimezone] = priorSummaryCall
    expect(priorButler).toBeUndefined()
    expect(formatCostDate(priorFrom as Date, "UTC")).toBe("2026-07-18")
    expect(formatCostDate(priorTo as Date, "UTC")).toBe("2026-07-24")
    expect(priorTimezone).toBe("UTC")

    const [topLimit, topFrom, topTo, topTimezone] = mockUseTopSessions.mock.calls.at(-1)!
    expect(topLimit).toBe(10)
    expect(formatCostDate(topFrom as Date, "UTC")).toBe("2026-07-25")
    expect(formatCostDate(topTo as Date, "UTC")).toBe("2026-07-31")
    expect(topTimezone).toBe("UTC")

    const [scheduleFrom, scheduleTo, scheduleTimezone] = mockUseCostsBySchedule.mock.calls.at(-1)!
    expect(formatCostDate(scheduleFrom as Date, "UTC")).toBe("2026-07-25")
    expect(formatCostDate(scheduleTo as Date, "UTC")).toBe("2026-07-31")
    expect(scheduleTimezone).toBe("UTC")
  })

  it.each(["Asia/Singapore", "America/Los_Angeles"])(
    "keeps the implicit mover comparison to seven UTC dates in %s",
    async (viewerTimezone) => {
      const originalTimezone = process.env.TZ
      process.env.TZ = viewerTimezone

      try {
        await act(async () => {
          renderPage()
        })

        const [, priorFrom, priorTo] = mockUseSpendSummary.mock.calls.slice(-2)[1]
        expect(formatCostDate(priorFrom as Date, "UTC")).toBe("2026-07-18")
        expect(formatCostDate(priorTo as Date, "UTC")).toBe("2026-07-24")
        expect(screen.getByTestId("movers-strip").textContent).toContain(
          "prior 7-day window",
        )
      } finally {
        process.env.TZ = originalTimezone
      }
    },
  )

  it.each([
    {
      viewerTimezone: "Asia/Singapore",
      edgeLabel: "From",
      enteredDate: "2026-07-26",
      expectedFrom: "2026-07-26",
      expectedTo: "2026-07-31",
    },
    {
      viewerTimezone: "America/Los_Angeles",
      edgeLabel: "To",
      enteredDate: "2026-07-30",
      expectedFrom: "2026-07-25",
      expectedTo: "2026-07-30",
    },
  ])(
    "preserves the untouched UTC date key when editing the $edgeLabel edge in $viewerTimezone",
    async ({ viewerTimezone, edgeLabel, enteredDate, expectedFrom, expectedTo }) => {
      const originalTimezone = process.env.TZ
      process.env.TZ = viewerTimezone

      try {
        await act(async () => {
          renderPage()
        })

        await act(async () => {
          fireEvent.change(screen.getByLabelText(edgeLabel), { target: { value: enteredDate } })
        })

        const [dailyFrom, dailyTo, dailyOptions] = mockUseDailySpend.mock.calls.at(-1)!
        expect(formatCostDate(dailyFrom as Date)).toBe(expectedFrom)
        expect(formatCostDate(dailyTo as Date)).toBe(expectedTo)
        expect(dailyOptions).not.toHaveProperty("dateKeyTimezone")
      } finally {
        process.env.TZ = originalTimezone
      }
    },
  )

  it("keeps an explicit operator-selected range on the owner-timezone path", async () => {
    await act(async () => {
      renderPage(["/?from=2026-08-01&to=2026-08-01"])
    })

    const [dailyFrom, dailyTo, dailyOptions] = mockUseDailySpend.mock.calls.at(-1)!
    expect(formatCostDate(dailyFrom as Date)).toBe("2026-08-01")
    expect(formatCostDate(dailyTo as Date)).toBe("2026-08-01")
    expect(dailyOptions).not.toHaveProperty("dateKeyTimezone")

    const [currentSummaryCall] = mockUseSpendSummary.mock.calls.slice(-2)
    const [, currentFrom, currentTo, , currentTimezone] = currentSummaryCall
    expect(formatCostDate(currentFrom as Date)).toBe("2026-08-01")
    expect(formatCostDate(currentTo as Date)).toBe("2026-08-01")
    expect(currentTimezone).toBeUndefined()
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
            breakdown: { "known-zero-example": 0 },
            billing_classes: { "known-zero-example": "subscription" },
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

    expect(await screen.findByText("known-zero-example")).toBeTruthy()
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
            projected_monthly_runs: 30.4369,
            projected_monthly_usd: 3.0437,
            forecast_basis: FORECAST_BASIS,
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

  it("keeps showing cached Top Sessions rows with a stale badge on a background refetch error (bu-ep4ks.5)", async () => {
    // A background refetch failing must not clobber good cached rows with a
    // bare "Failed to load" line -- react-query keeps the last-successful
    // `data` around across a failed refetch, and the page must too.
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
      isError: true,
    })
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("top-sessions-section")
    expect(section.textContent).toContain("general")
    expect(section.textContent).not.toBe("Failed to load top sessions.")
    const stale = await screen.findByTestId("top-sessions-stale")
    expect(stale.textContent).toContain("showing last loaded data")
  })

  it("shows the full failure message for Top Sessions only when there is no cached data at all (bu-ep4ks.5)", async () => {
    mockUseTopSessions.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    })
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("top-sessions-section")
    expect(section.textContent).toContain("Failed to load top sessions.")
    expect(screen.queryByTestId("top-sessions-stale")).toBeNull()
  })

  it("keeps showing cached By Schedule rows with a stale badge on a background refetch error (bu-ep4ks.5)", async () => {
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
            projected_monthly_runs: 30.4369,
            projected_monthly_usd: 3.0437,
            forecast_basis: FORECAST_BASIS,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: true,
    })
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("by-schedule-section")
    expect(section.textContent).toContain("morning-briefing")
    const stale = await screen.findByTestId("by-schedule-stale")
    expect(stale.textContent).toContain("showing last loaded data")
  })

  it("shows the full failure message for By Schedule only when there is no cached data at all (bu-ep4ks.5)", async () => {
    mockUseCostsBySchedule.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    })
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("by-schedule-section")
    expect(section.textContent).toContain("Failed to load schedule costs.")
    expect(screen.queryByTestId("by-schedule-stale")).toBeNull()
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
// Keyboard reorder (bu-mmdef, keyboard chassis remainder) — the routing-rules
// table was drag-only. A tiny in-memory store fulfils GET /spend/rules and
// PUT /spend/rules/:id the same way the real backend would (shifting the
// other rows' positions), so a reorder's server round trip is exercised for
// real rather than asserting only that a mutation fired.
// ---------------------------------------------------------------------------

interface RuleFixture {
  id: string
  position: number
  condition: Record<string, unknown>
  action: Record<string, unknown>
  saved_7d: number | null
  created_at: string
  updated_at: string
}

function makeRulesStore(initial: RuleFixture[]) {
  let rules = initial.map((r) => ({ ...r }))
  return {
    get: () => ({ data: [...rules].sort((a, b) => a.position - b.position), meta: {} }),
    reorder(id: string, to: number) {
      const rule = rules.find((r) => r.id === id)
      if (!rule) return
      const from = rule.position
      if (from === to) return
      rules = rules.map((r) => {
        if (r.id === id) return { ...r, position: to }
        if (from < to && r.position > from && r.position <= to) return { ...r, position: r.position - 1 }
        if (from > to && r.position >= to && r.position < from) return { ...r, position: r.position + 1 }
        return r
      })
    },
  }
}

function mockRulesApi(store: ReturnType<typeof makeRulesStore>) {
  apiFetchMock.mockReset()
  apiFetchMock.mockImplementation((path: string, opts?: RequestInit) => {
    if (path === "/spend/rules") return Promise.resolve(store.get())
    const match = /^\/spend\/rules\/([^/]+)$/.exec(path)
    if (match && opts?.method === "PUT") {
      const body = JSON.parse(opts.body as string) as { position: number }
      store.reorder(match[1], body.position)
      return Promise.resolve({})
    }
    return defaultApiFetch(path)
  })
}

describe("SpendPage — keyboard reorder (bu-mmdef)", () => {
  beforeEach(() => {
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  const THREE_RULES: RuleFixture[] = [
    { id: "rule-1", position: 1, condition: {}, action: { model: "claude-haiku" }, saved_7d: null, created_at: "", updated_at: "" },
    { id: "rule-2", position: 2, condition: {}, action: { model: "claude-sonnet" }, saved_7d: null, created_at: "", updated_at: "" },
    { id: "rule-3", position: 3, condition: {}, action: { model: "claude-opus" }, saved_7d: null, created_at: "", updated_at: "" },
  ]

  it("grabs a row, moves it with arrow keys, and keeps real DOM focus on it through the server round trip", async () => {
    const store = makeRulesStore(THREE_RULES)
    mockRulesApi(store)

    await act(async () => {
      renderPage()
    })

    const row = (await screen.findByTestId("spend-rule-row-rule-2")) as HTMLElement
    row.focus()
    expect(document.activeElement).toBe(row)

    fireEvent.keyDown(row, { key: " " })
    expect(row.getAttribute("data-grabbed")).toBe("true")

    fireEvent.keyDown(row, { key: "ArrowUp" })

    await waitFor(() => {
      expect(screen.getByTestId("spend-rule-position-rule-2").textContent).toBe("1")
    })

    // The row keeps the same key (rule id) across the reorder-driven refetch,
    // so React's keyed reconciliation moves the existing DOM node instead of
    // remounting it -- focus survives the round trip without any manual
    // restore effect (the #3586 focus-reality doctrine: assert real DOM
    // focus, not just that the handler fired).
    const rowAfter = screen.getByTestId("spend-rule-row-rule-2")
    expect(document.activeElement).toBe(rowAfter)

    fireEvent.keyDown(rowAfter, { key: "Enter" })
    expect(screen.getByTestId("spend-rule-row-rule-2").getAttribute("data-grabbed")).toBeNull()
  })

  it("Escape cancels and restores the row to the position it was grabbed from", async () => {
    const store = makeRulesStore(THREE_RULES)
    mockRulesApi(store)

    await act(async () => {
      renderPage()
    })

    const row = (await screen.findByTestId("spend-rule-row-rule-3")) as HTMLElement
    row.focus()

    fireEvent.keyDown(row, { key: "Enter" })
    fireEvent.keyDown(row, { key: "ArrowUp" })

    await waitFor(() => {
      expect(screen.getByTestId("spend-rule-position-rule-3").textContent).toBe("2")
    })

    fireEvent.keyDown(screen.getByTestId("spend-rule-row-rule-3"), { key: "Escape" })

    await waitFor(() => {
      expect(screen.getByTestId("spend-rule-position-rule-3").textContent).toBe("3")
    })
    expect(document.activeElement).toBe(screen.getByTestId("spend-rule-row-rule-3"))
    expect(screen.getByTestId("spend-rule-row-rule-3").getAttribute("data-grabbed")).toBeNull()
  })

  it("never fires a bare Up/Down page scroll while a row is grabbed (preventDefault)", async () => {
    const store = makeRulesStore(THREE_RULES)
    mockRulesApi(store)

    await act(async () => {
      renderPage()
    })

    const row = (await screen.findByTestId("spend-rule-row-rule-2")) as HTMLElement
    row.focus()
    fireEvent.keyDown(row, { key: " " })

    const event = new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true })
    const prevented = !row.dispatchEvent(event)
    expect(prevented).toBe(true)
  })

  it("Escape-cancel race: reorderMutation's scope serializes the restore behind the in-flight move, so a slow/reordered response can never leave the server moved (bu-mmdef)", async () => {
    // The previous race test's mock applied store.reorder() synchronously at
    // request-send time and stashed a single `resolveReorder` handle that the
    // second PUT would silently clobber -- it could never fail even with no
    // ordering guarantee at all, because the store only ever saw call order,
    // never arrival order, and there was only ever one resolver to invoke.
    // This test instead: (1) keeps one deferred handle per PUT call so
    // neither is lost, (2) applies store.reorder() at *resolve* time (i.e.
    // simulated response-arrival time, not send time) so resolution order is
    // the thing that determines server-visible state, and (3) asserts the
    // restore's PUT is not even dispatched until the move's PUT has settled
    // -- the actual client-side serialization guarantee `scope` provides,
    // not just a lucky final value.
    const store = makeRulesStore(THREE_RULES)
    const puts: Array<{ id: string; position: number; resolve: () => void }> = []
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string, opts?: RequestInit) => {
      if (path === "/spend/rules") return Promise.resolve(store.get())
      const match = /^\/spend\/rules\/([^/]+)$/.exec(path)
      if (match && opts?.method === "PUT") {
        const body = JSON.parse(opts.body as string) as { position: number }
        return new Promise((resolve) => {
          puts.push({
            id: match[1],
            position: body.position,
            resolve: () => {
              store.reorder(match[1], body.position)
              resolve({})
            },
          })
        })
      }
      return defaultApiFetch(path)
    })

    await act(async () => {
      renderPage()
    })

    const row = (await screen.findByTestId("spend-rule-row-rule-2")) as HTMLElement
    row.focus()

    fireEvent.keyDown(row, { key: " " }) // grab rule-2 at position 2
    fireEvent.keyDown(row, { key: "ArrowUp" }) // move mutation: PUT rule-2 position=1

    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0]).toMatchObject({ id: "rule-2", position: 1 })

    fireEvent.keyDown(row, { key: "Escape" }) // compensating restore: mutate({id: rule-2, position: 2})

    // The restore shares reorderMutation's scope with the still-pending move,
    // so TanStack Query must hold its request back rather than dispatching a
    // second, concurrent PUT. Give any (incorrect) immediate dispatch a full
    // microtask+macrotask flush to show up before asserting it didn't.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(puts).toHaveLength(1)

    // Settle the move. Only now should the queued restore mutation actually
    // dispatch its request.
    await act(async () => {
      puts[0].resolve()
    })
    await waitFor(() => expect(puts).toHaveLength(2))
    expect(puts[1]).toMatchObject({ id: "rule-2", position: 2 })

    // Settle the restore last -- the scenario the old test structurally
    // could not exercise. Server-visible state must reflect the restore
    // (grab-origin position), never the moved position, however slow or
    // reordered a real network response might have been.
    await act(async () => {
      puts[1].resolve()
    })

    await waitFor(() => {
      expect(screen.getByTestId("spend-rule-position-rule-2").textContent).toBe("2")
    })
    expect(store.get().data.find((r) => r.id === "rule-2")?.position).toBe(2)
    expect(row.getAttribute("data-grabbed")).toBeNull()
  })

  it("grabbing row B while row A is grabbed implicitly drops row A (bu-mmdef)", async () => {
    // Scenario: grab row 2, then without Escaping, grab row 3. Row 2 should be
    // implicitly dropped (announced) before row 3 is grabbed.
    const store = makeRulesStore(THREE_RULES)
    mockRulesApi(store)

    await act(async () => {
      renderPage()
    })

    const row2 = (await screen.findByTestId("spend-rule-row-rule-2")) as HTMLElement
    const row3 = screen.getByTestId("spend-rule-row-rule-3") as HTMLElement

    row2.focus()

    // Grab row 2
    fireEvent.keyDown(row2, { key: " " })
    expect(row2.getAttribute("data-grabbed")).toBe("true")
    expect(row3.getAttribute("data-grabbed")).toBeNull()

    // Now grab row 3 without Escaping row 2 first
    fireEvent.keyDown(row3, { key: " " })

    // Row 2 should no longer be grabbed (implicitly dropped)
    expect(row2.getAttribute("data-grabbed")).toBeNull()

    // Row 3 should now be grabbed
    expect(row3.getAttribute("data-grabbed")).toBe("true")
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

// ---------------------------------------------------------------------------
// Forecast vs. measured history in the By Schedule table (bu-6jv4m.2)
//
// The projection used to be presented as if it were another measured column.
// It is a forecast, and the table must say so and state the basis it was
// computed on.
// ---------------------------------------------------------------------------

describe("SpendPage — By Schedule forecast honesty (bu-6jv4m.2)", () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string) => defaultApiFetch(path))
    setHooks()
  })

  afterEach(() => {
    cleanup()
  })

  it("labels the forecast columns as a forecast, separate from the measured range", async () => {
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("by-schedule-section")
    const measured = screen.getByTestId("by-schedule-measured-group")
    const forecast = screen.getByTestId("by-schedule-forecast-group")
    expect(measured.textContent?.toLowerCase()).toContain("measured")
    expect(forecast.textContent?.toLowerCase()).toContain("forecast")
    expect(section.textContent).toContain("morning-briefing")
  })

  it("renders projected runs alongside projected cost, and states the basis", async () => {
    await act(async () => {
      renderPage()
    })

    const runs = await screen.findByTestId("schedule-projected-runs-general-morning-briefing")
    expect(runs.textContent).toContain("30.4")

    const basis = screen.getByTestId("by-schedule-forecast-basis")
    expect(basis.textContent).toContain("30.436875")
  })

  it("says so plainly when a schedule's cadence cannot be projected", async () => {
    // projected_monthly_runs === 0 means the cron did not parse. Rendering "$0.00"
    // would read as "this schedule is free", which is a different claim.
    mockUseCostsBySchedule.mockReturnValue({
      data: {
        data: [
          {
            schedule_name: "mystery",
            butler: "general",
            cron: "not a cron",
            total_runs: 5,
            total_cost_usd: 0.5,
            avg_cost_per_run: 0.1,
            projected_monthly_runs: 0,
            projected_monthly_usd: 0,
            forecast_basis: FORECAST_BASIS,
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
    })
    await act(async () => {
      renderPage()
    })

    const runs = await screen.findByTestId("schedule-projected-runs-general-mystery")
    expect(runs.textContent).toContain("—")
    const cost = screen.getByTestId("schedule-projected-cost-general-mystery")
    expect(cost.textContent).toContain("—")
    expect(cost.textContent).not.toContain("$0.00")
    // The measured history is still reported.
    expect(screen.getByTestId("by-schedule-section").textContent).toContain("$0.50")
  })
})

// ---------------------------------------------------------------------------
// Routing-rule deletion: confirm + exact-order undo (bu-6jv4m.2)
//
// Deleting a first-match routing rule on a single click is unrecoverable and
// silently re-points every dispatch the rule used to catch. The store below
// models the real backend: DELETE compacts the positions below the removed
// rule, POST with an explicit `position` shifts them back down. An Undo that
// restores the rule at the wrong position is worse than no Undo, so the
// position is what these tests pin.
// ---------------------------------------------------------------------------

function makeDeletableRulesStore(initial: RuleFixture[]) {
  let rules = initial.map((r) => ({ ...r }))
  let nextId = 100
  return {
    get: () => ({ data: [...rules].sort((a, b) => a.position - b.position), meta: {} }),
    snapshot: () => [...rules].sort((a, b) => a.position - b.position),
    remove(id: string) {
      const rule = rules.find((r) => r.id === id)
      if (!rule) return
      const gone = rule.position
      rules = rules
        .filter((r) => r.id !== id)
        .map((r) => (r.position > gone ? { ...r, position: r.position - 1 } : r))
    },
    insert(position: number, condition: Record<string, unknown>, action: Record<string, unknown>) {
      rules = rules.map((r) => (r.position >= position ? { ...r, position: r.position + 1 } : r))
      const created = {
        id: `restored-${nextId++}`,
        position,
        condition,
        action,
        saved_7d: null,
        created_at: "2026-05-17T00:00:00Z",
        updated_at: "2026-05-17T00:00:00Z",
      }
      rules = [...rules, created]
      return created
    },
  }
}

const THREE_RULES: RuleFixture[] = [
  {
    id: "rule-1",
    position: 1,
    condition: { butler: "general" },
    action: { model: "claude-haiku" },
    saved_7d: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
  {
    id: "rule-2",
    position: 2,
    condition: { complexity: "workhorse" },
    action: { model: "claude-sonnet" },
    saved_7d: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
  {
    id: "rule-3",
    position: 3,
    condition: { trigger: "route" },
    action: { max_cost_per_call: 0.5 },
    saved_7d: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
  },
]

describe("SpendPage — routing-rule deletion safety (bu-6jv4m.2)", () => {
  let store: ReturnType<typeof makeDeletableRulesStore>
  let deleteCalls: string[]
  let postBodies: Array<Record<string, unknown>>
  let failDelete: boolean

  beforeEach(() => {
    setHooks()
    store = makeDeletableRulesStore(THREE_RULES)
    deleteCalls = []
    postBodies = []
    failDelete = false
    apiFetchMock.mockReset()
    apiFetchMock.mockImplementation((path: string, opts?: RequestInit) => {
      if (path === "/spend/rules" && opts?.method === "POST") {
        const body = JSON.parse(opts.body as string) as {
          position: number
          condition: Record<string, unknown>
          action: Record<string, unknown>
        }
        postBodies.push(body)
        return Promise.resolve({ data: store.insert(body.position, body.condition, body.action) })
      }
      if (path === "/spend/rules") return Promise.resolve(store.get())
      const match = /^\/spend\/rules\/([^/]+)$/.exec(path)
      if (match && opts?.method === "DELETE") {
        deleteCalls.push(match[1])
        if (failDelete) return Promise.reject(new Error("boom"))
        store.remove(match[1])
        return Promise.resolve(undefined)
      }
      if (match && opts?.method === "PUT") return Promise.resolve({})
      return defaultApiFetch(path)
    })
  })

  afterEach(() => {
    cleanup()
  })

  async function openDeleteDialog(ruleId: string) {
    await act(async () => {
      renderPage()
    })
    const remove = (await screen.findByTestId(`spend-rule-remove-${ruleId}`)) as HTMLButtonElement
    await act(async () => {
      fireEvent.click(remove)
    })
    return remove
  }

  it("does not delete on one activation -- it opens a confirm dialog first", async () => {
    await act(async () => {
      renderPage()
    })
    expect(screen.queryByTestId("spend-rule-delete-dialog")).toBeNull()

    const remove = await screen.findByTestId("spend-rule-remove-rule-2")
    await act(async () => {
      fireEvent.click(remove)
    })

    expect(screen.getByTestId("spend-rule-delete-dialog")).toBeTruthy()
    expect(deleteCalls).toEqual([])
    expect(store.snapshot()).toHaveLength(3)
  })

  it("shows the exact condition, action, position and first-match effect", async () => {
    await openDeleteDialog("rule-2")

    const dialog = screen.getByTestId("spend-rule-delete-dialog")
    const text = dialog.textContent ?? ""
    // Exact condition and action of THIS rule, not a generic warning.
    expect(text).toContain("complexity")
    expect(text).toContain("workhorse")
    expect(text).toContain("claude-sonnet")
    // Its position in the first-match order.
    expect(text).toContain("position 2 of 3")
    // What first-match evaluation does after the deletion.
    expect(text).toContain("trigger")
    expect(text.toLowerCase()).toContain("first match")
  })

  it("names default routing when the deleted rule is the last one", async () => {
    await openDeleteDialog("rule-3")

    const text = screen.getByTestId("spend-rule-delete-dialog").textContent ?? ""
    expect(text).toContain("position 3 of 3")
    expect(text.toLowerCase()).toContain("default")
  })

  it("cancelling deletes nothing and returns focus to the Remove button", async () => {
    const remove = await openDeleteDialog("rule-2")

    const cancel = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent === "Keep rule",
    )!
    await act(async () => {
      fireEvent.click(cancel)
    })

    await waitFor(() => {
      expect(screen.queryByTestId("spend-rule-delete-dialog")).toBeNull()
    })
    expect(deleteCalls).toEqual([])
    await waitFor(() => {
      expect(document.activeElement).toBe(remove)
    })
  })

  it("sends exactly one DELETE even if confirm is activated repeatedly", async () => {
    await openDeleteDialog("rule-2")

    const confirm = screen.getByTestId("spend-rule-delete-dialog-confirm")
    await act(async () => {
      fireEvent.click(confirm)
      fireEvent.click(confirm)
      fireEvent.click(confirm)
    })

    await waitFor(() => {
      expect(deleteCalls).toEqual(["rule-2"])
    })
  })

  it("offers an Undo that restores the rule at its EXACT original position", async () => {
    await openDeleteDialog("rule-2")

    await act(async () => {
      fireEvent.click(screen.getByTestId("spend-rule-delete-dialog-confirm"))
    })

    // Deleted, and the rules below it compacted -- rule-3 is now position 2.
    await waitFor(() => {
      expect(store.snapshot().map((r) => [r.id, r.position])).toEqual([
        ["rule-1", 1],
        ["rule-3", 2],
      ])
    })

    const undo = await screen.findByTestId("spend-rule-undo-button")
    await act(async () => {
      fireEvent.click(undo)
    })

    await waitFor(() => {
      expect(postBodies).toHaveLength(1)
    })
    // The load-bearing assertion: restored at position 2, not appended at 3.
    expect(postBodies[0].position).toBe(2)
    expect(postBodies[0].condition).toEqual({ complexity: "workhorse" })
    expect(postBodies[0].action).toEqual({ model: "claude-sonnet" })

    const restored = store.snapshot()
    expect(restored.map((r) => r.position)).toEqual([1, 2, 3])
    expect(restored[1].condition).toEqual({ complexity: "workhorse" })
    expect(restored[2].id).toBe("rule-3")
  })

  it("clears the Undo affordance once used, so it cannot restore twice", async () => {
    await openDeleteDialog("rule-2")
    await act(async () => {
      fireEvent.click(screen.getByTestId("spend-rule-delete-dialog-confirm"))
    })

    const undo = await screen.findByTestId("spend-rule-undo-button")
    await act(async () => {
      fireEvent.click(undo)
    })

    await waitFor(() => {
      expect(screen.queryByTestId("spend-rule-undo")).toBeNull()
    })
    expect(postBodies).toHaveLength(1)
  })

  it("offers no Undo when the delete failed -- nothing was destroyed to restore", async () => {
    failDelete = true
    await openDeleteDialog("rule-2")

    await act(async () => {
      fireEvent.click(screen.getByTestId("spend-rule-delete-dialog-confirm"))
    })

    await waitFor(() => {
      expect(deleteCalls).toEqual(["rule-2"])
    })
    expect(screen.queryByTestId("spend-rule-undo")).toBeNull()
    expect(store.snapshot()).toHaveLength(3)
  })

  it("serializes the restore behind an in-flight reorder (bu-6jv4m.2 concurrency)", async () => {
    // Delete, restore, and reorder all renumber positions. They share one
    // mutation scope so a restore can never overtake a reorder that is still in
    // flight and land the rule at a position computed from stale ordering.
    const order: string[] = []
    let releaseReorder: (() => void) | undefined
    const reorderGate = new Promise<void>((resolve) => {
      releaseReorder = resolve
    })
    apiFetchMock.mockImplementation((path: string, opts?: RequestInit) => {
      if (path === "/spend/rules" && opts?.method === "POST") {
        order.push("POST")
        const body = JSON.parse(opts.body as string) as {
          position: number
          condition: Record<string, unknown>
          action: Record<string, unknown>
        }
        postBodies.push(body)
        return Promise.resolve({ data: store.insert(body.position, body.condition, body.action) })
      }
      if (path === "/spend/rules") return Promise.resolve(store.get())
      const match = /^\/spend\/rules\/([^/]+)$/.exec(path)
      if (match && opts?.method === "DELETE") {
        deleteCalls.push(match[1])
        store.remove(match[1])
        return Promise.resolve(undefined)
      }
      if (match && opts?.method === "PUT") {
        order.push("PUT")
        return reorderGate.then(() => ({}))
      }
      return defaultApiFetch(path)
    })

    await openDeleteDialog("rule-2")
    await act(async () => {
      fireEvent.click(screen.getByTestId("spend-rule-delete-dialog-confirm"))
    })
    const undo = await screen.findByTestId("spend-rule-undo-button")

    // Start a reorder that will not settle yet, then activate Undo.
    // Separate dispatches: grab, then move. Batching them inside one `act`
    // would run the ArrowUp handler against a pre-grab render.
    const row = screen.getByTestId("spend-rule-row-rule-3")
    fireEvent.keyDown(row, { key: " " })
    fireEvent.keyDown(row, { key: "ArrowUp" })
    await waitFor(() => {
      expect(order).toContain("PUT")
    })

    await act(async () => {
      fireEvent.click(undo)
    })
    // The restore must not have been dispatched while the PUT is unsettled.
    expect(order).toEqual(["PUT"])

    await act(async () => {
      releaseReorder!()
    })
    await waitFor(() => {
      expect(order).toEqual(["PUT", "POST"])
    })
  })
})
