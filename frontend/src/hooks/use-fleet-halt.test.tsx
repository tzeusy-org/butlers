// @vitest-environment jsdom
/**
 * Tests for useFleetHaltStatus (bu-7o89u.3) -- derives the monthly
 * spend-ceiling fleet-halt state from GET /api/dispatch/attempts.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import type { DispatchAttemptEntry, PaginatedResponse } from "@/api/types.ts";

const mockGetDispatchAttempts = vi.fn();

vi.mock("@/api/client", () => ({
  getDispatchAttempts: (...args: unknown[]) => mockGetDispatchAttempts(...args),
}));

// useFleetHaltStatus's useBusAwarePollInterval reads the shared EventBusProvider
// context -- stub it rather than wrapping every renderHook call in a real
// provider; these tests only care about the derived halt state, not cadence.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", lastEventAt: null, subscribe: vi.fn() }),
}));

import { useFleetHaltStatus, CEILING_DENIAL_REASON_PREFIX } from "./use-fleet-halt";

function attempt(overrides: Partial<DispatchAttemptEntry> = {}): DispatchAttemptEntry {
  return {
    ts: "2026-07-10T08:00:00.000Z",
    butler: "general",
    outcome: "quota_skip",
    attempt_index: 0,
    failure_reason: `${CEILING_DENIAL_REASON_PREFIX}: month-to-date $50.00 >= ceiling $50.00`,
    error_code: null,
    error_message: null,
    tool_call_count: 0,
    session_id: null,
    logical_session_id: "req-001",
    ...overrides,
  };
}

function page(
  data: DispatchAttemptEntry[],
  total: number,
): PaginatedResponse<DispatchAttemptEntry> {
  return { data, meta: { total, offset: 0, limit: 100, has_more: false } };
}

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return {
    client,
    Wrapper: function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useFleetHaltStatus", () => {
  it("is inactive when no ceiling denials exist this month", async () => {
    mockGetDispatchAttempts.mockResolvedValue(page([], 0));

    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useFleetHaltStatus(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.active).toBe(false);
    expect(result.current.deniedTotal).toBe(0);
    expect(result.current.since).toBeNull();
  });

  it("is active with denied totals, today count, since-ts, and recent rows when denials exist", async () => {
    const earliest = attempt({ ts: "2026-07-10T08:00:00.000Z", butler: "general" });
    const latest = attempt({ ts: "2026-07-12T09:00:00.000Z", butler: "finance" });

    mockGetDispatchAttempts.mockImplementation((params: { since?: string; order?: string }) => {
      // onset query: order=asc, limit=1 -> earliest row, total=7 this month
      if (params.order === "asc") {
        return Promise.resolve(page([earliest], 7));
      }
      // today query: since=start-of-today, no order override (default desc), limit=1
      if (params.since && !params.order) {
        return Promise.resolve(page([latest], 3));
      }
      // recent (drawer) query: order=desc, limit=20
      return Promise.resolve(page([latest, earliest], 7));
    });

    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useFleetHaltStatus(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.active).toBe(true);
    expect(result.current.deniedTotal).toBe(7);
    expect(result.current.deniedToday).toBe(3);
    expect(result.current.since).toBe("2026-07-10T08:00:00.000Z");
    expect(result.current.recentAttempts.map((a) => a.butler)).toEqual(["finance", "general"]);
  });

  it("reports isError (not a false 'no halt') when a query fails", async () => {
    mockGetDispatchAttempts.mockRejectedValue(new Error("503"));

    const { Wrapper } = makeWrapper();
    const { result } = renderHook(() => useFleetHaltStatus(), { wrapper: Wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.active).toBe(false);
    expect(result.current.deniedTotal).toBe(0);
  });

  it("scopes queries to outcome=quota_skip and the ceiling reason prefix", async () => {
    mockGetDispatchAttempts.mockResolvedValue(page([], 0));

    const { Wrapper } = makeWrapper();
    renderHook(() => useFleetHaltStatus(), { wrapper: Wrapper });

    await waitFor(() => expect(mockGetDispatchAttempts).toHaveBeenCalled());
    for (const call of mockGetDispatchAttempts.mock.calls) {
      const params = call[0] as { outcome?: string; reason_prefix?: string };
      expect(params.outcome).toBe("quota_skip");
      expect(params.reason_prefix).toBe(CEILING_DENIAL_REASON_PREFIX);
    }
  });
});
