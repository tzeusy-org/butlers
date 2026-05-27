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
