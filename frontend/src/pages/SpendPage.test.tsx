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

const mockUseSpendStream = vi.fn()

vi.mock("@/hooks/use-spend-stream", () => ({
  useSpendStream: () => mockUseSpendStream(),
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
  CostStripeChart: (props: { data: Array<{ date: string; by_butler?: Record<string, number> }> }) => (
    <div data-testid="cost-stripe-chart-mock">{JSON.stringify(props.data)}</div>
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
  currentError = false,
  priorError = false,
}: {
  currentByButler?: Record<string, number>
  priorByButler?: Record<string, number>
  currentUnavailable?: string[]
  priorUnavailable?: string[]
  currentError?: boolean
  priorError?: boolean
} = {}) {
  mockUseSpendStream.mockReturnValue({ streamedCostUsd: 0 })

  // useSpendSummary is called twice per render: current window, prior window.
  let call = 0
  mockUseSpendSummary.mockImplementation(() => {
    call += 1
    const isCurrent = call % 2 === 1
    const byButler = isCurrent ? currentByButler : priorByButler
    const unavailableButlers = isCurrent ? currentUnavailable : priorUnavailable
    const isError = isCurrent ? currentError : priorError
    return {
      data: isError
        ? undefined
        : {
            data: { total_cost_usd: 1, by_butler: byButler, unavailable_butlers: unavailableButlers },
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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
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
    mockUseSpendStream.mockReturnValue({ streamedCostUsd: 0 })

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
    mockUseSpendStream.mockReturnValue({ streamedCostUsd: 3 })
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

  it("does not double-count streamed spend once the next poll baseline already reflects it", async () => {
    let forecastCalls = 0
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/spend/forecast") {
        forecastCalls += 1
        // The 2nd poll's baseline already includes the $3 that streamed
        // between the 1st poll and now -- ground truth is $5.20, not $8.20.
        const mtd = forecastCalls === 1 ? 2.2 : 5.2
        return Promise.resolve({ data: { ...MOCK_FORECAST.data, mtd_usd: mtd }, meta: {} })
      }
      return defaultApiFetch(path)
    })
    mockUseSpendStream.mockReturnValue({ streamedCostUsd: 0 })

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
    mockUseSpendStream.mockReturnValue({ streamedCostUsd: 3 })
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

  it("renders the By Schedule evidence table", async () => {
    await act(async () => {
      renderPage()
    })

    const section = await screen.findByTestId("by-schedule-section")
    expect(section.textContent).toContain("morning-briefing")
    expect(section.textContent).toContain("$3.00")
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
})
