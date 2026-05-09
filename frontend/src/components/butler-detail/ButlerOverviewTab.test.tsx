/**
 * ButlerOverviewTab — identity card, heartbeat row, and module health card tests.
 *
 * Pins the required elements:
 *   Identity card:
 *   1. Butler name
 *   2. Status badge
 *   3. Description (serif italic, when present)
 *   4. Port
 *   5. Eligibility state (with quarantine reason when quarantined)
 *   6. 24h eligibility timeline (EligibilityTimeline)
 *   Heartbeat row (bu-8hbph.3):
 *   7. Heartbeat row visible with freshness pill
 *   8. Timestamp shown when heartbeat exists
 *   9. "No heartbeat recorded" when no heartbeat
 *   Module health card (bu-8hbph.3):
 *   10. Per-module grid rendered when modules exist
 *   11. "No modules registered" when empty
 *
 * Beads: bu-8hbph.1, bu-8hbph.3
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

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
  } as ReturnType<typeof useButler>);

  vi.mocked(useRegistry).mockReturnValue({
    data: { data: [REGISTRY_ACTIVE], meta: {} },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useRegistry>);

  vi.mocked(useSetEligibility).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as ReturnType<typeof useSetEligibility>);

  vi.mocked(useCostSummary).mockReturnValue({
    data: undefined,
    isLoading: false,
  } as ReturnType<typeof useCostSummary>);

  vi.mocked(useButlerNotifications).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
  } as ReturnType<typeof useButlerNotifications>);

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
}

function renderTab(butlerName = "general"): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <ButlerOverviewTab butlerName={butlerName} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — identity card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  // -------------------------------------------------------------------------
  // Element 1: Butler name
  // -------------------------------------------------------------------------

  it("renders the butler name in the identity card", () => {
    const html = renderTab();
    expect(html).toContain("general");
  });

  // -------------------------------------------------------------------------
  // Element 2: Status badge
  // -------------------------------------------------------------------------

  it("renders the status badge (ButlerStatusBadge) for an ok butler", () => {
    const html = renderTab();
    // ButlerStatusBadge renders "Up" for status=ok
    expect(html).toContain("Up");
  });

  it("renders 'Down' status badge for a down butler", () => {
    vi.mocked(useButler).mockReturnValue({
      data: { data: { ...BUTLER_OK, status: "error" }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).toContain("Down");
  });

  // -------------------------------------------------------------------------
  // Element 3: Description (serif italic when present)
  // -------------------------------------------------------------------------

  it("renders the description when present", () => {
    const html = renderTab();
    expect(html).toContain("Your everyday assistant for general tasks");
  });

  it("renders description with serif italic styling", () => {
    const html = renderTab();
    // CardDescription receives className="italic font-[family-name:var(--font-serif,serif)]"
    // which ends up in the rendered class attribute
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
    } as ReturnType<typeof useButler>);

    const html = renderTab();
    expect(html).not.toContain("Your everyday assistant for general tasks");
  });

  // -------------------------------------------------------------------------
  // Element 4: Port
  // -------------------------------------------------------------------------

  it("renders the port number in the identity card", () => {
    const html = renderTab();
    expect(html).toContain("Port");
    expect(html).toContain("40101");
  });

  // -------------------------------------------------------------------------
  // Element 5: Eligibility state
  // -------------------------------------------------------------------------

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

  it("omits quarantine reason when state is active", () => {
    const html = renderTab();
    // No quarantine_reason on the active registry entry
    expect(html).not.toContain("Health check failed");
  });

  it("omits eligibility row when no registry entry exists for this butler", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [{ name: "other", eligibility_state: "active", quarantine_reason: null }], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    // No eligibility row should render when registry entry is missing
    expect(html).not.toContain("Eligibility");
  });

  // -------------------------------------------------------------------------
  // Element 6: 24h eligibility timeline
  // -------------------------------------------------------------------------

  it("renders the EligibilityTimeline when registry entry exists", () => {
    const html = renderTab();
    expect(html).toContain('data-testid="eligibility-timeline"');
    expect(html).toContain('data-butler="general"');
  });

  it("omits EligibilityTimeline when no registry entry exists for this butler", () => {
    vi.mocked(useRegistry).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useRegistry>);

    const html = renderTab();
    expect(html).not.toContain('data-testid="eligibility-timeline"');
  });

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

  it("renders loading skeleton when butler data is loading", () => {
    vi.mocked(useButler).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn().mockResolvedValue(undefined),
    } as ReturnType<typeof useButler>);

    const html = renderTab();
    // The skeleton renders without crashing; content elements are absent
    expect(html).not.toContain("40101");
    expect(html).not.toContain("Your everyday assistant");
  });
});

// ---------------------------------------------------------------------------
// Heartbeat row (bu-8hbph.3)
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — heartbeat row", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders the Heartbeat label in the identity card", () => {
    const html = renderTab();
    expect(html).toContain("Heartbeat");
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

  it("renders Unknown pill when no heartbeat entry exists for this butler", () => {
    vi.mocked(useButlerHeartbeats).mockReturnValue({
      data: { data: { butlers: [] }, meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useButlerHeartbeats>);

    const html = renderTab();
    expect(html).toContain("Unknown");
  });

  it("renders timestamp when last_heartbeat_at is present", () => {
    const html = renderTab();
    // Stubbed Time renders the raw value
    expect(html).toContain("2026-05-10T12:00:00Z");
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
});

// ---------------------------------------------------------------------------
// Module health card (bu-8hbph.3)
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — module health card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setupDefaultMocks();
  });

  it("renders Module Health card heading", () => {
    const html = renderTab();
    expect(html).toContain("Module Health");
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
    // Both modules have status "connected"
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
    } as ReturnType<typeof useButlerModules>);

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
    // Skeleton renders without the grid testid
    expect(html).not.toContain('data-testid="module-health-grid"');
    expect(html).not.toContain("No modules registered");
  });
});
