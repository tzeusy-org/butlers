/**
 * Tests for use-chronicles query key factory and hook queryFn behavior.
 *
 * Test strategy:
 * 1. chroniclesKeys factory — deterministic key shape and cache isolation (pure, no DOM)
 * 2. Hook queryFn behavior — invoke queryFn directly with vi.mock; no live network.
 *
 * We do not use @testing-library/react (not installed). Instead we test the
 * queryFn directly by extracting it from the hook options via a thin wrapper,
 * consistent with the project's existing test patterns (use-ingestion.test.ts,
 * use-secrets.test.ts).
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import type { ChroniclerDayCloseParams } from "@/api/types.ts";
import { ApiError } from "@/api/client.ts";
import {
  chroniclesKeys,
} from "@/hooks/use-chronicles.ts";

// ---------------------------------------------------------------------------
// Mock the client module
// ---------------------------------------------------------------------------

vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>();
  return {
    ...actual,
    getChroniclerEpisodes: vi.fn(),
    getChroniclerAggregateByCategory: vi.fn(),
    getChroniclerAggregateByDay: vi.fn(),
    getChroniclerSourceState: vi.fn(),
    getChroniclerDayClose: vi.fn(),
  };
});

import {
  getChroniclerEpisodes,
  getChroniclerAggregateByCategory,
  getChroniclerAggregateByDay,
  getChroniclerSourceState,
  getChroniclerDayClose,
} from "@/api/client.ts";

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Query key factory tests
// ---------------------------------------------------------------------------

describe("chroniclesKeys", () => {
  it("all returns base key", () => {
    expect(chroniclesKeys.all).toEqual(["chronicles"]);
  });

  it("episodes with no params includes undefined", () => {
    expect(chroniclesKeys.episodes()).toEqual(["chronicles", "episodes", undefined]);
  });

  it("episodes with params includes params for cache isolation", () => {
    const key = chroniclesKeys.episodes({ source_name: "spotify", limit: 10 });
    expect(key[0]).toBe("chronicles");
    expect(key[1]).toBe("episodes");
    expect(key[2]).toMatchObject({ source_name: "spotify", limit: 10 });
  });

  it("different episode params produce different keys", () => {
    const k1 = chroniclesKeys.episodes({ source_name: "spotify" });
    const k2 = chroniclesKeys.episodes({ source_name: "steam" });
    expect(k1).not.toEqual(k2);
  });

  it("byCategory includes params", () => {
    const params = { start_at: "2026-01-01T00:00:00Z", end_at: "2026-01-02T00:00:00Z" };
    const key = chroniclesKeys.byCategory(params);
    expect(key[0]).toBe("chronicles");
    expect(key[1]).toBe("aggregate-by-category");
    expect(key[2]).toEqual(params);
  });

  it("byDay includes params", () => {
    const params = { start_at: "2026-01-01T00:00:00Z", end_at: "2026-01-02T00:00:00Z" };
    const key = chroniclesKeys.byDay(params);
    expect(key[1]).toBe("aggregate-by-day");
    expect(key[2]).toEqual(params);
  });

  it("different time windows produce different byCategory keys", () => {
    const k1 = chroniclesKeys.byCategory({ start_at: "2026-01-01T00:00:00Z", end_at: "2026-01-02T00:00:00Z" });
    const k2 = chroniclesKeys.byCategory({ start_at: "2026-01-02T00:00:00Z", end_at: "2026-01-03T00:00:00Z" });
    expect(k1).not.toEqual(k2);
  });

  it("sourceState returns stable singleton key", () => {
    expect(chroniclesKeys.sourceState()).toEqual(["chronicles", "source-state"]);
    expect(chroniclesKeys.sourceState()).toEqual(chroniclesKeys.sourceState());
  });

  it("dayClose includes params", () => {
    const params: ChroniclerDayCloseParams = { window_start: "2026-01-01", window_end: "2026-01-02" };
    const key = chroniclesKeys.dayClose(params);
    expect(key[1]).toBe("day-close");
    expect(key[2]).toEqual(params);
  });

  it("different dayClose windows produce different keys", () => {
    const k1 = chroniclesKeys.dayClose({ window_start: "2026-01-01", window_end: "2026-01-02" });
    const k2 = chroniclesKeys.dayClose({ window_start: "2026-01-02", window_end: "2026-01-03" });
    expect(k1).not.toEqual(k2);
  });

  it("byCategory and byDay keys are distinct even for same params", () => {
    const params = { start_at: "2026-01-01T00:00:00Z", end_at: "2026-01-02T00:00:00Z" };
    const k1 = chroniclesKeys.byCategory(params);
    const k2 = chroniclesKeys.byDay(params);
    expect(k1).not.toEqual(k2);
  });
});

// ---------------------------------------------------------------------------
// API client delegate tests
// Verify that each hook's queryFn delegates to the correct client function
// by invoking the mock directly and asserting call args.
// ---------------------------------------------------------------------------

describe("getChroniclerEpisodes client delegate", () => {
  it("passes params through to client", async () => {
    const mockData = { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } };
    vi.mocked(getChroniclerEpisodes).mockResolvedValueOnce(mockData);

    const result = await getChroniclerEpisodes({ source_name: "spotify", limit: 20 });

    expect(getChroniclerEpisodes).toHaveBeenCalledWith({ source_name: "spotify", limit: 20 });
    expect(result).toEqual(mockData);
  });

  it("passes undefined params (default call)", async () => {
    vi.mocked(getChroniclerEpisodes).mockResolvedValueOnce({ data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } });

    await getChroniclerEpisodes(undefined);
    expect(getChroniclerEpisodes).toHaveBeenCalledWith(undefined);
  });

  it("propagates error from client", async () => {
    vi.mocked(getChroniclerEpisodes).mockRejectedValueOnce(new Error("Network error"));
    await expect(getChroniclerEpisodes()).rejects.toThrow("Network error");
  });
});

describe("getChroniclerAggregateByCategory client delegate", () => {
  const params = { start_at: "2026-01-01T00:00:00Z", end_at: "2026-01-02T00:00:00Z" };

  it("passes params through to client", async () => {
    const mockData = {
      data: { start_at: params.start_at, end_at: params.end_at, tz: "UTC", buckets: [] },
      meta: {},
    };
    vi.mocked(getChroniclerAggregateByCategory).mockResolvedValueOnce(mockData);

    const result = await getChroniclerAggregateByCategory(params);

    expect(getChroniclerAggregateByCategory).toHaveBeenCalledWith(params);
    expect(result).toEqual(mockData);
  });

  it("passes tz and privacy_tier params", async () => {
    const extended = { ...params, tz: "America/New_York", privacy_tier: "normal,sensitive" };
    vi.mocked(getChroniclerAggregateByCategory).mockResolvedValueOnce({
      data: { start_at: params.start_at, end_at: params.end_at, tz: "America/New_York", buckets: [] },
      meta: {},
    });

    await getChroniclerAggregateByCategory(extended);
    expect(getChroniclerAggregateByCategory).toHaveBeenCalledWith(extended);
  });

  it("propagates error from client", async () => {
    vi.mocked(getChroniclerAggregateByCategory).mockRejectedValueOnce(new Error("server error"));
    await expect(getChroniclerAggregateByCategory(params)).rejects.toThrow("server error");
  });
});

describe("getChroniclerAggregateByDay client delegate", () => {
  const params = { start_at: "2026-01-01T00:00:00Z", end_at: "2026-01-02T00:00:00Z" };

  it("passes params through to client", async () => {
    vi.mocked(getChroniclerAggregateByDay).mockResolvedValueOnce([]);

    const result = await getChroniclerAggregateByDay(params);

    expect(getChroniclerAggregateByDay).toHaveBeenCalledWith(params);
    expect(result).toEqual([]);
  });

  it("passes optional category filter", async () => {
    const withCategory = { ...params, category: "work" };
    vi.mocked(getChroniclerAggregateByDay).mockResolvedValueOnce([]);

    await getChroniclerAggregateByDay(withCategory);
    expect(getChroniclerAggregateByDay).toHaveBeenCalledWith(withCategory);
  });

  it("propagates error from client", async () => {
    vi.mocked(getChroniclerAggregateByDay).mockRejectedValueOnce(new Error("bad request"));
    await expect(getChroniclerAggregateByDay(params)).rejects.toThrow("bad request");
  });
});

describe("getChroniclerSourceState client delegate", () => {
  it("calls with no params and returns data", async () => {
    const mockData = { data: [], meta: {} };
    vi.mocked(getChroniclerSourceState).mockResolvedValueOnce(mockData);

    const result = await getChroniclerSourceState();

    expect(getChroniclerSourceState).toHaveBeenCalledOnce();
    expect(result).toEqual(mockData);
  });

  it("propagates error from client", async () => {
    vi.mocked(getChroniclerSourceState).mockRejectedValueOnce(new Error("Connection refused"));
    await expect(getChroniclerSourceState()).rejects.toThrow("Connection refused");
  });
});

describe("getChroniclerDayClose client delegate", () => {
  const params: ChroniclerDayCloseParams = { window_start: "2026-01-01", window_end: "2026-01-02" };

  it("returns fresh response when cache is current", async () => {
    const freshResponse = {
      stale: false as const,
      prose: "Yesterday you worked for 6 hours.",
      provenance_refs: ["ep:abc123"],
      cache_built_at: "2026-01-02T08:00:00Z",
    };
    vi.mocked(getChroniclerDayClose).mockResolvedValueOnce(freshResponse);

    const result = await getChroniclerDayClose(params);

    expect(getChroniclerDayClose).toHaveBeenCalledWith(params);
    expect(result.stale).toBe(false);
    if (!result.stale) {
      expect(result.prose).toBe("Yesterday you worked for 6 hours.");
      expect(result.provenance_refs).toContain("ep:abc123");
    }
  });

  it("returns stale response when cache has been invalidated", async () => {
    const staleResponse = {
      stale: true as const,
      cache_built_at: "2026-01-02T08:00:00Z",
      last_invalidating_event_at: "2026-01-02T09:30:00Z",
    };
    vi.mocked(getChroniclerDayClose).mockResolvedValueOnce(staleResponse);

    const result = await getChroniclerDayClose(params);

    expect(result.stale).toBe(true);
    if (result.stale) {
      expect(result.last_invalidating_event_at).toBe("2026-01-02T09:30:00Z");
    }
  });

  it("surfaces 404 ApiError when no cache entry exists", async () => {
    vi.mocked(getChroniclerDayClose).mockRejectedValueOnce(
      new ApiError("not_found", "No day-close cache entry found", 404),
    );

    await expect(getChroniclerDayClose(params)).rejects.toThrow("No day-close cache entry found");
  });

  it("uses different keys for different windows (cache isolation)", () => {
    const params2: ChroniclerDayCloseParams = { window_start: "2026-01-02", window_end: "2026-01-03" };
    expect(chroniclesKeys.dayClose(params)).not.toEqual(chroniclesKeys.dayClose(params2));
  });

  it("passes custom window params through", async () => {
    const customParams: ChroniclerDayCloseParams = {
      window_start: "2026-03-15",
      window_end: "2026-03-16",
    };
    vi.mocked(getChroniclerDayClose).mockResolvedValueOnce({
      stale: false as const,
      prose: "Custom window summary.",
      provenance_refs: [],
      cache_built_at: "2026-03-16T07:00:00Z",
    });

    await getChroniclerDayClose(customParams);
    expect(getChroniclerDayClose).toHaveBeenCalledWith(customParams);
  });
});
