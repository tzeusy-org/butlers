// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, useParams, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, cleanup, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ButlerDetailPage from "@/pages/ButlerDetailPage";
import SystemPage from "@/pages/SystemPage";
import { getAllTabs, isValidTab } from "@/pages/butler-detail-tabs";
import { useButler, useButlers, useRuntimeConfig } from "@/hooks/use-butlers";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useButlerHeartbeats } from "@/hooks/use-system";
import type { ButlerSummary } from "@/api/types";
import type { StatusBoardRow, StatusBoardAggregates } from "@/hooks/use-butler-status-board";

// ---------------------------------------------------------------------------
// bu-86c4c.18 -- one butler console, one tab set
//
// The former resident/operator mode toggle (and this file's ~2600 lines of
// mode-vocabulary coverage) is gone: BASE_TABS_OPERATOR/BASE_TABS_RESIDENT,
// getAllTabs(name, mode), isValidTab(value, name, mode), localStorage
// persistence, and deep-link auto-promotion have all been deleted. There is
// now exactly one tab vocabulary, one command bar (replacing Force Run +
// the Trigger tab), and Sessions/Logs/Config/Skills/Schedules/MCP/State/
// Models/Manage/CRM have been folded into Activity/System (CRM deleted
// outright as a dead panel on every non-relationship butler).
// ---------------------------------------------------------------------------

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ name: "general" })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  };
});

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false })),
  useButlerConfig: vi.fn(() => ({ data: null, isLoading: false })),
  useButlerModules: vi.fn(() => ({ data: null, isLoading: false })),
  useButlerSkills: vi.fn(() => ({ data: null, isLoading: false })),
  useRuntimeConfig: vi.fn(() => ({ data: null, isLoading: false })),
  usePatchRuntimeConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  // Trigger-tick remedy on stale butlers (bu-86c4c.15) -- ButlerHeartbeatTile
  // (rendered by SystemPage in the spec-scenario test below) needs this.
  usePingButler: vi.fn(() => ({ mutate: vi.fn(), isPending: false, variables: undefined })),
  useForceButlerTick: vi.fn(() => ({ mutate: vi.fn(), isPending: false, variables: undefined })),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(() => ({ data: null, isLoading: false })),
  useGlobalSessionDetail: vi.fn(() => ({ data: null, isLoading: false })),
  useSessionAggregate: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useUpcomingDates: vi.fn(() => ({ data: [], isLoading: false })),
}));

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useInstanceFacts: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useDatabaseFacts: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useBackupFacts: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useEgressFacts: vi.fn(() => ({ data: null, isLoading: false, error: null, isForbidden: false })),
  useHealthPosture: vi.fn(() => ({ data: undefined, isPending: false, isError: false, error: null })),
  useInsightDeliveryState: vi.fn(() => ({ data: undefined, isPending: true, isError: false, error: null })),
  useDriftFacts: vi.fn(() => ({ data: undefined, isPending: true, isError: false, error: null })),
  useDeploymentFacts: vi.fn(() => ({ data: undefined, isPending: true, isError: false, error: null })),
  useSystemConditions: vi.fn(() => ({
    data: { data: { conditions: [], total: 0, conditions_available: true } },
    isPending: false,
    isError: false,
  })),
}));

vi.mock("@/hooks/use-healing", () => ({
  useInfraConditionSuppressionCounts: vi.fn(() => ({
    counts: new Map(),
    isLoading: false,
    isError: false,
  })),
}));

vi.mock("@/hooks/use-delegation", () => ({
  useDelegationLedger: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-domain-events", () => ({
  useDomainEventSubscriptions: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useDomainEventDeliveries: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(() => ({ data: null, isLoading: false, isError: false, error: null })),
}));

vi.mock("@/components/topology/TopologyGraph", () => ({
  default: () => <div data-testid="topology-graph-stub" />,
}));

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(() => ({
    rows: [],
    aggregates: { isLoading: false, isError: false, error: null, refetch: vi.fn() },
  })),
}));

