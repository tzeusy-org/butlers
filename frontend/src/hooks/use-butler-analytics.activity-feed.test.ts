/**
 * Tests for useButlerActivityFeed hook (bu-y7lo7).
 *
 * Strategy: mock @tanstack/react-query's useQuery, call the real hook, and
 * assert on the options object that was passed through.
 *
 * Covers:
 * - Loading state
 * - Success state (mocked response)
 * - Error state
 * - limit parameter passes through
 * - Query key shape
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock useQuery BEFORE importing the hooks under test.
// ---------------------------------------------------------------------------

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn(() => ({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    })),
  };
});

// Mock the API client so tests never hit the network.
vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    getButlerActivityFeed: vi.fn(),
  };
});

import { useQuery } from "@tanstack/react-query";
import { useButlerActivityFeed } from "@/hooks/use-butler-analytics";
import { getButlerActivityFeed } from "@/api/index.ts";
import type { ActivityFeed, ActivityEvent } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeEvent(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    event_type: "session_completed",
    ts: "2026-05-12T10:00:00Z",
    summary: "Session completed",
    entity_id: "abc-123",
    metadata: { trigger_source: "scheduler", success: true, duration_ms: 1500 },
    ...overrides,
  };
}

function makeActivityFeed(events: ActivityEvent[] = []): ActivityFeed {
  return { events };
}

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("useButlerActivityFeed -- loading state", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof useQuery>);
  });

  it("returns isLoading=true when data is pending", () => {
    const result = useButlerActivityFeed("my-butler");
    expect(result.isLoading).toBe(true);
    expect(result.data).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Success state
// ---------------------------------------------------------------------------

describe("useButlerActivityFeed -- success state", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
  });

  it("returns data when query succeeds", () => {
    const feed = makeActivityFeed([
      makeEvent({ event_type: "session_completed" }),
      makeEvent({ event_type: "memory_write", summary: "Memory episode written" }),
    ]);

    vi.mocked(useQuery).mockReturnValue({
      data: feed,
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useQuery>);

    const result = useButlerActivityFeed("my-butler");
    expect(result.isLoading).toBe(false);
    expect(result.isError).toBe(false);
    expect(result.data).toEqual(feed);
  });

  it("returns empty events list when feed is empty", () => {
    const feed = makeActivityFeed([]);

    vi.mocked(useQuery).mockReturnValue({
      data: feed,
      isLoading: false,
      isError: false,
      error: null,
    } as ReturnType<typeof useQuery>);

    const result = useButlerActivityFeed("my-butler");
    expect(result.data?.events).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("useButlerActivityFeed -- error state", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
  });

  it("returns isError=true and the error when query fails", () => {
    const err = new Error("network failure");

    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: err,
    } as ReturnType<typeof useQuery>);

    const result = useButlerActivityFeed("my-butler");
    expect(result.isError).toBe(true);
    expect(result.error).toBe(err);
    expect(result.data).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Query key shape
// ---------------------------------------------------------------------------

describe("useButlerActivityFeed -- query key", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof useQuery>);
  });

  it("uses ['butlers', name, 'activity-feed', { limit: undefined }] when no limit given", () => {
    useButlerActivityFeed("my-butler");
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ["butlers", "my-butler", "activity-feed", { limit: undefined }],
      }),
    );
  });

  it("uses { limit } in query key when limit is provided", () => {
    useButlerActivityFeed("my-butler", 20);
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ["butlers", "my-butler", "activity-feed", { limit: 20 }],
      }),
    );
  });

  it("disables query when butlerName is empty string", () => {
    useButlerActivityFeed("");
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false }),
    );
  });
});

// ---------------------------------------------------------------------------
// limit parameter passes through
// ---------------------------------------------------------------------------

describe("useButlerActivityFeed -- limit parameter", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
    vi.mocked(useQuery).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    } as ReturnType<typeof useQuery>);
    vi.mocked(getButlerActivityFeed).mockResolvedValue({
      data: makeActivityFeed([]),
      meta: {},
    });
  });

  it("calls getButlerActivityFeed with no params when limit is omitted", () => {
    useButlerActivityFeed("my-butler");

    const calls = vi.mocked(useQuery).mock.calls;
    expect(calls.length).toBeGreaterThan(0);

    const opts = calls[0][0] as { queryFn: () => unknown };
    opts.queryFn();

    expect(vi.mocked(getButlerActivityFeed)).toHaveBeenCalledWith("my-butler", undefined);
  });

  it("calls getButlerActivityFeed with { limit } when limit is provided", () => {
    useButlerActivityFeed("my-butler", 25);

    const calls = vi.mocked(useQuery).mock.calls;
    expect(calls.length).toBeGreaterThan(0);

    const opts = calls[0][0] as { queryFn: () => unknown };
    opts.queryFn();

    expect(vi.mocked(getButlerActivityFeed)).toHaveBeenCalledWith("my-butler", { limit: 25 });
  });
});
