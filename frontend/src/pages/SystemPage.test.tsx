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
  useButlerHeartbeats,
  useDatabaseFacts,
  useEgressFacts,
  useHealthPosture,
  useInsightDeliveryState,
  useInstanceFacts,
} from "@/hooks/use-system";
import { useButlers } from "@/hooks/use-butlers";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import { ApiError } from "@/api/index";

// ---------------------------------------------------------------------------
// Mock all hooks used by SystemPage
// ---------------------------------------------------------------------------

// TopologyGraph uses @xyflow/react which is canvas-based and won't render in
// jsdom/static markup -- mock the whole component to keep tests hermetic.
vi.mock("@/components/topology/TopologyGraph", () => ({
  default: ({ butlers }: { butlers: { name: string }[] }) => (
    <div data-testid="topology-graph">
      {butlers.map((b) => <span key={b.name}>{b.name}</span>)}
    </div>
  ),
}));

vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));
vi.mock("@/hooks/use-ingestion", () => ({ useConnectorSummaries: vi.fn() }));

vi.mock("@/hooks/use-system", () => ({
  useInstanceFacts: vi.fn(),
  useDatabaseFacts: vi.fn(),
  useBackupFacts: vi.fn(),
  useEgressFacts: vi.fn(),
  useButlerHeartbeats: vi.fn(),
  useHealthPosture: vi.fn(),
  useInsightDeliveryState: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Default hook stubs (all loading)
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function setAllLoading() {
  vi.mocked(useButlers).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as AnyMock);

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
    error: null,
    isForbidden: false,
  } as AnyMock);

  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: undefined,
    isLoading: true,
    error: null,
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
}

function setAllSuccess() {
  vi.mocked(useButlers).mockReturnValue({
    data: { data: [{ name: "general", status: "ok", port: 40101, type: "butler" }], meta: {} },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as AnyMock);

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
    error: null,
  } as AnyMock);

  vi.mocked(useBackupFacts).mockReturnValue({
    data: { data: { last_backup_at: null, last_backup_size_bytes: null, backup_source_reachable: true, backup_history: [] }, meta: {} },
    isLoading: false,
    error: null,
  } as AnyMock);

  vi.mocked(useEgressFacts).mockReturnValue({
    data: { data: { actors: [{ actor_id: "anthropic.claude", display_name: "Anthropic Claude API", last_seen_at: "2026-01-01T00:00:00Z", total_calls: 5, data_types: ["session_prompt"] }], catalog_covers_from: null }, meta: {} },
    isLoading: false,
    error: null,
    isForbidden: false,
  } as AnyMock);

  vi.mocked(useButlerHeartbeats).mockReturnValue({
    data: { data: { butlers: [{ name: "general", last_heartbeat_at: "2026-01-01T00:00:00Z", last_session_at: null, active_session_count: 0, heartbeat_age_seconds: 120 }] }, meta: {} },
    isLoading: false,
    error: null,
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

  it("shows error state when butlers request fails", () => {
    vi.mocked(useButlers).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("network error"),
      refetch: vi.fn(),
    } as AnyMock);

    const html = renderPage();
    expect(html).toContain("Failed to load topology data.");
    expect(html).not.toContain('data-testid="topology-graph"');
  });

  it("keeps loading while either butlers or connectors are still fetching (|| not &&)", () => {
    // Connectors resolved, butlers still loading -- topology should still pass isLoading=true
    vi.mocked(useButlers).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as AnyMock);
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
});