vi.mock("@/hooks/use-schedules", () => ({
  useSchedules: vi.fn(() => ({ data: { data: [] }, isLoading: false })),
}));

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: null, isLoading: false })),
  useSetEligibility: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalActions: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerActivityFeed: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerHourlyActivity: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerDailyActivity: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerSessionKinds: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerLatencyStats: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
}));

vi.mock("@/components/chat/ChatPanel", () => ({
  ChatPanel: ({ butlerName, triggerLabel }: { butlerName: string; triggerLabel?: string }) => (
    <div data-testid="chat-panel">{triggerLabel ?? "Chat"}:{butlerName}</div>
  ),
}));

// Mock triggerButler so the command bar does not fire real HTTP requests.
vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    triggerButler: vi.fn(() => Promise.resolve({ success: true, session_id: null, output: "" })),
  };
});

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type UseButlerResult = ReturnType<typeof useButler>;

const BASE_BUTLER: ButlerSummary = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 0,
};

function setButlerState(butler: ButlerSummary | null, opts: Partial<UseButlerResult> = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: butler ? { data: butler } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseButlerResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderPageLive() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
  vi.mocked(useParams).mockReturnValue({ name: "general" });
  setButlerState(BASE_BUTLER);
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Single-H1 contract
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — single-H1 contract", () => {
  it("renders exactly one <h1> element", () => {
    const html = renderPage();
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });

  it("h1 contains the butler name", () => {
    const html = renderPage();
    const h1Match = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1Match).not.toBeNull();
    expect(h1Match![1].toLowerCase()).toContain("general");
  });

  it("tabs block remains inside the primary content — no second h1", () => {
    const html = renderPage();
    expect(html).toContain("Overview");
    expect(html).toContain("Activity");
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// ChatPanel — actions slot placement
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — ChatPanel actions slot", () => {
  it("renders exactly one ChatPanel instance", () => {
    const html = renderPage();
    const occurrences = (html.match(/data-testid="chat-panel"/g) ?? []).length;
    expect(occurrences).toBe(1);
  });

  it("ChatPanel receives the butler name as butlerName prop, labeled Chat (not Prompt)", () => {
    const html = renderPage();
    // "Chat" (not "Prompt") avoids vocabulary collision with the command bar,
    // which is itself the unified prompt-first surface (bu-86c4c.18).
    expect(html).toContain('<div data-testid="chat-panel">Chat:general</div>');
  });
});

// ---------------------------------------------------------------------------
// Status-board archetype contract
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — status-board archetype", () => {
  it("renders the butler-detail-header slot", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-detail-header"');
  });

  it("does NOT render ButlerHeartbeatTile on the butler detail page", () => {
    const html = renderPage();
    expect(html).not.toContain("butler-heartbeat-tile");
  });

  it("renders ButlerDetailActions in the actions slot (single occurrence)", () => {
    const html = renderPage();
    const occurrences = (html.match(/data-testid="butler-detail-actions"/g) ?? []).length;
    expect(occurrences).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Unified command bar (replaces Force Run + Trigger tab + mode toggle)
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — unified command bar in the actions slot", () => {
  it("does not render a duplicated status pill in the actions slot", () => {
    const html = renderPage();
    expect(html).not.toContain('data-testid="butler-status-pill"');
  });

  it("renders the command bar (prompt input + Run button)", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-command-bar"');
    expect(html).toContain('data-testid="butler-command-input"');
    expect(html).toContain('data-testid="butler-force-run"');
  });

  it("renders the pause button in the actions slot", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-pause"');
  });

  it("renders logs, config, and chat actions in the actions slot", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-logs-link"');
    expect(html).toContain('data-testid="butler-config-link"');
    expect(html).toContain("Chat:general");
  });

  it("logs and config links deep-link into the folded Activity/System sections", () => {
    const html = renderPage();
    expect(html).toContain("tab=activity&amp;section=logs");
    expect(html).toContain("tab=system&amp;section=config");
  });

  it("does NOT render the mode toggle (deleted)", () => {
    const html = renderPage();
    expect(html).not.toContain('data-testid="butler-mode-toggle"');
  });

  it("command bar, logs, config, chat, and pause all appear after the h1", () => {
    const html = renderPage();
    const h1Index = html.indexOf("<h1");
    const commandBarIndex = html.indexOf('data-testid="butler-command-bar"');
    const logsIndex = html.indexOf('data-testid="butler-logs-link"');
    const configIndex = html.indexOf('data-testid="butler-config-link"');
    const chatIndex = html.indexOf('data-testid="chat-panel"');
    const pauseIndex = html.indexOf('data-testid="butler-pause"');

    expect(h1Index).toBeGreaterThanOrEqual(0);
    expect(commandBarIndex).toBeGreaterThan(h1Index);
    expect(logsIndex).toBeGreaterThan(h1Index);
    expect(configIndex).toBeGreaterThan(h1Index);
    expect(chatIndex).toBeGreaterThan(h1Index);
    expect(pauseIndex).toBeGreaterThan(h1Index);
  });

  it("does NOT render a Tier-2 hero block above the tabs", () => {
    const html = renderPage();
    expect(html).not.toContain('data-testid="hero"');
    expect(html).toContain("Overview");
  });
});

