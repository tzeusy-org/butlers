/**
 * Unit tests for the issues hooks.
 *
 * useDismissIssue/useUndismissIssue now delegate their cancel/snapshot/
 * rollback/invalidate mechanics to the shared useOptimisticListMutation
 * (bu-86c4c.13) — that generic machinery has its own dedicated tests in
 * use-optimistic-mutation.test.ts. These tests only verify the *wiring*:
 * which API function is called, which list prefix is targeted, how items are
 * filtered, and which keys are invalidated.
 *
 * Covers:
 *   - useIssues passes include_dismissed through to getIssues, distinct keys
 *   - useDismissIssue/useUndismissIssue call the right API function
 *   - useDismissIssue/useUndismissIssue filter the dismissed issue out
 *   - useDismissIssue/useUndismissIssue invalidate the shared ["issues"] prefix
 */

import { useQuery } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn((opts: unknown) => opts),
  };
});

// useIssues now calls useBusAwarePollInterval (bu-01r64.3), which reads the
// real EventBusProvider context via useContext -- invalid outside a React
// render. These tests call the hook directly (no renderHook), so mock the
// bus status as always "open" (the same reconciliation cadence the prior
// static POLL_BUS_RECONCILE_MS gave every test here).
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", lastEventAt: null, subscribe: vi.fn() }),
}));

const { mockUseOptimisticListMutation } = vi.hoisted(() => ({
  mockUseOptimisticListMutation: vi.fn((opts: unknown) => opts),
}));
vi.mock("@/hooks/use-optimistic-mutation.ts", () => ({
  useOptimisticListMutation: mockUseOptimisticListMutation,
}));

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    getIssues: vi.fn(() => Promise.resolve({ data: [] })),
    dismissIssue: vi.fn(() => Promise.resolve({ data: {} })),
    undismissIssue: vi.fn(() => Promise.resolve({ data: {} })),
  };
});

import { dismissIssue, getIssues, undismissIssue } from "@/api/index.ts";
import type { Issue } from "@/api/types";
import { useDismissIssue, useIssues, useUndismissIssue } from "./use-issues";

const mockUseQuery = vi.mocked(useQuery);

// The mutation hooks (useDismissIssue/useUndismissIssue) target the plain
// 2-element list-key prefix -- only useIssues's own queryKey carries the
// window suffix (bu-qvnce.13).
const ACTIVE_KEY = ["issues", { dismissed: false }];
const DISMISSED_KEY = ["issues", { dismissed: true }];

// bu-qvnce.13: useIssues now takes an options object ({includeDismissed,
// window}) instead of a positional boolean, and the query key carries the
// window alongside the dismissed flag.
const ACTIVE_QUERY_KEY = ["issues", { dismissed: false }, { window: undefined }];
const DISMISSED_QUERY_KEY = ["issues", { dismissed: true }, { window: undefined }];

interface CapturedQueryOptions {
  queryKey: unknown;
  queryFn: () => unknown;
}

interface CapturedListMutationOptions<TVariables = string> {
  mutationFn: (variables: TVariables) => unknown;
  listKeyPrefix: readonly unknown[];
  updateItems: (issues: Issue[], variables: TVariables) => Issue[];
  invalidateQueryKeys?: (readonly unknown[])[];
}

function lastQueryOptions(): CapturedQueryOptions {
  const calls = mockUseQuery.mock.calls;
  return calls[calls.length - 1][0] as unknown as CapturedQueryOptions;
}

function lastListMutationOptions<TVariables = string>(): CapturedListMutationOptions<TVariables> {
  const calls = mockUseOptimisticListMutation.mock.calls;
  return calls[calls.length - 1][0] as unknown as CapturedListMutationOptions<TVariables>;
}

function makeIssue(overrides: Partial<Issue> = {}): Issue {
  return {
    severity: "warning",
    type: "audit_error_group:boom",
    butler: "general",
    description: "boom (general)",
    link: null,
    issue_key: "audit_error_group:boom::general",
    dismissed: true,
    ...overrides,
  };
}

describe("useIssues", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses distinct query keys for the active vs dismissed views", () => {
    useIssues({ includeDismissed: false });
    expect(lastQueryOptions().queryKey).toEqual(ACTIVE_QUERY_KEY);
    useIssues({ includeDismissed: true });
    expect(lastQueryOptions().queryKey).toEqual(DISMISSED_QUERY_KEY);
  });

  it("forwards includeDismissed to getIssues via queryFn", () => {
    useIssues({ includeDismissed: true });
    lastQueryOptions().queryFn();
    expect(getIssues).toHaveBeenCalledWith({ includeDismissed: true, window: undefined });

    vi.mocked(getIssues).mockClear();
    useIssues({ includeDismissed: false });
    lastQueryOptions().queryFn();
    expect(getIssues).toHaveBeenCalledWith({ includeDismissed: false, window: undefined });
  });

  it("defaults to includeDismissed=false when called with no options", () => {
    useIssues();
    expect(lastQueryOptions().queryKey).toEqual(ACTIVE_QUERY_KEY);
  });

  it("threads window through the query key and to getIssues (bu-qvnce.13)", () => {
    useIssues({ window: "24h" });
    expect(lastQueryOptions().queryKey).toEqual(["issues", { dismissed: false }, { window: "24h" }]);
    lastQueryOptions().queryFn();
    expect(getIssues).toHaveBeenCalledWith({ includeDismissed: false, window: "24h" });
  });
});

describe("useDismissIssue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls dismissIssue with the issue key and last_seen_at watermark, targets the active list", () => {
    useDismissIssue();
    const opts = lastListMutationOptions<{ issueKey: string; lastSeenAt?: string | null }>();
    opts.mutationFn({
      issueKey: "audit_error_group:boom::general",
      lastSeenAt: "2026-07-01T12:00:00Z",
    });
    expect(dismissIssue).toHaveBeenCalledWith(
      "audit_error_group:boom::general",
      "2026-07-01T12:00:00Z",
    );
    expect(opts.listKeyPrefix).toEqual(ACTIVE_KEY);
  });

  it("filters the dismissed issue out of the cached items", () => {
    useDismissIssue();
    const issue = makeIssue();
    const other = makeIssue({ issue_key: "other::general" });
    const result = lastListMutationOptions<{
      issueKey: string;
      lastSeenAt?: string | null;
    }>().updateItems([issue, other], { issueKey: issue.issue_key });
    expect(result).toEqual([other]);
  });

  it("invalidates the broad ['issues'] prefix so both views refresh", () => {
    useDismissIssue();
    expect(lastListMutationOptions().invalidateQueryKeys).toEqual([["issues"]]);
  });
});

describe("useUndismissIssue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls undismissIssue with the issue key, targets the dismissed list", () => {
    useUndismissIssue();
    const opts = lastListMutationOptions();
    opts.mutationFn("audit_error_group:boom::general");
    expect(undismissIssue).toHaveBeenCalledWith("audit_error_group:boom::general");
    expect(opts.listKeyPrefix).toEqual(DISMISSED_KEY);
  });

  it("filters the restored issue out of the cached dismissed items", () => {
    useUndismissIssue();
    const issue = makeIssue();
    const other = makeIssue({ issue_key: "other::general" });
    const result = lastListMutationOptions().updateItems([issue, other], issue.issue_key);
    expect(result).toEqual([other]);
  });

  it("invalidates the broad ['issues'] prefix so both views refresh", () => {
    useUndismissIssue();
    expect(lastListMutationOptions().invalidateQueryKeys).toEqual([["issues"]]);
  });
});
