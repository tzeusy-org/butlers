import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ConnectorDetailPage from "@/pages/ConnectorDetailPage";
import {
  useConnectorDetail,
  useConnectorStats,
  useUpdateConnectorCursor,
  useUpdateConnectorSettings,
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
  useUpdateConnectorCursor: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUpdateConnectorSettings: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

// Stub complex child components to avoid deep dependency chains in SSR tests
vi.mock("@/components/ingestion/LivenessBadge", () => ({
  LivenessBadge: ({ liveness, state }: { liveness: string; state: string }) => (
    <span data-testid="liveness-badge">{liveness} {state}</span>
  ),
}));

vi.mock("@/components/ingestion/VolumeTrendChart", () => ({
  VolumeTrendChart: () => <div data-testid="volume-trend-chart" />,
}));

vi.mock("@/components/ingestion/ConnectorRulesSection", () => ({
  ConnectorRulesSection: () => <div data-testid="connector-rules-section" />,
}));

vi.mock("@/components/ingestion/BatchSettingsCard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ingestion/BatchSettingsCard")>();
  return {
    ...actual,
    BatchSettingsCard: () => <div data-testid="batch-settings-card" />,
  };
});

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

describe("ConnectorDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useUpdateConnectorCursor).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateConnectorCursor>);
    vi.mocked(useUpdateConnectorSettings).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateConnectorSettings>);
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
    // In loading state, Page renders a skeleton (no H1 from HeadingBlock)
    expect(html.match(/<h1[^>]*>/g) ?? []).toHaveLength(0);
  });
});

describe("ConnectorDetailPage — content", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useUpdateConnectorCursor).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateConnectorCursor>);
    vi.mocked(useUpdateConnectorSettings).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateConnectorSettings>);
  });

  it("renders connector type as the H1 title", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("gmail");
    expect(html.match(/<h1[^>]*>.*?<\/h1>/s)?.[0]).toContain("gmail");
  });

  it("renders endpoint_identity as page description", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("user@example.com");
  });

  it("renders version in status card", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("Version 1.2.3");
  });

  it("renders liveness badge", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("online");
    expect(html).toContain("healthy");
  });

  it("renders lifetime counters when present", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("Lifetime Counters");
    expect(html).toContain("Ingested");
  });

  it("renders checkpoint cursor", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("Checkpoint Cursor");
    expect(html).toContain("token-xyz");
  });

  it("renders discretion settings card", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("Discretion Settings");
  });

  it("renders volume trend chart", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("volume-trend-chart");
  });

  it("renders connector rules section", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("connector-rules-section");
  });

  it("renders breadcrumbs to Ingestion and Connectors", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("/ingestion");
    expect(html).toContain("/ingestion?tab=connectors");
  });

  it("renders error message when connector has one", () => {
    setConnectorState({
      ...BASE_CONNECTOR,
      error_message: "Auth token expired",
    });
    setStatsState();
    const html = renderPage();
    expect(html).toContain("Auth token expired");
  });

  it("does not render back button (replaced by Page breadcrumbs)", () => {
    setConnectorState(BASE_CONNECTOR);
    setStatsState();
    const html = renderPage();
    expect(html).not.toContain("Back to Connectors");
  });

  it("renders period summary when stats are present", () => {
    setConnectorState(BASE_CONNECTOR);
    vi.mocked(useConnectorStats).mockReturnValue({
      data: {
        data: {
          connector_type: "gmail",
          endpoint_identity: "user@example.com",
          period: "24h",
          summary: {
            messages_ingested: 120,
            messages_failed: 2,
            error_rate_pct: 1.6,
            uptime_pct: 99.5,
            avg_messages_per_hour: 5.0,
          },
          timeseries: [],
        },
      },
      isLoading: false,
      error: null,
    } as unknown as UseConnectorStatsResult);
    const html = renderPage();
    expect(html).toContain("Period Summary");
  });
});

describe("ConnectorDetailPage — error state", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useUpdateConnectorCursor).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateConnectorCursor>);
    vi.mocked(useUpdateConnectorSettings).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateConnectorSettings>);
  });

  it("shows an error region when connector fetch fails", () => {
    vi.mocked(useConnectorDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Network error"),
    } as UseConnectorDetailResult);
    setStatsState();
    const html = renderPage();
    expect(html).toContain("Something went wrong");
    expect(html).toContain("Network error");
  });
});
