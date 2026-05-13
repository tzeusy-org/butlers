import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockResponse(data: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: { get: () => "application/json" },
  });
}

import { getDashboardBriefing } from "./client.ts";

describe("getDashboardBriefing", () => {
  it("unwraps the API response envelope before returning the briefing", async () => {
    mockResponse({
      data: {
        greet: "Good night.",
        headline: "Everything is in hand.",
        elaboration: "The local runtime wrote the briefing.",
        source: "llm",
        state_class: "quiet",
        generated_at: "2026-05-13T16:05:00+00:00",
      },
      meta: {},
    });

    const briefing = await getDashboardBriefing();

    expect(briefing.greet).toBe("Good night.");
    expect(briefing.headline).toBe("Everything is in hand.");
    expect(briefing.elaboration).toBe("The local runtime wrote the briefing.");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/dashboard/briefing"),
      expect.anything(),
    );
  });
});
