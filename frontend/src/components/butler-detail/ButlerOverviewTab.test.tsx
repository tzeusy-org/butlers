// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useRef, useState } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter, useLocation } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"

import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab"

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
}))

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}))

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(),
}))

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalActions: vi.fn(),
}))

vi.mock("@/hooks/use-approval-decisions", () => ({
  useApprovalDecisionMutations: vi.fn(),
}))

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerActivityFeed: vi.fn(),
}))

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <span data-testid="time-value">{value}</span>,
}))

vi.mock("@/hooks/use-sessions", () => ({
  useGlobalSessionDetail: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useSessionAggregate: vi.fn(() => ({
    data: { data: { failed_count: 0 }, meta: {} },
    isLoading: false,
    isError: false,
  })),
}))

import { useButler } from "@/hooks/use-butlers"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import { useSpendSummary } from "@/hooks/use-spend"
import { useApprovalActions } from "@/hooks/use-approvals"
import { useApprovalDecisionMutations } from "@/hooks/use-approval-decisions"
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics"

let approveMutate: ReturnType<typeof vi.fn>
let denyMutate: ReturnType<typeof vi.fn>

function useConcurrentApprovalDecisionMock() {
  const [latestPendingId, setLatestPendingId] = useState<string | undefined>(undefined)
  const neverSettles = useRef(new Promise<never>(() => {}))
  const startApproval = (id: string) => {
    setLatestPendingId(id)
    return neverSettles.current
  }

  return {
    approveMut: {
      mutate: startApproval,
      mutateAsync: startApproval,
      isPending: latestPendingId !== undefined,
      variables: latestPendingId,
    },
    denyMut: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, variables: undefined },
    deferMut: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, variables: undefined },
    scheduledDecisions: new Map(),
    scheduleDecision: vi.fn(),
    cancelDecision: vi.fn(),
  } as unknown as ReturnType<typeof useApprovalDecisionMutations>
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location-search">{location.search}</output>
}

