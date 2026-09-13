// @vitest-environment jsdom

/**
 * Tests for ApprovalsPage load-more affordance (bu-rkc25).
 *
 * Covers:
 * 1. Renders rail items from getApprovalsFlat
 * 2. Shows "Load more" button only when response is full (length === limit)
 * 3. Does NOT show "Load more" when response is smaller than limit
 * 4. Clicking "Load more" bumps limit and re-fetches
 * 5. "Load more" button is disabled while fetching
 * 6. Empty state renders when no pending approvals
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ApprovalsPage from "@/pages/ApprovalsPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("sonner", () => {
  // sonner's real export is a CALLABLE function (toast(msg, opts)) that also
  // carries .success/.error/.warning statics -- the undo-toast path
  // (bu-86c4c.14) calls it directly, existing call sites use the statics.
  const toastFn = Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  });
  return { toast: toastFn };
});

// Records every navigate(to, options) call while forwarding to the real
// react-router navigate so actual routing behavior (used by most tests in
// this file) is unaffected -- purely a tap for the history-replace
// regression test below (bu-2nnhj). vi.hoisted keeps `navigateCalls`
// initialized before the mock factory runs (import hoisting would otherwise
// evaluate the factory before a plain `const` had been assigned).
const { navigateCalls } = vi.hoisted(() => ({
  navigateCalls: [] as Array<[string, { replace?: boolean } | undefined]>,
}));
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useNavigate: () => {
      const realNavigate = actual.useNavigate();
      return ((to: unknown, options?: { replace?: boolean }) => {
        navigateCalls.push([String(to), options]);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        return (realNavigate as any)(to, options);
      }) as ReturnType<typeof actual.useNavigate>;
    },
  };
});

// useApprovalMetrics (bu-p5sg6, callback-secret degraded banner) calls
// useBusAwarePollInterval, which reads the real EventBusProvider context via
// useContext -- invalid without a provider in this render tree. Stub the bus
// health as always "healthy" (same pattern as SpendPage.test.tsx /
// SessionStripeChart.test.tsx), giving this page's other bus-aware hooks the
// reconciliation cadence too.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({
    status: "open",
    health: "healthy",
    lastEventAt: null,
    subscribe: vi.fn(),
  }),
}));

// Mock the API module — we only need getApprovalsFlat + getApprovalsHistory +
// getApprovalsPolicy for these tests. Others are stubs to satisfy imports.
vi.mock("@/api/index.ts", () => ({
  getApprovalsFlat: vi.fn(),
  getApprovalsHistory: vi.fn(),
  getApprovalsPolicy: vi.fn(),
  getUnroutableAttention: vi.fn(),
  retryUnroutableAttention: vi.fn(),
  getApprovalDetail: vi.fn(),
  approveApproval: vi.fn(),
  denyApproval: vi.fn(),
  deferApproval: vi.fn(),
  retryApproval: vi.fn(),
  abandonApproval: vi.fn(),
  updateApprovalsPolicy: vi.fn(),
  // Autonomy suggestions banner data + verbs (wired into ApprovalsPage via
  // the use-approvals hooks).
  getAutonomySuggestions: vi.fn(),
  confirmAutonomySuggestion: vi.fn(),
  dismissAutonomySuggestion: vi.fn(),
  // AutonomyPanel (bu-86c4c.12) — always rendered alongside the dossier.
  getApprovalRules: vi.fn(),
  getApprovalGatedTools: vi.fn(() => Promise.resolve({ data: [], meta: {} })),
  createApprovalRule: vi.fn(),
  getApprovalRuleSuggestions: vi.fn(),
  createApprovalRuleFromAction: vi.fn(),
  revokeApprovalRule: vi.fn(),
  // Module-level callback-secret degraded note (bu-p5sg6) -- default stub,
  // per-test overrides set callback_secret_configured explicitly.
  getApprovalMetrics: vi.fn(),
  // Rule-promotion readers are independently query-backed. Keep their empty
  // success path explicit so error-honesty tests can distinguish it from a
  // failed request.
  getRulePromotionSuggestions: vi.fn(),
  getRulePromotionStats: vi.fn(),
  confirmRulePromotionSuggestion: vi.fn(),
  dismissRulePromotionSuggestion: vi.fn(),
  setRulePromotionRuleEnabled: vi.fn(),
}));

import {
  approveApproval,
  abandonApproval,
  confirmAutonomySuggestion,
  deferApproval,
  denyApproval,
  dismissAutonomySuggestion,
  getApprovalDetail,
  getApprovalGatedTools,
  getApprovalMetrics,
  getApprovalRuleSuggestions,
  getApprovalRules,
  getApprovalsFlat,
  getApprovalsHistory,
  getApprovalsPolicy,
  getUnroutableAttention,
  getAutonomySuggestions,
  getRulePromotionStats,
  getRulePromotionSuggestions,
  retryApproval,
  retryUnroutableAttention,
  revokeApprovalRule,
  createApprovalRuleFromAction,
  updateApprovalsPolicy,
} from "@/api/index.ts";
import { applyFleetEvent } from "@/hooks/event-cache-registry";
import { toast } from "sonner";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSummary(id: string, toolName = "send_email") {
  return {
    id,
    butler: "general",
    tool_name: toolName,
    status: "pending",
    why: null,
    created_at: "2026-05-17T10:00:00Z",
    expires_at: null,
  };
}

function makeApiResponse<T>(data: T) {
  // Include meta to match ApiResponse<T> shape ({ data, meta: ApiMeta }).
  return Promise.resolve({ data, meta: {} });
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

// Degraded fan-out: same envelope, but meta names the butler pools that were
// dropped from the response (approvals `meta.sources_degraded`; bu-jad4j.4).
function makeDegradedResponse<T>(data: T, sourcesDegraded: string[]) {
  return Promise.resolve({ data, meta: { sources_degraded: sourcesDegraded } });
}

function makeEmptyHistory() {
  return makeApiResponse([]);
}

function makeEmptyPolicy() {
  return makeApiResponse({
    quiet_start_hour: null,
    quiet_end_hour: null,
    timezone: "UTC",
  });
}

// Minimal ApprovalMetrics fixture -- only callback_secret_configured varies
// across the module-banner tests; the rest are filler zeros/defaults so the
// shape matches the API contract.
function makeMetrics(
  callbackSecretConfigured: boolean | null,
  meta: Record<string, unknown> = {},
) {
  return makeApiResponse({
    total_pending: 0,
    total_approved_today: 0,
    total_rejected_today: 0,
    total_auto_approved_today: 0,
    total_expired_today: 0,
    avg_decision_latency_seconds: null,
    auto_approval_rate: 0,
    rejection_rate: 0,
    failure_count_today: 0,
    active_rules_count: 0,
    callback_secret_configured: callbackSecretConfigured,
  }).then((response) => ({ ...response, meta }));
}

function resetPageMocks() {
  vi.resetAllMocks();
  vi.mocked(getApprovalGatedTools).mockReturnValue(makeApiResponse([]) as AnyMock);
  vi.mocked(getApprovalRuleSuggestions).mockImplementation(
    ((actionId: string) =>
      makeApiResponse({
        action_id: actionId,
        tool_name: "send_email",
        tool_args: {},
        suggested_constraints: {},
      })) as AnyMock,
  );
  // Undetermined by default (null) -- individual tests override to assert
  // the false/true branches. null must render neither the degraded note nor
  // a false all-clear.
  vi.mocked(getApprovalMetrics).mockReturnValue(makeMetrics(null) as AnyMock);
  vi.mocked(getRulePromotionSuggestions).mockReturnValue(
    makeApiResponse({ pending: [], auto_applied: [] }) as AnyMock,
  );
  vi.mocked(getRulePromotionStats).mockReturnValue(
    makeApiResponse({
      suggestions_pending: 0,
      suggestions_confirmed: 0,
      suggestions_dismissed: 0,
      suggestions_superseded: 0,
      promoted_rules_active: 0,
      promoted_rule_matches: 0,
      llm_sessions_avoided_estimate: 0,
      demotion_pending: 0,
      promoted_rule_spot_checks: 0,
    }) as AnyMock,
  );
  vi.mocked(getUnroutableAttention).mockReturnValue(makeApiResponse([]) as AnyMock);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Drain pending macrotasks and microtasks so react-query can settle.
 * A single setTimeout(0) is not always enough in CI; repeat several times.
 */
async function flush(rounds = 5): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

function findButton(
  container: HTMLElement,
  label: string,
): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find(
    (btn) => btn.textContent?.trim() === label,
  );
}

/**
 * Repeatedly flush inside act() until `predicate` is satisfied or `max`
 * iterations elapse. Needed for nested react-query queries (e.g. the dossier
 * detail query fires only after the rail query resolves and auto-selects a row).
 */
async function flushUntil(predicate: () => boolean, max = 25): Promise<void> {
  for (let i = 0; i < max; i++) {
    if (predicate()) return;
    await act(async () => {
      await flush(1);
    });
  }
}

// ---------------------------------------------------------------------------
// Test harness
// ---------------------------------------------------------------------------

