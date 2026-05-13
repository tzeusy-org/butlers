import { beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { MemoryRouter } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab"

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
}))

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}))

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(),
}))

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalActions: vi.fn(),
}))

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerActivityFeed: vi.fn(),
}))

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <span data-testid="time-value">{value}</span>,
}))

import { useButler } from "@/hooks/use-butlers"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import { useCostSummary } from "@/hooks/use-costs"
import { useApprovalActions } from "@/hooks/use-approvals"
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics"

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

beforeEach(() => {
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
    rows: [
      {
        name: "general",
        type: "butler",
        description: "General-purpose assistant",
        status: "ok",
        activity: "idle",
        cellTone: "neutral",
        eligibility: "active",
        sessions24h: 7,
        costToday: 1.23,
        loadPct: null,
        lastRunISO: "2026-05-13T12:00:00Z",
        hourlyStripe: [0, 0, 1, 0, 2, 0, 3, 0, 0, 1, 0, 4, 0, 0, 2, 0, 1, 0, 0, 3, 0, 0, 1, 0],
      },
    ],
    aggregates: {
      total: 1,
      butlerCount: 1,
      stafferCount: 0,
      active: 0,
      paused: 0,
      awaiting: 0,
      quarantined: 0,
      totalSessions24h: 7,
      totalSpendToday: 1.23,
      avgLoadPct: null,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    },
  })

  vi.mocked(useCostSummary).mockReturnValue({
    data: { data: { by_butler: { general: 1.23 }, total_cost_usd: 1.23 }, meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useCostSummary>)

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
          source_id: "session-1",
        },
        {
          ts: "2026-05-13T12:03:00Z",
          event_type: "memory_write",
          summary: "Stored one memory fact",
          source_id: "memory-1",
        },
      ],
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerActivityFeed>)
})

describe("ButlerOverviewTab target overview grid", () => {
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