// ---------------------------------------------------------------------------
// One tab set for every butler (no mode split)
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — one tab set for every butler", () => {
  it("BASE_TABS is exactly Overview, Activity, Approvals, Spend, Memory, System", () => {
    // Use a butler name with no bespoke domain tab (e.g. "general" adds
    // Collections/Entities) so this pins the shared base vocabulary only.
    const tabs = getAllTabs("plain-butler-with-no-domain-tab");
    expect([...tabs]).toEqual(["overview", "activity", "approvals", "spend", "memory", "system"]);
  });

  it("renders the unified tab labels for a plain butler", () => {
    const html = renderPage();
    for (const label of ["Overview", "Activity", "Approvals", "Spend", "Memory", "System"]) {
      expect(html).toContain(`>${label}<`);
    }
  });

  it("does NOT render the deleted CRM tab", () => {
    const html = renderPage();
    expect(html).not.toContain(">CRM<");
  });

  it("does NOT render the deleted Trigger tab", () => {
    const html = renderPage();
    expect(html).not.toContain(">Trigger<");
  });

  it("does NOT render standalone Sessions/Skills/Schedules/MCP/State/Models/Manage tab triggers (folded into Activity/System)", () => {
    // "Logs" and "Config" still appear as header quick-link buttons (deep
    // links into Activity/System), so they are checked separately below --
    // this asserts none of them are top-level TAB TRIGGERS.
    const html = renderPage();
    for (const label of ["Sessions", "Skills", "Schedules", "MCP", "State", "Models", "Manage"]) {
      expect(html).not.toContain(`>${label}<`);
    }
  });

  it("Logs and Config quick-link buttons are not top-level tab triggers", () => {
    const html = renderPage();
    const tabTriggerLabels = Array.from(html.matchAll(/role="tab"[^>]*>([^<]*)</g)).map((m) => m[1]);
    expect(tabTriggerLabels).not.toContain("Logs");
    expect(tabTriggerLabels).not.toContain("Config");
  });

  it("isValidTab accepts every base tab and rejects unknown/null values", () => {
    for (const tab of getAllTabs("general")) {
      expect(isValidTab(tab, "general")).toBe(true);
    }
    expect(isValidTab(null, "general")).toBe(false);
    expect(isValidTab("bogus-tab", "general")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — rendering", () => {
  it("renders the butler name as page title", () => {
    const html = renderPage();
    expect(html).toContain("general");
  });

  it("renders compact route metadata instead of legacy breadcrumbs", () => {
    const html = renderPage();
    expect(html).toContain("/butlers/general");
    expect(html).not.toContain('aria-label="Breadcrumb"');
  });
});

// ---------------------------------------------------------------------------
// Deep-linking: ?tab= param semantics
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — deep-linking via ?tab=", () => {
  it.each(["overview", "activity", "approvals", "spend", "memory", "system"] as const)(
    "?tab=%s is a valid deep-link (isValidTab returns true)",
    (tabKey) => {
      expect(isValidTab(tabKey, "general")).toBe(true);
    },
  );

  it("overview is the default tab when ?tab= is absent", () => {
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
    const html = renderPage();
    expect(html).toContain("Overview");
  });

  it("overview tab active when ?tab=invalid strips to default", () => {
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams("tab=nonexistent"), vi.fn()]);
    const html = renderPage();
    expect(isValidTab("nonexistent", "general")).toBe(false);
    expect(html).toContain("Overview");
  });

  it("setSearchParams is not called during initial render (no spurious history entries)", () => {
    const mockSet = vi.fn();
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams("tab=system"), mockSet]);
    renderPage();
    expect(mockSet).not.toHaveBeenCalled();
  });

  it("?tab=system&section=skills renders the Skills lazy-loaded fallback inside System", () => {
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=system&section=skills"),
      vi.fn(),
    ]);
    const html = renderPage();
    expect(html).toContain("Loading skills...");
  });

  it("?tab=system&section=manage renders the Manage lazy-loaded fallback inside System", () => {
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=system&section=manage"),
      vi.fn(),
    ]);
    const html = renderPage();
    expect(html).toContain("Loading manage...");
  });

  it("?tab=activity&section=sessions renders the Sessions lazy-loaded fallback inside Activity", () => {
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=activity&section=sessions"),
      vi.fn(),
    ]);
    const html = renderPage();
    expect(html).toContain("Loading sessions...");
  });
});