describe("ApprovalsPage — load-more", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    // Default stubs for side-sections; override in individual tests.
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeEmptyHistory() as AnyMock,
    );
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeApiResponse([]) as AnyMock,
    );
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  // -------------------------------------------------------------------------

  it("renders rail items returned by getApprovalsFlat", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([
        makeSummary("a1", "send_email"),
        makeSummary("a2", "delete_file"),
      ]) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(container.textContent).toContain("send email");
    expect(container.textContent).toContain("delete file");
  });

  it("shows 'Load more' button when response length equals the current limit", async () => {
    // Build 100 summaries (= PENDING_PAGE_SIZE) to simulate a full page.
    const full = Array.from({ length: 100 }, (_, i) => makeSummary(`id-${i}`));
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse(full) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(findButton(container, "Load more")).toBeDefined();
  });

  it("does NOT show 'Load more' when response is smaller than limit", async () => {
    // 3 results < 100 limit → no more pages.
    const partial = [makeSummary("a1"), makeSummary("a2"), makeSummary("a3")];
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse(partial) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(findButton(container, "Load more")).toBeUndefined();
  });

  it("shows empty state message when no pending approvals", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(container.textContent).toContain("No pending approvals");
    expect(findButton(container, "Load more")).toBeUndefined();
  });

  it("shows unroutable questions on the Command surface and retries once", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getUnroutableAttention).mockReturnValue(
      makeApiResponse([
        {
          id: "dead-letter-1",
          question: "Which butler owns this?",
          failure_reason: "No target acknowledged the route",
          created_at: "2026-09-13T01:00:00Z",
        },
      ]) as AnyMock,
    );
    vi.mocked(retryUnroutableAttention).mockReturnValue(
      makeApiResponse({
        dead_letter_id: "dead-letter-1",
        replayed_request_id: "replay-1",
        status: "queued",
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Retry") !== undefined);

    expect(container.textContent).toContain("Unroutable: Which butler owns this?");
    expect(container.textContent).toContain("No target acknowledged the route");
    expect(container.querySelector('.attention-row[data-tone="red"]')).not.toBeNull();
    await act(async () => {
      findButton(container, "Retry")?.click();
      await flush();
    });

    expect(retryUnroutableAttention).toHaveBeenCalledTimes(1);
    expect(vi.mocked(retryUnroutableAttention).mock.calls[0]?.[0]).toBe("dead-letter-1");
  });

  it("labels the shared policy and rejects an incomplete quiet-hour pair locally", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);

    renderPage();
    await flushUntil(() => findButton(container, "Edit") !== undefined);

    expect(container.textContent).toContain("Owner Attention Policy");
    expect(container.textContent).toContain(
      "Suppress routine owner attention during these hours",
    );

    await act(async () => {
      findButton(container, "Edit")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const start = container.querySelector<HTMLInputElement>(
      "#approvals-quiet-start-hour",
    );
    expect(start).not.toBeNull();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(start, "22");
      start?.dispatchEvent(new Event("input", { bubbles: true }));
      await flush();
    });

    await act(async () => {
      findButton(container, "Save")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(updateApprovalsPolicy).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      "Set both start and end hours, or leave both blank.",
    );
  });

  it("rejects an invalid IANA timezone locally", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);

    renderPage();
    await flushUntil(() => findButton(container, "Edit") !== undefined);
    await act(async () => {
      findButton(container, "Edit")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const setInput = async (selector: string, value: string) => {
      const input = container.querySelector<HTMLInputElement>(selector);
      expect(input).not.toBeNull();
      await act(async () => {
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          "value",
        )?.set;
        setter?.call(input, value);
        input?.dispatchEvent(new Event("input", { bubbles: true }));
        await flush();
      });
    };

    await setInput("#approvals-quiet-start-hour", "22");
    await setInput("#approvals-quiet-end-hour", "7");
    await setInput("#approvals-quiet-timezone", "Mars/Olympus");

    await act(async () => {
      findButton(container, "Save")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(updateApprovalsPolicy).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      "Enter a valid IANA timezone, such as Asia/Singapore or UTC.",
    );
  });

  it.each([
    ["a missing timezone", undefined],
    ["a blank timezone", "   "],
  ])("rejects %s locally before a browser-local fallback", async (_label, timezone) => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(
      makeApiResponse({
        quiet_start_hour: 22,
        quiet_end_hour: 7,
        timezone,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => container.textContent?.includes("22:00") === true);
    await act(async () => {
      findButton(container, "Edit")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    await act(async () => {
      findButton(container, "Save")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(updateApprovalsPolicy).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      "Enter a valid IANA timezone, such as Asia/Singapore or UTC.",
    );
  });

  it("never renders 'No pending approvals.' when the queue fetch fails (bu-86c4c.2 -- truth amnesty)", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      Promise.reject(new Error("queue unreachable")) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(container.textContent).not.toContain("No pending approvals.");
    expect(container.textContent).toContain("approvals queue");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    // A retry affordance must be present.
    expect(findButton(container, "Retry")).toBeDefined();
  });

  it("names the degraded pools instead of the calm empty state when the queue fan-out drops a pool (bu-jad4j.4)", async () => {
    // Degraded 200: the queue is empty because a butler pool dropped out of the
    // fan-out, not because nothing is waiting. The rail must name the pool, not
    // render "No pending approvals." as an all-clear.
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeDegradedResponse([], ["finance", "home"]) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    const note = container.querySelector('[data-testid="approvals-queue-degraded"]');
    expect(note).not.toBeNull();
    expect(note?.getAttribute("role")).toBe("alert");
    expect(note?.textContent).toContain("finance, home");
    // The calm empty state must NOT appear alongside the degraded note.
    expect(container.textContent).not.toContain("No pending approvals.");
  });

  it("renders the degraded note above the (incomplete) rows when a partial fan-out still returned rows (bu-jad4j.4)", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeDegradedResponse([makeSummary("a1", "send_email")], ["finance"]) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    // Both the note and the surviving row render.
    expect(container.querySelector('[data-testid="approvals-queue-degraded"]')).not.toBeNull();
    expect(container.textContent).toContain("send email");
  });

  it("keeps the honest empty state for a reachable-but-empty queue (mutation guard, bu-jad4j.4)", async () => {
    // Flag absent + zero rows: a legitimate all-clear. No degraded note.
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('[data-testid="approvals-queue-degraded"]')).toBeNull();
    expect(container.textContent).toContain("No pending approvals.");
  });

  it("re-calls getApprovalsFlat with bumped limit after clicking 'Load more'", async () => {
    // First call: full page of 100.
    const full = Array.from({ length: 100 }, (_, i) => makeSummary(`id-${i}`));
    // Second call (limit=200): still full → Load more persists.
    const larger = Array.from({ length: 200 }, (_, i) =>
      makeSummary(`id-${i}`),
    );

    vi.mocked(getApprovalsFlat)
      .mockReturnValueOnce(makeApiResponse(full) as AnyMock)
      .mockReturnValueOnce(makeApiResponse(larger) as AnyMock);

    renderPage();
    await act(async () => {
      await flush();
    });

    const btn = findButton(container, "Load more");
    expect(btn).toBeDefined();

    await act(async () => {
      btn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    // Verify that getApprovalsFlat was called with the bumped limit.
    expect(getApprovalsFlat).toHaveBeenCalledWith("waiting", 200);
  });

  it("keeps current-lane rows visible while same-lane pagination is loading", async () => {
    const firstPage = Array.from({ length: 100 }, (_, i) => makeSummary(`page-${i}`));
    const expandedPage = Array.from({ length: 200 }, (_, i) => makeSummary(`page-${i}`));
    const expandedRequest = deferred<{
      data: typeof expandedPage;
      meta: Record<string, never>;
    }>();
    vi.mocked(getApprovalsFlat).mockImplementation(
      ((_: "waiting" | "stalled", limit: number) =>
        limit === 100 ? makeApiResponse(firstPage) : expandedRequest.promise) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Load more") !== undefined);

    await act(async () => {
      findButton(container, "Load more")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush(1);
    });

    expect(
      container.querySelector('[data-testid="rail-item"][data-approval-id="page-0"]'),
    ).not.toBeNull();

    await act(async () => {
      expandedRequest.resolve({ data: expandedPage, meta: {} });
      await flush();
    });
    await flushUntil(
      () =>
        container.querySelector('[data-testid="rail-item"][data-approval-id="page-199"]') !==
        null,
    );
  });
});

// ---------------------------------------------------------------------------
// Failed-push indicator + callback-secret degraded banner (bu-p5sg6)
//
// PR #3567 (bu-mda0r) added push_outcome / push_failed / callback_secret_
// configured to the backend but never wired the frontend -- the owner could
// not SEE in the dashboard that a parked approval was never delivered. These
// tests cover: (1) a push_failed pending row renders a clear, distinct
// indicator; a healthy pending row does not, (2) the module-level degraded
// banner renders only on a genuine callback_secret_configured===false, never
// on true or the undetermined null.
// ---------------------------------------------------------------------------

describe("ApprovalsPage - failed-push indicator + callback-secret banner (bu-p5sg6)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("renders the failed-push indicator on a push_failed pending row", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([
        { ...makeSummary("a1", "send_email"), push_outcome: "failed", push_failed: true },
      ]) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    const badge = container.querySelector('[data-testid="rail-item-push-failed"]');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toContain("Owner not notified");
    expect(
      container.querySelector('[data-testid="rail-item"][data-push-failed="true"]'),
    ).not.toBeNull();
  });

  it("does NOT render the failed-push indicator on a healthy pending row", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([
        { ...makeSummary("a1", "send_email"), push_outcome: "delivered", push_failed: false },
      ]) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(container.querySelector('[data-testid="rail-item-push-failed"]')).toBeNull();
    expect(
      container.querySelector('[data-testid="rail-item"][data-push-failed]'),
    ).toBeNull();
  });

  it("renders the dossier push-failed alert when the selected approval's push failed", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([
        { ...makeSummary("a1", "send_email"), push_outcome: "failed", push_failed: true },
      ]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse({
        ...(await makePendingDetail("a1")).data,
        push_outcome: "failed",
        push_failed: true,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() =>
      container.querySelector('[data-testid="dossier-push-failed"]') !== null,
    );

    const alertEl = container.querySelector('[data-testid="dossier-push-failed"]');
    expect(alertEl).not.toBeNull();
    expect(alertEl?.textContent).toContain("never notified");
  });

  it("renders the callback-secret degraded banner when callback_secret_configured is false", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalMetrics).mockReturnValue(makeMetrics(false) as AnyMock);

    renderPage();
    await act(async () => {
      await flush();
    });

    const banner = container.querySelector(
      '[data-testid="approvals-callback-secret-degraded"]',
    );
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain("callback secret not configured");
  });

  it("does NOT render the callback-secret banner when callback_secret_configured is true", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalMetrics).mockReturnValue(makeMetrics(true) as AnyMock);

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(
      container.querySelector('[data-testid="approvals-callback-secret-degraded"]'),
    ).toBeNull();
  });

  it("does NOT render the callback-secret banner when callback_secret_configured is undetermined (null)", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalMetrics).mockReturnValue(makeMetrics(null) as AnyMock);

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(
      container.querySelector('[data-testid="approvals-callback-secret-degraded"]'),
    ).toBeNull();
  });

  it("names a partial pending-actions metric source instead of treating its zero as complete", async () => {
    vi.mocked(getApprovalMetrics).mockReturnValue(
      makeMetrics(null, {
        pending_actions_sources_degraded: ["home"],
        sources_degraded: ["home"],
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () =>
        container.querySelector(
          '[data-testid="approvals-pending-metrics-degraded"]',
        ) !== null,
    );

    const note = container.querySelector(
      '[data-testid="approvals-pending-metrics-degraded"]',
    );
    expect(note?.textContent).toContain("Pending approval metrics: home unavailable");
    expect(findButton(container, "Retry")).toBeDefined();
  });

  it("names a partial approval-rules metric source without hiding the healthy action family", async () => {
    vi.mocked(getApprovalMetrics).mockReturnValue(
      makeMetrics(null, {
        approval_rules_sources_degraded: ["home"],
        sources_degraded: ["home"],
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () =>
        container.querySelector(
          '[data-testid="approvals-rules-metrics-degraded"]',
        ) !== null,
    );

    const note = container.querySelector(
      '[data-testid="approvals-rules-metrics-degraded"]',
    );
    expect(note?.textContent).toContain("Active approval rules: home unavailable");
    expect(findButton(container, "Retry")).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Honest dispatch status + retry affordance (bu-j1xkd)
// ---------------------------------------------------------------------------

function makeHistoryItem(
  id: string,
  status: string,
  toolName = "send_email",
  executionResult: Record<string, unknown> | null | undefined = null,
) {
  return {
    id,
    butler: "general",
    tool_name: toolName,
    status,
    why: null,
    created_at: "2026-05-17T10:00:00Z",
    expires_at: null,
    execution_result: executionResult,
  };
}

function makePendingDetail(id: string) {
  return makeApiResponse({
    id,
    title: "Send Email (general)",
    butler: "general",
    created_at: "2026-05-17T10:00:00Z",
    expires_at: null,
    why: null,
    evidence: [],
    proposed_action: {
      tool_name: "send_email",
      tool_args: {},
      agent_summary: null,
    },
    status: "pending",
    decided_by: null,
    decided_at: null,
    target_contact: null,
  });
}

function makeStalledDetailData(
  id: string,
  overrides: Partial<Record<string, unknown>> = {},
) {
  return {
    id,
    title: `Stalled ${id}`,
    butler: "general",
    created_at: "2026-05-17T10:00:00Z",
    expires_at: null,
    why: "The owner approved this.",
    evidence: [],
    proposed_action: {
      tool_name: "send_email",
      tool_args: {},
      agent_summary: null,
    },
    status: "approved",
    decided_by: "human:owner",
    decided_at: "2026-05-17T10:05:00Z",
    execution_result: null,
    target_contact: null,
    ...overrides,
  };
}

describe("ApprovalsPage — honest dispatch status + retry (bu-j1xkd)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeEmptyHistory() as AnyMock,
    );
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeApiResponse([]) as AnyMock,
    );
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("toasts an un-run warning (not success) when approve does not dispatch", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("a1") as AnyMock,
    );
    // Backend approved but could not dispatch: status stays "approved", dispatched=false.
    vi.mocked(approveApproval).mockReturnValue(
      makeApiResponse({
        id: "a1",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "approved",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: false,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Approve") !== undefined);

    const approveBtn = findButton(container, "Approve");
    expect(approveBtn).toBeDefined();

    await act(async () => {
      approveBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(toast.warning).toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalledWith("Approved & dispatched");
  });

  it("toasts success when approve actually dispatches (executed)", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a2")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("a2") as AnyMock,
    );
    vi.mocked(approveApproval).mockReturnValue(
      makeApiResponse({
        id: "a2",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "executed",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: true,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Approve") !== undefined);

    const approveBtn = findButton(container, "Approve");
    await act(async () => {
      approveBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(toast.success).toHaveBeenCalledWith("Approved & dispatched");
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it("offers a second, explicit standing-rule confirmation after approval", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("teach-1")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("teach-1") as AnyMock,
    );
    vi.mocked(approveApproval).mockReturnValue(
      makeApiResponse({
        id: "teach-1",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "executed",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: true,
      }) as AnyMock,
    );
    vi.mocked(getApprovalRuleSuggestions).mockReturnValue(
      makeApiResponse({
        action_id: "teach-1",
        tool_name: "send_email",
        tool_args: { to: "alice@example.com" },
        suggested_constraints: {
          to: { type: "exact", value: "alice@example.com" },
        },
      }) as AnyMock,
    );
    vi.mocked(createApprovalRuleFromAction).mockReturnValue(
      makeApiResponse(makeRule("teach-rule")) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Approve") !== undefined);

    await act(async () => {
      findButton(container, "Approve")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    await flushUntil(
      () => container.textContent?.includes("Always allow this shape?") ?? false,
    );
    expect(getApprovalRuleSuggestions).toHaveBeenCalledWith("teach-1");
    expect(createApprovalRuleFromAction).not.toHaveBeenCalled();
    await flushUntil(
      () => findButton(container, "Always allow this shape") !== undefined,
    );
    expect(findButton(container, "Always allow this shape")).toBeDefined();

    await act(async () => {
      findButton(container, "Always allow this shape")?.click();
      await flush();
    });
    await flushUntil(
      () => findButton(container, "Create standing rule") !== undefined,
    );
    expect(findButton(container, "Create standing rule")).toBeDefined();

    await act(async () => {
      findButton(container, "Create standing rule")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(createApprovalRuleFromAction).toHaveBeenCalledWith({
      action_id: "teach-1",
    });
  });

  it("keeps asking without creating a standing rule", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("teach-keep")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("teach-keep") as AnyMock,
    );
    vi.mocked(approveApproval).mockReturnValue(
      makeApiResponse({
        id: "teach-keep",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "executed",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: true,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Approve") !== undefined);

    await act(async () => {
      findButton(container, "Approve")?.click();
      await flush();
    });
    await flushUntil(() => findButton(container, "Keep asking") !== undefined);

    await act(async () => {
      findButton(container, "Keep asking")?.click();
      await flush();
    });

    expect(createApprovalRuleFromAction).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("Always allow this shape?");
  });

  it("renders the typed decision dossier risk labels and evidence", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("dossier-1")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse({
        id: "dossier-1",
        title: "Send Email (general)",
        butler: "general",
        created_at: "2026-05-17T10:00:00Z",
        expires_at: null,
        why: "The recipient asked for the account update.",
        blast_radius: "contact",
        reversibility: "irreversible",
        evidence: [
          {
            type: "url",
            ref: "https://example.test/request/42",
            note: "Original request",
          },
        ],
        proposed_action: {
          tool_name: "send_email",
          tool_args: {},
          agent_summary: null,
        },
        status: "pending",
        decided_by: null,
        decided_at: null,
        target_contact: null,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="approval-dossier-risk"]') !== null,
    );

    const risk = container.querySelector('[data-testid="approval-dossier-risk"]');
    expect(risk?.textContent).toContain("contact");
    expect(risk?.textContent).toContain("irreversible");
    expect(container.querySelector('[data-testid="approval-evidence-item"]')?.textContent).toContain(
      "Original request",
    );
    expect(container.textContent).toContain("https://example.test/request/42");
  });

  it("renders Telegram decision provenance for a resolved approval", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("telegram-provenance")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse({
        id: "telegram-provenance",
        title: "Send Email (general)",
        butler: "general",
        created_at: "2026-05-17T10:00:00Z",
        expires_at: null,
        why: "The owner approved this from Telegram.",
        evidence: [],
        proposed_action: {
          tool_name: "send_email",
          tool_args: {},
          agent_summary: null,
        },
        status: "executed",
        decided_by: "human:owner@telegram",
        decided_at: "2026-05-17T10:05:00Z",
        target_contact: null,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () =>
        container.querySelector('[data-testid="approval-decision-provenance"]') !== null,
    );

    const provenance = container.querySelector(
      '[data-testid="approval-decision-provenance"]',
    );
    expect(provenance).not.toBeNull();
    expect(provenance?.textContent ?? "").toContain("Decision provenance");
    expect(provenance?.textContent ?? "").toContain("Decided by");
    expect(provenance?.textContent ?? "").toContain("human:owner@telegram");
    expect(provenance?.querySelector("time")?.dateTime).toBe("2026-05-17T10:05:00Z");
  });

  it("renders the redacted decision and execution outcome without losing provenance", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("terminal-outcome")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse({
        id: "terminal-outcome",
        title: "Send Email (general)",
        butler: "general",
        created_at: "2026-05-17T10:00:00Z",
        expires_at: null,
        why: "The previous execution failed.",
        evidence: [],
        proposed_action: {
          tool_name: "send_email",
          tool_args: {},
          agent_summary: null,
        },
        status: "executed",
        decided_by: "human:owner@telegram",
        decided_at: "2026-05-17T10:05:00Z",
        denial_reason: "The owner chose a different response.",
        execution_result: {
          success: false,
          error: "***REDACTED***",
          result: { retryable: false },
        },
        target_contact: null,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="approval-decision-outcome"]') !== null,
    );

    const outcome = container.querySelector('[data-testid="approval-decision-outcome"]');
    expect(outcome).not.toBeNull();
    const outcomeText = outcome?.textContent ?? "";
    expect(outcomeText).toContain("Decision outcome");
    expect(outcomeText).toContain("Denial reason");
    expect(outcomeText).toContain("The owner chose a different response.");
    expect(outcomeText).toContain("Execution outcome");
    expect(outcomeText).toContain("***REDACTED***");
    expect(outcomeText).not.toContain("postgres://operator:top-secret");
    expect(container.querySelector('[data-testid="approval-decision-provenance"]')).not.toBeNull();
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
  });

  it("renders dossier Retry only while an approved action has no execution result", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("retry-eligible")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse({
        id: "retry-eligible",
        title: "Send Email (general)",
        butler: "general",
        created_at: "2026-05-17T10:00:00Z",
        expires_at: null,
        why: "The owner approved this.",
        evidence: [],
        proposed_action: {
          tool_name: "send_email",
          tool_args: {},
          agent_summary: null,
        },
        status: "approved",
        decided_by: "human:owner",
        decided_at: "2026-05-17T10:05:00Z",
        denial_reason: null,
        execution_result: null,
        target_contact: null,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);
    expect(findButton(container, "Retry dispatch")).toBeDefined();
    expect(findButton(container, "Abandon")).toBeDefined();
  });

  it("requires a reason before dashboard abandonment and sends it on confirm", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("abandon-eligible")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse({
        id: "abandon-eligible",
        title: "Send Email (general)",
        butler: "general",
        created_at: "2026-05-17T10:00:00Z",
        expires_at: null,
        why: "The owner approved this.",
        evidence: [],
        proposed_action: { tool_name: "send_email", tool_args: {}, agent_summary: null },
        status: "approved",
        decided_by: "human:owner",
        decided_at: "2026-05-17T10:05:00Z",
        denial_reason: null,
        execution_result: null,
        target_contact: null,
      }) as AnyMock,
    );
    vi.mocked(abandonApproval).mockReturnValue(
      makeApiResponse({ id: "abandon-eligible", status: "abandoned" }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Abandon") !== undefined);
    await act(async () => {
      findButton(container, "Abandon")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const reason = container.querySelector<HTMLInputElement>('input[aria-label="Abandon reason"]');
    const confirm = findButton(container, "Confirm");
    expect(reason).not.toBeNull();
    expect(confirm?.disabled).toBe(true);
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(reason, "No longer needed");
      reason?.dispatchEvent(new Event("input", { bubbles: true }));
      await flush();
    });
    await act(async () => {
      findButton(container, "Confirm")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(abandonApproval).toHaveBeenCalledWith("abandon-eligible", {
      reason: "No longer needed",
    });
  });

  it("does not render dossier Retry after an approved action records execution", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("execution-recorded")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse({
        id: "execution-recorded",
        title: "Send Email (general)",
        butler: "general",
        created_at: "2026-05-17T10:00:00Z",
        expires_at: null,
        why: "The owner approved this.",
        evidence: [],
        proposed_action: {
          tool_name: "send_email",
          tool_args: {},
          agent_summary: null,
        },
        status: "approved",
        decided_by: "human:owner",
        decided_at: "2026-05-17T10:05:00Z",
        denial_reason: null,
        execution_result: { success: false, error: "***REDACTED***" },
        target_contact: null,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="approval-decision-outcome"]') !== null,
    );
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
    expect(findButton(container, "Abandon")).toBeUndefined();
  });

  it("does not render dossier Retry when an approved action omits execution_result", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("missing-dossier-result")]) as AnyMock,
    );
    const detailWithoutResult = {
      id: "missing-dossier-result",
      title: "Send Email (general)",
      butler: "general",
      created_at: "2026-05-17T10:00:00Z",
      expires_at: null,
      why: "The owner approved this.",
      evidence: [],
      proposed_action: {
        tool_name: "send_email",
        tool_args: {},
        agent_summary: null,
      },
      status: "approved",
      decided_by: "human:owner",
      decided_at: "2026-05-17T10:05:00Z",
      denial_reason: null,
      target_contact: null,
    };
    vi.mocked(getApprovalDetail).mockReturnValue(
      makeApiResponse(detailWithoutResult) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="approval-decision-outcome"]') !== null,
    );

    expect(findButton(container, "Retry dispatch")).toBeUndefined();
    expect(findButton(container, "Abandon")).toBeUndefined();
  });

  it("denies in a single click — no 'Confirm Deny' step (optimistic)", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("d1")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("d1") as AnyMock,
    );
    vi.mocked(denyApproval).mockReturnValue(
      makeApiResponse({
        id: "d1",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "denied",
        requested_at: "2026-05-17T10:00:00Z",
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Deny") !== undefined);

    // The deny button is a direct action — there is no two-step confirm panel.
    expect(findButton(container, "Confirm Deny")).toBeUndefined();

    const denyBtn = findButton(container, "Deny");
    await act(async () => {
      denyBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    // Denies with no reason payload when the optional field is left blank.
    expect(denyApproval).toHaveBeenCalledWith("d1", undefined);
    expect(toast.success).toHaveBeenCalledWith("Denied");
  });

  it("passes the optional inline reason to deny when provided", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("d2")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("d2") as AnyMock,
    );
    vi.mocked(denyApproval).mockReturnValue(
      makeApiResponse({
        id: "d2",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "denied",
        requested_at: "2026-05-17T10:00:00Z",
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Deny") !== undefined);

    const reasonInput = container.querySelector<HTMLInputElement>(
      'input[placeholder="Deny reason (optional)"]',
    );
    expect(reasonInput).not.toBeNull();
    await act(async () => {
      // Set value via the native setter so React's onChange fires.
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(reasonInput, "spammy");
      reasonInput?.dispatchEvent(new Event("input", { bubbles: true }));
      await flush();
    });

    const denyBtn = findButton(container, "Deny");
    await act(async () => {
      denyBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(denyApproval).toHaveBeenCalledWith("d2", { reason: "spammy" });
  });

  it("renders a 'Retry dispatch' affordance for approved-but-un-run history rows", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([
        makeHistoryItem("h-approved", "approved"),
        makeHistoryItem("h-executed", "executed"),
      ]) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => findButton(container, "Retry dispatch") !== undefined,
    );

    // Exactly one retry button — only the approved (un-run) row gets it.
    const retryButtons = Array.from(
      container.querySelectorAll("button"),
    ).filter((b) => b.textContent?.trim() === "Retry dispatch");
    expect(retryButtons.length).toBe(1);
  });

  it("does not render Retry dispatch for an approved history row with a persisted result", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([
        makeHistoryItem("h-completed", "approved", "send_email", { success: false }),
      ]) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="history-row-link"]') !== null,
    );

    expect(findButton(container, "Retry dispatch")).toBeUndefined();
  });

  it("does not render Retry dispatch when an approved history row omits execution_result", async () => {
    const rowWithoutResult = makeHistoryItem(
      "h-missing-result",
      "approved",
    );
    Reflect.deleteProperty(rowWithoutResult, "execution_result");
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([rowWithoutResult]) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="history-row-link"]') !== null,
    );

    expect(findButton(container, "Retry dispatch")).toBeUndefined();
  });

  it("calls retryApproval and toasts success when retry dispatches", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([makeHistoryItem("h-approved", "approved")]) as AnyMock,
    );
    vi.mocked(retryApproval).mockReturnValue(
      makeApiResponse({
        id: "h-approved",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "executed",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: true,
      }) as AnyMock,
    );

    const invalidateQueries = vi.spyOn(qc, "invalidateQueries");
    renderPage();
    await flushUntil(
      () => findButton(container, "Retry dispatch") !== undefined,
    );

    const retryBtn = findButton(container, "Retry dispatch");
    expect(retryBtn).toBeDefined();

    await act(async () => {
      retryBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(retryApproval).toHaveBeenCalledWith("h-approved");
    expect(toast.success).toHaveBeenCalledWith("Dispatched");
    expect(invalidateQueries.mock.calls.map(([filters]) => filters?.queryKey)).toEqual(
      expect.arrayContaining([
        ["approvals", "flat"],
        ["approvals", "history"],
        ["approvals", "detail", "h-approved"],
        ["approvals", "metrics"],
      ]),
    );
  });

  it("keeps a retrying row visible and defers invalidation until Retry settles", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([makeHistoryItem("h-retrying", "approved")]) as AnyMock,
    );
    const retry = deferred<{
      data: {
        id: string;
        butler: string;
        tool_name: string;
        tool_args: Record<string, never>;
        status: string;
        requested_at: string;
        dispatched: boolean;
      };
      meta: Record<string, never>;
    }>();
    vi.mocked(retryApproval).mockReturnValue(retry.promise as AnyMock);
    const invalidateQueries = vi.spyOn(qc, "invalidateQueries");

    renderPage();
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    await act(async () => {
      findButton(container, "Retry dispatch")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush(1);
    });

    const retryingButton = findButton(container, "retrying…");
    expect(retryingButton).toBeDefined();
    expect(retryingButton?.disabled).toBe(true);
    expect(retryingButton?.getAttribute("aria-busy")).toBe("true");
    expect(container.querySelector('[data-testid="history-row-link"]')).not.toBeNull();
    expect(invalidateQueries).not.toHaveBeenCalled();

    await act(async () => {
      retry.resolve({
        data: {
          id: "h-retrying",
          butler: "general",
          tool_name: "send_email",
          tool_args: {},
          status: "executed",
          requested_at: "2026-05-17T10:00:00Z",
          dispatched: true,
        },
        meta: {},
      });
      await flush();
    });

    expect(toast.success).toHaveBeenCalledWith("Dispatched");
    expect(invalidateQueries.mock.calls.map(([filters]) => filters?.queryKey)).toEqual(
      expect.arrayContaining([
        ["approvals", "flat"],
        ["approvals", "history"],
        ["approvals", "detail", "h-retrying"],
        ["approvals", "metrics"],
      ]),
    );
  });

  it("reports a completed non-dispatch retry outcome without inventing a cause", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([makeHistoryItem("h-still-stalled", "approved")]) as AnyMock,
    );
    vi.mocked(retryApproval).mockReturnValue(
      makeApiResponse({
        id: "h-still-stalled",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "approved",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: false,
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    await act(async () => {
      findButton(container, "Retry dispatch")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(toast.warning).toHaveBeenCalledWith("Still not run");
    expect(toast.warning).not.toHaveBeenCalledWith("Still not run: no reachable butler");
  });

  it("keeps the retryable row and approval reads untouched when retry fails", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([makeHistoryItem("h-retry-error", "approved")]) as AnyMock,
    );
    const retryFailure = Promise.reject(
      new Error("executor rejected: tool arguments were invalid"),
    );
    // The mutation consumes this rejection; attach a test-side handler too so
    // Vitest never reports it as unhandled before React schedules onError.
    void retryFailure.catch(() => undefined);
    vi.mocked(retryApproval).mockReturnValue(retryFailure as AnyMock);
    const invalidateQueries = vi.spyOn(qc, "invalidateQueries");

    renderPage();
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    await act(async () => {
      findButton(container, "Retry dispatch")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(container.querySelector('[data-testid="history-row-link"]')).not.toBeNull();
    expect(toast.error).toHaveBeenCalledWith(
      "Retry failed: executor rejected: tool arguments were invalid",
    );
    expect(invalidateQueries).not.toHaveBeenCalled();
  });

  it("renders resolved entity names in a 'Referenced Entities' block (bu-4ni21)", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a3")]) as AnyMock,
    );
    const detail = makeApiResponse({
      id: "a3",
      title: "Relationship Assert Fact (relationship)",
      butler: "relationship",
      created_at: "2026-05-17T10:00:00Z",
      expires_at: null,
      why: null,
      evidence: [],
      proposed_action: {
        tool_name: "relationship_assert_fact",
        tool_args: {
          subject: "c64f5aed-9b1f-492e-bab2-86c986c31ebd",
          predicate: "works-at",
          object: "9510c225-4764-4ef5-8a0f-3d62be654b28",
        },
        agent_summary: null,
      },
      status: "pending",
      decided_by: null,
      decided_at: null,
      target_contact: null,
      referenced_entities: [
        {
          id: "c64f5aed-9b1f-492e-bab2-86c986c31ebd",
          name: "Tze How Lee",
          entity_type: "person",
          roles: ["owner"],
        },
        {
          id: "9510c225-4764-4ef5-8a0f-3d62be654b28",
          name: "Qube Research & Technologies",
          entity_type: "organization",
          roles: [],
        },
      ],
    });
    vi.mocked(getApprovalDetail).mockReturnValue(detail as AnyMock);

    renderPage();
    await flushUntil(
      () => container.textContent?.includes("Referenced Entities") ?? false,
    );

    expect(container.textContent).toContain("Referenced Entities");
    expect(container.textContent).toContain("Qube Research & Technologies");
    expect(container.textContent).toContain("Tze How Lee");
    // Object UUID is no longer presented bare — the name resolves it.
    expect(container.textContent).toContain("organization");
  });

  it("renders a subject-predicate-object digest, mapping by id not array order", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a4")]) as AnyMock,
    );
    // referenced_entities are deliberately in OBJECT-first order (as the live
    // resolver returns them) to prove the digest keys off the tool_args UUIDs,
    // not the array position.
    const detail = makeApiResponse({
      id: "a4",
      title: "Relationship Assert Fact (relationship)",
      butler: "relationship",
      created_at: "2026-05-17T10:00:00Z",
      expires_at: null,
      why: null,
      evidence: [],
      proposed_action: {
        tool_name: "relationship_assert_fact",
        tool_args: {
          subject: "c64f5aed-9b1f-492e-bab2-86c986c31ebd",
          predicate: "knows",
          object: "2b4e034d-4138-4eef-a011-20eed5bedcab",
        },
        agent_summary: null,
      },
      status: "pending",
      decided_by: null,
      decided_at: null,
      target_contact: null,
      referenced_entities: [
        {
          id: "2b4e034d-4138-4eef-a011-20eed5bedcab",
          name: "Yustynn Panicker",
          entity_type: "person",
          roles: [],
        },
        {
          id: "c64f5aed-9b1f-492e-bab2-86c986c31ebd",
          name: "Tze How Lee",
          entity_type: "person",
          roles: ["owner"],
        },
      ],
    });
    vi.mocked(getApprovalDetail).mockReturnValue(detail as AnyMock);

    renderPage();
    await flushUntil(
      () => container.textContent?.includes("Approve:") ?? false,
    );

    // Subject (Tze) precedes object (Yustynn), regardless of array order.
    expect(container.textContent).toContain(
      "Approve: Tze How Lee knows Yustynn Panicker",
    );
  });
});

// ---------------------------------------------------------------------------
// Routing: every approval has a URL (bu-86c4c.12 — One Trust Console)
// ---------------------------------------------------------------------------

describe("ApprovalsPage — /approvals/:id routing (bu-86c4c.12)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeApiResponse([]) as AnyMock,
    );
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderAt(initialPath: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <QueryClientProvider client={qc}>
            <Routes>
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/approvals/:id" element={<ApprovalsPage />} />
            </Routes>
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("selects the approval named in the URL, not the first-arrived item", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1"), makeSummary("a2")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    renderAt("/approvals/a2");
    await flushUntil(() => vi.mocked(getApprovalDetail).mock.calls.length > 0);

    expect(getApprovalDetail).toHaveBeenCalledWith("a2");
    expect(getApprovalDetail).not.toHaveBeenCalledWith("a1");
  });

  it("keeps the direct-link workspace from collapsing behind policy/history", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1"), makeSummary("a2")]) as AnyMock,
    );

    renderAt("/approvals/a2");
    await flushUntil(() => vi.mocked(getApprovalDetail).mock.calls.length > 0);

    const workspace = container.querySelector(
      '[data-testid="approvals-workspace"]',
    );
    expect(workspace).not.toBeNull();
    expect(workspace?.className).toContain("min-h-[32rem]");
    expect(workspace?.className).toContain("flex-col");
    expect(workspace?.className).toContain("md:flex-row");
  });

  it("clicking a rail item navigates to that approval's URL and fetches its dossier", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1"), makeSummary("a2")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    renderAt("/approvals");
    await flushUntil(
      () => container.querySelectorAll('[data-testid="rail-item"]').length === 2,
    );

    const items = container.querySelectorAll<HTMLButtonElement>(
      '[data-testid="rail-item"]',
    );
    const secondId = items[1].getAttribute("data-approval-id");
    await act(async () => {
      items[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(getApprovalDetail).toHaveBeenCalledWith(secondId);
  });

  it("history rows link into the read-only dossier at /approvals/:id", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([makeHistoryItem("h1", "executed")]) as AnyMock,
    );

    renderAt("/approvals");
    await flushUntil(
      () => container.querySelector('[data-testid="history-row-link"]') !== null,
    );

    const link = container.querySelector<HTMLAnchorElement>(
      '[data-testid="history-row-link"]',
    );
    expect(link?.getAttribute("href")).toBe("/approvals/h1");
  });
});

// ---------------------------------------------------------------------------
// Queue ranking: expiry urgency + blast radius, not arrival order (bu-86c4c.12)
// ---------------------------------------------------------------------------

describe("ApprovalsPage — expiry + blast-radius ranking (bu-86c4c.12)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeApiResponse([]) as AnyMock,
    );
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("ranks an about-to-expire item ahead of an item that arrived first with no expiry", async () => {
    const soon = {
      ...makeSummary("late-arrival", "delete_file"),
      expires_at: new Date(Date.now() + 5 * 60_000).toISOString(), // 5 min left
    };
    const noExpiry = makeSummary("first-arrival", "assert_fact");
    // API returns arrival order: noExpiry first, soon second.
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([noExpiry, soon]) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelectorAll('[data-testid="rail-item"]').length === 2,
    );

    const items = container.querySelectorAll<HTMLButtonElement>(
      '[data-testid="rail-item"]',
    );
    // The expiring item ranks first despite arriving second.
    expect(items[0].getAttribute("data-approval-id")).toBe("late-arrival");
    expect(items[1].getAttribute("data-approval-id")).toBe("first-arrival");
  });

  it("does not let already-expired pending rows outrank active outbound approvals", async () => {
    const expired = {
      ...makeSummary("stale-expired", "relationship_assert_fact"),
      expires_at: new Date(Date.now() - 60 * 60_000).toISOString(),
    };
    const activeOutbound = {
      ...makeSummary("active-email", "email_reply_to_thread"),
      expires_at: new Date(Date.now() + 36 * 60 * 60_000).toISOString(),
    };

    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([expired, activeOutbound]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("active-email") as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelectorAll('[data-testid="rail-item"]').length === 2,
    );

    const items = container.querySelectorAll<HTMLButtonElement>(
      '[data-testid="rail-item"]',
    );
    expect(items[0].getAttribute("data-approval-id")).toBe("active-email");
    expect(items[1].getAttribute("data-approval-id")).toBe("stale-expired");
  });

  it("shows a warning-colored countdown chip for an item expiring within the hour", async () => {
    const soon = {
      ...makeSummary("expiring-soon", "notify"),
      expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
    };
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([soon]) as AnyMock);

    renderPage();
    await flushUntil(() => container.textContent?.includes("expires in") ?? false);

    expect(container.textContent).toMatch(/expires in \d+m/);
  });
});

