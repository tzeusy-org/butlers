/**
 * Polling config tests for useButlers and useApprovalMetrics (bu-bm58r.2).
 *
 * Verifies that both hooks consumed by RuntimeSummaryKpi are configured with:
 *   - refetchInterval: 30_000  (30s background polling)
 *   - staleTime: 30_000        (stale-while-revalidate: data stays fresh for 30s)
 *
 * Shared query key for useButlers (["butlers"]) means the butler-list page and
 * the KPI card share one cache entry — a single network call serves both.
 *
 * Strategy: mock @tanstack/react-query's useQuery, call the real hooks, and
 * assert on the options object that was passed through.
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

import { useQuery } from "@tanstack/react-query";
import { useButlers } from "@/hooks/use-butlers";
import { useApprovalMetrics } from "@/hooks/use-approvals";

// ---------------------------------------------------------------------------
// useButlers polling config
// ---------------------------------------------------------------------------

describe("useButlers -- polling config (bu-bm58r.2)", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
  });

  it("passes refetchInterval=30_000 to useQuery", () => {
    useButlers();
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ refetchInterval: 30_000 }),
    );
  });

  it("passes staleTime=30_000 to useQuery", () => {
    useButlers();
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ staleTime: 30_000 }),
    );
  });

  it("uses cache key ['butlers'] (shared with butler-list page)", () => {
    useButlers();
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ["butlers"] }),
    );
  });
});

// ---------------------------------------------------------------------------
// useApprovalMetrics polling config
// ---------------------------------------------------------------------------

describe("useApprovalMetrics -- polling config (bu-bm58r.2)", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
  });

  it("passes refetchInterval=30_000 to useQuery", () => {
    useApprovalMetrics();
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ refetchInterval: 30_000 }),
    );
  });

  it("passes staleTime=30_000 to useQuery", () => {
    useApprovalMetrics();
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ staleTime: 30_000 }),
    );
  });
});
