// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
  useConnectorSummariesWithAggregates: vi.fn(),
  usePipelineStats: vi.fn(),
}));

vi.mock("@/hooks/use-ingestion-events", () => ({
  useIngestionWindowRollup: vi.fn(),
}));

import {
  IngestionConnectorsVerdictOpener,
  IngestionFiltersVerdictOpener,
  IngestionTimelineVerdictOpener,
} from "@/components/ingestion/dispatch/IngestionVerdictOpeners";
import {
  useConnectorSummaries,
  useConnectorSummariesWithAggregates,
  usePipelineStats,
} from "@/hooks/use-ingestion";
import { useIngestionWindowRollup } from "@/hooks/use-ingestion-events";

function render(ui: React.ReactElement): string {
  return renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);
}

const healthyConnector = {
  connector_type: "gmail",
  endpoint_identity: "default",
  liveness: "online",
  state: "healthy",
  error_message: null,
  version: "1.0",
  uptime_s: 1,
  last_heartbeat_at: "2026-07-19T00:00:00Z",
  first_seen_at: "2026-07-01T00:00:00Z",
  today: { messages_ingested: 2, messages_failed: 0, uptime_pct: 100 },
  hourly_events: [],
};

const offlineConnector = {
  ...healthyConnector,
  connector_type: "calendar",
  endpoint_identity: "primary",
  liveness: "offline",
};

beforeEach(() => {
  vi.mocked(useIngestionWindowRollup).mockReturnValue({
    data: { events: 12, sessions: 3, cost: 0.41, window: { from: null, to: null } },
    isLoading: false,
    isError: false,
  } as never);
  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: { data: [healthyConnector] },
    isLoading: false,
    isError: false,
  } as never);
  vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue({
    data: { data: { connectors: [healthyConnector] } },
    isLoading: false,
    isError: false,
  } as never);
  vi.mocked(usePipelineStats).mockReturnValue({
    data: {
      aggregates_available: true,
      ingested: 80,
      filtered: 20,
      errored: 0,
      routed_by_butler: {},
      spark24h: [],
      rate1h: 0,
      routed_pct: 0,
      filtered24h: 20,
      failed_total: null,
      replay_pending_total: null,
      written_off_total: null,
      backlog_available: true,
      window: "24h",
    },
    isLoading: false,
    isError: false,
  } as never);
});

describe("Ingestion verdict openers", () => {
  it("makes an attention connector on the timeline a real detail door", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: { data: [offlineConnector] },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionTimelineVerdictOpener range="24h" />);

    expect(html).toContain("calendar · primary needs attention");
    expect(html).toContain('href="/ingestion/connectors/calendar/primary"');
    expect(html).not.toContain("ingestion-timeline-verdict-all-clear");
  });

  it("names connector activity degradation instead of rendering an all-clear", () => {
    vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue({
      data: {
        data: {
          connectors: [healthyConnector],
          hourly_events_available: false,
        },
      },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionConnectorsVerdictOpener />);

    expect(html).toContain("24h connector activity unavailable");
    expect(html).not.toContain("ingestion-connectors-verdict-all-clear");
  });

  it("names a registry fallback on the timeline instead of rendering an all-clear", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: { data: [], meta: { connector_registry_available: false } },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionTimelineVerdictOpener range="24h" />);

    expect(html).toContain("connector registry unavailable");
    expect(html).not.toContain("ingestion-timeline-verdict-all-clear");
  });

  it("names a registry fallback on the connectors roster instead of healthy zero", () => {
    vi.mocked(useConnectorSummariesWithAggregates).mockReturnValue({
      data: { data: { connectors: [], connector_registry_available: false } },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionConnectorsVerdictOpener />);

    expect(html).toContain("connector registry unavailable");
    expect(html).not.toContain("ingestion-connectors-verdict-all-clear");
  });

  it("reports the filters funnel drop without linking back to its current page", () => {
    const html = render(<IngestionFiltersVerdictOpener />);

    expect(html).toContain("gates filtered 20% of 100 signals");
    expect(html).not.toContain('href="/ingestion/filters"');
  });

  it("suppresses the filters all-clear when metrics are degraded in a 200 response", () => {
    vi.mocked(usePipelineStats).mockReturnValue({
      data: { aggregates_available: false },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionFiltersVerdictOpener />);

    expect(html).toContain("pipeline metrics unavailable");
    expect(html).not.toContain("ingestion-filters-verdict-all-clear");
  });
});