// ---------------------------------------------------------------------------
// Approved-but-never-dispatched renders amber, never success-green (bu-86c4c.12)
// ---------------------------------------------------------------------------

describe("ApprovalsPage — stalled (approved-but-undispatched) state (bu-86c4c.12)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeApiResponse([]) as AnyMock,
    );
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("feeds the verdict the flat response's whole-population stalled radar", async () => {
    // History is deliberately empty: the count must not depend on the
    // bounded history query, which cannot represent every stalled row.
    vi.mocked(getApprovalsFlat).mockReturnValue(
      Promise.resolve({ data: [], meta: { stalled_count: 2 } }) as AnyMock,
    );
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);

    renderPage();
    await flushUntil(() => container.textContent?.includes("2 stalled actions never ran") ?? false);

    expect(container.textContent).toContain("2 stalled actions never ran");
    expect(container.textContent).not.toContain("No approvals waiting.");
  });

  it("renders an 'approved' history row as 'stalled', never as green success text", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([
        makeHistoryItem("h-approved", "approved"),
        makeHistoryItem("h-executed", "executed"),
      ]) as AnyMock,
    );

    renderPage();
    await flushUntil(() => container.textContent?.includes("stalled") ?? false);

    expect(container.textContent).toContain("stalled");
    expect(container.textContent).not.toContain("approved");

    const stalledLabel = Array.from(container.querySelectorAll("span")).find(
      (el) => el.textContent?.trim() === "stalled",
    );
    expect(stalledLabel?.className).not.toMatch(/text-green/);
  });
});

