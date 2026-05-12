/**
 * ButlerOverviewTab — Panel-grid restyle tests (bu-t0n03, epic bu-hdavr F.1).
 *
 * Verifies the 7-panel grid layout:
 *   1. identity panel (span=2)    — ButlerMark, name, status badge, description
 *   2. process panel (span=2)     — container, port, duration, config path; no pid
 *   3. heartbeat panel (span=2)   — last heartbeat via <Time>, eligibility badge
 *   4. modules panel (span=2)     — module health badge list
 *   5. cost panel (span=1)        — today's USD cost
 *   6. recent sessions panel (span=3) — up to 5 sessions
 *   7. activity feed panel (span=4)   — event stream from useButlerActivityFeed
 *
 * Key assertions:
 *   - All 7 Panel atoms present by data-testid.
 *   - No <Card> wrapper elements (no data-slot="card").
 *   - No pid field in DOM.
 *   - Timestamps use <Time> (mocked to expose data-testid="time-value").
 *   - Activity feed renders event rows from mocked hook.
 *   - Loading state: skeleton per panel.
 *   - Error state: graceful per-panel fallback.
 *
 * Bead: bu-t0n03
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";

// ---------------------------------------------------------------------------
// Mock all hooks consumed by ButlerOverviewTab
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlerModules: vi.fn(),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(),
  useSetEligibility: vi.fn(),
}));

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(),
}));

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(),
}));

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerActivityFeed: vi.fn(),
}));

// Stub EligibilityTimeline to avoid additional hook chains in SSR tests
vi.mock("@/components/butler-detail/EligibilityTimeline", () => ({
  default: ({ butlerName }: { butlerName: string }) => (
    <div data-testid="eligibility-timeline" data-butler={butlerName} />
  ),
}));

// Stub Time to avoid date formatting in SSR tests
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => <span data-testid="time-value">{value}</span>,
}));

// Stub NotificationFeed to avoid dependency chain
vi.mock("@/components/notifications/notification-feed", () => ({
  NotificationFeed: () => <div data-testid="notification-feed" />,
}));

import { useButler, useButlerModules } from "@/hooks/use-butlers";
import { useRegistry, useSetEligibility } from "@/hooks/use-general";
import { useCostSummary } from "@/hooks/use-costs";
import { useButlerNotifications } from "@/hooks/use-notifications";
import { useButlerHeartbeats } from "@/hooks/use-system";
import { useButlerSessions } from "@/hooks/use-sessions";
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics";

// ---------------------------------------------------------------------------
// Shared mock data
// ---------------------------------------------------------------------------

const BUTLER_OK = {
  name: "general",
  status: "ok",
  port: 40101,
  type: "butler" as const,
  description: "Your everyday assistant for general tasks",
  sessions_24h: 3,
};

const REGISTRY_ACTIVE = {
  name: "general",
  eligibility_state: "active",
  quarantine_reason: null,
};

const REGISTRY_QUARANTINED = {
  name: "general",
  eligibility_state: "quarantined",
  quarantine_reason: "Health check failed: timeout after 30s",
};

const HEARTBEAT_FRESH = {
  name: "general",
  last_heartbeat_at: "2026-05-10T12:00:00Z",
  last_session_at: null,
  active_session_count: 0,
  heartbeat_age_seconds: 30,
  error: null,
};

const HEARTBEAT_STALE = {
  name: "general",
  last_heartbeat_at: "2026-05-10T11:45:00Z",
  last_session_at: null,
  active_session_count: 0,
  heartbeat_age_seconds: 900,
  error: null,
};

const MODULES_OK = [
  { name: "email", enabled: true, status: "connected", error: null },
  { name: "calendar", enabled: true, status: "connected", error: null },
];

const MODULES_WITH_ERROR = [
  { name: "email", enabled: true, status: "connected", error: null },
  { name: "telegram", enabled: true, status: "error", error: "Connection refused" },
];

const ACTIVITY_EVENTS = [
  {
    event_type: "session_completed" as const,
    ts: "2026-05-10T12:00:00Z",
    summary: "Checked emails and replied to 3 messages",
    entity_id: null,
    metadata: {},
  },
  {
    event_type: "memory_write" as const,
    ts: "2026-05-10T11:30:00Z",
    summary: "Saved contact preference for Alice",
    entity_id: "ent-001",
    metadata: {},
  },
];

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

function setupDefaultMocks() {
  vi.mocked(useButler).mockReturnValue({
    data: { data: BUTLER_OK, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
  } as unknown as ReturnType<typeof useButler>);

  vi.mocked(useRegistry).mockReturnValue({
    data: { data: [REGISTRY_ACTIVE], meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useRegistry>);

  vi.mocked(useSetEligibility).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useSetEligibility>);

  vi.mocked(useCostSummary).mockReturnValue({
    data: undefined,
    isLoading: false,
  } as ReturnType<typeof useCostSummary>);

  vi.mocked(useButlerNotifications).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as unknown as ReturnType<typeof useButlerNotifications>);

  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: { data: { butlers: [HEARTBEAT_FRESH] }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as ReturnType<typeof useButlerHeartbeats>);

  vi.mocked(useButlerModules).mockReturnValue({
    data: { data: MODULES_OK, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as ReturnType<typeof useButlerModules>);

  vi.mocked(useButlerSessions).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 5 } },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerSessions>);

  vi.mocked(useButlerActivityFeed).mockReturnValue({
    data: { events: ACTIVITY_EVENTS },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerActivityFeed>);
}

function renderTab(butlerName = "general"): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerOverviewTab butlerName={butlerName} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// 7-panel grid structure
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — panel grid structure", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders all 7 Panel atoms by testid", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="panel-identity"');
    expect(html).toContain('data-testid="panel-process"');
    expect(html).toContain('data-testid="panel-heartbeat"');
    expect(html).toContain('data-testid="panel-modules"');
    expect(html).toContain('data-testid="panel-cost"');
    expect(html).toContain('data-testid="panel-recent-sessions"');
    expect(html).toContain('data-testid="panel-activity-feed"');
  });

  it("renders the outer panel-grid frame container", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="overview-panel-grid"');
  });

  it("renders NO legacy Card wrappers (no data-slot=card)", () => {
    const html = renderTab();
    expect(html).not.toContain('data-slot="card"');
  });

  it("renders NO pid field anywhere in the DOM", () => {
    const html = renderTab();
    // Should not contain the literal text "pid" as a field label
    expect(html.toLowerCase()).not.toMatch(/>\s*pid\s*<\/dt/);
    expect(html.toLowerCase()).not.toMatch(/>\s*pid\s*<\/dt/);
  });
});

// ---------------------------------------------------------------------------
// Identity panel (span=2)
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — identity panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders the ButlerMark glyph with butler name", () => {
    const html = renderTab();
    expect(html).toContain('aria-label="general"');
    expect(html).toContain('title="general"');
  });

  it("renders ButlerMark with fill tone (white color style)", () => {
    const html = renderTab();
    expect(html).toContain("color:white");
  });

  it("renders the butler name", () => {
    const html = renderTab();
    expect(html).toContain("general");
  });

  it("renders the status badge (ButlerStatusBadge) for an ok butler", () => {
    const html = renderTab();
    expect(html).toContain("Up");
  });

  it("renders 'Down' status badge for a down butler", () => {
    vi.mocked(useButler).mockReturnValue({
      data: { data: { ...BUTLER_OK, status: "error" }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as unknown as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).toContain("Down");
  });

  it("renders the description when present", () => {
    const html = renderTab();
    expect(html).toContain("Your everyday assistant for general tasks");
  });

  it("renders description with italic serif styling", () => {
    const html = renderTab();
    expect(html).toContain("italic");
    expect(html).toContain("font-serif");
  });

  it("omits description when not present in butler data", () => {
    vi.mocked(useButler).mockReturnValue({
      data: { data: { ...BUTLER_OK, description: undefined }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as unknown as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).not.toContain("Your everyday assistant for general tasks");
  });

  it("renders the port number in the identity panel", () => {
    const html = renderTab();
    expect(html).toContain("Port");
    expect(html).toContain("40101");
  });
});

// ---------------------------------------------------------------------------
// Process panel (span=2) — no pid
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — process panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders process panel with container, port, registered, config labels", () => {
    const html = renderTab();
    expect(html).toContain("Container");
    expect(html).toContain("Registered");
    expect(html).toContain("Config");
  });

  it("renders process_facts values when present", () => {
    vi.mocked(useButler).mockReturnValue({
      data: {
        data: {
          ...BUTLER_OK,
          process_facts: {
            container_name: "butlers-general",
            port: 40101,
            registered_duration_seconds: 3600,
            config_path: "/app/roster/general/butler.toml",
          },
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as unknown as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).toContain("butlers-general");
    expect(html).toContain("/app/roster/general/butler.toml");
  });

  it("does NOT render a pid field in the process panel", () => {
    // Even if butler data contained a pid, it should never appear
    const html = renderTab();
    // No dt element containing just "pid" (case-insensitive)
    expect(html).not.toContain(">pid<");
    expect(html).not.toContain(">Pid<");
    expect(html).not.toContain(">PID<");
  });
});

// ---------------------------------------------------------------------------
// Heartbeat panel (span=2)
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — heartbeat panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders the Heartbeat panel", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="panel-heartbeat"');
  });

  it("renders 'Last heartbeat' label", () => {
    const html = renderTab();
    expect(html).toContain("Last heartbeat");
  });

  it("renders Fresh pill for a recently-heartbeating butler", () => {
    const html = renderTab();
    expect(html).toContain("Fresh");
  });

  it("renders Stale pill when heartbeat age exceeds 5 minutes", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: { data: { butlers: [HEARTBEAT_STALE] }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useButlerHeartbeats>);

    const html = renderTab();
    expect(html).toContain("Stale");
  });

  it("renders Unknown pill when no heartbeat entry exists", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: { data: { butlers: [] }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useButlerHeartbeats>);

    const html = renderTab();
    expect(html).toContain("Unknown");
  });

  it("renders heartbeat timestamp via <Time> when last_heartbeat_at is present", () => {
    const html = renderTab();
    // Stubbed Time renders the raw ISO value
    expect(html).toContain("2026-05-10T12:00:00Z");
    expect(html).toContain('data-testid="time-value"');
  });

  it("renders 'No heartbeat recorded' when last_heartbeat_at is null", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: {
        data: {
          butlers: [
            {
              ...HEARTBEAT_FRESH,
              last_heartbeat_at: null,
              heartbeat_age_seconds: null,
            },
          ],
        },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useButlerHeartbeats>);

    const html = renderTab();
    expect(html).toContain("No heartbeat recorded");
  });

  it("renders data-testid=heartbeat-row on the heartbeat dd element", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="heartbeat-row"');
  });

  // Eligibility rows live in the heartbeat panel
  it("renders Active eligibility badge when state is active", () => {
    const html = renderTab();
    expect(html).toContain("Active");
  });

  it("renders Quarantined eligibility badge when state is quarantined", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [REGISTRY_QUARANTINED], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).toContain("Quarantined");
  });

  it("renders quarantine reason as muted text when present", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [REGISTRY_QUARANTINED], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).toContain("Health check failed: timeout after 30s");
  });

  it("omits eligibility row when no registry entry exists for this butler", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [{ name: "other", eligibility_state: "active", quarantine_reason: null }], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).not.toContain("Eligibility");
  });

  it("renders the EligibilityTimeline when registry entry exists", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="eligibility-timeline"');
    expect(html).toContain('data-butler="general"');
  });

  it("omits EligibilityTimeline when no registry entry exists", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).not.toContain('data-testid="eligibility-timeline"');
  });
});

// ---------------------------------------------------------------------------
// Modules panel (span=2)
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — modules panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders the modules panel", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="panel-modules"');
  });

  it("renders module-health-grid when modules exist", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="module-health-grid"');
  });

  it("renders a cell for each module", () => {
    const html = renderTab();
    expect(html).toContain("email");
    expect(html).toContain("calendar");
  });

  it("renders the module status in each cell", () => {
    const html = renderTab();
    const count = (html.match(/connected/g) ?? []).length;
    expect(count).toBeGreaterThanOrEqual(2);
  });

  it("renders error status for a failing module", () => {
    vi.mocked(useButlerModules).mockReturnValue({
      data: { data: MODULES_WITH_ERROR, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useButlerModules>);

    const html = renderTab();
    expect(html).toContain("telegram");
    expect(html).toContain("error");
  });

  it("renders 'No modules registered' when module list is empty", () => {
    vi.mocked(useButlerModules).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useButlerModules>);

    const html = renderTab();
    expect(html).toContain("No modules registered");
    expect(html).not.toContain('data-testid="module-health-grid"');
  });

  it("renders skeleton cells when modules are loading", () => {
    vi.mocked(useButlerModules).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof useButlerModules>);

    const html = renderTab();
    expect(html).not.toContain('data-testid="module-health-grid"');
    expect(html).not.toContain("No modules registered");
  });
});

// ---------------------------------------------------------------------------
// Activity feed panel (span=4)
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — activity feed panel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders the activity feed panel", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="panel-activity-feed"');
  });

  it("renders event rows from mocked useButlerActivityFeed", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="activity-feed-list"');
    expect(html).toContain('data-testid="activity-feed-row"');
  });

  it("renders event summary text", () => {
    const html = renderTab();
    expect(html).toContain("Checked emails and replied to 3 messages");
    expect(html).toContain("Saved contact preference for Alice");
  });

  it("renders event type badges for session and memory events", () => {
    const html = renderTab();
    expect(html).toContain("session");
    expect(html).toContain("memory");
  });

  it("renders timestamps via <Time> for each event", () => {
    const html = renderTab();
    // Stubbed Time renders raw ISO value
    expect(html).toContain("2026-05-10T12:00:00Z");
    expect(html).toContain("2026-05-10T11:30:00Z");
  });

  it("renders 'No recent activity.' empty state when events list is empty", () => {
    vi.mocked(useButlerActivityFeed).mockReturnValue({
      data: { events: [] },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useButlerActivityFeed>);

    const html = renderTab();
    expect(html).toContain("No recent activity.");
    expect(html).not.toContain('data-testid="activity-feed-list"');
  });

  it("empty state has no em-dash", () => {
    vi.mocked(useButlerActivityFeed).mockReturnValue({
      data: { events: [] },
      isLoading: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useButlerActivityFeed>);

    const html = renderTab();
    expect(html).not.toContain("—"); // em-dash
    expect(html).not.toContain("&mdash;");
  });

  it("renders loading skeleton when activity feed is loading", () => {
    vi.mocked(useButlerActivityFeed).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useButlerActivityFeed>);

    const html = renderTab();
    expect(html).toContain('data-testid="activity-feed-loading"');
    expect(html).not.toContain('data-testid="activity-feed-list"');
  });

  it("renders error fallback when activity feed request fails", () => {
    vi.mocked(useButlerActivityFeed).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network error"),
    } as unknown as ReturnType<typeof useButlerActivityFeed>);

    const html = renderTab();
    expect(html).toContain("Could not load activity feed.");
    expect(html).toContain('data-testid="error-state-line"');
  });
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — loading state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders overview-skeleton when butler data is loading", () => {
    vi.mocked(useButler).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as unknown as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).toContain('data-testid="overview-skeleton"');
    // No live content should appear
    expect(html).not.toContain("40101");
    expect(html).not.toContain("Your everyday assistant");
  });

  it("skeleton does not render Panel grid or panel testids", () => {
    vi.mocked(useButler).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as unknown as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).not.toContain('data-testid="panel-identity"');
    expect(html).not.toContain('data-testid="overview-panel-grid"');
  });
});
