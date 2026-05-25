/**
 * ConnectorDetailPage tests — updated for the Dispatch-language redesign.
 *
 * The old card-based UI has been replaced with a Dispatch-language two-zone
 * layout (ConnectorDetailView). This test file covers the page-level contract:
 * - Correct H1 rendering
 * - Key content rendered (connector type, endpoint, liveness)
 * - Error and not-found states render explicit messages
 * - Loading state renders skeleton (no H1)
 * - Reauth callout renders when auth is broken
 *
 * Component-level tests for ConnectorDetailView (KPI strip, scope list, etc.)
 * live in ConnectorDetailView.test.tsx.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ConnectorDetailPage from "@/pages/ConnectorDetailPage";
import {
  useConnectorDetail,
  useConnectorStats,
} from "@/hooks/use-ingestion";
import type { ConnectorDetail } from "@/api/types";

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({
      connectorType: "gmail",
      endpointIdentity: "user@example.com",
    })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  };
});

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorDetail: vi.fn(),
  useConnectorStats: vi.fn(),
  useConnectorEvents: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
  useConnectorIncidents: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
  useConnectorRoutingRules: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
}));

type UseConnectorDetailResult = ReturnType<typeof useConnectorDetail>;
type UseConnectorStatsResult = ReturnType<typeof useConnectorStats>;

const BASE_CONNECTOR: ConnectorDetail = {
  connector_type: "gmail",
  endpoint_identity: "user@example.com",
  liveness: "online",
  state: "healthy",
  error_message: null,
  version: "1.2.3",
  uptime_s: 3600,
  last_heartbeat_at: "2025-01-15T10:00:00Z",
  first_seen_at: "2025-01-01T00:00:00Z",
  today: { uptime_pct: 99.5, messages_ingested: 50, messages_failed: 0 },
  instance_id: "inst-abc",
  registered_via: "auto",
  checkpoint: { cursor: "token-xyz", updated_at: "2025-01-15T09:00:00Z" },
  counters: {
    messages_ingested: 1000,
    messages_failed: 5,
    source_api_calls: 200,
    checkpoint_saves: 50,
    dedupe_accepted: 10,
  },
  settings: null,
};

const REAUTH_CONNECTOR: ConnectorDetail = {
  ...BASE_CONNECTOR,
  liveness: "offline",
  state: "error",
  error_message: "401 Unauthorized — token expired",
  today: { messages_ingested: 0, messages_failed: 8, uptime_pct: null },
};

function setConnectorState(
  connector: ConnectorDetail | null,
  opts: Partial<UseConnectorDetailResult> = {},
) {
  vi.mocked(useConnectorDetail).mockReturnValue({
    data: connector ? { data: connector } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseConnectorDetailResult);
}

function setStatsState(opts: Partial<UseConnectorStatsResult> = {}) {
  vi.mocked(useConnectorStats).mockReturnValue({
    data: undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseConnectorStatsResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ConnectorDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// H1 contract
// ---------------------------------------------------------------------------

describe("ConnectorDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders exactly one H1 when connector is loaded", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(1);
  });

  it("renders zero H1s in loading state (skeleton, no heading)", () => {
    vi.mocked(useConnectorDetail).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as UseConnectorDetailResult);
    setStatsState();
    const html = renderPage();
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Content rendering
// ---------------------------------------------------------------------------

describe("ConnectorDetailPage — content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders connector type as the H1 display headline", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("gmail");
    expect(html.match(/<h1[^>]*>.*?<\/h1>/s)?.[0]).toContain("gmail");
  });

  it("renders endpoint_identity in the page", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("user@example.com");
  });

  it("renders liveness status in the header band", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("online");
  });

  it("renders lifetime counters", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    // New layout: "lifetime counters" eyebrow + counter values
    expect(html).toContain("lifetime counters");
    expect(html).toContain("1,000"); // messages_ingested lifetime
  });

  it("renders checkpoint cursor in the config block", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("token-xyz");
  });

  it("renders version in the config block", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("1.2.3");
  });

  it("renders KPI strip for today's events", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    // KPI strip shows today.messages_ingested = 50
    expect(html).toContain("50");
    expect(html).toContain("events · today");
  });

  it("renders scope list unavailable state (backend capability not yet present)", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    // ScopeList should show the unavailable state with scopes=null
    expect(html).toContain("scopes-unavailable");
  });

  it("renders breadcrumb link back to /ingestion/connectors", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("/ingestion/connectors");
    expect(html).toContain("ingestion / connectors");
  });

  it("renders reauth callout when connector has error state", () => {
    setConnectorState(REAUTH_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("reauth-callout");
    expect(html).toContain("reauth required");
  });

  it("does NOT render reauth callout when connector is healthy", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).not.toContain("reauth-callout");
  });

  it("renders error message text when connector has one", () => {
    setConnectorState(REAUTH_CONNECTOR);
    setStatsState();
    const html = renderPage();
    // Error note appears in the reauth callout or header
    expect(html).toContain("401 Unauthorized");
  });
});

// ---------------------------------------------------------------------------
// Error + not-found states
// ---------------------------------------------------------------------------

describe("ConnectorDetailPage — error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("shows an error region when connector fetch fails", () => {
    vi.mocked(useConnectorDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Network error"),
    } as UseConnectorDetailResult);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("detail-error");
    expect(html).toContain("Network error");
  });

  it("shows not-found state when connector data is missing after load", () => {
    setConnectorState(null);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("detail-not-found");
    expect(html).toContain("Connector not found");
  });
});