// ---------------------------------------------------------------------------
// URL-backed stalled lane (bu-kqnum.10.3)
// ---------------------------------------------------------------------------

describe("ApprovalsPage — URL-backed stalled lane", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    navigateCalls.length = 0;
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) =>
        makeApiResponse({
          id,
          title: "Send Email (general)",
          butler: "general",
          created_at: "2026-05-17T10:00:00Z",
          expires_at: null,
          why: "The owner approved this.",
          evidence: [],
          proposed_action: { tool_name: "send_email", tool_args: {}, agent_summary: null },
          status: "approved",
          decided_by: "human:owner",
          decided_at: "2026-05-17T10:05:00Z",
          execution_result: null,
          target_contact: null,
        })) as AnyMock,
    );
    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage(initialEntry: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialEntry]}>
          <QueryClientProvider client={qc}>
            <Routes>
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/approvals/:id" element={<ApprovalsPage />} />
            </Routes>
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("opens a direct stalled URL as a semantic stalled lane and preserves it for dossier selection", async () => {
    const stalled = {
      ...makeSummary("stalled-1", "send_email"),
      status: "approved",
      execution_result: null,
    };
    vi.mocked(getApprovalsFlat).mockImplementation(
      ((state: "waiting" | "stalled") =>
        makeApiResponse(state === "stalled" ? [stalled] : [makeSummary("waiting-1")])) as AnyMock,
    );

    renderPage("/approvals?state=stalled");
    await flushUntil(() => container.querySelector('[data-testid="rail-item"]') !== null);

    expect(getApprovalsFlat).toHaveBeenCalledWith("stalled", 100);
    expect(container.textContent).toContain("Stalled approvals");
    expect(container.textContent).not.toContain("Approve selected");

    const laneNav = container.querySelector('nav[aria-label="Approval lanes"]');
    const stalledLink = laneNav?.querySelector<HTMLAnchorElement>(
      'a[href="/approvals?state=stalled"]',
    );
    const waitingLink = laneNav?.querySelector<HTMLAnchorElement>('a[href="/approvals"]');
    expect(laneNav).not.toBeNull();
    expect(stalledLink?.getAttribute("aria-current")).toBe("page");
    expect(stalledLink?.className).toContain("focus-visible:outline");
    expect(stalledLink?.className.split(" ")).toEqual(
      expect.arrayContaining([
        "rounded-[3px]",
        "border",
        "px-2.5",
        "py-1",
        "font-mono",
        "text-[11px]",
        "bg-foreground",
        "text-background",
      ]),
    );
    expect(waitingLink?.className.split(" ")).toEqual(
      expect.arrayContaining([
        "rounded-[3px]",
        "border",
        "px-2.5",
        "py-1",
        "font-mono",
        "text-[11px]",
        "bg-transparent",
      ]),
    );

    navigateCalls.length = 0;
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="rail-item"]')?.click();
      await flush();
    });
    expect(navigateCalls).toContainEqual([
      "/approvals/stalled-1?state=stalled",
      { replace: true },
    ]);
  });

  it("opens a valid 101st stalled direct dossier after the bounded rail settles", async () => {
    const outOfWindowId = "stalled-101";
    const firstHundred = Array.from({ length: 100 }, (_, index) => ({
      ...makeSummary(`stalled-${index + 1}`, "stalled_action"),
      status: "approved",
      execution_result: null,
    }));
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse(firstHundred) as AnyMock);

    renderPage(`/approvals/${outOfWindowId}?state=stalled`);
    await flushUntil(
      () =>
        vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === outOfWindowId),
    );
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    expect(getApprovalsFlat).toHaveBeenCalledWith("stalled", 100);
    expect(getApprovalDetail).toHaveBeenCalledWith(outOfWindowId);
    expect(container.textContent).toContain("Approved, awaiting dispatch.");
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();
    // The Stalled lane exposes only its established Retry action. Abandon is
    // deliberately a waiting/history workflow, not a second stalled action.
    expect(findButton(container, "Abandon")).toBeUndefined();
  });

  it("does not expose cached pending decision controls while a listed Stalled dossier refetches", async () => {
    const actionId = "listed-stalled-cache-pending";
    const listedStalled = {
      ...makeSummary(actionId, "stalled_action"),
      status: "approved",
      execution_result: null,
    };
    const detailRefresh = deferred<{
      data: ReturnType<typeof makeStalledDetailData>;
      meta: Record<string, never>;
    }>();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([listedStalled]) as AnyMock);
    // An ordinary dossier prefetch can retain a pending payload while React
    // Query starts its required fresh detail read. The Stalled lane must never
    // reuse its decision controls during that window.
    qc.setQueryDefaults(["approvals", "detail", actionId], { gcTime: Infinity });
    qc.setQueryData(["approvals", "detail", actionId], {
      data: makeStalledDetailData(actionId, {
        title: "Cached pending ordinary dossier",
        status: "pending",
      }),
      meta: {},
    });
    vi.mocked(getApprovalDetail).mockReturnValue(detailRefresh.promise as AnyMock);

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(
      () => vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === actionId),
    );

    expect(container.textContent).toContain("Cached pending ordinary dossier");
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();
    expect(findButton(container, "Retry dispatch")).toBeUndefined();

    await act(async () => {
      detailRefresh.resolve({ data: makeStalledDetailData(actionId), meta: {} });
      await flush();
    });

    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);
    expect(findButton(container, "Retry dispatch")).toBeDefined();
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();
  });

  it("revalidates a direct 101st stalled dossier after successful Retry before retaining Retry", async () => {
    const actionId = "stalled-101-retry";
    const firstHundred = Array.from({ length: 100 }, (_, index) => ({
      ...makeSummary(`stalled-${index + 1}`, "stalled_action"),
      status: "approved",
      execution_result: null,
    }));
    const flatRefresh = deferred<{
      data: typeof firstHundred;
      meta: Record<string, never>;
    }>();
    const verifierRefresh = deferred<{
      data: ReturnType<typeof makeStalledDetailData>;
      meta: Record<string, never>;
    }>();
    let detailCalls = 0;
    vi.mocked(getApprovalsFlat)
      .mockReturnValueOnce(makeApiResponse(firstHundred) as AnyMock)
      .mockReturnValueOnce(flatRefresh.promise as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => {
        detailCalls += 1;
        if (detailCalls === 1) {
          return makeApiResponse(makeStalledDetailData(id)) as AnyMock;
        }
        if (detailCalls === 2) {
          return verifierRefresh.promise as AnyMock;
        }
        return makeApiResponse(
          makeStalledDetailData(id, { execution_result: { dispatched: true } }),
        ) as AnyMock;
      }) as AnyMock,
    );
    vi.mocked(retryApproval).mockReturnValue(
      makeApiResponse({
        id: actionId,
        butler: "general",
        tool_name: "stalled_action",
        tool_args: {},
        status: "executed",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: true,
      }) as AnyMock,
    );
    const invalidateQueries = vi.spyOn(qc, "invalidateQueries");

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    await act(async () => {
      findButton(container, "Retry dispatch")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    // Keep the stale flat response pending. The verifier must independently
    // refetch now, rather than letting its old approved/null payload retain
    // Retry until the rail happens to settle again.
    await flushUntil(() => detailCalls >= 2);
    expect(invalidateQueries.mock.calls.map(([filters]) => filters?.queryKey)).toEqual(
      expect.arrayContaining([["approvals", "stalled-route-verification", actionId]]),
    );
    expect(findButton(container, "Retry dispatch")).toBeUndefined();

    await act(async () => {
      verifierRefresh.resolve({
        data: makeStalledDetailData(actionId, { execution_result: { dispatched: true } }),
        meta: {},
      });
      await flush();
    });

    expect(findButton(container, "Retry dispatch")).toBeUndefined();

    await act(async () => {
      flatRefresh.resolve({ data: firstHundred, meta: {} });
      await flush();
    });
  });

  it("clears a 101st stalled Retry during an approval event's deferred flat refresh", async () => {
    const actionId = "stalled-101-fleet-event";
    const firstHundred = Array.from({ length: 100 }, (_, index) => ({
      ...makeSummary(`stalled-${index + 1}`, "stalled_action"),
      status: "approved",
      execution_result: null,
    }));
    const flatRefresh = deferred<{
      data: typeof firstHundred;
      meta: Record<string, never>;
    }>();
    const verifierRefresh = deferred<{
      data: ReturnType<typeof makeStalledDetailData>;
      meta: Record<string, never>;
    }>();
    let detailCalls = 0;
    vi.mocked(getApprovalsFlat)
      .mockReturnValueOnce(makeApiResponse(firstHundred) as AnyMock)
      .mockReturnValueOnce(flatRefresh.promise as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => {
        detailCalls += 1;
        if (detailCalls === 1) {
          return makeApiResponse(makeStalledDetailData(id)) as AnyMock;
        }
        if (detailCalls === 2) {
          return verifierRefresh.promise as AnyMock;
        }
        return makeApiResponse(
          makeStalledDetailData(id, { execution_result: { dispatched: true } }),
        ) as AnyMock;
      }) as AnyMock,
    );

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    await act(async () => {
      applyFleetEvent(qc, {
        type: "approval",
        ts: 101,
        data: { kind: "executed", approval_id: actionId },
      });
      await flush();
    });

    // The flat rail is intentionally held stale. The fleet event must still
    // revalidate the direct verifier so its previous approved/null payload
    // cannot retain Retry in the event-to-flat window.
    await flushUntil(() => detailCalls >= 2);
    expect(detailCalls).toBe(2);
    expect(findButton(container, "Retry dispatch")).toBeUndefined();

    await act(async () => {
      verifierRefresh.resolve({
        data: makeStalledDetailData(actionId, { execution_result: { dispatched: true } }),
        meta: {},
      });
      flatRefresh.resolve({ data: firstHundred, meta: {} });
      await flush();
    });

    expect(findButton(container, "Retry dispatch")).toBeUndefined();
  });

  it("does not trust an ordinary cached dossier while the unlisted Stalled verifier is pending", async () => {
    const actionId = "stalled-cache-pending";
    const freshPending = {
      data: makeStalledDetailData(actionId, {
        title: "Fresh pending detail",
        status: "pending",
      }),
      meta: {},
    };
    const verification = deferred<typeof freshPending>();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    // Simulate a prefetch from the ordinary dossier route. The Stalled route
    // must not treat this stale success as its eligibility verification.
    qc.setQueryData(["approvals", "detail", actionId], {
      data: makeStalledDetailData(actionId, { title: "Cached stale dossier" }),
      meta: {},
    });
    vi.mocked(getApprovalDetail).mockReturnValue(verification.promise as AnyMock);

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(
      () => vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === actionId),
    );

    // A separate fresh request begins despite the ordinary cache entry, and
    // neither its stale title nor its Retry control becomes visible first.
    expect(getApprovalDetail).toHaveBeenCalledWith(actionId);
    expect(container.textContent).not.toContain("Cached stale dossier");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();

    await act(async () => {
      verification.resolve(freshPending);
      await flush();
    });

    // A fresh but no-longer-stalled result remains suppressed; the ordinary
    // cached approval cannot reopen its dossier after the verifier settles.
    expect(container.textContent).not.toContain("Cached stale dossier");
    expect(container.textContent).not.toContain("Fresh pending detail");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
    expect(findButton(container, "Approve")).toBeUndefined();
  });

  it("suppresses an unlisted stalled dossier when fresh verification has a result", async () => {
    const actionId = "stalled-cache-result";
    const freshExecuted = {
      data: makeStalledDetailData(actionId, {
        title: "Fresh completed detail",
        execution_result: { dispatched: false },
      }),
      meta: {},
    };
    const verification = deferred<typeof freshExecuted>();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    qc.setQueryData(["approvals", "detail", actionId], {
      data: makeStalledDetailData(actionId, { title: "Cached stale retry" }),
      meta: {},
    });
    vi.mocked(getApprovalDetail).mockReturnValue(verification.promise as AnyMock);

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(
      () => vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === actionId),
    );
    expect(findButton(container, "Retry dispatch")).toBeUndefined();

    await act(async () => {
      verification.resolve(freshExecuted);
      await flush();
    });

    expect(container.textContent).not.toContain("Cached stale retry");
    expect(container.textContent).not.toContain("Fresh completed detail");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
  });

  it("suppresses an unlisted stalled dossier when fresh verification omits execution_result", async () => {
    const actionId = "stalled-cache-missing-result";
    const freshMissingResult = {
      data: makeStalledDetailData(actionId, { title: "Fresh missing result" }),
      meta: {},
    };
    Reflect.deleteProperty(freshMissingResult.data, "execution_result");
    const verification = deferred<typeof freshMissingResult>();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    qc.setQueryData(["approvals", "detail", actionId], {
      data: makeStalledDetailData(actionId, { title: "Cached stale retry" }),
      meta: {},
    });
    vi.mocked(getApprovalDetail).mockReturnValue(verification.promise as AnyMock);

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(
      () => vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === actionId),
    );
    expect(container.textContent).not.toContain("Cached stale retry");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();

    await act(async () => {
      verification.resolve(freshMissingResult);
      await flush();
    });

    expect(container.textContent).not.toContain("Cached stale retry");
    expect(container.textContent).not.toContain("Fresh missing result");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
    expect(findButton(container, "Abandon")).toBeUndefined();
  });

  it("suppresses an unlisted stalled dossier when fresh verification fails", async () => {
    const actionId = "stalled-cache-error";
    const verification = deferred<never>();
    // React Query consumes the rejection; prevent the deferred from being
    // observed as unhandled before the query attaches its handler.
    void verification.promise.catch(() => undefined);
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    qc.setQueryData(["approvals", "detail", actionId], {
      data: makeStalledDetailData(actionId, { title: "Cached stale error" }),
      meta: {},
    });
    vi.mocked(getApprovalDetail).mockReturnValue(verification.promise as AnyMock);

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(
      () => vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === actionId),
    );
    expect(container.textContent).not.toContain("Cached stale error");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();

    await act(async () => {
      verification.reject(new Error("fresh verifier unavailable"));
      await flush();
    });

    expect(container.textContent).not.toContain("Cached stale error");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
    expect(findButton(container, "Approve")).toBeUndefined();
  });

  it("requires the fresh verifier response to match the direct stalled id", async () => {
    const actionId = "stalled-cache-id";
    const freshMismatch = {
      data: makeStalledDetailData("different-id", { title: "Fresh wrong id" }),
      meta: {},
    };
    const verification = deferred<typeof freshMismatch>();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    qc.setQueryData(["approvals", "detail", actionId], {
      data: makeStalledDetailData(actionId, { title: "Cached stale id" }),
      meta: {},
    });
    vi.mocked(getApprovalDetail).mockReturnValue(verification.promise as AnyMock);

    renderPage(`/approvals/${actionId}?state=stalled`);
    await flushUntil(
      () => vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === actionId),
    );

    await act(async () => {
      verification.resolve(freshMismatch);
      await flush();
    });

    expect(container.textContent).not.toContain("Cached stale id");
    expect(container.textContent).not.toContain("Fresh wrong id");
    expect(findButton(container, "Retry dispatch")).toBeUndefined();
  });

  it("describes stalled rows as stalled when the flat metadata is unavailable", async () => {
    const stalled = {
      ...makeSummary("stalled-without-radar", "stalled_action"),
      status: "approved",
      execution_result: null,
    };
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([stalled]) as AnyMock);

    renderPage("/approvals?state=stalled");
    await flushUntil(
      () =>
        container.querySelector('[data-testid="rail-item"][data-approval-id="stalled-without-radar"]') !==
        null,
    );

    expect(container.textContent).toContain("one stalled action never ran");
    expect(container.textContent).not.toContain("1 waiting");
  });

  it("keeps a settled empty stalled lane truthful", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);

    renderPage("/approvals?state=stalled");
    await flushUntil(
      () => container.querySelector('[data-testid="approvals-verdict-all-clear"]') !== null,
    );

    expect(container.textContent).toContain("No stalled approvals.");
    expect(container.textContent).not.toContain("No approvals waiting.");
    expect(container.textContent).toContain("Select a stalled approval to review.");
    expect(container.textContent).not.toContain("Select a pending approval to review.");
  });

  it("retains keyboard navigation but disables approval shortcuts in the stalled lane", async () => {
    const stalled = (id: string) => ({
      ...makeSummary(id, "send_email"),
      status: "approved",
      execution_result: null,
    });
    vi.mocked(getApprovalsFlat).mockImplementation(
      ((state: "waiting" | "stalled") =>
        makeApiResponse(state === "stalled" ? [stalled("stalled-1"), stalled("stalled-2")] : [])) as AnyMock,
    );

    renderPage("/approvals/stalled-1?state=stalled");
    await flushUntil(
      () => container.querySelectorAll('[data-testid="rail-item"]').length === 2,
    );
    expect(getApprovalDetail).toHaveBeenCalledWith("stalled-1");
    navigateCalls.length = 0;
    vi.mocked(toast).mockClear();

    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "j", bubbles: true, cancelable: true }),
      );
      await flush();
    });
    expect(navigateCalls).toContainEqual([
      "/approvals/stalled-2?state=stalled",
      { replace: true },
    ]);

    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "a", bubbles: true, cancelable: true }),
      );
      await flush();
    });
    expect(toast).not.toHaveBeenCalled();
    expect(approveApproval).not.toHaveBeenCalled();
    expect(container.querySelector('[data-pending-verb="approve"]')).toBeNull();
  });

  it("does not mount a pending direct dossier while Stalled loads or after detail rejects it", async () => {
    const pendingId = "pending-direct";
    const stalled = {
      ...makeSummary("stalled-other", "stalled_action"),
      status: "approved",
      execution_result: null,
    };
    const stalledRequest = deferred<{
      data: (typeof stalled)[];
      meta: Record<string, never>;
    }>();
    vi.mocked(getApprovalsFlat).mockImplementation(
      ((state: "waiting" | "stalled") =>
        state === "stalled" ? stalledRequest.promise : makeApiResponse([])) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(makePendingDetail(pendingId) as AnyMock);

    renderPage(`/approvals/${pendingId}?state=stalled`);
    await act(async () => {
      await flush(1);
    });

    expect(getApprovalsFlat).toHaveBeenCalledWith("stalled", 100);
    expect(getApprovalDetail).not.toHaveBeenCalled();
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();

    await act(async () => {
      stalledRequest.resolve({ data: [stalled], meta: {} });
      await flush();
    });
    await flushUntil(
      () =>
        container.querySelector('[data-testid="rail-item"][data-approval-id="stalled-other"]') !==
        null,
    );

    await flushUntil(
      () => vi.mocked(getApprovalDetail).mock.calls.some(([id]) => id === pendingId),
    );
    expect(getApprovalDetail).toHaveBeenCalledWith(pendingId);
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();
  });

  it("does not mount a pending direct dossier when the Stalled request fails", async () => {
    const pendingId = "pending-direct-error";
    const stalledRequest = deferred<never>();
    // Query consumes the rejection; keep a test-side handler too so the
    // deferred error cannot briefly register as unhandled before React does.
    void stalledRequest.promise.catch(() => undefined);
    vi.mocked(getApprovalsFlat).mockImplementation(
      ((state: "waiting" | "stalled") =>
        state === "stalled" ? stalledRequest.promise : makeApiResponse([])) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(makePendingDetail(pendingId) as AnyMock);

    renderPage(`/approvals/${pendingId}?state=stalled`);
    await act(async () => {
      await flush(1);
    });
    expect(getApprovalDetail).not.toHaveBeenCalled();

    await act(async () => {
      stalledRequest.reject(new Error("stalled lane unavailable"));
      await flush();
    });
    await flushUntil(
      () =>
        container.textContent?.includes("Couldn't reach the stalled approvals lane.") ?? false,
    );

    expect(getApprovalDetail).not.toHaveBeenCalled();
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();
  });

  it("does not retain Waiting rows or decision controls while Stalled is loading", async () => {
    const waiting = makeSummary("waiting-transition", "waiting_sensitive_action");
    const stalled = {
      ...makeSummary("stalled-transition", "stalled_action"),
      status: "approved",
      execution_result: null,
    };
    const stalledRequest = deferred<{
      data: (typeof stalled)[];
      meta: Record<string, never>;
    }>();
    vi.mocked(getApprovalsFlat).mockImplementation(
      ((state: "waiting" | "stalled") =>
        state === "stalled"
          ? stalledRequest.promise
          : makeApiResponse([waiting])) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("waiting-transition") as AnyMock,
    );

    renderPage("/approvals");
    await flushUntil(() => findButton(container, "Approve") !== undefined);
    expect(container.textContent).toContain("waiting sensitive action");

    await act(async () => {
      container
        .querySelector<HTMLAnchorElement>('a[href="/approvals?state=stalled"]')
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await flush(1);
    });

    expect(
      container.querySelector('[data-testid="rail-item"][data-approval-id="waiting-transition"]'),
    ).toBeNull();
    expect(container.textContent).not.toContain("waiting sensitive action");
    expect(findButton(container, "Approve")).toBeUndefined();
    expect(findButton(container, "Deny")).toBeUndefined();
    expect(findButton(container, "Defer")).toBeUndefined();
    expect(container.textContent).toContain("loading…");

    await act(async () => {
      stalledRequest.resolve({ data: [stalled], meta: {} });
      await flush();
    });

    await flushUntil(
      () =>
        container.querySelector('[data-testid="rail-item"][data-approval-id="stalled-transition"]') !==
        null,
    );
    expect(container.textContent).toContain("stalled action");
    expect(findButton(container, "Approve")).toBeUndefined();
  });

  it("shows the Stalled query error instead of retained Waiting data when the lane fetch fails", async () => {
    const waiting = makeSummary("waiting-transition-error", "waiting_sensitive_action");
    const stalledRequest = deferred<never>();
    // Query consumes the rejection; keep a test-side handler too so the
    // deferred error cannot briefly register as unhandled before React does.
    void stalledRequest.promise.catch(() => undefined);
    vi.mocked(getApprovalsFlat).mockImplementation(
      ((state: "waiting" | "stalled") =>
        state === "stalled"
          ? stalledRequest.promise
          : makeApiResponse([waiting])) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockReturnValue(
      makePendingDetail("waiting-transition-error") as AnyMock,
    );

    renderPage("/approvals");
    await flushUntil(() => findButton(container, "Approve") !== undefined);

    await act(async () => {
      container
        .querySelector<HTMLAnchorElement>('a[href="/approvals?state=stalled"]')
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await flush(1);
      stalledRequest.reject(new Error("stalled lane unavailable"));
      await flush();
    });

    await flushUntil(
      () =>
        container.textContent?.includes("Couldn't reach the stalled approvals lane.") ?? false,
    );
    expect(container.textContent).toContain("Couldn't reach the stalled approvals lane.");
    expect(container.textContent).not.toContain("waiting sensitive action");
    expect(
      container.querySelector('[data-testid="rail-item"][data-approval-id="waiting-transition-error"]'),
    ).toBeNull();
    expect(findButton(container, "Approve")).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Autonomy panel — merges /approvals/rules into /approvals (bu-86c4c.12)
// ---------------------------------------------------------------------------

function makeRule(id: string, overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id,
    tool_name: "notify",
    arg_constraints: { chat_id: { type: "exact", value: "mom_123" } },
    description: "Auto-approve notify to mom",
    created_from: null,
    created_at: "2026-05-17T10:00:00Z",
    expires_at: null,
    max_uses: null,
    use_count: 4,
    active: true,
    ...overrides,
  };
}

function makeGatedTool(
  toolName: string,
  activeRules: ReturnType<typeof makeRule>[] = [],
) {
  return {
    butler: "messenger",
    tool_name: toolName,
    risk_tier: "medium",
    expiry_hours: 24,
    active_rules: activeRules,
  };
}

describe("ApprovalsPage — Autonomy panel (bu-86c4c.12)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeApiResponse([]) as AnyMock,
    );
    vi.mocked(getApprovalGatedTools).mockReturnValue(
      makeApiResponse([]) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("renders standing rules with live use counts, always visible (not behind a route)", async () => {
    vi.mocked(getApprovalGatedTools).mockReturnValue(
      makeApiResponse([makeGatedTool("notify", [makeRule("r1")])]) as AnyMock,
    );

    renderPage();
    await flushUntil(() => container.textContent?.includes("notify") ?? false);

    expect(container.textContent).toContain("Autonomy");
    expect(container.textContent).toContain("notify");
    expect(container.textContent).toContain("4 uses");
  });

  it("makes a configured zero-rule gate visibly always ask", async () => {
    vi.mocked(getApprovalGatedTools).mockReturnValue(
      makeApiResponse([makeGatedTool("telegram_reply_to_message")]) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () =>
        container.textContent?.includes("telegram_reply_to_message") ?? false,
    );

    expect(container.textContent).toContain("always ask");
  });

  it("names a degraded rule source even when configured gates remain visible", async () => {
    vi.mocked(getApprovalGatedTools).mockReturnValue(
      makeDegradedResponse([makeGatedTool("notify")], ["approval_rules"]) as AnyMock,
    );

    renderPage();
    await flushUntil(() => container.textContent?.includes("notify") ?? false);

    expect(container.textContent).toContain("approval_rules unavailable");
    expect(container.textContent).toContain("rule state unavailable");
    expect(container.textContent).not.toContain("always ask");
  });

  it("revokes a rule inline (no window.confirm) via a two-step confirm", async () => {
    vi.mocked(getApprovalGatedTools).mockReturnValue(
      makeApiResponse([makeGatedTool("notify", [makeRule("r1")])]) as AnyMock,
    );
    vi.mocked(revokeApprovalRule).mockReturnValue(
      makeApiResponse(makeRule("r1", { active: false })) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Revoke") !== undefined);

    await act(async () => {
      findButton(container, "Revoke")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    const confirmBtn = findButton(container, "confirm");
    expect(confirmBtn).toBeDefined();
    await act(async () => {
      confirmBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(revokeApprovalRule).toHaveBeenCalledWith("r1");
  });
});

// ---------------------------------------------------------------------------
// Autonomy Suggestions banner on /approvals (bu-phy21)
//
// The AutonomySuggestionsBanner was fully built (component + hook + client) but
// imported by no page. These tests prove it now renders on the approvals
// surface when pending suggestions exist, is absent when none exist, and that
// its action buttons are wired to the confirm/dismiss client fns (no dead
// onClick).
// ---------------------------------------------------------------------------

function makePromotionSuggestion(id: string, toolName = "send_telegram") {
  return {
    id,
    suggestion_type: "promotion",
    pattern_fingerprint: `fp-${id}`,
    tool_name: toolName,
    representative_args: { chat_id: "mom_123" },
    scope_description: `Auto-approve ${toolName} when chat_id = 'mom_123'`,
    status: "pending",
    approval_count_at_creation: 5,
    created_at: "2026-05-17T10:00:00Z",
    decided_at: null,
    decided_by: null,
    resulting_rule_id: null,
    velocity: { avg_seconds: 12, sample_count: 5, fast_approval: true },
  };
}

function makeSuggestionsResponse<T>(data: T[]) {
  // PaginatedResponse<AutonomySuggestion> shape: { data, meta }.
  return Promise.resolve({ data, meta: {} });
}

describe("ApprovalsPage — autonomy suggestions banner (bu-phy21)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeEmptyHistory() as AnyMock,
    );
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    // Default: no suggestions; individual tests override.
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeSuggestionsResponse([]) as AnyMock,
    );
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("renders the Autonomy Suggestions banner when pending suggestions exist", async () => {
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeSuggestionsResponse([makePromotionSuggestion("s1")]) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () =>
        container.querySelector(
          '[data-testid="autonomy-suggestions-banner"]',
        ) !== null,
    );

    expect(
      container.querySelector('[data-testid="autonomy-suggestions-banner"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("Autonomy Suggestions");
    expect(container.textContent).toContain("Promote to standing rule");
    expect(container.textContent).toContain(
      "Auto-approve send_telegram when chat_id = 'mom_123'",
    );
  });

  it("does NOT render the banner when no pending suggestions exist", async () => {
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeSuggestionsResponse([]) as AnyMock,
    );

    renderPage();
    await act(async () => {
      await flush();
    });

    expect(
      container.querySelector('[data-testid="autonomy-suggestions-banner"]'),
    ).toBeNull();
    expect(container.textContent).not.toContain("Autonomy Suggestions");
  });

  it("renders a retryable unavailable state when autonomy suggestions cannot be read", async () => {
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      Promise.reject(new Error("autonomy suggestions unavailable")) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="autonomy-suggestions-unavailable"]') !== null,
    );

    const note = container.querySelector('[data-testid="autonomy-suggestions-unavailable"]');
    expect(note?.textContent).toContain("Autonomy suggestions: unavailable");
    expect(findButton(container, "Retry")).toBeDefined();
    expect(container.querySelector('[data-testid="autonomy-suggestions-banner"]')).toBeNull();
  });

  it("renders a retryable unavailable state when rule-promotion suggestions cannot be read", async () => {
    vi.mocked(getRulePromotionSuggestions).mockReturnValue(
      Promise.reject(new Error("rule-promotion suggestions unavailable")) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () =>
        container.querySelector(
          '[data-testid="rule-promotion-suggestions-unavailable"]',
        ) !== null,
    );

    const note = container.querySelector(
      '[data-testid="rule-promotion-suggestions-unavailable"]',
    );
    expect(note?.textContent).toContain("Rule promotion suggestions: unavailable");
    expect(findButton(container, "Retry")).toBeDefined();
  });

  it("renders a retryable unavailable state when rule-promotion metrics cannot be read", async () => {
    vi.mocked(getRulePromotionStats).mockReturnValue(
      Promise.reject(new Error("rule-promotion metrics unavailable")) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => container.querySelector('[data-testid="rule-promotion-stats-unavailable"]') !== null,
    );

    const note = container.querySelector('[data-testid="rule-promotion-stats-unavailable"]');
    expect(note?.textContent).toContain("Rule promotion metrics: unavailable");
    expect(findButton(container, "Retry")).toBeDefined();
  });

  it("calls confirmAutonomySuggestion when 'Confirm rule' is clicked (no dead onClick)", async () => {
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeSuggestionsResponse([makePromotionSuggestion("s1")]) as AnyMock,
    );
    vi.mocked(confirmAutonomySuggestion).mockReturnValue(
      makeApiResponse({
        ...makePromotionSuggestion("s1"),
        status: "confirmed",
        resulting_rule_id: "rule-1",
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(
      () => findButton(container, "Confirm rule") !== undefined,
    );

    const confirmBtn = findButton(container, "Confirm rule");
    expect(confirmBtn).toBeDefined();

    await act(async () => {
      confirmBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(confirmAutonomySuggestion).toHaveBeenCalledWith("s1");
  });

  it("calls dismissAutonomySuggestion when 'Dismiss' is clicked (no dead onClick)", async () => {
    vi.mocked(getAutonomySuggestions).mockReturnValue(
      makeSuggestionsResponse([makePromotionSuggestion("s1")]) as AnyMock,
    );
    vi.mocked(dismissAutonomySuggestion).mockReturnValue(
      makeApiResponse({
        ...makePromotionSuggestion("s1"),
        status: "dismissed",
      }) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Dismiss") !== undefined);

    const dismissBtn = findButton(container, "Dismiss");
    expect(dismissBtn).toBeDefined();

    await act(async () => {
      dismissBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(dismissAutonomySuggestion).toHaveBeenCalledWith("s1", undefined);
  });
});

// ---------------------------------------------------------------------------
// Per-item pending state — approving one item must not mislabel the NEXT
// dossier's buttons as already in flight (bu-86c4c.14, JARVIS audit move 9).
// ---------------------------------------------------------------------------

describe("ApprovalsPage — per-item pending state (bu-86c4c.14)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  function renderPage() {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  it("does not mislabel the next dossier's Approve button as pending after approving the previous item", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1"), makeSummary("a2")]) as AnyMock,
    );
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );
    // Never resolves -- keeps approveMut.isPending true for a1's in-flight call
    // for the lifetime of the test, so we can observe whether that pending
    // flag leaks onto the next-selected item's dossier.
    vi.mocked(approveApproval).mockReturnValue(new Promise(() => {}) as AnyMock);

    renderPage(); // implicit selection -> top-ranked item (a1)
    await flushUntil(() => findButton(container, "Approve") !== undefined);

    const approveBtn = findButton(container, "Approve");
    await act(async () => {
      approveBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    // Selection auto-advances to a2 (dropFromPending's onDecided navigation).
    await flushUntil(() => findButton(container, "Approve") !== undefined);

    const nextApproveBtn = findButton(container, "Approve");
    expect(nextApproveBtn?.textContent).toBe("Approve");
    expect(nextApproveBtn?.disabled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Focus-visible outline on rail items (bu-86c4c.14 -- regression lock; the
// classes shipped with bu-86c4c.12's RailItem and must survive this bead's
// j/k roving-focus layer, not be re-suppressed).
// ---------------------------------------------------------------------------

describe("ApprovalsPage — rail item focus outline (bu-86c4c.14)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1")]) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("keeps focus-visible:outline classes on rail item buttons", async () => {
    act(() => {
      root.render(
        <MemoryRouter>
          <QueryClientProvider client={qc}>
            <ApprovalsPage />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
    await flushUntil(
      () => container.querySelector('[data-testid="rail-item"]') !== null,
    );

    const item = container.querySelector('[data-testid="rail-item"]');
    expect(item?.className).toContain("focus-visible:outline");
    expect(item?.className).toContain("focus-visible:outline-2");
  });
});

// ---------------------------------------------------------------------------
// Keyboard triage: j/k roving focus + a/d/x scheduled decisions with an undo
// window (bu-86c4c.14 -- Act loop / hot queue).
//
// Keyboard verbs schedule the real mutation UNDO_WINDOW_MS in the future
// instead of firing immediately (dossier mouse clicks stay instant,
// unaffected -- see the "per-item pending state" and existing "honest
// dispatch status" describes above). These tests use fake timers to control
// that window; the shared `flush`/`flushUntil` helpers above are real-timer
// based and are NOT used in this describe.
// ---------------------------------------------------------------------------

describe("ApprovalsPage — keyboard triage (bu-86c4c.14)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    resetPageMocks();
    vi.useFakeTimers();
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(
      ((id: string) => makePendingDetail(id)) as AnyMock,
    );

    qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function renderAt(initialPath: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <QueryClientProvider client={qc}>
            <Routes>
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/approvals/:id" element={<ApprovalsPage />} />
            </Routes>
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
  }

  /** Fake-timer-aware analogue of the module's real-timer `flush` above. */
  async function flushFake(rounds = 5): Promise<void> {
    for (let i = 0; i < rounds; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
    }
  }

  async function flushUntilFake(
    predicate: () => boolean,
    max = 25,
  ): Promise<void> {
    for (let i = 0; i < max; i++) {
      if (predicate()) return;
      await flushFake(1);
    }
  }

  async function pressKey(key: string): Promise<void> {
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
      );
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it("'j' moves selection to the next rail item and 'k' moves back", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1"), makeSummary("a2")]) as AnyMock,
    );

    renderAt("/approvals/a1");
    // Wait for BOTH the rail (getApprovalsFlat) and the dossier
    // (getApprovalDetail, which fires off the URL param independently and
    // can resolve before the rail list does) so `pending` is non-empty when
    // 'j' is pressed.
    await flushUntilFake(
      () =>
        container.querySelectorAll('[data-testid="rail-item"]').length === 2 &&
        vi.mocked(getApprovalDetail).mock.calls.length > 0,
    );
    expect(getApprovalDetail).toHaveBeenCalledWith("a1");
    expect(getApprovalDetail).not.toHaveBeenCalledWith("a2");

    await pressKey("j");
    await flushUntilFake(() =>
      vi.mocked(getApprovalDetail).mock.calls.some((c) => c[0] === "a2"),
    );
    expect(getApprovalDetail).toHaveBeenCalledWith("a2");

    await pressKey("k");
    await flushUntilFake(
      () =>
        vi
          .mocked(getApprovalDetail)
          .mock.calls.filter((c) => c[0] === "a1").length > 1,
    );
    expect(
      vi.mocked(getApprovalDetail).mock.calls.filter((c) => c[0] === "a1").length,
    ).toBeGreaterThan(1);
  });

  // -------------------------------------------------------------------
  // History-spam regression (bu-2nnhj) -- same defect family as PR #2928's
  // follow-up (bu-k14bg, free-text filter inputs) and bu-wlku1 (SessionsPage's
  // ?selected= mirroring): j/k roving selection navigated via a bare
  // navigate(), which defaults to react-router's PUSH behavior and spams one
  // browser-history entry per keypress -- Back then takes one click per item
  // triaged instead of one click to leave the page. The top-of-file
  // "react-router" mock taps every navigate(to, options) call (forwarding to
  // the real implementation) so this asserts the actual options object react
  // -router receives, not just the resulting route.
  // -------------------------------------------------------------------
  it("j/k roving selection navigates with {replace: true}, not a bare push", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1"), makeSummary("a2"), makeSummary("a3")]) as AnyMock,
    );

    renderAt("/approvals/a1");
    await flushUntilFake(
      () =>
        container.querySelectorAll('[data-testid="rail-item"]').length === 3 &&
        vi.mocked(getApprovalDetail).mock.calls.length > 0,
    );

    // Clear mount-time navigation noise -- only j/k-triggered navigations
    // matter for this assertion.
    navigateCalls.length = 0;

    await pressKey("j");
    await flushUntilFake(() =>
      vi.mocked(getApprovalDetail).mock.calls.some((c) => c[0] === "a2"),
    );
    await pressKey("j");
    await flushUntilFake(() =>
      vi.mocked(getApprovalDetail).mock.calls.some((c) => c[0] === "a3"),
    );
    await pressKey("k");
    await flushUntilFake(
      () =>
        vi
          .mocked(getApprovalDetail)
          .mock.calls.filter((c) => c[0] === "a2").length > 1,
    );

    expect(navigateCalls.length).toBeGreaterThan(0);
    expect(navigateCalls).toEqual([
      ["/approvals/a2", { replace: true }],
      ["/approvals/a3", { replace: true }],
      ["/approvals/a2", { replace: true }],
    ]);
  });

  it("ignores j/k/a/d/x while typing in an input (deny-reason field)", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1"), makeSummary("a2")]) as AnyMock,
    );

    renderAt("/approvals/a1");
    await flushUntilFake(
      () =>
        container.querySelector<HTMLInputElement>(
          'input[placeholder="Deny reason (optional)"]',
        ) !== null,
    );

    const reasonInput = container.querySelector<HTMLInputElement>(
      'input[placeholder="Deny reason (optional)"]',
    );
    expect(reasonInput).not.toBeNull();

    for (const key of ["j", "k", "a", "d", "x"]) {
      await act(async () => {
        const evt = new KeyboardEvent("keydown", {
          key,
          bubbles: true,
          cancelable: true,
        });
        reasonInput?.dispatchEvent(evt);
        await vi.advanceTimersByTimeAsync(0);
      });
    }

    expect(getApprovalDetail).not.toHaveBeenCalledWith("a2");
    expect(approveApproval).not.toHaveBeenCalled();
    expect(denyApproval).not.toHaveBeenCalled();
    expect(deferApproval).not.toHaveBeenCalled();
    expect(
      container.querySelector('[data-pending-verb]'),
    ).toBeNull();
  });

  it("'a' schedules an approval (no immediate call) and fires it once the undo window elapses", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1")]) as AnyMock,
    );
    vi.mocked(approveApproval).mockReturnValue(
      makeApiResponse({
        id: "a1",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "executed",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: true,
      }) as AnyMock,
    );

    renderAt("/approvals/a1");
    await flushUntilFake(
      () =>
        container.querySelectorAll('[data-testid="rail-item"]').length === 1 &&
        vi.mocked(getApprovalDetail).mock.calls.length > 0,
    );

    await pressKey("a");

    // Not called yet -- scheduled, not fired.
    expect(approveApproval).not.toHaveBeenCalled();
    // An undo-toast was shown.
    expect(toast).toHaveBeenCalledWith(
      expect.stringContaining("Approving"),
      expect.objectContaining({
        action: expect.objectContaining({ label: "Undo" }),
      }),
    );
    // The rail item renders the per-item pending state.
    expect(
      container.querySelector('[data-testid="rail-item"][data-pending-verb="approve"]'),
    ).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(approveApproval).toHaveBeenCalledWith("a1");
  });

  it("undo (clicking the scheduled rail item) cancels the decision before it fires", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1")]) as AnyMock,
    );

    renderAt("/approvals/a1");
    await flushUntilFake(
      () =>
        container.querySelectorAll('[data-testid="rail-item"]').length === 1 &&
        vi.mocked(getApprovalDetail).mock.calls.length > 0,
    );

    await pressKey("d");
    expect(denyApproval).not.toHaveBeenCalled();

    const pendingItem = container.querySelector<HTMLButtonElement>(
      '[data-testid="rail-item"][data-pending-verb="deny"]',
    );
    expect(pendingItem).not.toBeNull();

    await act(async () => {
      pendingItem?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await vi.advanceTimersByTimeAsync(0);
    });

    // Undone -- no longer rendered as pending.
    expect(
      container.querySelector('[data-pending-verb]'),
    ).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(denyApproval).not.toHaveBeenCalled();
  });

  it("'x' schedules a defer with the keyboard default hour count", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1")]) as AnyMock,
    );
    vi.mocked(deferApproval).mockReturnValue(
      makeApiResponse({
        id: "a1",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "pending",
        requested_at: "2026-05-17T10:00:00Z",
      }) as AnyMock,
    );

    renderAt("/approvals/a1");
    await flushUntilFake(
      () =>
        container.querySelectorAll('[data-testid="rail-item"]').length === 1 &&
        vi.mocked(getApprovalDetail).mock.calls.length > 0,
    );

    await pressKey("x");
    expect(deferApproval).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(deferApproval).toHaveBeenCalledWith("a1", { hours: 24 });
  });

  it("pressing 'a' twice on the same item schedules only one decision", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1")]) as AnyMock,
    );
    vi.mocked(approveApproval).mockReturnValue(
      makeApiResponse({
        id: "a1",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "executed",
        requested_at: "2026-05-17T10:00:00Z",
        dispatched: true,
      }) as AnyMock,
    );

    renderAt("/approvals/a1");
    await flushUntilFake(
      () =>
        container.querySelectorAll('[data-testid="rail-item"]').length === 1 &&
        vi.mocked(getApprovalDetail).mock.calls.length > 0,
    );

    await pressKey("a");
    await pressKey("a");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(approveApproval).toHaveBeenCalledTimes(1);
  });

  it("navigating away and back to /approvals mid-undo-window does not double-fire the scheduled decision", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(
      makeApiResponse([makeSummary("a1")]) as AnyMock,
    );
    vi.mocked(denyApproval).mockReturnValue(
      makeApiResponse({
        id: "a1",
        butler: "general",
        tool_name: "send_email",
        tool_args: {},
        status: "rejected",
        requested_at: "2026-05-17T10:00:00Z",
      }) as AnyMock,
    );

    // A harness with real <Link>-driven navigation (not a raw MemoryRouter
    // re-render, which would just re-seed initialEntries rather than
    // simulating an in-app route change) so ApprovalsPage genuinely unmounts
    // when leaving /approvals and remounts fresh on return -- the exact
    // "triage then navigate away" scenario this guards against.
    function Nav() {
      const navigate = useNavigate();
      return (
        <div>
          <button data-testid="go-dashboard" onClick={() => navigate("/dashboard")}>
            dashboard
          </button>
          <button data-testid="go-approvals-a1" onClick={() => navigate("/approvals/a1")}>
            approvals
          </button>
        </div>
      );
    }

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/approvals/a1"]}>
          <QueryClientProvider client={qc}>
            <Nav />
            <Routes>
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/approvals/:id" element={<ApprovalsPage />} />
              <Route path="/dashboard" element={<div>dashboard-marker</div>} />
            </Routes>
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
    await flushUntilFake(
      () =>
        container.querySelectorAll('[data-testid="rail-item"]').length === 1 &&
        vi.mocked(getApprovalDetail).mock.calls.length > 0,
    );

    // Schedule a deny via keyboard -- not fired yet.
    await pressKey("d");
    expect(denyApproval).not.toHaveBeenCalled();

    // Navigate entirely away from /approvals (unmounts ApprovalsPage) while
    // 5s undo window is still ticking.
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="go-dashboard"]')?.click();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(container.textContent).toContain("dashboard-marker");

    // Navigate back to /approvals/a1 -- ApprovalsPage remounts as a fresh
    // component instance. The remounted instance must see the decision as
    // already scheduled (module-scoped store), not schedule a second one.
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="go-approvals-a1"]')?.click();
      await vi.advanceTimersByTimeAsync(0);
    });
    await flushFake();

    // The remounted rail correctly shows the item as still pending/dimmed.
    expect(
      container.querySelector('[data-testid="rail-item"][data-pending-verb="deny"]'),
    ).not.toBeNull();

    // Pressing 'd' again on the same item in the fresh instance must be a
    // no-op (already scheduled) rather than scheduling a second timer.
    await pressKey("d");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(denyApproval).toHaveBeenCalledTimes(1);
    expect(denyApproval).toHaveBeenCalledWith("a1", undefined);
  });
});
