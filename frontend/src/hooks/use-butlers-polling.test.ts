/**
 * Polling config tests for useButlers and useApprovalMetrics (bu-bm58r.2).
 *
 * Verifies both hooks consumed by RuntimeSummaryKpi are configured with:
 *   - staleTime: 30_000        (stale-while-revalidate: data stays fresh for 30s)
 *   - useButlers: refetchInterval 30_000 (30s background polling; not yet
 *     covered by a fleet-event-bus invalidation).
 *   - useApprovalMetrics: refetchInterval demoted to 5*60_000 (bu-86c4c.8,
 *     §JARVIS audit move 5) — the fleet event bus now invalidates this key
 *     live on every approval state transition, so polling is a reconciliation
 *     safety net rather than the primary path.
 *
 * Shared query key for useButlers (["butlers"]) means the butler-list page and
 * the KPI card share one cache entry — a single network call serves both.
 *
 * Strategy: mock @tanstack/react-query's useQuery, call the real hooks, and
 * assert on the options object that was passed through.
 *
 * Also includes spec §Auto-refresh polling fake-timer test (bu-insd4.3):
 * The ButlersPage renders via SSR (renderToStaticMarkup) with a fully-mocked
 * useButlers, so timer-driven refetch cannot be observed at the page level.
 * The canonical test for the 30s timer is here, at the hook configuration layer.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

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

// useApprovalMetrics now calls useBusAwarePollInterval (bu-01r64.3), which
// reads the real EventBusProvider context via useContext -- invalid outside a
// React render. These tests call the hook directly (no renderHook), so mock
// the bus status as always "open" (the same reconciliation cadence the prior
// static POLL_BUS_RECONCILE_MS gave this test).
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", lastEventAt: null, subscribe: vi.fn() }),
}));

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
    // Hardcoded 30_000 (not BUTLERS_POLL_MS) is intentional: this test
    // verifies the ACTUAL configured cadence, not that the hook echoes back
    // whatever its own constant happens to hold (bu-ep4ks.15).
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      // eslint-disable-next-line no-restricted-syntax -- see reason above
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
// Spec §Auto-refresh polling — fake-timer alignment (bu-insd4.3)
//
// The ButlersPage renders via SSR (renderToStaticMarkup) with a fully-mocked
// useButlers hook, so TanStack Query never runs real timers in page-level
// tests. This suite tests the polling contract at the hook configuration
// layer: the captured refetchInterval MUST equal the 30 000 ms that
// vi.advanceTimersByTime(30_000) advances.
// ---------------------------------------------------------------------------

describe("useButlers -- 30s polling fake-timer alignment (bu-insd4.3)", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("refetchInterval equals vi.advanceTimersByTime(30_000) step size", () => {
    vi.useFakeTimers();

    useButlers();

    const calls = vi.mocked(useQuery).mock.calls;
    expect(calls.length).toBeGreaterThan(0);

    const opts = calls[0][0] as { refetchInterval?: number };
    const captured = opts.refetchInterval;

    // Advance fake timers by exactly 30s — the same amount that should trigger
    // one polling cycle. The assertion confirms the hook's refetchInterval is
    // the precise interval the spec requires.
    vi.advanceTimersByTime(30_000);

    expect(captured).toBe(30_000);
  });
});

// ---------------------------------------------------------------------------
// useApprovalMetrics polling config
// ---------------------------------------------------------------------------

describe("useApprovalMetrics -- polling config (bu-bm58r.2, demoted bu-86c4c.8)", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
  });

  // bu-86c4c.8 (§JARVIS audit move 5): the fleet event bus now invalidates
  // this key on every approval state-transition event, so the poll is a
  // 5-minute reconciliation sweep rather than the 30s primary path.
  it("passes refetchInterval=5*60_000 (5-minute reconciliation sweep) to useQuery", () => {
    useApprovalMetrics();
    // Hardcoded (not POLL_BUS_RECONCILE_MS) is intentional -- see reason on
    // the useButlers assertion above (bu-ep4ks.15).
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      // eslint-disable-next-line no-restricted-syntax -- see reason above
      expect.objectContaining({ refetchInterval: 5 * 60_000 }),
    );
  });

  it("passes staleTime=30_000 to useQuery", () => {
    useApprovalMetrics();
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ staleTime: 30_000 }),
    );
  });
});
