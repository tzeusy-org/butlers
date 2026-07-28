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

import { getTimeline } from "./client.ts";

describe("getTimeline", () => {
  it("defaults additive degraded-butler metadata for an older server response", async () => {
    mockTimelineResponse();

    const response = await getTimeline();

    expect(response.meta.degraded_butlers).toEqual([]);
  });

  it("forwards a trace scope", async () => {
    mockTimelineResponse();
    const params: TimelineParams & { trace: string } = { trace: "trace-001" };

    await getTimeline(params);

    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("/timeline?trace=trace-001");
  });
});
