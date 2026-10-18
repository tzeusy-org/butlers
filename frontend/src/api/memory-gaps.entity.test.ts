/** The entity filter of the knowledge-gap client (bu-q7vx1q.71). */

import { afterEach, describe, expect, it, vi } from "vitest";

import { getMemoryGaps } from "./memory-gaps.ts";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockEmptyPage() {
  const body = { data: [], meta: { limit: 50, next_cursor: null, has_more: false } };
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

describe("getMemoryGaps entity filter", () => {
  it("sends entity_id alongside the cursor so the server filters every page", async () => {
    mockEmptyPage();

    await getMemoryGaps({ entityId: "11111111-2222-3333-4444-555555555555", cursor: "CUR", limit: 5 });

    const search = new URL(String(mockFetch.mock.calls[0][0]), "http://x").searchParams;
    expect(search.get("entity_id")).toBe("11111111-2222-3333-4444-555555555555");
    expect(search.get("cursor")).toBe("CUR");
    expect(search.get("limit")).toBe("5");
  });

  it("leaves entity_id off an unfiltered request", async () => {
    mockEmptyPage();

    await getMemoryGaps({ limit: 5 });

    expect(String(mockFetch.mock.calls[0][0])).not.toContain("entity_id");
  });
});