// ---------------------------------------------------------------------------
// Keyboard tab navigation
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — keyboard tab navigation", () => {
  it("maps visible tab positions 1 through 9 to their tab URLs", () => {
    const setSearchParams = vi.fn();
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), setSearchParams]);
    renderPageLive();

    fireEvent.keyDown(window, { key: "1" });
    expect(setSearchParams).toHaveBeenLastCalledWith({}, { replace: true });

    fireEvent.keyDown(window, { key: "2" });
    expect(setSearchParams).toHaveBeenLastCalledWith({ tab: "activity" }, { replace: true });

    fireEvent.keyDown(window, { key: "6" });
    expect(setSearchParams).toHaveBeenLastCalledWith({ tab: "collections" }, { replace: true });

    fireEvent.keyDown(window, { key: "8" });
    expect(setSearchParams).toHaveBeenLastCalledWith({ tab: "system" }, { replace: true });
  });

  it("cycles the current tab with [ and ]", () => {
    const setSearchParams = vi.fn();
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=activity"),
      setSearchParams,
    ]);
    renderPageLive();

    fireEvent.keyDown(window, { key: "[" });
    expect(setSearchParams).toHaveBeenLastCalledWith({}, { replace: true });

    fireEvent.keyDown(window, { key: "]" });
    expect(setSearchParams).toHaveBeenLastCalledWith({ tab: "approvals" }, { replace: true });
  });

  it("wraps bracket cycling from the first and last visible tabs", () => {
    const fromOverview = vi.fn();
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), fromOverview]);
    const overviewPage = renderPageLive();

    fireEvent.keyDown(window, { key: "[" });
    expect(fromOverview).toHaveBeenLastCalledWith({ tab: "system" }, { replace: true });
    overviewPage.unmount();

    const fromSystem = vi.fn();
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=system"),
      fromSystem,
    ]);
    renderPageLive();

    fireEvent.keyDown(window, { key: "]" });
    expect(fromSystem).toHaveBeenLastCalledWith({}, { replace: true });
  });

  it("does not steal number keys from the command input", () => {
    const setSearchParams = vi.fn();
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), setSearchParams]);
    renderPageLive();

    const commandInput = screen.getByTestId("butler-command-input");
    commandInput.focus();
    fireEvent.keyDown(commandInput, { key: "2" });

    expect(setSearchParams).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Spec items 5-6: loading/error forwarded to DetailPage shell
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — loading and error states via DetailPage shell", () => {
  it("renders a loading skeleton (role=status) when butler record is loading", () => {
    setButlerState(null, { isLoading: true, error: null });
    const html = renderPage();
    expect(html).toContain('role="status"');
    expect(html).toContain('aria-label="Loading"');
  });

  it("does NOT render tab triggers during loading", () => {
    setButlerState(null, { isLoading: true, error: null });
    const html = renderPage();
    expect(html).not.toContain('role="tab"');
  });

  it("renders the destructive error card (role=alert) when butler fetch fails", () => {
    setButlerState(null, { isLoading: false, error: new Error("butler not found") });
    const html = renderPage();
    expect(html).toContain('role="alert"');
    expect(html).toContain("butler not found");
  });

  it("renders a Retry button alongside the error card", () => {
    setButlerState(null, { isLoading: false, error: new Error("fetch failed") });
    const html = renderPage();
    expect(html).toContain("Retry");
  });
});

