/**
 * Tests for listIngestionEvents API client function.
 *
 * Verifies:
 * - `channels` CSV param is sent correctly (single and multi-channel)
 * - `source_channel` (deprecated) is still forwarded when present
 * - No channel params are sent when activeChannels is empty
 * - `channels` and `source_channel` can coexist in the same request
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

// Helper to make fetch return a JSON response with the cursor-paginated envelope
function mockEventsResponse(events: unknown[] = []) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => ({ data: events, meta: { next_cursor: null, has_more: false } }),
    text: async () => JSON.stringify({ data: events, meta: { next_cursor: null, has_more: false } }),
    headers: { get: () => "application/json" },
  });
}

// ---------------------------------------------------------------------------
// Import the function under test (after mock setup)
// ---------------------------------------------------------------------------

import { listIngestionEvents } from "./client.ts";

// ---------------------------------------------------------------------------
// channels= CSV param
// ---------------------------------------------------------------------------

describe("listIngestionEvents — channels param", () => {
  it("sends channels=email when a single channel is provided", async () => {
    mockEventsResponse();
    await listIngestionEvents({ channels: "email" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("channels=email");
    expect(url).not.toContain("source_channel");
  });

  it("sends channels=email,telegram when two channels are provided", async () => {
    mockEventsResponse();
    await listIngestionEvents({ channels: "email,telegram" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("channels=email%2Ctelegram");
    expect(url).not.toContain("source_channel");
  });

  it("sends no channel params when channels is omitted", async () => {
    mockEventsResponse();
    await listIngestionEvents({});
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("channels");
    expect(url).not.toContain("source_channel");
  });

  it("sends no channel params when params is undefined", async () => {
    mockEventsResponse();
    await listIngestionEvents();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("channels");
    expect(url).not.toContain("source_channel");
  });

  it("still sends source_channel when provided (backward compat)", async () => {
    mockEventsResponse();
    await listIngestionEvents({ source_channel: "gmail" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("source_channel=gmail");
  });

  it("sends both channels and source_channel when both are provided", async () => {
    mockEventsResponse();
    await listIngestionEvents({ channels: "email,telegram", source_channel: "gmail" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("channels=");
    expect(url).toContain("source_channel=gmail");
  });
});

// ---------------------------------------------------------------------------
// getIngestionEventsHistogram — GET /api/ingestion/events/histogram (bu-4utdw.6)
// ---------------------------------------------------------------------------

import { getIngestionEventsHistogram } from "./client.ts";

function mockHistogramResponse(body: { buckets: unknown[]; bucket: string }) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

describe("getIngestionEventsHistogram", () => {
  it("always sends from and to (required params)", async () => {
    mockHistogramResponse({ buckets: [], bucket: "1m" });
    await getIngestionEventsHistogram({
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-02T00:00:00Z",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from=2026-01-01T00%3A00%3A00Z");
    expect(url).toContain("to=2026-01-02T00%3A00%3A00Z");
    // bucket/channels/statuses/q/trace_id are omitted when not provided.
    expect(url).not.toContain("bucket=");
    expect(url).not.toContain("channels=");
    expect(url).not.toContain("statuses=");
    expect(url).not.toContain("q=");
    expect(url).not.toContain("trace_id=");
  });

  it("forwards bucket, channels, statuses, and q when provided", async () => {
    mockHistogramResponse({ buckets: [], bucket: "5m" });
    await getIngestionEventsHistogram({
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-02T00:00:00Z",
      bucket: "5m",
      channels: "email,telegram",
      statuses: "ingested,error",
      q: "alice",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("bucket=5m");
    expect(url).toContain("channels=email%2Ctelegram");
    expect(url).toContain("statuses=ingested%2Cerror");
    expect(url).toContain("q=alice");
  });

  it("forwards trace_id when provided (bu-q750c drill-down spine consistency)", async () => {
    mockHistogramResponse({ buckets: [], bucket: "1m" });
    await getIngestionEventsHistogram({
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-02T00:00:00Z",
      trace_id: "trace-abc-123",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("trace_id=trace-abc-123");
  });

  it("hits /ingestion/events/histogram", async () => {
    mockHistogramResponse({ buckets: [], bucket: "1m" });
    await getIngestionEventsHistogram({
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-02T00:00:00Z",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/ingestion/events/histogram?");
  });

  it("returns the parsed response body", async () => {
    const body = {
      buckets: [
        {
          ts: "2026-01-01T00:01:00+00:00",
          counts: {
            ingested: 2,
            skipped: 0,
            filtered: 0,
            error: 1,
            replay_pending: 0,
            replay_complete: 0,
            replay_failed: 0,
          },
        },
      ],
      bucket: "1m",
    };
    mockHistogramResponse(body);
    const result = await getIngestionEventsHistogram({
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-02T00:00:00Z",
    });
    expect(result).toEqual(body);
  });
});

// ---------------------------------------------------------------------------
// getIngestionWindowRollup — GET /api/ingestion/rollup (bu-q750c trace_id threading)
// ---------------------------------------------------------------------------

import { getIngestionWindowRollup } from "./client.ts";

function mockRollupResponse(body: {
  events: number;
  sessions: number;
  cost: number | null;
  window: { from: string | null; to: string | null };
}) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

describe("getIngestionWindowRollup", () => {
  it("sends no params when called with no args", async () => {
    mockRollupResponse({ events: 0, sessions: 0, cost: null, window: { from: null, to: null } });
    await getIngestionWindowRollup();
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/ingestion/rollup");
    expect(url).not.toContain("trace_id=");
    expect(url).not.toContain("from=");
  });

  it("forwards trace_id when provided (bu-q750c drill-down spine consistency)", async () => {
    mockRollupResponse({ events: 3, sessions: 5, cost: null, window: { from: null, to: null } });
    await getIngestionWindowRollup({ trace_id: "trace-abc-123" });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("trace_id=trace-abc-123");
  });

  it("forwards from/to/channels/statuses/q alongside trace_id", async () => {
    mockRollupResponse({ events: 0, sessions: 0, cost: null, window: { from: null, to: null } });
    await getIngestionWindowRollup({
      from: "2026-01-01T00:00:00Z",
      to: "2026-01-02T00:00:00Z",
      channels: "email,telegram",
      statuses: "ingested,error",
      q: "alice",
      trace_id: "trace-abc-123",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("from=2026-01-01T00%3A00%3A00Z");
    expect(url).toContain("to=2026-01-02T00%3A00%3A00Z");
    expect(url).toContain("channels=email%2Ctelegram");
    expect(url).toContain("statuses=ingested%2Cerror");
    expect(url).toContain("q=alice");
    expect(url).toContain("trace_id=trace-abc-123");
  });

  it("returns the parsed response body", async () => {
    const body = { events: 3, sessions: 5, cost: 1.23, window: { from: null, to: null } };
    mockRollupResponse(body);
    const result = await getIngestionWindowRollup({ trace_id: "trace-abc-123" });
    expect(result).toEqual(body);
  });
});
