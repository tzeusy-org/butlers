/**
 * Tests for connector statistics API client functions.
 *
 * Verifies:
 * - Canonical /ingestion/connectors path prefixes
 * - Backend-to-frontend detail and stats transformations
 */

import { afterEach, describe, expect, it, vi } from "vitest";

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
  getConnectorDetail,
  getConnectorFanout,
  getConnectorSummaries,
  getConnectorStats,
  updateConnectorSettings,
} from "./client.ts";

// ---------------------------------------------------------------------------
// Path prefix tests
// ---------------------------------------------------------------------------

describe("connector API path prefixes", () => {
  it("getConnectorSummaries calls /api/ingestion/connectors/summaries", async () => {
    mockResponse({ data: { connectors: [] } });
    await getConnectorSummaries();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/ingestion/connectors/summaries");
    expect(url).not.toContain("/api/switchboard/connectors");
  });

  it("getConnectorDetail calls /api/ingestion/connectors/:type/:id", async () => {
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
    expect(url).toContain("/api/ingestion/connectors/gmail/user%40example.com");
    expect(url).not.toContain("/api/switchboard/connectors");
  });

  it("getConnectorStats calls /api/ingestion/connectors/:type/:id/stats", async () => {
    mockResponse({ data: [] });
    await getConnectorStats("gmail", "user@example.com", "24h");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/api/ingestion/connectors/gmail/user%40example.com/stats");
    expect(url).not.toContain("/api/switchboard/connectors");
  });

  it("getConnectorFanout calls the Switchboard aggregate route with its period", async () => {
    mockResponse({ data: [], meta: { aggregates_available: true } });
    const response = await getConnectorFanout("7d");
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toBe("/api/switchboard/ingestion/fanout?period=7d");
    expect(response.meta.aggregates_available).toBe(true);
  });

  it("updateConnectorSettings calls the canonical settings route", async () => {
    mockResponse({ data: {} });
    await updateConnectorSettings("gmail", "user@example.com", { flush_interval_s: 60 });
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/ingestion/connectors/gmail/user%40example.com/settings");
    expect(url).not.toContain("/api/switchboard/connectors");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe(JSON.stringify({ settings: { flush_interval_s: 60 } }));
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
    expect(stats.bucket_window).toBeUndefined(); // Legacy rows declare no complete window.
    const metadata = { window_start:"2026-02-23T00:00:00Z", window_end:"2026-02-24T00:00:00Z",
      bucket_width_s:3600, hourly_events_available:false };
    mockResponse({ data:[], meta:metadata });
    expect((await getConnectorStats("gmail", "u@x.com")).data.bucket_window)
      .toEqual({ window_start:metadata.window_start, window_end:metadata.window_end,
        bucket_width_s:3600, counts_available:false });
    mockResponse({ data:[], meta:{ ...metadata, hourly_events_available:true } });
    expect((await getConnectorStats("gmail", "u@x.com")).data.bucket_window?.counts_available).toBe(true);
    mockResponse({ data:[], meta:{ ...metadata, hourly_events_available:"true" } });
    expect((await getConnectorStats("gmail", "u@x.com")).data.bucket_window).toBeNull();
    mockResponse({ data:[], meta:{ bucket_width_s:3600 } });
    expect((await getConnectorStats("gmail", "u@x.com")).data.bucket_window).toBeNull();
  });

  it("returns empty timeseries and zero summary when no rows", async () => {
    mockResponse({ data: [] });
    const resp = await getConnectorStats("gmail", "u@x.com", "24h");
    expect(resp.data.timeseries).toEqual([]);
    expect(resp.data.summary.messages_ingested).toBe(0);
    expect(resp.data.summary.error_rate_pct).toBe(0);
  });

  // Skip-aware filtered series + degraded flag (bu-c48im)

  it("maps messages_filtered per bucket (distinct from ingested)", async () => {
    mockResponse({
      data: [
        {
          connector_type: "home_assistant",
          endpoint_identity: "default",
          hour: "2026-02-23T10:00:00Z",
          messages_ingested: 0,
          messages_failed: 0,
          messages_filtered: 7,
          heartbeat_count: 0,
          healthy_count: 0,
          degraded_count: 0,
          error_count: 0,
        },
      ],
    });
    const resp = await getConnectorStats("home_assistant", "default", "24h");
    expect(resp.data.timeseries[0].messages_filtered).toBe(7);
    // The filtered series is DISTINCT — never folded into ingested.
    expect(resp.data.timeseries[0].messages_ingested).toBe(0);
    expect(resp.data.summary.messages_ingested).toBe(0);
  });

  it("defaults messages_filtered to 0 for older rows missing the field", async () => {
    mockResponse({
      data: [
        {
          connector_type: "gmail",
          endpoint_identity: "u@x.com",
          hour: "2026-02-23T10:00:00Z",
          messages_ingested: 5,
          messages_failed: 0,
          heartbeat_count: 0,
          healthy_count: 0,
          degraded_count: 0,
          error_count: 0,
        },
      ],
    });
    const resp = await getConnectorStats("gmail", "u@x.com", "24h");
    expect(resp.data.timeseries[0].messages_filtered).toBe(0);
  });

  it("threads hourly_events_available from response meta", async () => {
    mockResponse({ data: [], meta: { hourly_events_available: false } });
    const resp = await getConnectorStats("gmail", "u@x.com", "24h");
    expect(resp.data.hourly_events_available).toBe(false);
    const unavailable = { messages_ingested: null, messages_failed: null,
      error_rate_pct: null, avg_messages_per_hour: null, uptime_pct: null };
    expect(resp.data.summary).toEqual(unavailable);
    const row = { hour: "2026-02-23T10:00:00Z", messages_ingested: null, messages_failed: null };
    mockResponse({ data: [row], meta: { hourly_events_available: false } });
    expect((await getConnectorStats("gmail", "u@x.com")).data.summary).toEqual(unavailable);
    // NULL observations remain unknown even beside an otherwise readable source.
    mockResponse({ data: [row], meta: { hourly_events_available: true } });
    expect((await getConnectorStats("gmail", "u@x.com")).data.summary).toEqual(unavailable);
    mockResponse({ data: [{ ...row, messages_failed: 2 }], meta: { hourly_events_available: true } });
    expect((await getConnectorStats("gmail", "u@x.com")).data.summary)
      .toEqual({ ...unavailable, messages_failed: 2 });
    for (const [ingested, failed] of [[0, 0], [48, 2]]) {
      mockResponse({ data: [{ ...row, messages_ingested: ingested, messages_failed: failed }],
        meta: { hourly_events_available: true } });
      const measured = (await getConnectorStats("gmail", "u@x.com")).data.summary;
      expect(measured.messages_ingested).toBe(ingested);
      expect(measured.messages_failed).toBe(failed);
      expect(measured.error_rate_pct).toBe(ingested + failed ? failed / (ingested + failed) * 100 : 0);
      expect(measured.avg_messages_per_hour).toBe(ingested / 24);
    }
  });

  it("treats absent hourly_events_available as available (true)", async () => {
    mockResponse({ data: [] });
    const resp = await getConnectorStats("gmail", "u@x.com", "24h");
    expect(resp.data.hourly_events_available).toBe(true);
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
