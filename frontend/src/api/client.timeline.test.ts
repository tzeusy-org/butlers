/** Tests for the timeline API client's URL query contract. */

import { afterEach, describe, expect, it, vi } from "vitest";

import type { TimelineParams } from "./types.ts";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockTimelineResponse() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => ({
      data: [],
      meta: {
        cursor: null,
        has_more: false,
        heartbeat_rollup: { ticks: 0, butlers: 0, failed: 0 },
        degraded_sources: [],
      },
    }),
    text: async () => "",
    headers: { get: () => "application/json" },
  });
}

import { getTimeline, getTimelineAttention, getTimelineHistogram } from "./client.ts";

describe("getTimeline", () => {
  it("defaults additive degraded-butler metadata for an older server response", async () => {
    mockTimelineResponse();

    const response = await getTimeline();

    expect(response.meta.degraded_butlers).toEqual([]);
  });

  it("forwards a trace scope", async () => {
    mockTimelineResponse();
    const params: TimelineParams & { trace: string } = {
      trace: "trace-001",
      since: "2026-07-04T14:01:00Z",
      until: "2026-07-04T14:02:00Z",
    };

    await getTimeline(params);

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/timeline?trace=trace-001");
    expect(url).toContain("since=2026-07-04T14%3A01%3A00Z");
    expect(url).toContain("until=2026-07-04T14%3A02%3A00Z");

    mockTimelineResponse();
    await getTimelineHistogram({
      since: params.since!,
      until: params.until!,
      trace: params.trace,
      butler: ["home"],
      event_type: ["error"],
    });
    const histogramUrl: string = mockFetch.mock.calls[1][0];
    expect(histogramUrl).toContain("/timeline/histogram?");
    expect(histogramUrl).toContain("butler=home");
    expect(histogramUrl).toContain("event_type=error");
  });

  it("forwards attention butler and trace scope without caller-selected bounds", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        data: [],
        meta: {
          since: "2026-07-03T14:00:00Z",
          until: "2026-07-04T14:00:00Z",
          failed_sessions: 0,
          failed_notifications: 0,
          total: 0,
          has_more: false,
          availability: "complete",
          expected_sources: 0,
          healthy_sources: 0,
          degraded_sources: [],
          degraded_butlers: [],
        },
      }),
      text: async () => "",
      headers: { get: () => "application/json" },
    });

    await getTimelineAttention({ butler: ["home", "atlas"], trace: "trace-7" });

    const url: string = mockFetch.mock.calls[0][0];
    const parsed = new URL(url, "http://test");
    expect(parsed.pathname).toBe("/api/timeline/attention");
    expect(parsed.searchParams.getAll("butler")).toEqual(["home", "atlas"]);
    expect(parsed.searchParams.get("trace")).toBe("trace-7");
    expect(parsed.searchParams.has("since")).toBe(false);
    expect(parsed.searchParams.has("until")).toBe(false);
  });
});
