// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
  usePipelineStats: vi.fn(),
}));

vi.mock("@/hooks/use-ingestion-events", () => ({
  useIngestionWindowRollup: vi.fn(),
  useIngestionDroppedKnown: vi.fn(),
}));

import {
  IngestionConnectorsVerdictOpener,
  IngestionFiltersVerdictOpener,
  IngestionTimelineVerdictOpener,
} from "@/components/ingestion/dispatch/IngestionVerdictOpeners";
import {
  useConnectorSummaries,
  usePipelineStats,
} from "@/hooks/use-ingestion";
import { useIngestionDroppedKnown, useIngestionWindowRollup } from "@/hooks/use-ingestion-events";

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
    data: { data: { connectors: [healthyConnector] } },
    isLoading: false,
    isError: false,
  } as never);
  vi.mocked(useIngestionDroppedKnown).mockReturnValue({
    data: { available: true, counts_available: true, classification_available: true, uncertain_drops: 0, availability_reason: "none", window: "24h", dropped: 0, episodes: 0 },
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
      data: { data: { connectors: [offlineConnector] } },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionTimelineVerdictOpener range="24h" />);

    expect(html).toContain("calendar · primary needs attention");
    expect(html).toContain('href="/ingestion/connectors/calendar/primary"');
    expect(html).not.toContain("ingestion-timeline-verdict-all-clear");
  });

  it("names connector activity degradation instead of rendering an all-clear", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
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
      data: { data: { connectors: [], connector_registry_available: false } },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionTimelineVerdictOpener range="24h" />);

    expect(html).toContain("connector registry unavailable");
    expect(html).not.toContain("ingestion-timeline-verdict-all-clear");
  });

  it("names unpriced session coverage instead of rendering the timeline all-clear", () => {
    vi.mocked(useIngestionWindowRollup).mockReturnValue({
      data: {
        events: 12,
        sessions: 3,
        cost: 0.41,
        unpriced_session_count: 1,
        window: { from: null, to: null },
      },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionTimelineVerdictOpener range="24h" />);

    expect(html).toContain("1 session cost unavailable");
    expect(html).not.toContain("ingestion-timeline-verdict-all-clear");
  });

  it("distinguishes sessions with no usage evidence from unpriced sessions", () => {
    vi.mocked(useIngestionWindowRollup).mockReturnValue({
      data: {
        events: 12,
        sessions: 3,
        cost: 0.41,
        unpriced_session_count: 0,
        no_usage_session_count: 1,
        window: { from: null, to: null },
      },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionTimelineVerdictOpener range="24h" />);

    expect(html).toContain("1 session recorded no token usage");
    expect(html).not.toContain("1 session cost unavailable");
  });

  it("names a registry fallback on the connectors roster instead of healthy zero", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: { data: { connectors: [], connector_registry_available: false } },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionConnectorsVerdictOpener />);

    expect(html).toContain("connector registry unavailable");
    expect(html).not.toContain("ingestion-connectors-verdict-all-clear");
  });

  it("excludes archived identities from timeline and roster verdict attention", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: { data: { connectors: [{ ...offlineConnector, archived: true }] } },
      isLoading: false,
      isError: false,
    } as never);

    const timeline = render(<IngestionTimelineVerdictOpener range="24h" />);
    const roster = render(<IngestionConnectorsVerdictOpener />);

    expect(timeline).not.toContain("calendar · primary needs attention");
    expect(roster).not.toContain("calendar · primary needs attention");
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

  it("names drops from known contacts as a door and never renders all clear over them", () => {
    vi.mocked(useIngestionDroppedKnown).mockReturnValue({
      data: { available: true, counts_available: true, classification_available: true, uncertain_drops: 0, availability_reason: "none", window: "24h", dropped: 3, episodes: 2 },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionFiltersVerdictOpener />);

    expect(html).toContain("3 dropped from people you know");
    expect(html).toContain('href="/ingestion?statuses=filtered&amp;range=24h"');
    expect(html).not.toContain("ingestion-filters-verdict-all-clear");
    for (const isError of [false, true]) {
      vi.mocked(useIngestionDroppedKnown).mockReturnValue({
        data: { available: false, counts_available: true, classification_available: false,
          uncertain_drops: 1, availability_reason: "classification_unknown",
          window: "24h", dropped: 3, episodes: 2 },
        isLoading: false, isError,
      } as never);
      const partial = render(<IngestionFiltersVerdictOpener />);
      expect(partial).toContain("3 dropped from people you know");
      expect(partial).toContain("gate harm unknown");
      expect(partial).toContain('href="/ingestion?statuses=filtered&amp;range=24h"');
      expect(partial).not.toContain("ingestion-filters-verdict-all-clear");
    }
    vi.mocked(useIngestionDroppedKnown).mockReturnValue({
      data: undefined, isLoading: true, isError: false,
    } as never);
    const loading = render(<IngestionFiltersVerdictOpener />);
    expect(loading).toContain("ingestion-filters-verdict-skeleton");
    expect(loading).not.toContain("All gates clear");

  });

  it.each([
    ["the aggregate is degraded in a 200 response", { data: { available: false }, isError: false }],
    ["the aggregate request fails", { data: undefined, isError: true }],
  ])("says gate harm is unknown, not all clear, when %s", (_case, result) => {
    vi.mocked(useIngestionDroppedKnown).mockReturnValue({
      ...result,
      isLoading: false,
    } as never);

    const html = render(<IngestionFiltersVerdictOpener />);

    expect(html).toContain("gate harm unknown");
    expect(html).not.toContain("ingestion-filters-verdict-all-clear");
  });

  it("keeps the calm line when no known-contact drops are outstanding", () => {
    vi.mocked(usePipelineStats).mockReturnValue({
      data: { aggregates_available: true, ingested: 100, filtered: 0 },
      isLoading: false,
      isError: false,
    } as never);

    const html = render(<IngestionFiltersVerdictOpener />);

    expect(html).toContain("ingestion-filters-verdict-all-clear");
  });
});
