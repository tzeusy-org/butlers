/**
 * ButlerOverviewTab — config/process facts panel regression tests.
 *
 * Asserts:
 *   1. The config panel renders the process facts currently exposed by the API.
 *   2. Values from the mock data are visible in the rendered output.
 *   3. No "pid" label or value appears anywhere in the rendered panel.
 *
 * Bead: bu-8hbph.2
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";
import { useButler } from "@/hooks/use-butlers";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import type { ButlerDetail } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks — silence heavy hooks that are out of scope for this test
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlerModules: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(),
}));

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(() => ({ data: null, isLoading: false, error: null })),
}));

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: null, isLoading: false })),
  useSetEligibility: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalActions: vi.fn(() => ({ data: { data: [], meta: {} }, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerActivityFeed: vi.fn(() => ({ data: { events: [] }, isLoading: false, isError: false, error: null })),
}));

// Stub components that require full DOM / router context not needed here.
vi.mock("@/components/notifications/notification-feed", () => ({
  NotificationFeed: () => <div data-testid="notification-feed" />,
}));

vi.mock("@/components/butler-detail/EligibilityTimeline", () => ({
  default: () => <div data-testid="eligibility-timeline" />,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type UseButlerResult = ReturnType<typeof useButler>;

const BASE_BUTLER: ButlerDetail = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 3,
  modules: [],
  schedules: [],
  skills: [],
  process_facts: {
    container_name: "butlers-up",
    port: 8001,
    registered_duration_seconds: 3723,
    config_path: "roster/general/butler.toml",
  },
};

function setButlerState(butler: ButlerDetail | null, opts: Partial<UseButlerResult> = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: butler ? { data: butler, meta: {} } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseButlerResult);

  vi.mocked(useButlerStatusBoard).mockReturnValue({
    rows: [
      {
        name: "general",
        type: "butler",
        description: null,
        status: "ok",
        activity: "idle",
        cellTone: "neutral",
        eligibility: "active",
        sessions24h: butler?.sessions_24h ?? 0,
        costToday: 0,
        loadPct: null,
        lastRunISO: null,
        hourlyStripe: Array(24).fill(0),
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
      totalSessions24h: butler?.sessions_24h ?? 0,
      totalSpendToday: 0,
      avgLoadPct: null,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    },
  });
}

function renderTab(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerOverviewTab butlerName="general" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — config/process facts panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setButlerState(BASE_BUTLER);
  });

  it("renders the process-backed config labels", () => {
    const html = renderTab();
    expect(html).toContain("port");
    expect(html).toContain("registered");
    expect(html).toContain("modules");
    expect(html).toContain("schedules");
    expect(html).toContain("skills");
  });

  it("renders the config_path value in the panel sublabel", () => {
    const html = renderTab();
    expect(html).toContain("roster/general/butler.toml");
  });

  it("renders the port value", () => {
    const html = renderTab();
    // Port 8001 should appear at least once in the process facts section
    expect(html).toContain("8001");
  });

  it("renders the registered duration in compact hours", () => {
    const html = renderTab();
    // Current compact grid floors runtime to whole hours.
    expect(html).toContain("1h");
  });

  it("does NOT render any pid label or value", () => {
    const html = renderTab();
    // Check for common pid representations (case-insensitive via lowercase)
    expect(html.toLowerCase()).not.toContain("pid");
    expect(html.toLowerCase()).not.toContain("process id");
    expect(html.toLowerCase()).not.toContain("process-id");
  });

  it("falls back to the butler port when process_facts is null", () => {
    setButlerState({ ...BASE_BUTLER, process_facts: null });
    const html = renderTab();
    expect(html).toContain("8001");
    expect(html).toContain("--");
  });

  it("renders '--' for registered when registered_duration_seconds is null", () => {
    setButlerState({
      ...BASE_BUTLER,
      process_facts: {
        container_name: "butlers-up",
        port: 8001,
        registered_duration_seconds: null,
        config_path: "roster/general/butler.toml",
      },
    });
    const html = renderTab();
    expect(html).toContain("--");
  });
});
