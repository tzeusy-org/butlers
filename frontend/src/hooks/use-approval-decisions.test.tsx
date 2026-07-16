// @vitest-environment jsdom
/**
 * Tests for useApprovalDecisionMutations' shared undo-window contract
 * (bu-qvnce.4): scheduleDecision() defers `run()` by UNDO_WINDOW_MS unless
 * cancelDecision() fires first, and this is opt-in via the `undoWindow`
 * option -- a bare call (no option) runs `run()` immediately, matching every
 * pre-existing one-click call site.
 *
 * This is the machinery ApprovalsPage's keyboard triage (a/d/x) and
 * DashboardPage's one-click attention-list rows both now share, so both
 * surfaces are exactly as undoable as each other.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { getApprovalActions } from "@/api/index.ts";
import type { ApprovalAction, PaginatedResponse } from "@/api/types";

vi.mock("@/api/index.ts", () => ({
  approveApproval: vi.fn(() => Promise.resolve({ data: { id: "a1", status: "executed", dispatched: true } })),
  denyApproval: vi.fn(() => Promise.resolve({ data: { id: "a1", status: "denied" } })),
  deferApproval: vi.fn(() => Promise.resolve({ data: { id: "a1", status: "deferred" } })),
  getApprovalActions: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}));

import { UNDO_WINDOW_MS, useApprovalDecisionMutations } from "./use-approval-decisions";
import { useApprovalActions } from "./use-approvals";

function approvalAction(id: string): ApprovalAction {
  return {
    id,
    butler: "general",
    tool_name: "send_reminder",
    tool_args: {},
    status: "pending",
    requested_at: "2026-07-16T00:00:00Z",
  };
}

function pagedActions(actions: ApprovalAction[], total: number): PaginatedResponse<ApprovalAction> {
  return { data: actions, meta: { total, offset: 0, limit: 5, has_more: false } };
}

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useApprovalDecisionMutations -- scheduleDecision (bu-qvnce.4)", () => {
  it("without undoWindow, scheduleDecision runs immediately (matches every pre-existing one-click caller)", () => {
    const { result } = renderHook(() => useApprovalDecisionMutations(), {
      wrapper: makeWrapper(),
    });
    const run = vi.fn();

    act(() => {
      const scheduled = result.current.scheduleDecision("a1", "approve", run);
      expect(scheduled).toBe(true);
    });

    expect(run).toHaveBeenCalledTimes(1);
    expect(result.current.scheduledDecisions.has("a1")).toBe(false);
  });

  it("with undoWindow: true, defers run() until UNDO_WINDOW_MS elapses", () => {
    const { result } = renderHook(() => useApprovalDecisionMutations({ undoWindow: true }), {
      wrapper: makeWrapper(),
    });
    const run = vi.fn();

    act(() => {
      result.current.scheduleDecision("a1", "approve", run);
    });

    expect(run).not.toHaveBeenCalled();
    expect(result.current.scheduledDecisions.get("a1")?.verb).toBe("approve");

    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(run).toHaveBeenCalledTimes(1);
    expect(result.current.scheduledDecisions.has("a1")).toBe(false);
  });

  it("keeps scheduleDecision stable when the undo-window mode is unchanged", () => {
    const { result, rerender } = renderHook(
      ({ undoWindow }: { undoWindow: boolean }) => useApprovalDecisionMutations({ undoWindow }),
      { initialProps: { undoWindow: true }, wrapper: makeWrapper() },
    );
    const scheduleDecision = result.current.scheduleDecision;

    rerender({ undoWindow: true });

    expect(result.current.scheduleDecision).toBe(scheduleDecision);
  });

  it("cancelDecision prevents the scheduled run from ever firing", () => {
    const { result } = renderHook(() => useApprovalDecisionMutations({ undoWindow: true }), {
      wrapper: makeWrapper(),
    });
    const run = vi.fn();

    act(() => {
      result.current.scheduleDecision("a1", "deny", run);
    });
    act(() => {
      result.current.cancelDecision("a1");
    });
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(run).not.toHaveBeenCalled();
    expect(result.current.scheduledDecisions.has("a1")).toBe(false);
  });

  it("ignores a repeat schedule call for an id that's already scheduled", () => {
    const { result } = renderHook(() => useApprovalDecisionMutations({ undoWindow: true }), {
      wrapper: makeWrapper(),
    });
    const firstRun = vi.fn();
    const secondRun = vi.fn();

    act(() => {
      result.current.scheduleDecision("a1", "approve", firstRun);
    });
    act(() => {
      const scheduled = result.current.scheduleDecision("a1", "deny", secondRun);
      expect(scheduled).toBe(false);
    });
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    // Only the first-scheduled verb ever fires -- a slip of a second key/click
    // on the same row must not silently overwrite or double-fire the decision.
    expect(firstRun).toHaveBeenCalledTimes(1);
    expect(secondRun).not.toHaveBeenCalled();
  });

  it("tracks two different ids independently", () => {
    const { result } = renderHook(() => useApprovalDecisionMutations({ undoWindow: true }), {
      wrapper: makeWrapper(),
    });
    const runA = vi.fn();
    const runB = vi.fn();

    act(() => {
      result.current.scheduleDecision("a1", "approve", runA);
      result.current.scheduleDecision("b2", "defer", runB);
    });
    act(() => {
      result.current.cancelDecision("a1");
    });
    act(() => {
      vi.advanceTimersByTime(UNDO_WINDOW_MS);
    });

    expect(runA).not.toHaveBeenCalled();
    expect(runB).toHaveBeenCalledTimes(1);
  });

  it("refreshes paged action-preview rows and count after an inline approval decision", async () => {
    vi.useRealTimers();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    vi.mocked(getApprovalActions)
      .mockResolvedValueOnce(pagedActions([approvalAction("a1"), approvalAction("a2")], 2))
      .mockResolvedValueOnce(pagedActions([approvalAction("a2")], 1));

    function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    }

    const { result } = renderHook(() => ({
      preview: useApprovalActions({ status: "pending", butler: "general", limit: 5 }),
      decisions: useApprovalDecisionMutations(),
    }), { wrapper: Wrapper });

    await waitFor(() => {
      expect(result.current.preview.data?.data.map((action) => action.id)).toEqual(["a1", "a2"]);
      expect(result.current.preview.data?.meta.total).toBe(2);
    });

    await act(async () => {
      await result.current.decisions.approveMut.mutateAsync("a1");
    });

    await waitFor(() => {
      expect(result.current.preview.data?.data.map((action) => action.id)).toEqual(["a2"]);
      expect(result.current.preview.data?.meta.total).toBe(1);
    });
    expect(vi.mocked(getApprovalActions)).toHaveBeenCalledTimes(2);
  });
});
