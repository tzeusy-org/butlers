/**
 * Polling-interval scaling tests for the Reviews-tab aggregate hooks (bu-39359).
 *
 * After the 5-map cap was removed in PR #1518 (bu-az8fq), the per-map polling
 * cost is O(N): each map runs 3 polled queries on 15s/30s intervals. To keep
 * total per-second request volume bounded for residents with many active maps,
 * the aggregate hooks scale their `refetchInterval` with the active map count
 * via `scaledPollInterval(baseMs, perMapMs, mapCount)`.
 *
 * Strategy: mock `useQueries` so we can capture the options array passed in by
 * the hook under test, then assert on the `refetchInterval` of each query slot.
 * The `scaledPollInterval` helper itself is exercised directly with unit tests.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock useQueries BEFORE importing the hooks under test.
// ---------------------------------------------------------------------------

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQueries: vi.fn(() => []),
  };
});

import { useQueries } from "@tanstack/react-query";
import {
  scaledPollInterval,
  useAllPendingReviews,
  useAllMasterySummaries,
  useAllFrontierNodes,
} from "@/hooks/use-education";

type QueryOpts = {
  refetchInterval?: number;
  refetchIntervalInBackground?: boolean;
  staleTime?: number;
};

function capturedQueries(): QueryOpts[] {
  const calls = vi.mocked(useQueries).mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  const arg = calls[calls.length - 1][0] as { queries: QueryOpts[] };
  return arg.queries;
}

// ---------------------------------------------------------------------------
// scaledPollInterval — unit tests
// ---------------------------------------------------------------------------

describe("scaledPollInterval", () => {
  it("returns base interval when mapCount is 0 (no maps)", () => {
    expect(scaledPollInterval(15_000, 5_000, 0)).toBe(15_000);
  });

  it("returns base interval for low map counts under the inflection point", () => {
    // 15s base, 5s perMap → inflection at floor(15/5)=3 maps
    expect(scaledPollInterval(15_000, 5_000, 1)).toBe(15_000);
    expect(scaledPollInterval(15_000, 5_000, 2)).toBe(15_000);
    expect(scaledPollInterval(15_000, 5_000, 3)).toBe(15_000);
  });

  it("scales linearly past the inflection point", () => {
    // 15s base, 5s perMap
    expect(scaledPollInterval(15_000, 5_000, 4)).toBe(20_000);
    expect(scaledPollInterval(15_000, 5_000, 5)).toBe(25_000);
    expect(scaledPollInterval(15_000, 5_000, 10)).toBe(50_000);
  });

  it("scales with the 30s base profile used by mastery and frontier", () => {
    // 30s base, 10s perMap → inflection at 3 maps
    expect(scaledPollInterval(30_000, 10_000, 1)).toBe(30_000);
    expect(scaledPollInterval(30_000, 10_000, 3)).toBe(30_000);
    expect(scaledPollInterval(30_000, 10_000, 4)).toBe(40_000);
    expect(scaledPollInterval(30_000, 10_000, 10)).toBe(100_000);
  });

  it("monotonically non-decreasing as mapCount grows", () => {
    let prev = -1;
    for (let n = 0; n <= 20; n++) {
      const next = scaledPollInterval(15_000, 5_000, n);
      expect(next).toBeGreaterThanOrEqual(prev);
      prev = next;
    }
  });
});

// ---------------------------------------------------------------------------
// useAllPendingReviews — interval scales with map count
// ---------------------------------------------------------------------------

describe("useAllPendingReviews — polling interval scales with map count", () => {
  beforeEach(() => {
    vi.mocked(useQueries).mockClear();
  });

  it("uses the 15s base interval for a single map", () => {
    useAllPendingReviews(["m1"]);
    const queries = capturedQueries();
    expect(queries).toHaveLength(1);
    expect(queries[0].refetchInterval).toBe(15_000);
  });

  it("keeps the 15s base interval for ≤3 maps", () => {
    useAllPendingReviews(["m1", "m2", "m3"]);
    const queries = capturedQueries();
    expect(queries).toHaveLength(3);
    for (const q of queries) {
      expect(q.refetchInterval).toBe(15_000);
    }
  });

  it("scales the interval to 50s when 10 active maps are present", () => {
    const ids = Array.from({ length: 10 }, (_, i) => `m${i}`);
    useAllPendingReviews(ids);
    const queries = capturedQueries();
    expect(queries).toHaveLength(10);
    for (const q of queries) {
      expect(q.refetchInterval).toBe(50_000);
    }
  });

  it("interval is strictly larger for many maps than for few maps", () => {
    useAllPendingReviews(["m1", "m2"]);
    const fewInterval = capturedQueries()[0].refetchInterval ?? 0;

    vi.mocked(useQueries).mockClear();
    useAllPendingReviews(Array.from({ length: 12 }, (_, i) => `m${i}`));
    const manyInterval = capturedQueries()[0].refetchInterval ?? 0;

    expect(manyInterval).toBeGreaterThan(fewInterval);
  });

  it("keeps refetchIntervalInBackground=false (no background polling)", () => {
    useAllPendingReviews(["m1"]);
    const queries = capturedQueries();
    expect(queries[0].refetchIntervalInBackground).toBe(false);
  });

  it("pins staleTime to the scaled interval so focus/mount refetches respect the backoff", () => {
    // 1 map: interval = 15s; staleTime should match.
    useAllPendingReviews(["m1"]);
    let queries = capturedQueries();
    expect(queries[0].staleTime).toBe(queries[0].refetchInterval);
    expect(queries[0].staleTime).toBe(15_000);

    // 10 maps: scaled interval = 50s; staleTime must scale with it, not stay
    // at the global default (30s) which would let focus/mount refetches
    // punch through the backoff.
    vi.mocked(useQueries).mockClear();
    useAllPendingReviews(Array.from({ length: 10 }, (_, i) => `m${i}`));
    queries = capturedQueries();
    for (const q of queries) {
      expect(q.staleTime).toBe(50_000);
      expect(q.staleTime).toBe(q.refetchInterval);
    }
  });
});

// ---------------------------------------------------------------------------
// useAllMasterySummaries — interval scales with map count
// ---------------------------------------------------------------------------

describe("useAllMasterySummaries — polling interval scales with map count", () => {
  beforeEach(() => {
    vi.mocked(useQueries).mockClear();
  });

  it("uses the 30s base interval for ≤3 maps", () => {
    useAllMasterySummaries(["m1", "m2", "m3"]);
    const queries = capturedQueries();
    expect(queries).toHaveLength(3);
    for (const q of queries) {
      expect(q.refetchInterval).toBe(30_000);
    }
  });

  it("scales the interval to 100s when 10 active maps are present", () => {
    const ids = Array.from({ length: 10 }, (_, i) => `m${i}`);
    useAllMasterySummaries(ids);
    const queries = capturedQueries();
    expect(queries).toHaveLength(10);
    for (const q of queries) {
      expect(q.refetchInterval).toBe(100_000);
    }
  });

  it("interval is strictly larger for many maps than for few maps", () => {
    useAllMasterySummaries(["m1", "m2"]);
    const fewInterval = capturedQueries()[0].refetchInterval ?? 0;

    vi.mocked(useQueries).mockClear();
    useAllMasterySummaries(Array.from({ length: 8 }, (_, i) => `m${i}`));
    const manyInterval = capturedQueries()[0].refetchInterval ?? 0;

    expect(manyInterval).toBeGreaterThan(fewInterval);
  });

  it("pins staleTime to the scaled interval", () => {
    useAllMasterySummaries(Array.from({ length: 10 }, (_, i) => `m${i}`));
    const queries = capturedQueries();
    for (const q of queries) {
      expect(q.staleTime).toBe(100_000);
      expect(q.staleTime).toBe(q.refetchInterval);
    }
  });
});

// ---------------------------------------------------------------------------
// useAllFrontierNodes — interval scales with map count
// ---------------------------------------------------------------------------

describe("useAllFrontierNodes — polling interval scales with map count", () => {
  beforeEach(() => {
    vi.mocked(useQueries).mockClear();
  });

  it("uses the 30s base interval for ≤3 maps", () => {
    useAllFrontierNodes(["m1", "m2", "m3"]);
    const queries = capturedQueries();
    expect(queries).toHaveLength(3);
    for (const q of queries) {
      expect(q.refetchInterval).toBe(30_000);
    }
  });

  it("scales the interval to 100s when 10 active maps are present", () => {
    const ids = Array.from({ length: 10 }, (_, i) => `m${i}`);
    useAllFrontierNodes(ids);
    const queries = capturedQueries();
    expect(queries).toHaveLength(10);
    for (const q of queries) {
      expect(q.refetchInterval).toBe(100_000);
    }
  });

  it("pins staleTime to the scaled interval", () => {
    useAllFrontierNodes(Array.from({ length: 10 }, (_, i) => `m${i}`));
    const queries = capturedQueries();
    for (const q of queries) {
      expect(q.staleTime).toBe(100_000);
      expect(q.staleTime).toBe(q.refetchInterval);
    }
  });
});
