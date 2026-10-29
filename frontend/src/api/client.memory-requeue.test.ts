/**
 * The dead-letter requeue verb posts to the single spec route [bu-kqnum.14.3].
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

import { retryEpisodeConsolidation } from "./client.ts";

describe("retryEpisodeConsolidation", () => {
  it("POSTs /api/memory/episodes/{id}/requeue with no butler in the path", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ data: {}, meta: {} }),
      text: async () => "{}",
      headers: { get: () => "application/json" },
    });

    await retryEpisodeConsolidation("ep/1 2");

    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/memory/episodes/ep%2F1%202/requeue");
    expect(String(url)).not.toContain("/butlers/");
    expect(String(url)).not.toContain("retry-consolidation");
    expect((init as RequestInit).method).toBe("POST");
  });
});
