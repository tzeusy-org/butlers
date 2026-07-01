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
 * - BatchSettingsCard mounts for batch-capable connectors (telegram_user_client,
 *   whatsapp_user_client) and is absent for others (e.g. gmail)
 *
 * Component-level tests for ConnectorDetailView (KPI strip, scope list, etc.)
 * live in ConnectorDetailView.test.tsx.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, useParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ConnectorDetailPage from "@/pages/ConnectorDetailPage";
import {
  useConnectorDetail,
  useConnectorStats,
} from "@/hooks/use-ingestion";
import type { ConnectorDetail } from "@/api/types";

const mockMutate = vi.fn();

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({
      connectorType: "gmail",
      endpointIdentity: "user@example.com",
    })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
    useNavigate: vi.fn(() => vi.fn()),
  };
});

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorDetail: vi.fn(),
  useConnectorStats: vi.fn(),
  useConnectorEvents: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
  useConnectorIncidents: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
  useConnectorRoutingRules: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
  useUpdateConnectorSettings: vi.fn(() => ({
    mutate: mockMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
    isIdle: true,
    error: null,
    data: undefined,
    status: "idle",
  })),
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
  hourly_events: Array(24).fill(0),
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
  auth: null,
  scopes: null,
};

const REAUTH_CONNECTOR: ConnectorDetail = {
  ...BASE_CONNECTOR,
  liveness: "offline",
  state: "error",
  error_message: "401 Unauthorized — token expired",
  today: { messages_ingested: 0, messages_failed: 8, uptime_pct: null },
};

/** Degraded connector with api_forbidden error (real google_health live scenario). */
const API_FORBIDDEN_CONNECTOR: ConnectorDetail = {
  ...BASE_CONNECTOR,
  connector_type: "google_health",
  liveness: "online",
  state: "degraded",
  error_message: "api_forbidden: 403 Forbidden from Google Fit API",
  today: { messages_ingested: 0, messages_failed: 2, uptime_pct: 80 },
};

/** Degraded connector with no_primary_account error. */
const NO_PRIMARY_ACCOUNT_CONNECTOR: ConnectorDetail = {
  ...BASE_CONNECTOR,
  connector_type: "google_health",
  liveness: "online",
  state: "degraded",
  error_message: "no_primary_account",
  today: { messages_ingested: 0, messages_failed: 1, uptime_pct: 90 },
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

  it("renders reauth callout with needs_reauth for degraded+api_forbidden connector", () => {
    setConnectorState(API_FORBIDDEN_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("reauth-callout");
    expect(html).toContain("reauth required");
    // re-authorize button must be present
    expect(html).toContain("reauth-button");
  });

  it("renders recovery callout with set-primary-account for degraded+no_primary_account connector", () => {
    setConnectorState(NO_PRIMARY_ACCOUNT_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("reauth-callout");
    expect(html).toContain("no primary account");
    // set-primary-account button must be present; re-authorize button must NOT
    expect(html).toContain("set-primary-account-button");
    expect(html).not.toContain("reauth-button");
  });

  it("does NOT render reauth callout for degraded connector with non-auth error", () => {
    const degradedOther: ConnectorDetail = {
      ...BASE_CONNECTOR,
      state: "degraded",
      error_message: "rate_limit_exceeded: too many requests",
    };
    setConnectorState(degradedOther);
    setStatsState();
    const html = renderPage();
    // callout must not render — rate limit is not an auth issue
    expect(html).not.toContain("reauth-callout");
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
    // The Page shell owns the error state — check for the error message text.
    expect(html).toContain("Network error");
    expect(html).toContain("Something went wrong");
  });

  it("shows not-found state when connector data is missing after load", () => {
    setConnectorState(null);
    setStatsState();
    const html = renderPage();
    // The Page shell owns the empty state — check for the title/description text.
    expect(html).toContain("Connector not found");
  });
});

// ---------------------------------------------------------------------------
// BatchSettingsCard gate
// ---------------------------------------------------------------------------

describe("ConnectorDetailPage — BatchSettingsCard mount gate", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setStatsState();
  });

  it("renders batch-settings-section for telegram_user_client", () => {
    vi.mocked(useParams).mockReturnValue({
      connectorType: "telegram_user_client",
      endpointIdentity: "test-user",
    });
    setConnectorState({ ...BASE_CONNECTOR, connector_type: "telegram_user_client" });
    const html = renderPage();
    expect(html).toContain("batch-settings-section");
    expect(html).toContain("Batch Settings");
  });

  it("renders batch-settings-section for whatsapp_user_client", () => {
    vi.mocked(useParams).mockReturnValue({
      connectorType: "whatsapp_user_client",
      endpointIdentity: "test-user",
    });
    setConnectorState({ ...BASE_CONNECTOR, connector_type: "whatsapp_user_client" });
    const html = renderPage();
    expect(html).toContain("batch-settings-section");
    expect(html).toContain("Batch Settings");
  });

  it("does NOT render batch-settings-section for gmail", () => {
    vi.mocked(useParams).mockReturnValue({
      connectorType: "gmail",
      endpointIdentity: "user@example.com",
    });
    setConnectorState({ ...BASE_CONNECTOR, connector_type: "gmail" });
    const html = renderPage();
    expect(html).not.toContain("batch-settings-section");
    expect(html).not.toContain("Batch Settings");
  });

  it("does NOT render batch-settings-section for google_drive", () => {
    vi.mocked(useParams).mockReturnValue({
      connectorType: "google_drive",
      endpointIdentity: "user@example.com",
    });
    setConnectorState({ ...BASE_CONNECTOR, connector_type: "google_drive" });
    const html = renderPage();
    expect(html).not.toContain("batch-settings-section");
  });
});