// ---------------------------------------------------------------------------
// Bespoke conditional (domain) tabs
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — bespoke conditional tabs", () => {
  function setButlerName(name: string) {
    vi.mocked(useParams).mockReturnValue({ name });
    setButlerState({ ...BASE_BUTLER, name });
  }

  it.each([
    ["chronicler", "timelines", "Timelines"],
    ["finance", "finances", "Finances"],
    ["home", "devices", "Devices"],
    ["relationship", "contacts", "Contacts"],
    ["travel", "trips", "Trips"],
    ["health", "health", "Measurements"],
    ["education", "reviews", "Reviews"],
  ] as const)("%s butler renders its bespoke %s tab", (butlerName, tabKey, label) => {
    setButlerName(butlerName);
    const html = renderPage();
    expect(html).toContain(`>${label}<`);
    expect(getAllTabs(butlerName)).toContain(tabKey);
  });

  it("a domain tab is NOT rendered for an unrelated butler", () => {
    setButlerName("general");
    const html = renderPage();
    expect(html).not.toContain("Timelines");
    expect(html).not.toContain("Finances");
    expect(getAllTabs("general")).not.toContain("timelines");
  });

  it("switchboard butler renders Routing Log and Registry", () => {
    setButlerName("switchboard");
    const html = renderPage();
    expect(html).toContain("Routing Log");
    expect(html).toContain("Registry");
  });

  it("general butler renders Collections and Entities", () => {
    setButlerName("general");
    const html = renderPage();
    expect(html).toContain("Collections");
    expect(html).toContain("Entities");
  });
});

// ---------------------------------------------------------------------------
// Scenario: status-board archetype resolves on /butlers/{name}
// ---------------------------------------------------------------------------

function makeAggregates(overrides: Partial<StatusBoardAggregates> = {}): StatusBoardAggregates {
  return {
    total: 0,
    butlerCount: 0,
    stafferCount: 0,
    active: 0,
    offline: 0,
    quarantined: 0,
    overdue: 0,
    totalSessions24h: 0,
    totalSpendToday: 0,
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
    ...overrides,
  };
}

function makeRow(name: string, overrides: Partial<StatusBoardRow> = {}): StatusBoardRow {
  return {
    name,
    type: "butler",
    description: null,
    status: "ok",
    activity: "idle",
    cellTone: "neutral",
    eligibility: "active",
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 0,
    costToday: 0,
    loadPct: null,
    activeSessionCount: 0,
    lastRunISO: null,
    lastHeartbeatISO: null,
    heartbeatAgeSeconds: null,
    hourlyStripe: Array(24).fill(0) as number[],
    hourlyTotal: 0,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    cadenceSeconds: null,
    cadenceLabel: null,
    silenceSeconds: null,
    cadenceStatus: "unknown",
    ...overrides,
  };
}

const ROSTER_NAMES = [
  "chronicler",
  "education",
  "finance",
  "general",
  "health",
  "home",
  "lifestyle",
  "messenger",
  "qa",
  "relationship",
  "travel",
  "switchboard",
] as const;

