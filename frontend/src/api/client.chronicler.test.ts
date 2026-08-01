/** Tests for the Chronicler day-close API client contract. */

import { afterEach, describe, expect, it, vi } from "vitest";

import { getChroniclerDayClose } from "./client.ts";
import type {
  ChroniclerDayCloseRefreshResult,
  ChroniclerDayCloseResponse,
} from "./types.ts";

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

const INVALID_DAY_CLOSE_RESPONSE = {
  invalid: true,
  invalid_reason: "date_mismatch",
  cache_built_at: "2026-03-16T07:00:00Z",
} satisfies ChroniclerDayCloseResponse;

const QUIET_DAY_CLOSE_REFRESH_RESPONSE = {
  cache_key: "day_close:2026-03-15",
  quiet: true,
} satisfies ChroniclerDayCloseRefreshResult;

describe("getChroniclerDayClose", () => {
  it("requests one canonical day and preserves an invalid cache response", async () => {
    mockResponse(INVALID_DAY_CLOSE_RESPONSE);

    const response = await getChroniclerDayClose({ date: "2026-03-15" });

    const requestUrl = new URL(mockFetch.mock.calls[0][0], "http://butlers.test");
    expect(requestUrl.pathname).toBe("/api/chronicler/aggregate/day-close");
    expect(requestUrl.searchParams.get("date")).toBe("2026-03-15");
    expect(requestUrl.searchParams.has("window_start")).toBe(false);
    expect(requestUrl.searchParams.has("window_end")).toBe(false);
    expect(response).toEqual(INVALID_DAY_CLOSE_RESPONSE);
  });
});

describe("Chronicler day-close refresh response contract", () => {
  it("keeps a quiet close distinct from a cached response", () => {
    expect(QUIET_DAY_CLOSE_REFRESH_RESPONSE).toEqual({
      cache_key: "day_close:2026-03-15",
      quiet: true,
    });
    expect(QUIET_DAY_CLOSE_REFRESH_RESPONSE).not.toHaveProperty("cache_built_at");
  });
});
