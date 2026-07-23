/**
 * Tests for SystemPage.
 *
 * All tests use renderToStaticMarkup with mocked hooks to keep execution fast
 * and avoid network calls.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SystemPage from "@/pages/SystemPage";
import {
  useBackupFacts,
  useDatabaseFacts,
  useDeploymentFacts,
  useDriftFacts,
  useEgressFacts,
  useHealthPosture,
  useInsightDeliveryState,
  useInstanceFacts,
} from "@/hooks/use-system";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import { ApiError } from "@/api/index";

// ---------------------------------------------------------------------------
// Mock all hooks used by SystemPage
// ---------------------------------------------------------------------------

// TopologyGraph uses @xyflow/react which is canvas-based and won't render in
// jsdom/static markup -- mock the whole component to keep tests hermetic.
vi.mock("@/components/topology/TopologyGraph", () => ({
  default: ({
    butlers,
    connectorsError,
  }: {
    butlers: { name: string }[];
    connectorsError?: boolean;
  }) => (
    <div data-testid="topology-graph">
      {butlers.map((b) => <span key={b.name}>{b.name}</span>)}
      {connectorsError && <span data-testid="topology-connectors-error" />}
    </div>
  ),
}));

// Canonical liveness source (bu-86c4c.17): TopologyTile and
// ButlerHeartbeatTile both consume useButlerStatusBoard now, replacing the
// former separate useButlers()/useButlerHeartbeats() fetches.
vi.mock("@/hooks/use-butler-status-board", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-butler-status-board")>();
  return { ...actual, useButlerStatusBoard: vi.fn() };
});
vi.mock("@/hooks/use-ingestion", () => ({ useConnectorSummaries: vi.fn() }));

vi.mock("@/hooks/use-system", () => ({
  useInstanceFacts: vi.fn(),
  useDatabaseFacts: vi.fn(),
  useBackupFacts: vi.fn(),
  useEgressFacts: vi.fn(),
  useHealthPosture: vi.fn(),
  useInsightDeliveryState: vi.fn(),
  useDriftFacts: vi.fn(),
  useDeploymentFacts: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Default hook stubs (all loading)
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

const BOARD_AGGREGATES_DEFAULTS = {
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
  heartbeatSourceError: false,
  registrySourceError: false,
  eligibilityUnavailable: 0,
  hasPerEntryErrors: false,
  costSourceError: false,
  sessionsSourceError: false,
  sourcesPartiallyDegraded: false,
};

function setBoardLoading() {
  vi.mocked(useButlerStatusBoard).mockReturnValue({
    rows: [],
    needsYou: [],
    aggregates: {
      ...BOARD_AGGREGATES_DEFAULTS,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    },
  } as AnyMock);
}

function setBoardSuccess(overrides: Partial<typeof BOARD_AGGREGATES_DEFAULTS> = {}) {
  const row = {
    name: "general",
    type: "butler" as const,
    description: null,
    status: "ok",
    activity: "idle" as const,
    cellTone: "neutral" as const,
    eligibility: "active" as const,
    quarantineReason: null,
    quarantinedAt: null,
    sessions24h: 0,
    costToday: 0,
    loadPct: null,
    activeSessionCount: 0,
    lastRunISO: null,
    lastHeartbeatISO: "2026-01-01T00:00:00Z",
    heartbeatAgeSeconds: 120,
    hourlyStripe: Array(24).fill(0),
    hourlyTotal: 0,
    hourlyStripeLoading: false,
    hourlyStripeError: false,
    schemaUnreachable: false,
    heartbeatUnavailable: false,
    cadenceSeconds: null,
    cadenceLabel: null,
    silenceSeconds: null,
    cadenceStatus: "unknown" as const,
  };
  vi.mocked(useButlerStatusBoard).mockReturnValue({
    rows: [row],
    needsYou: [],
    aggregates: {
      ...BOARD_AGGREGATES_DEFAULTS,
      total: 1,
      butlerCount: 1,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      ...overrides,
    },
  } as AnyMock);
}

function setAllLoading() {
  setBoardLoading();

  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useInstanceFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
  } as AnyMock);

  vi.mocked(useDatabaseFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useBackupFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
  } as AnyMock);

  vi.mocked(useEgressFacts).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
    isForbidden: false,
  } as AnyMock);

  vi.mocked(useHealthPosture).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useInsightDeliveryState).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useDriftFacts).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useDeploymentFacts).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
    error: null,
  } as AnyMock);
}

function setAllSuccess(boardOverrides: Partial<typeof BOARD_AGGREGATES_DEFAULTS> = {}) {
  setBoardSuccess(boardOverrides);

  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useInstanceFacts).mockReturnValue({
    data: { data: { version: "1.0.0", uptime_seconds: 3600, started_at: "2026-01-01T00:00:00Z" }, meta: {} },
    isLoading: false,
    error: null,
  } as AnyMock);

  vi.mocked(useDatabaseFacts).mockReturnValue({
    data: { data: { total_size_bytes: 1024, schemas: [], largest_tables: [], growth_rate_bytes_per_day: null }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useBackupFacts).mockReturnValue({
    data: {
      data: {
        last_backup_at: "2026-01-01T00:00:00Z",
        last_backup_size_bytes: 2048,
        backup_source_reachable: true,
        backup_history: [],
        last_backup_status: "healthy",
        backup_stale: false,
        restore_drill: { checked_at: "2026-01-01T00:00:00Z", result: "pass", detail: "restored 12 tables" },
      },
      meta: {},
    },
    isLoading: false,
    error: null,
  } as AnyMock);

  vi.mocked(useEgressFacts).mockReturnValue({
    data: { data: { actors: [{ actor_id: "anthropic.claude", display_name: "Anthropic Claude API", last_seen_at: "2026-01-01T00:00:00Z", total_calls: 5, data_types: ["session_prompt"] }], catalog_covers_from: null }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    isForbidden: false,
  } as AnyMock);

  vi.mocked(useHealthPosture).mockReturnValue({
    data: { status: "ok", auth: { api_key_auth_enabled: true, export_secret_insecure_default: false } },
    isPending: false,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useInsightDeliveryState).mockReturnValue({
    data: { data: { queued: 2, delivered: 5, failed: 0, last_delivery_at: "2026-06-17T10:00:00Z" }, meta: {} },
    isPending: false,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useDriftFacts).mockReturnValue({
    data: {
      data: {
        checked_at: "2026-06-17T10:00:00Z",
        is_drifted: false,
        drifted: [],
        first_detected_at: null,
        escalated: false,
        drift_check_available: true,
      },
      meta: {},
    },
    isPending: false,
    isError: false,
    error: null,
  } as AnyMock);

  vi.mocked(useDeploymentFacts).mockReturnValue({
    data: {
      data: {
        current: {
          id: "11111111-1111-1111-1111-111111111111",
          git_sha: "abc1234",
          migration_head: "core_163",
          started_at: "2026-06-17T10:00:00Z",
          finished_at: "2026-06-17T10:00:00Z",
          result: "success",
          source: "deploy",
          serving_mode: "image",
          serving_worktree: null,
        },
        recent: [],
        commits_behind_main: 0,
        commits_behind_available: true,
      },
      meta: {},
    },
    isPending: false,
    isError: false,
    error: null,
  } as AnyMock);
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SystemPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SystemPage -- page title and description", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllLoading();
  });

  it("renders the page title 'System'", () => {
    const html = renderPage();
    expect(html).toContain("System");
  });

  it("renders the page description", () => {
    const html = renderPage();
    expect(html).toContain("Your instance, your data, your butlers.");
  });
});

describe("SystemPage -- breadcrumbs", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllLoading();
  });

  it("renders breadcrumbs per spec [bu-ngfzz.4]", () => {
    const html = renderPage();
    // Breadcrumbs are rendered via the Page component with explicit prop
    expect(html).toContain('aria-label="Breadcrumb"');
  });

  it("renders Home breadcrumb link with correct href", () => {
    const html = renderPage();
    expect(html).toContain('href="/"');
    expect(html).toContain(">Home<");
  });

  it("renders System breadcrumb without href (current page)", () => {
    const html = renderPage();
    expect(html).toContain(">System<");
  });
});

describe("SystemPage -- tiles render with mock data", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders Version tile with version data", () => {
    const html = renderPage();
    expect(html).toContain("Version");
    expect(html).toContain("1.0.0");
  });

  it("renders Database Size tile with humanized size data", () => {
    const html = renderPage();
    expect(html).toContain("Database Size");
    expect(html).toContain("1.0 KB");
  });

  it("renders Backups tile", () => {
    const html = renderPage();
    expect(html).toContain("Backups");
  });

  it("renders Data Egress tile with actor data", () => {
    const html = renderPage();
    expect(html).toContain("Data Egress");
    expect(html).toContain("Anthropic Claude API");
  });

  it("renders Butler Heartbeats tile with butler data", () => {
    const html = renderPage();
    expect(html).toContain("Butler Heartbeats");
    expect(html).toContain("general");
  });

  it("renders Deployment tile with the current deployment's git sha (bu-hmdqz.1)", () => {
    const html = renderPage();
    expect(html).toContain("Deployment");
    expect(html).toContain("abc1234");
  });
});

describe("SystemPage -- egress 403 handling", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders 'Owner only' indicator when egress returns 403", () => {
    const forbidden403 = new ApiError("forbidden", "Owner contact not found", 403);
    vi.mocked(useEgressFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: forbidden403,
      isForbidden: true,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("Owner only");
    // Page must not crash -- other tiles still render
    expect(html).toContain("Version");
    expect(html).toContain("Butler Heartbeats");
  });

  it("does not crash or show generic error for 403 on egress", () => {
    const forbidden403 = new ApiError("forbidden", "Owner contact not found", 403);
    vi.mocked(useEgressFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: forbidden403,
      isForbidden: true,
    } as AnyMock);

    const html = renderPage();
    // The generic "Failed to load" text should NOT appear for a 403
    const egressSection = html.slice(html.indexOf("Data Egress"));
    const nextTileIdx = egressSection.indexOf("Butler Heartbeats");
    const egressContent = egressSection.slice(0, nextTileIdx);
    expect(egressContent).not.toContain("Failed to load");
  });
});

describe("SystemPage -- backup source unreachable", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders 'Backup status unavailable' when backup_source_reachable is false", () => {
    vi.mocked(useBackupFacts).mockReturnValue({
      data: { data: { last_backup_at: null, last_backup_size_bytes: null, backup_source_reachable: false, backup_history: [] }, meta: {} },
      isLoading: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("Backup status unavailable");
  });
});

describe("SystemPage -- tile sizing (bu-ozbtv)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllLoading();
  });

  it("wraps EgressCatalogTile in a lg:col-span-3 div (full-width privacy headline)", () => {
    const html = renderPage();
    expect(html).toContain('class="lg:col-span-3 h-full"');
  });

  it("wraps BackupTile in a lg:col-span-2 div", () => {
    const html = renderPage();
    // Two lg:col-span-2 wrappers exist (BackupTile and ButlerHeartbeatTile)
    const matches = html.match(/class="lg:col-span-2 h-full"/g) ?? [];
    expect(matches.length).toBe(2);
  });

  it("wraps ButlerHeartbeatTile in a lg:col-span-2 div", () => {
    const html = renderPage();
    const matches = html.match(/class="lg:col-span-2 h-full"/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("wrapper divs include h-full so cards fill grid-row height", () => {
    const html = renderPage();
    expect(html).not.toContain('"lg:col-span-2"');
    expect(html).not.toContain('"lg:col-span-3"');
  });
});

describe("SystemPage -- topology tile (bu-2okpr.5)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setAllSuccess();
  });

  it("renders the topology graph section below the ownership tiles", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="topology-graph"');
  });

  it("passes butlers data to the topology graph", () => {
    const html = renderPage();
    // The mock TopologyGraph renders butler names as spans
    expect(html).toContain("general");
  });

  it("shows error state when the board query fails with no cached data", () => {
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      needsYou: [],
      aggregates: { ...BOARD_AGGREGATES_DEFAULTS, isLoading: false, isError: true, error: new Error("network error"), refetch: vi.fn() },
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("Failed to load topology data.");
    expect(html).not.toContain('data-testid="topology-graph"');
  });

  it("keeps loading while either the board or connectors are still fetching (|| not &&)", () => {
    // Connectors resolved, board still loading -- topology should still pass isLoading=true
    setBoardLoading();
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: { data: [], meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as AnyMock);

    // The mock TopologyGraph renders regardless of isLoading; the key check is that
    // the component doesn't crash and still renders (it should not show error state).
    const html = renderPage();
    expect(html).toContain('data-testid="topology-graph"');
    expect(html).not.toContain("Failed to load topology data.");
  });

  it("passes connectorsError through to TopologyGraph when connectors fail to load (#2873)", () => {
    // TopologyGraph itself renders the degraded note (see TopologyGraph.test.tsx)
    // -- this wiring test only verifies SystemPage forwards the error flag
    // rather than silently defaulting connectors to an empty array.
    setAllSuccess();
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("connectors down"),
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain('data-testid="topology-graph"');
    expect(html).toContain('data-testid="topology-connectors-error"');
  });
});

describe("SystemPage -- SystemVerdictBanner (bu-86c4c.17)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders a loading skeleton while any source is still loading", () => {
    setAllLoading();
    const html = renderPage();
    expect(html).toContain('data-testid="system-verdict-skeleton"');
    expect(html).toContain('aria-label="Loading instance verdict"');
  });

  it("renders an all-clear verdict line when nothing needs the owner", () => {
    setAllSuccess();
    const html = renderPage();
    expect(html).toContain('data-testid="system-verdict-all-clear"');
    expect(html).toContain("Instance healthy");
    expect(html).toContain("v1.0.0");
    expect(html).toContain("all 1 beating");
  });

  it("keeps the verdict loading while database facts load", () => {
    setAllSuccess();
    vi.mocked(useDatabaseFacts).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain('data-testid="system-verdict-skeleton"');
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("names unavailable database facts instead of rendering all-clear", () => {
    setAllSuccess();
    vi.mocked(useDatabaseFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("database unavailable"),
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("database facts unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("keeps the verdict loading while the egress catalog loads", () => {
    setAllSuccess();
    vi.mocked(useEgressFacts).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      isForbidden: false,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain('data-testid="system-verdict-skeleton"');
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("names unavailable egress catalog on a non-forbidden error", () => {
    setAllSuccess();
    vi.mocked(useEgressFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("egress unavailable"),
      isForbidden: false,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("data egress catalog unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("keeps owner-only egress denial settled and non-failing", () => {
    setAllSuccess();
    vi.mocked(useEgressFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError("forbidden", "Owner contact not found", 403),
      isForbidden: true,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain('data-testid="system-verdict-all-clear"');
    expect(html).not.toContain("data egress catalog unavailable");
  });

  it("renders a ranked problem list when butlers are offline/quarantined/overdue", () => {
    setAllSuccess({ offline: 2, quarantined: 1, overdue: 3 });
    const html = renderPage();
    expect(html).toContain('data-testid="system-verdict-clauses"');
    expect(html).toContain("2 butlers offline");
    expect(html).toContain("1 quarantined");
    expect(html).toContain("3 overdue against their own schedule");
    expect(html).toContain('href="/butlers"');
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces a failed-insights problem instead of staying silent", () => {
    setAllSuccess();
    vi.mocked(useInsightDeliveryState).mockReturnValue({
      data: { data: { queued: 0, delivered: 5, failed: 3, last_delivery_at: null }, meta: {} },
      isPending: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("3 insights failed to deliver");
  });

  it("surfaces backup source unreachable as a problem", () => {
    setAllSuccess();
    vi.mocked(useBackupFacts).mockReturnValue({
      data: { data: { last_backup_at: null, last_backup_size_bytes: null, backup_source_reachable: false, backup_history: [] }, meta: {} },
      isLoading: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("backup source unreachable");
  });

  it("surfaces a corrupt backup artifact instead of a falsely confident all-clear (bu-9r3hd.5)", () => {
    setAllSuccess();
    vi.mocked(useBackupFacts).mockReturnValue({
      data: {
        data: {
          last_backup_at: "2026-01-01T00:00:00Z",
          last_backup_size_bytes: 2048,
          backup_source_reachable: true,
          backup_history: [],
          last_backup_status: "corrupt",
          backup_stale: false,
          restore_drill: { checked_at: null, result: "pending", detail: null },
        },
        meta: {},
      },
      isLoading: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("backup artifact corrupt");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces a stale backup as a problem (bu-9r3hd.5)", () => {
    setAllSuccess();
    vi.mocked(useBackupFacts).mockReturnValue({
      data: {
        data: {
          last_backup_at: "2026-01-01T00:00:00Z",
          last_backup_size_bytes: 2048,
          backup_source_reachable: true,
          backup_history: [],
          last_backup_status: "healthy",
          backup_stale: true,
          restore_drill: { checked_at: "2026-01-01T00:00:00Z", result: "pass", detail: null },
        },
        meta: {},
      },
      isLoading: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("backup is stale");
  });

  it("surfaces a failed restore drill as a problem (bu-9r3hd.5)", () => {
    setAllSuccess();
    vi.mocked(useBackupFacts).mockReturnValue({
      data: {
        data: {
          last_backup_at: "2026-01-01T00:00:00Z",
          last_backup_size_bytes: 2048,
          backup_source_reachable: true,
          backup_history: [],
          last_backup_status: "healthy",
          backup_stale: false,
          restore_drill: {
            checked_at: "2026-01-01T00:00:00Z",
            result: "fail",
            detail: "restore failed",
          },
        },
        meta: {},
      },
      isLoading: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("restore drill failed");
  });

  it("surfaces a never-run restore drill as a problem rather than a fabricated all-clear (bu-9r3hd.5)", () => {
    setAllSuccess();
    vi.mocked(useBackupFacts).mockReturnValue({
      data: {
        data: {
          last_backup_at: "2026-01-01T00:00:00Z",
          last_backup_size_bytes: 2048,
          backup_source_reachable: true,
          backup_history: [],
          last_backup_status: "healthy",
          backup_stale: false,
          restore_drill: { checked_at: null, result: "pending", detail: null },
        },
        meta: {},
      },
      isLoading: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("restore drill never run");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces degraded fleet sources rather than a falsely confident all-clear", () => {
    setAllSuccess({ sourcesPartiallyDegraded: true });
    const html = renderPage();
    expect(html).toContain("some fleet data is degraded or unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces board.isError instead of a falsely confident all-clear (bu-qvnce.1)", () => {
    setAllSuccess();
    vi.mocked(useButlerStatusBoard).mockReturnValue({
      rows: [],
      needsYou: [],
      aggregates: {
        ...BOARD_AGGREGATES_DEFAULTS,
        isLoading: false,
        isError: true,
        error: null,
        refetch: vi.fn(),
      },
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("fleet status unavailable");
    expect(html).toContain('href="/butlers"');
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces instance.isError instead of a falsely confident all-clear (bu-qvnce.1)", () => {
    setAllSuccess();
    vi.mocked(useInstanceFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("instance facts unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces backups.isError instead of a falsely confident all-clear (bu-qvnce.1)", () => {
    setAllSuccess();
    vi.mocked(useBackupFacts).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("backup facts unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces insights.isError instead of a falsely confident all-clear (bu-qvnce.1)", () => {
    setAllSuccess();
    vi.mocked(useInsightDeliveryState).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("insight delivery status unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces a failed deploy instead of a falsely confident all-clear (bu-hmdqz.1)", () => {
    setAllSuccess();
    vi.mocked(useDeploymentFacts).mockReturnValue({
      data: {
        data: {
          current: {
            id: "11111111-1111-1111-1111-111111111111",
            git_sha: "deadbee",
            migration_head: null,
            started_at: "2026-06-17T10:00:00Z",
            finished_at: "2026-06-17T10:00:00Z",
            result: "failed",
          },
          recent: [],
          commits_behind_main: null,
          commits_behind_available: false,
        },
        meta: {},
      },
      isPending: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("last deploy failed");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces a bind-mounted worktree boot as a red problem", () => {
    setAllSuccess();
    vi.mocked(useDeploymentFacts).mockReturnValue({
      data: {
        data: {
          current: {
            id: "11111111-1111-1111-1111-111111111111",
            git_sha: "abc1234",
            migration_head: "core_163",
            started_at: "2026-06-17T10:00:00Z",
            finished_at: "2026-06-17T10:00:00Z",
            result: "success",
            source: "boot",
            serving_mode: "hotreload-worktree",
            serving_worktree: ".worktrees/frozen-checkout",
          },
          recent: [],
          commits_behind_main: 0,
          commits_behind_available: true,
        },
        meta: {},
      },
      isPending: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("boot from bind-mounted worktree .worktrees/frozen-checkout (hotreload)");
    expect(html).toContain("--red-text");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces N commits behind origin/main as a problem (bu-hmdqz.1)", () => {
    setAllSuccess();
    vi.mocked(useDeploymentFacts).mockReturnValue({
      data: {
        data: {
          current: {
            id: "11111111-1111-1111-1111-111111111111",
            git_sha: "abc1234",
            migration_head: "core_163",
            started_at: "2026-06-17T10:00:00Z",
            finished_at: "2026-06-17T10:00:00Z",
            result: "success",
          },
          recent: [],
          commits_behind_main: 16,
          commits_behind_available: true,
        },
        meta: {},
      },
      isPending: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("serving 16 commits behind origin/main");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces an unavailable commits-behind check instead of silence (bu-hmdqz.1)", () => {
    setAllSuccess();
    vi.mocked(useDeploymentFacts).mockReturnValue({
      data: {
        data: {
          current: {
            id: "11111111-1111-1111-1111-111111111111",
            git_sha: "abc1234",
            migration_head: "core_163",
            started_at: "2026-06-17T10:00:00Z",
            finished_at: "2026-06-17T10:00:00Z",
            result: "success",
          },
          recent: [],
          commits_behind_main: null,
          commits_behind_available: false,
        },
        meta: {},
      },
      isPending: false,
      isError: false,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("commits-behind-origin/main check unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces deployments.isError instead of a falsely confident all-clear (bu-hmdqz.1)", () => {
    setAllSuccess();
    vi.mocked(useDeploymentFacts).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("deployment status unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });

  it("surfaces posture.isError instead of a falsely confident all-clear (bu-qvnce.1)", () => {
    setAllSuccess();
    vi.mocked(useHealthPosture).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      error: null,
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("security posture unavailable");
    expect(html).not.toContain('data-testid="system-verdict-all-clear"');
  });
});