function renderOverview(): string {
  const queryClient = new QueryClient()
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerOverviewTab butlerName="general" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function renderOverviewLive() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/butlers/general"]}>
        <ButlerOverviewTab butlerName="general" />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  approveMutate = vi.fn().mockResolvedValue(undefined)
  denyMutate = vi.fn().mockResolvedValue(undefined)
  vi.mocked(useApprovalDecisionMutations).mockReturnValue({
    approveMut: {
      mutate: approveMutate,
      mutateAsync: approveMutate,
      isPending: false,
      variables: undefined,
    },
    denyMut: {
      mutate: denyMutate,
      mutateAsync: denyMutate,
      isPending: false,
      variables: undefined,
    },
    deferMut: { mutate: vi.fn(), isPending: false, variables: undefined },
    scheduledDecisions: new Map(),
    scheduleDecision: vi.fn(),
    cancelDecision: vi.fn(),
  } as unknown as ReturnType<typeof useApprovalDecisionMutations>)

  vi.mocked(useButler).mockReturnValue({
    data: {
      data: {
        name: "general",
        status: "ok",
        port: 40101,
        type: "butler",
        description: "General-purpose assistant",
        sessions_24h: 3,
        modules: [{ name: "memory", enabled: true }],
        schedules: [{ name: "tick", cron: "*/5 * * * *" }],
        skills: ["search"],
        process_facts: {
          container_name: "butlers-general",
          port: 40101,
          registered_duration_seconds: 7200,
          config_path: "roster/general/butler.toml",
        },
      },
      meta: {},
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useButler>)

  vi.mocked(useButlerStatusBoard).mockReturnValue({
    needsYou: [],
    rows: [
      {
        name: "general",
        type: "butler",
        description: "General-purpose assistant",
        status: "ok",
        activity: "idle",
        cellTone: "neutral",
        eligibility: "active",
        quarantineReason: null,
        quarantinedAt: null,
        sessions24h: 7,
        costToday: 1.23,
        loadPct: null,
        activeSessionCount: 0,
        lastRunISO: "2026-05-13T12:00:00Z",
        lastHeartbeatISO: null,
        heartbeatAgeSeconds: null,
        hourlyStripe: [0, 0, 1, 0, 2, 0, 3, 0, 0, 1, 0, 4, 0, 0, 2, 0, 1, 0, 0, 3, 0, 0, 1, 0],
        hourlyTotal: 7,
        hourlyStripeLoading: false,
        hourlyStripeError: false,
        schemaUnreachable: false,
        heartbeatUnavailable: false,
        cadenceSeconds: null,
        cadenceLabel: null,
        silenceSeconds: null,
        cadenceStatus: "unknown",
      },
    ],
    aggregates: {
      total: 1,
      butlerCount: 1,
      stafferCount: 0,
      active: 0,
      offline: 0,
      quarantined: 0,
      overdue: 0,
      totalSessions24h: 7,
      totalSpendToday: 1.23,
      avgLoadPct: null,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      heartbeatSourceError: false,
      registrySourceError: false,
      eligibilityUnavailable: 0,
      hasPerEntryErrors: false,
      costSourceError: false,
      sessionsSourceError: false,
      sourcesPartiallyDegraded: false,
    },
  })

  vi.mocked(useSpendSummary).mockReturnValue({
    data: { data: { by_butler: { general: 1.23 }, total_cost_usd: 1.23 }, meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useSpendSummary>)

  vi.mocked(useApprovalActions).mockReturnValue({
    data: {
      data: [
        {
          id: "approval-1",
          butler: "general",
          tool_name: "send_email",
          tool_args: {},
          status: "pending",
          requested_at: "2026-05-13T12:01:00Z",
          agent_summary: "Send draft follow-up",
        },
      ],
      meta: {},
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useApprovalActions>)

  vi.mocked(useButlerActivityFeed).mockReturnValue({
    data: {
      events: [
        {
          ts: "2026-05-13T12:02:00Z",
          event_type: "session_completed",
          summary: "Completed scheduled tick",
          entity_id: "session-1",
          metadata: {},
        },
        {
          ts: "2026-05-13T12:03:00Z",
          event_type: "memory_write",
          summary: "Stored one memory fact",
          entity_id: "memory-1",
          metadata: {},
        },
      ],
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerActivityFeed>)
})

afterEach(() => cleanup())

describe("ButlerOverviewTab target overview grid", () => {
  it("opens the overview with a labeled butler verdict region", () => {
    const html = renderOverview()

    expect(html).toContain('aria-label="Butler general verdict"')
  })

  it("renders the redesigned panel set", () => {
    const html = renderOverview()
    for (const testId of [
      "panel-status",
      "panel-sessions",
      "panel-spend",
      "panel-awaiting",
      "panel-activity",
      "panel-recent",
      "panel-awaiting-actions",
      "panel-config",
    ]) {
      expect(html).toContain(`data-testid="${testId}"`)
    }
  })

  it("uses relative anchors for the rolling activity stripe instead of clock hours", () => {
    renderOverviewLive()

    const axis = within(screen.getByTestId("panel-activity"))
    for (const label of ["-24h", "-12h", "now"]) {
      expect(axis.getByText(label)).toBeTruthy()
    }
    for (const label of ["00", "03", "06", "09", "12", "15", "18", "21"]) {
      expect(axis.queryByText(label)).toBeNull()
    }
  })

  it("does not render legacy identity/process/heartbeat/modules panels", () => {
    const html = renderOverview()
    expect(html).not.toContain('data-testid="panel-identity"')
    expect(html).not.toContain('data-testid="panel-process"')
    expect(html).not.toContain('data-testid="panel-heartbeat"')
    expect(html).not.toContain('data-testid="panel-modules"')
  })

  it("shows live status, sessions, spend, recent events, approvals, and config", () => {
    const html = renderOverview()
    expect(html).toContain("online · idle")
    expect(html).toContain(">7<")
    expect(html).toContain("$1.23")
    expect(html).toContain("Completed scheduled tick")
    expect(html).toContain("Stored one memory fact")
    expect(html).toContain("Send draft follow-up")
    expect(html).toContain("roster/general/butler.toml")
  })

  it("keeps the target grid free of legacy card wrappers and pid", () => {
    const html = renderOverview()
    expect(html).not.toContain('data-slot="card"')
    expect(html.toLowerCase()).not.toContain("pid")
  })

  it("renders a skeleton grid while butler data loads", () => {
    vi.mocked(useButler).mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useButler>)

    const html = renderOverview()
    expect(html).toContain('data-testid="overview-skeleton"')
  })
})

// ---------------------------------------------------------------------------
// bu-vyjoi correction — HTTP-200 partial sources must suppress Nominal
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab -- partial-source verdicts", () => {
  it("scopes spend to the current butler and names its unavailable spend source", () => {
    vi.mocked(useSpendSummary).mockReturnValue({
      data: {
        data: {
          by_butler: { general: 0 },
          unavailable_butlers: ["general"],
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useSpendSummary>)

    const html = renderOverview()

    expect(useSpendSummary).toHaveBeenCalledWith("today", undefined, undefined, "general")
    expect(html).toContain("general unavailable; spend may be incomplete")
    expect(html).not.toContain("butler-detail-verdict-all-clear")
  })

  it("names a partial approval source returned with HTTP 200", () => {
    vi.mocked(useApprovalActions).mockReturnValue({
      data: {
        data: [],
        meta: {
          total: 0,
          offset: 0,
          limit: 5,
          has_more: false,
          sources_degraded: ["general"],
        },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useApprovalActions>)

    const html = renderOverview()

    expect(html).toContain("general unavailable; approvals may be incomplete")
    expect(html).not.toContain("butler-detail-verdict-all-clear")
  })
})

// ---------------------------------------------------------------------------
// bu-86c4c.18 -- approvals KPI uses meta.total, not the page-size cap
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab -- awaiting KPI uses meta.total", () => {
  it("shows meta.total when it exceeds the fetched page size", () => {
    vi.mocked(useApprovalActions).mockReturnValue({
      data: {
        data: [
          {
            id: "approval-1",
            butler: "general",
            tool_name: "send_email",
            tool_args: {},
            status: "pending",
            requested_at: "2026-05-13T12:01:00Z",
            agent_summary: "Send draft follow-up",
          },
        ],
        meta: { total: 20, offset: 0, limit: 5, has_more: true },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useApprovalActions>)

    const html = renderOverview()
    expect(html).toContain(">20<")
    expect(html).not.toContain(">1<")
  })

  it("falls back to the fetched result length when meta.total is absent", () => {
    vi.mocked(useApprovalActions).mockReturnValue({
      data: {
        data: [
          {
            id: "approval-1",
            butler: "general",
            tool_name: "send_email",
            tool_args: {},
            status: "pending",
            requested_at: "2026-05-13T12:01:00Z",
          },
        ],
        meta: {},
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useApprovalActions>)

    const html = renderOverview()
    expect(html).toContain(">1<")
  })
})

// ---------------------------------------------------------------------------
// bu-86c4c.18 -- Overview signals are doors, not dead ends
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab -- doors", () => {
  it("activity-stripe bars navigate to the Activity tab's Sessions section", () => {
    renderOverviewLive()

    const stripe = screen.getByRole("group", { name: /24-hour activity/i })
    fireEvent.click(within(stripe).getAllByRole("button")[0])

    expect(screen.getByTestId("location-search").textContent).toContain(
      "tab=activity&section=sessions",
    )
  })

  it("session_completed recent-event rows render as a button (opens the session drawer)", () => {
    const html = renderOverview()
    // The session_completed event (entity_id="session-1") renders as a button;
    // the memory_write event (entity_id="memory-1") renders as a link to ?tab=memory.
    expect(html).toContain("<button");
    expect(html).toContain('tab=memory')
  })

  it("awaiting-your-action rows deep-link to /approvals scoped to butler and id", () => {
    const html = renderOverview()
    expect(html).toContain("/approvals?butler=general&amp;id=approval-1")
  })

  it("approves an awaiting action inline", () => {
    renderOverviewLive()

    fireEvent.click(screen.getByRole("button", { name: "Approve Send draft follow-up" }))

    expect(approveMutate).toHaveBeenCalledWith("approval-1")
  })

  it("rejects an awaiting action inline", () => {
    renderOverviewLive()

    fireEvent.click(screen.getByRole("button", { name: "Reject Send draft follow-up" }))

    expect(denyMutate).toHaveBeenCalledWith({ id: "approval-1" })
  })

  it("uses the text-safe red token for the inline reject action", () => {
    const html = renderOverview()

    expect(html).toContain("text-[var(--red-text)]")
    expect(html).not.toContain("text-destructive")
  })

  it("keeps each row pending when two approvals start before either request settles", () => {
    vi.mocked(useApprovalActions).mockReturnValue({
      data: {
        data: [
          {
            id: "approval-1",
            butler: "general",
            tool_name: "send_email",
            tool_args: {},
            status: "pending",
            requested_at: "2026-05-13T12:01:00Z",
            agent_summary: "Prepare first report",
          },
          {
            id: "approval-2",
            butler: "general",
            tool_name: "send_email",
            tool_args: {},
            status: "pending",
            requested_at: "2026-05-13T12:02:00Z",
            agent_summary: "Prepare second report",
          },
        ],
        meta: { total: 2, offset: 0, limit: 5, has_more: false },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useApprovalActions>)

    vi.mocked(useApprovalDecisionMutations).mockImplementation(useConcurrentApprovalDecisionMock)

    renderOverviewLive()

    const firstApprove = screen.getByRole("button", { name: "Approve Prepare first report" })
    const secondApprove = screen.getByRole("button", { name: "Approve Prepare second report" })
    fireEvent.click(firstApprove)
    expect(firstApprove.textContent).toBe("Approving…")

    fireEvent.click(secondApprove)

    expect(firstApprove.textContent).toBe("Approving…")
    expect(secondApprove.textContent).toBe("Approving…")
    expect(firstApprove.hasAttribute("disabled")).toBe(true)
    expect(secondApprove.hasAttribute("disabled")).toBe(true)
  })

  it("keeps a successful decision disabled until the stale preview row is reconciled", async () => {
    let resolveApproval: (() => void) | undefined
    approveMutate.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          resolveApproval = resolve
        }),
    )

    renderOverviewLive()

    const approve = screen.getByRole("button", { name: "Approve Send draft follow-up" })
    fireEvent.click(approve)
    expect(approve.textContent).toBe("Approving…")

    if (!resolveApproval) throw new Error("Expected the approval request to start")
    const resolvePendingApproval = resolveApproval
    await act(async () => {
      resolvePendingApproval()
      await Promise.resolve()
    })

    expect(approve.textContent).toBe("Approving…")
    expect(approve.hasAttribute("disabled")).toBe(true)
  })

  it("restores a row's controls after an inline decision fails", async () => {
    let rejectApproval: ((reason?: unknown) => void) | undefined
    approveMutate.mockImplementationOnce(
      () =>
        new Promise<never>((_resolve, reject) => {
          rejectApproval = reject
        }),
    )

    renderOverviewLive()

    const approve = screen.getByRole("button", { name: "Approve Send draft follow-up" })
    fireEvent.click(approve)
    expect(approve.textContent).toBe("Approving…")
    expect(approve.hasAttribute("disabled")).toBe(true)

    if (!rejectApproval) throw new Error("Expected the approval request to start")
    rejectApproval(new Error("network failure"))

    await waitFor(() => {
      expect(approve.textContent).toBe("Approve")
      expect(approve.hasAttribute("disabled")).toBe(false)
    })
  })
})
