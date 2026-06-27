// @vitest-environment jsdom
/**
 * Component tests for ConnectorsTab / ConnectorsListPage.
 *
 * Covers:
 * - Renders connector cards when connectors are present
 * - Renders "No connectors registered" when list is empty and no dormant available
 * - Renders dormant/available section when available connectors exist but are not registered (§3.5)
 * - Dormant section is hidden when all catalog connectors are registered
 * - Each dormant card shows display_name and channel
 *
 * §3.5 / §3.12 — Phase 3d (bu-1f91v.9)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
  useCrossConnectorSummary: vi.fn(),
  useConnectorFanout: vi.fn(),
  useIngestionVolume: vi.fn(),
  useAvailableConnectors: vi.fn(),
  usePipelineStats: vi.fn(),
  useDeleteConnector: vi.fn(),
}));

vi.mock("@/hooks/use-backfill", () => ({
  useBackfillJobs: vi.fn(),
}));

import {
  useAvailableConnectors,
  useConnectorSummaries,
  useCrossConnectorSummary,
  useConnectorFanout,
  useIngestionVolume,
  usePipelineStats,
  useDeleteConnector,
} from "@/hooks/use-ingestion";
import { useBackfillJobs } from "@/hooks/use-backfill";
import type { ConnectorProfile, ConnectorSummary } from "@/api/index.ts";

import { ConnectorsTab } from "./ConnectorsTab";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

const MOCK_CONNECTOR: ConnectorSummary = {
  connector_type: "gmail",
  endpoint_identity: "user@example.com",
  liveness: "online",
  state: "healthy",
  error_message: null,
  version: "1.0",
  uptime_s: 3600,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
  first_seen_at: "2026-01-01T00:00:00Z",
  today: { messages_ingested: 42, messages_failed: 1, uptime_pct: 99.5 },
  hourly_events: Array(24).fill(0),
};

const MOCK_PROFILES: ConnectorProfile[] = [
  {
    connector_type: "gmail",
    channel: "email",
    provider: "google",
    display_name: "Gmail",
    supports_backfill: true,
  },
  {
    connector_type: "telegram_bot",
    channel: "telegram",
    provider: "telegram",
    display_name: "Telegram Bot",
    supports_backfill: false,
  },
  {
    connector_type: "spotify",
    channel: "spotify",
    provider: "spotify",
    display_name: "Spotify",
    supports_backfill: false,
  },
];

function makeLoadingResult() {
  return { data: undefined, isLoading: true, isError: false };
}

function makeResult<T>(data: T) {
  return { data, isLoading: false, isError: false };
}

function setupDefaultMocks(
  connectors: ConnectorSummary[] = [],
  profiles: ConnectorProfile[] = MOCK_PROFILES,
) {
  vi.mocked(useConnectorSummaries).mockReturnValue(makeResult({ data: connectors }) as ReturnType<typeof useConnectorSummaries>);
  vi.mocked(useCrossConnectorSummary).mockReturnValue(makeLoadingResult() as ReturnType<typeof useCrossConnectorSummary>);
  vi.mocked(useConnectorFanout).mockReturnValue(makeLoadingResult() as ReturnType<typeof useConnectorFanout>);
  vi.mocked(useIngestionVolume).mockReturnValue(makeLoadingResult() as ReturnType<typeof useIngestionVolume>);
  vi.mocked(useAvailableConnectors).mockReturnValue(makeResult({ data: profiles }) as ReturnType<typeof useAvailableConnectors>);
  vi.mocked(usePipelineStats).mockReturnValue(makeLoadingResult() as ReturnType<typeof usePipelineStats>);
  vi.mocked(useDeleteConnector).mockReturnValue({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    reset: vi.fn(),
  } as unknown as ReturnType<typeof useDeleteConnector>);
  vi.mocked(useBackfillJobs).mockReturnValue(makeResult({ data: [] }) as unknown as ReturnType<typeof useBackfillJobs>);
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe("ConnectorsTab", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.clearAllMocks();
  });

  function render(isActive = true) {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={queryClient}>
            <ConnectorsTab isActive={isActive} />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  // -------------------------------------------------------------------------
  // Active connectors section
  // -------------------------------------------------------------------------

  it("renders 'No connectors registered' when connector list is empty and no dormant", () => {
    setupDefaultMocks([], []); // No registered, no available profiles
    render();
    expect(container.textContent).toContain("No connectors registered");
  });

  it("does NOT show 'No connectors registered' when there are active connectors", () => {
    setupDefaultMocks([MOCK_CONNECTOR]);
    render();
    expect(container.textContent).not.toContain("No connectors registered");
  });

  // -------------------------------------------------------------------------
  // Dormant/available section (§3.5)
  // -------------------------------------------------------------------------

  it("renders dormant/available section when some profiles are not registered", () => {
    // gmail is registered, telegram_bot and spotify are not
    setupDefaultMocks([MOCK_CONNECTOR], MOCK_PROFILES);
    render();

    const dormantSection = container.querySelector("[data-testid='dormant-available-section']");
    expect(dormantSection).not.toBeNull();
  });

  it("shows dormant connector cards for unregistered profile types", () => {
    setupDefaultMocks([MOCK_CONNECTOR], MOCK_PROFILES);
    render();

    // gmail is registered → should NOT appear as dormant
    const gmailDormant = container.querySelector("[data-testid='dormant-connector-gmail']");
    expect(gmailDormant).toBeNull();

    // telegram_bot is NOT registered → should appear as dormant
    const telegramDormant = container.querySelector("[data-testid='dormant-connector-telegram_bot']");
    expect(telegramDormant).not.toBeNull();
  });

  it("shows display_name and channel for dormant connectors", () => {
    setupDefaultMocks([], MOCK_PROFILES); // Nothing registered, all dormant
    render();

    const telegramDormant = container.querySelector("[data-testid='dormant-connector-telegram_bot']");
    expect(telegramDormant).not.toBeNull();
    expect(telegramDormant?.textContent).toContain("Telegram Bot");
    expect(telegramDormant?.textContent).toContain("telegram");
  });

  it("hides dormant section when all catalog profiles are already registered", () => {
    // Register all three connectors from MOCK_PROFILES
    const allRegistered: ConnectorSummary[] = MOCK_PROFILES.map((p) => ({
      ...MOCK_CONNECTOR,
      connector_type: p.connector_type,
      endpoint_identity: "test@example.com",
    }));
    setupDefaultMocks(allRegistered, MOCK_PROFILES);
    render();

    const dormantSection = container.querySelector("[data-testid='dormant-available-section']");
    expect(dormantSection).toBeNull();
  });

  it("hides dormant section when no available profiles are returned", () => {
    setupDefaultMocks([], []); // Empty profiles from API
    render();

    const dormantSection = container.querySelector("[data-testid='dormant-available-section']");
    expect(dormantSection).toBeNull();
  });

  it("shows 'Available — not yet configured' heading when dormant section is visible", () => {
    setupDefaultMocks([], MOCK_PROFILES);
    render();

    expect(container.textContent).toContain("Available: not yet configured");
  });
});