describe("Spec scenario -- no Tier 2 hero block, header precedes tab rail", () => {
  beforeEach(() => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
      rows: [],
      aggregates: makeAggregates(),
    });
  });

  it("does NOT render a data-testid=hero element anywhere on the page", () => {
    const html = renderPage();
    expect(html).not.toContain('data-testid="hero"');
  });

  it("the header slot comes before the tab rail (role=tablist)", () => {
    const html = renderPage();
    const headerIdx = html.indexOf('data-testid="butler-detail-header"');
    const tablistIdx = html.indexOf('role="tablist"');
    expect(headerIdx).toBeGreaterThanOrEqual(0);
    expect(tablistIdx).toBeGreaterThan(headerIdx);
  });

  it("no second h1 element appears between the header slot and the tab rail", () => {
    const html = renderPage();
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });
});

describe("Spec scenario -- sibling nav is owned by the shell PageHeader", () => {
  beforeEach(() => {
    vi.mocked(useParams).mockReturnValue({ name: "health" });
    setButlerState({ ...BASE_BUTLER, name: "health" });
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
      rows: ROSTER_NAMES.map((n) => makeRow(n)),
      aggregates: makeAggregates({ total: ROSTER_NAMES.length }),
    });
  });

  it("does not render the sibling nav inside ButlerDetailPage itself", () => {
    const html = renderPage();
    expect(html).not.toContain('aria-label="Navigate to butler"');
  });

  it("still renders the active butler identity in the detail header", () => {
    const html = renderPage();
    expect(html).toContain(">Health</h1>");
  });
});

describe("Spec scenario -- detail body remains token-only", () => {
  beforeEach(() => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
      rows: ROSTER_NAMES.map((n) => makeRow(n)),
      aggregates: makeAggregates({ total: ROSTER_NAMES.length }),
    });
  });

  it("no data-butler-hue attribute appears in the page body", () => {
    const html = renderPage();
    expect(html).not.toContain("data-butler-hue");
  });

  it("no hex or oklch color literals appear in the rendered page body", () => {
    const html = renderPage();
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}[^;]/);
    expect(html).not.toContain("oklch(");
  });
});

