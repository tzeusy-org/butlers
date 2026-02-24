/**
 * Tests for connector statistics API client functions.
 *
 * Verifies:
 * - Correct /switchboard/* path prefixes (no direct /api/connectors/* calls)
 * - Backend-to-frontend type transformations (liveness derivation, fanout matrix
 *   grouping, stats summary aggregation, CrossConnectorSummary field mapping)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockedFunction } from "vitest";

// ---------------------------------------------------------------------------
// Mock fetch so we never hit the network
// ---------------------------------------------------------------------------

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

// Helper to make fetch return a JSON response
function mockResponse(data: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: { get: () => "application/json" },
  });
}

// ---------------------------------------------------------------------------
// Import the functions under test (after mock setup)
// ---------------------------------------------------------------------------

import {
  listConnectorSummaries,
  getConnectorDetail,
  getConnectorStats,
  getCrossConnectorSummary,
  getConnectorFanout,
  getIngestionOverview,
} from "./client.ts";

// ---------------------------------------------------------------------------
// Path prefix tests
// ---------------------------------------------------------------------------

describe("connector API path prefixes", () => {
  it("listConnectorSummaries calls /api/switchboard/connectors", async () => {
    mockResponse({ data: [] });
    await listConnectorSummaries();
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/switchboard/connectors"),
      expect.anything(),
    );
    const url: string = mockFetch.mock.calls[0][0];
    // Must not be the bare /api/connectors path
    expect(url).not.toMatch(/\/api\/connectors(?!.*switchboard)/);
  });

  it("getConnectorDetail calls /api/switchboard/connectors/:type/:id", async () => {
    mockResponse({ data: {
      connector_type: "gmail",
      endpoint_identity: "user@example.com",
      instance_id: null,
      version: null,
      state: "healthy",
      error_message: null,
      uptime_s: null,
      last_heartbeat_at: null,
      first_seen_at: "2026-01-01T00:00:00Z",
      registered_via: "self",
      counter_messages_ingested: 0,
      counter_messages_failed: 0,
      counter_source_api_calls: 0,
      counter_checkpoint_saves: 0,
      counter_dedupe_accepted: 0,
      checkpoint_cursor: null,
      checkpoint_updated_at: null,
    }});
    await getConnectorDetail("gmail", "user@example.com");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/switchboard/connectors/gmail/user%40example.com");
  });

  it("getConnectorStats calls /api/switchboard/connectors/:type/:id/stats", async () => {
    mockResponse({ data: [] });
    await getConnectorStats("gmail", "user@example.com", "24h");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/switchboard/connectors/gmail/user%40example.com/stats");
  });

  it("getCrossConnectorSummary calls /api/switchboard/connectors/summary", async () => {
    mockResponse({ data: {
      total_connectors: 0,
      online_count: 0,
      stale_count: 0,
      offline_count: 0,
      unknown_count: 0,
      total_messages_ingested: 0,
      total_messages_failed: 0,
      error_rate_pct: 0,
    }});
    await getCrossConnectorSummary("24h");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/switchboard/connectors/summary");
  });

  it("getConnectorFanout calls /api/switchboard/ingestion/fanout", async () => {
    mockResponse({ data: [] });
    await getConnectorFanout("7d");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/switchboard/ingestion/fanout");
  });
});

// ---------------------------------------------------------------------------
// Liveness derivation
// ---------------------------------------------------------------------------

describe("listConnectorSummaries liveness derivation", () => {
  function makeEntry(overrides: Partial<{ last_heartbeat_at: string | null; state: string }> = {}) {
    return {
      connector_type: "gmail",
      endpoint_identity: "u@example.com",
      instance_id: null,
      version: "1.0",
      state: "healthy",
      error_message: null,
      uptime_s: null,
      last_heartbeat_at: null,
      first_seen_at: "2026-01-01T00:00:00Z",
      registered_via: "self",
      counter_messages_ingested: 0,
      counter_messages_failed: 0,
      counter_source_api_calls: 0,
      counter_checkpoint_saves: 0,
      counter_dedupe_accepted: 0,
      checkpoint_cursor: null,
      checkpoint_updated_at: null,
      ...overrides,
    };
  }

  it("derives liveness=online when heartbeat is within 5 minutes", async () => {
    const recent = new Date(Date.now() - 2 * 60 * 1000).toISOString(); // 2 mins ago
    mockResponse({ data: [makeEntry({ last_heartbeat_at: recent })] });
    const resp = await listConnectorSummaries();
    expect(resp.data[0].liveness).toBe("online");
  });

  it("derives liveness=stale when heartbeat is 6-29 minutes ago", async () => {
    const stale = new Date(Date.now() - 10 * 60 * 1000).toISOString(); // 10 mins ago
    mockResponse({ data: [makeEntry({ last_heartbeat_at: stale })] });
    const resp = await listConnectorSummaries();
    expect(resp.data[0].liveness).toBe("stale");
  });

  it("derives liveness=offline when heartbeat is 30+ minutes ago", async () => {
    const old = new Date(Date.now() - 60 * 60 * 1000).toISOString(); // 1 hour ago
    mockResponse({ data: [makeEntry({ last_heartbeat_at: old })] });
    const resp = await listConnectorSummaries();
    expect(resp.data[0].liveness).toBe("offline");
  });

  it("derives liveness=offline when no heartbeat", async () => {
    mockResponse({ data: [makeEntry({ last_heartbeat_at: null })] });
    const resp = await listConnectorSummaries();
    expect(resp.data[0].liveness).toBe("offline");
  });
});

// ---------------------------------------------------------------------------
// CrossConnectorSummary field mapping
// ---------------------------------------------------------------------------

describe("getCrossConnectorSummary field mapping", () => {
  it("maps backend field names to frontend CrossConnectorSummary shape", async () => {
    mockResponse({
      data: {
        total_connectors: 5,
        online_count: 3,
        stale_count: 1,
        offline_count: 1,
        unknown_count: 0,
        total_messages_ingested: 1000,
        total_messages_failed: 10,
        error_rate_pct: 1.0,
      },
    });
    const resp = await getCrossConnectorSummary("24h");
    const summary = resp.data;
    expect(summary.total_connectors).toBe(5);
    expect(summary.connectors_online).toBe(3);
    expect(summary.connectors_stale).toBe(1);
    expect(summary.connectors_offline).toBe(1);
    expect(summary.total_messages_ingested).toBe(1000);
    expect(summary.total_messages_failed).toBe(10);
    expect(summary.overall_error_rate_pct).toBe(1.0);
    expect(summary.period).toBe("24h");
    expect(summary.by_connector).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Fanout matrix grouping
// ---------------------------------------------------------------------------

describe("getConnectorFanout matrix grouping", () => {
  it("groups flat FanoutRow records into ConnectorFanout matrix", async () => {
    mockResponse({
      data: [
        { connector_type: "gmail", endpoint_identity: "u@x.com", target_butler: "finance", message_count: 100 },
        { connector_type: "gmail", endpoint_identity: "u@x.com", target_butler: "general", message_count: 50 },
        { connector_type: "telegram", endpoint_identity: "bot-1", target_butler: "health", message_count: 200 },
      ],
    });
    const resp = await getConnectorFanout("7d");
    const fanout = resp.data;
    expect(fanout.period).toBe("7d");
    expect(fanout.matrix).toHaveLength(2);

    const gmailEntry = fanout.matrix.find(
      (e) => e.connector_type === "gmail" && e.endpoint_identity === "u@x.com",
    );
    expect(gmailEntry).toBeDefined();
    expect(gmailEntry!.targets).toEqual({ finance: 100, general: 50 });

    const tgEntry = fanout.matrix.find(
      (e) => e.connector_type === "telegram" && e.endpoint_identity === "bot-1",
    );
    expect(tgEntry).toBeDefined();
    expect(tgEntry!.targets).toEqual({ health: 200 });
  });

  it("returns empty matrix when backend returns empty list", async () => {
    mockResponse({ data: [] });
    const resp = await getConnectorFanout("7d");
    expect(resp.data.matrix).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Stats timeseries + summary aggregation
// ---------------------------------------------------------------------------

describe("getConnectorStats timeseries transformation", () => {
  it("aggregates timeseries rows into ConnectorStats with summary", async () => {
    mockResponse({
      data: [
        {
          connector_type: "gmail",
          endpoint_identity: "u@x.com",
          hour: "2026-02-23T10:00:00Z",
          messages_ingested: 60,
          messages_failed: 3,
          source_api_calls: 10,
          dedupe_accepted: 5,
          heartbeat_count: 12,
          healthy_count: 10,
          degraded_count: 2,
          error_count: 0,
        },
        {
          connector_type: "gmail",
          endpoint_identity: "u@x.com",
          hour: "2026-02-23T11:00:00Z",
          messages_ingested: 40,
          messages_failed: 1,
          source_api_calls: 8,
          dedupe_accepted: 2,
          heartbeat_count: 12,
          healthy_count: 12,
          degraded_count: 0,
          error_count: 0,
        },
      ],
    });
    const resp = await getConnectorStats("gmail", "u@x.com", "24h");
    const stats = resp.data;
    expect(stats.connector_type).toBe("gmail");
    expect(stats.endpoint_identity).toBe("u@x.com");
    expect(stats.period).toBe("24h");
    // Summary aggregation
    expect(stats.summary.messages_ingested).toBe(100);
    expect(stats.summary.messages_failed).toBe(4);
    expect(stats.summary.error_rate_pct).toBeCloseTo((4 / 104) * 100, 1);
    // Timeseries buckets
    expect(stats.timeseries).toHaveLength(2);
    expect(stats.timeseries[0].bucket).toBe("2026-02-23T10:00:00Z");
    expect(stats.timeseries[0].messages_ingested).toBe(60);
  });

  it("returns empty timeseries and zero summary when no rows", async () => {
    mockResponse({ data: [] });
    const resp = await getConnectorStats("gmail", "u@x.com", "24h");
    expect(resp.data.timeseries).toEqual([]);
    expect(resp.data.summary.messages_ingested).toBe(0);
    expect(resp.data.summary.error_rate_pct).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// ConnectorDetail counters mapping
// ---------------------------------------------------------------------------

describe("getConnectorDetail counters and checkpoint mapping", () => {
  it("maps counter_ prefixed fields to counters object", async () => {
    mockResponse({
      data: {
        connector_type: "gmail",
        endpoint_identity: "u@x.com",
        instance_id: "inst-1",
        version: "2.0",
        state: "healthy",
        error_message: null,
        uptime_s: 3600,
        last_heartbeat_at: new Date(Date.now() - 60 * 1000).toISOString(),
        first_seen_at: "2026-01-01T00:00:00Z",
        registered_via: "self",
        counter_messages_ingested: 500,
        counter_messages_failed: 5,
        counter_source_api_calls: 50,
        counter_checkpoint_saves: 10,
        counter_dedupe_accepted: 20,
        checkpoint_cursor: "cursor-abc",
        checkpoint_updated_at: "2026-02-23T12:00:00Z",
      },
    });
    const resp = await getConnectorDetail("gmail", "u@x.com");
    const detail = resp.data;
    expect(detail.counters).toEqual({
      messages_ingested: 500,
      messages_failed: 5,
      source_api_calls: 50,
      checkpoint_saves: 10,
      dedupe_accepted: 20,
    });
    expect(detail.checkpoint).toEqual({
      cursor: "cursor-abc",
      updated_at: "2026-02-23T12:00:00Z",
    });
    expect(detail.liveness).toBe("online");
    expect(detail.state).toBe("healthy");
  });

  it("sets checkpoint=null when no cursor and no updated_at", async () => {
    mockResponse({
      data: {
        connector_type: "gmail",
        endpoint_identity: "u@x.com",
        instance_id: null,
        version: null,
        state: "unknown",
        error_message: null,
        uptime_s: null,
        last_heartbeat_at: null,
        first_seen_at: "2026-01-01T00:00:00Z",
        registered_via: "self",
        counter_messages_ingested: 0,
        counter_messages_failed: 0,
        counter_source_api_calls: 0,
        counter_checkpoint_saves: 0,
        counter_dedupe_accepted: 0,
        checkpoint_cursor: null,
        checkpoint_updated_at: null,
      },
    });
    const resp = await getConnectorDetail("gmail", "u@x.com");
    expect(resp.data.checkpoint).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Bug 1 fix: _toConnectorSummary maps counter_messages_ingested to today
// ---------------------------------------------------------------------------

describe("listConnectorSummaries today field mapping (Bug 1)", () => {
  function makeEntry(overrides: Partial<{ counter_messages_ingested: number; counter_messages_failed: number }> = {}) {
    return {
      connector_type: "gmail",
      endpoint_identity: "u@example.com",
      instance_id: null,
      version: "1.0",
      state: "healthy",
      error_message: null,
      uptime_s: null,
      last_heartbeat_at: null,
      first_seen_at: "2026-01-01T00:00:00Z",
      registered_via: "self",
      counter_messages_ingested: 0,
      counter_messages_failed: 0,
      counter_source_api_calls: 0,
      counter_checkpoint_saves: 0,
      counter_dedupe_accepted: 0,
      checkpoint_cursor: null,
      checkpoint_updated_at: null,
      ...overrides,
    };
  }

  it("maps counter_messages_ingested to today.messages_ingested (not null)", async () => {
    mockResponse({ data: [makeEntry({ counter_messages_ingested: 42, counter_messages_failed: 3 })] });
    const resp = await listConnectorSummaries();
    const connector = resp.data[0];
    expect(connector.today).not.toBeNull();
    expect(connector.today!.messages_ingested).toBe(42);
    expect(connector.today!.messages_failed).toBe(3);
  });

  it("maps zero counters to today with zeroes (not null)", async () => {
    mockResponse({ data: [makeEntry({ counter_messages_ingested: 0, counter_messages_failed: 0 })] });
    const resp = await listConnectorSummaries();
    const connector = resp.data[0];
    expect(connector.today).not.toBeNull();
    expect(connector.today!.messages_ingested).toBe(0);
    expect(connector.today!.messages_failed).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Bug 2 fix: getIngestionOverview — period-scoped stats from message_inbox
// ---------------------------------------------------------------------------

describe("getIngestionOverview (Bug 2)", () => {
  it("calls /api/switchboard/ingestion/overview with period param", async () => {
    mockResponse({
      data: {
        period: "24h",
        total_ingested: 0,
        total_skipped: 0,
        total_metadata_only: 0,
        llm_calls_saved: 0,
        active_connectors: 0,
        tier1_full_count: 0,
        tier2_metadata_count: 0,
        tier3_skip_count: 0,
      },
    });
    await getIngestionOverview("24h");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/switchboard/ingestion/overview");
    expect(url).toContain("period=24h");
  });

  it("returns period-scoped tier counts from backend", async () => {
    mockResponse({
      data: {
        period: "7d",
        total_ingested: 500,
        total_skipped: 10,
        total_metadata_only: 20,
        llm_calls_saved: 30,
        active_connectors: 3,
        tier1_full_count: 470,
        tier2_metadata_count: 20,
        tier3_skip_count: 10,
      },
    });
    const resp = await getIngestionOverview("7d");
    const overview = resp.data;
    expect(overview.period).toBe("7d");
    expect(overview.total_ingested).toBe(500);
    expect(overview.tier1_full_count).toBe(470);
    expect(overview.tier2_metadata_count).toBe(20);
    expect(overview.tier3_skip_count).toBe(10);
    expect(overview.active_connectors).toBe(3);
  });

  it("passes period=30d to the endpoint", async () => {
    mockResponse({
      data: {
        period: "30d",
        total_ingested: 0,
        total_skipped: 0,
        total_metadata_only: 0,
        llm_calls_saved: 0,
        active_connectors: 0,
        tier1_full_count: 0,
        tier2_metadata_count: 0,
        tier3_skip_count: 0,
      },
    });
    await getIngestionOverview("30d");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("period=30d");
  });
});