describe("Spec scenario -- redesigned overview owns page KPIs; legacy footer is removed", () => {
  beforeEach(() => {
    vi.mocked(useParams).mockReturnValue({ name: "general" });
    setButlerState({ ...BASE_BUTLER, name: "general", sessions_24h: 7 });
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
      rows: [],
      aggregates: makeAggregates(),
    });
    vi.mocked(useButlers).mockReturnValue({
      data: { data: [{ name: "general", status: "ok", port: 8001, type: "butler", sessions_24h: 7 }] },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  it("does not render the legacy footer aria-label", () => {
    const html = renderPage();
    expect(html).not.toContain('aria-label="KPI summary for general"');
  });

  it("overview grid shows the active butler sessions_24h value and KPI labels", () => {
    const html = renderPage();
    expect(html).toContain(">7<");
    expect(html).toContain("sessions");
    expect(html).toContain("spend");
  });
});

describe("Spec scenario -- overview config placeholder when process facts are null", () => {
  beforeEach(() => {
    setButlerState(BASE_BUTLER);
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
      rows: [],
      aggregates: makeAggregates(),
    });
    vi.mocked(useRuntimeConfig).mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      error: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  it("overview config renders the neutral placeholder glyph when process facts are unknown", () => {
    const html = renderPage();
    expect(html).toContain("--");
  });

  it("overview grid does not collapse or crash when process facts are null", () => {
    const html = renderPage();
    expect(html).toContain("status");
    expect(html).toContain("sessions");
    expect(html).toContain("spend");
    expect(html).toContain("config");
  });
});

describe("Spec scenario -- ButlerHeartbeatTile absent from detail page, present on SystemPage", () => {
  function renderSystemPage(): string {
    const queryClient = new QueryClient();
    return renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <SystemPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  beforeEach(() => {
    vi.mocked(useParams).mockReturnValue({ name: "relationship" });
    setButlerState({ ...BASE_BUTLER, name: "relationship" });
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
      rows: [],
      aggregates: makeAggregates(),
    });
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: { data: { butlers: [] } },
      isLoading: false,
      error: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    vi.mocked(useButlers).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  it("ButlerHeartbeatTile text is NOT present on the butler detail page", () => {
    const html = renderPage();
    expect(html).not.toContain("Butler Heartbeats");
  });

  it("Butler Heartbeats tile DOES render on SystemPage", () => {
    const html = renderSystemPage();
    expect(html).toContain("Butler Heartbeats");
  });
});

// ---------------------------------------------------------------------------
// Responsive tab rail (single unified vocabulary)
// ---------------------------------------------------------------------------

describe("Spec scenario -- responsive tab rail", () => {
  beforeEach(() => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      needsYou: [],
      rows: ROSTER_NAMES.map((n) => makeRow(n)),
      aggregates: makeAggregates({ total: ROSTER_NAMES.length }),
    });
  });

  it("tab rail container has overflow-x-auto and snap-x classes", () => {
    vi.mocked(useParams).mockReturnValue({ name: "general" });
    setButlerState({ ...BASE_BUTLER, name: "general" });

    const { container } = renderPageLive();
    const tablist = container.querySelector('[role="tablist"]');
    expect(tablist).not.toBeNull();
    expect(tablist!.className).toContain("overflow-x-auto");
    expect(tablist!.className).toContain("snap-x");
  });

  it("switchboard tab rail uses the detail-page line treatment and shows every unified + bespoke tab", () => {
    vi.mocked(useParams).mockReturnValue({ name: "switchboard" });
    setButlerState({ ...BASE_BUTLER, name: "switchboard" });

    const { container } = renderPageLive();
    const tablist = container.querySelector('[role="tablist"]');
    expect(tablist).not.toBeNull();
    expect(tablist!.getAttribute("data-variant")).toBe("line");
    expect(tablist!.className).toContain("bg-transparent");
    expect(tablist!.className).not.toContain("bg-muted");

    const triggerLabels = screen.getAllByRole("tab").map((tab) => tab.textContent?.trim());
    expect(triggerLabels).toEqual([
      "Overview",
      "Activity",
      "Approvals",
      "Spend",
      "Memory",
      "Routing Log",
      "Registry",
      "System",
    ]);
  });

  it("every butler has exactly 6 unified tabs plus its own bespoke tab count", () => {
    vi.mocked(useParams).mockReturnValue({ name: "finance" });
    setButlerState({ ...BASE_BUTLER, name: "finance" });

    renderPageLive();
    const triggers = screen.getAllByRole("tab");
    // 6 base tabs + 1 bespoke (finances) = 7
    expect(triggers.length).toBe(7);
  });

  it("Tab key advances focus through every trigger in document order", async () => {
    vi.mocked(useParams).mockReturnValue({ name: "general" });
    setButlerState({ ...BASE_BUTLER, name: "general" });

    const { container } = renderPageLive();
    const tablist = container.querySelector('[role="tablist"]');
    expect(tablist).not.toBeNull();

    const triggers = Array.from(tablist!.querySelectorAll('[role="tab"]'));
    expect(triggers.length).toBeGreaterThanOrEqual(6);

    const user = userEvent.setup();
    let attempts = 0;
    while (!tablist!.contains(document.activeElement) && attempts < 40) {
      await user.tab();
      attempts++;
    }
    expect(tablist!.contains(document.activeElement)).toBe(true);

    const firstIdx = triggers.indexOf(document.activeElement as HTMLElement);
    expect(firstIdx).toBeGreaterThanOrEqual(0);

    for (let i = firstIdx + 1; i < triggers.length; i++) {
      await user.keyboard("{ArrowRight}");
      expect(document.activeElement).toBe(triggers[i]);
    }
  });
});
