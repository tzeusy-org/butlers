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

// Mock the API module — we only need getApprovalsFlat + getApprovalsHistory +
// getApprovalsPolicy for these tests. Others are stubs to satisfy imports.
vi.mock("@/api/index.ts", () => ({
  getApprovalsFlat: vi.fn(),
  getApprovalsHistory: vi.fn(),
  getApprovalsPolicy: vi.fn(),
  getApprovalDetail: vi.fn(),
  approveApproval: vi.fn(),
  denyApproval: vi.fn(),
  deferApproval: vi.fn(),
  retryApproval: vi.fn(),
  updateApprovalsPolicy: vi.fn(),
  // Autonomy suggestions banner data + verbs (wired into ApprovalsPage via
  // the use-approvals hooks).
  getAutonomySuggestions: vi.fn(),
  confirmAutonomySuggestion: vi.fn(),
  dismissAutonomySuggestion: vi.fn(),
  // AutonomyPanel (bu-86c4c.12) — always rendered alongside the dossier.
  getApprovalRules: vi.fn(),
  createApprovalRule: vi.fn(),
  revokeApprovalRule: vi.fn(),
}));

import {
  approveApproval,
  confirmAutonomySuggestion,
  deferApproval,
  denyApproval,
  dismissAutonomySuggestion,
  getApprovalDetail,
  getApprovalRules,
  getApprovalsFlat,
  getApprovalsHistory,
  getApprovalsPolicy,
  getAutonomySuggestions,
  retryApproval,
  revokeApprovalRule,
} from "@/api/index.ts";
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
    vi.resetAllMocks();
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
});

// ---------------------------------------------------------------------------
// Honest dispatch status + retry affordance (bu-j1xkd)
// ---------------------------------------------------------------------------

function makeHistoryItem(id: string, status: string, toolName = "send_email") {
  return {
    id,
    butler: "general",
    tool_name: toolName,
    status,
    why: null,
    created_at: "2026-05-17T10:00:00Z",
    expires_at: null,
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

describe("ApprovalsPage — honest dispatch status + retry (bu-j1xkd)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    vi.resetAllMocks();
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
          name: "Acme Research & Technologies",
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
    expect(container.textContent).toContain("Acme Research & Technologies");
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
    vi.resetAllMocks();
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
    vi.resetAllMocks();
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
    vi.resetAllMocks();
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

describe("ApprovalsPage — Autonomy panel (bu-86c4c.12)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let qc: QueryClient;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getAutonomySuggestions).mockReturnValue(
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
    vi.mocked(getApprovalRules).mockReturnValue(
      makeApiResponse([makeRule("r1")]) as AnyMock,
    );

    renderPage();
    await flushUntil(() => container.textContent?.includes("notify") ?? false);

    expect(container.textContent).toContain("Autonomy");
    expect(container.textContent).toContain("notify");
    expect(container.textContent).toContain("4 uses");
  });

  it("shows a calm empty state when no standing rules exist", async () => {
    vi.mocked(getApprovalRules).mockReturnValue(makeApiResponse([]) as AnyMock);

    renderPage();
    await flushUntil(
      () =>
        container.textContent?.includes("No standing rules") ?? false,
    );

    expect(container.textContent).toContain(
      "No standing rules. Every action requires manual approval.",
    );
  });

  it("revokes a rule inline (no window.confirm) via a two-step confirm", async () => {
    vi.mocked(getApprovalRules).mockReturnValue(
      makeApiResponse([makeRule("r1")]) as AnyMock,
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
    vi.resetAllMocks();
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
    vi.resetAllMocks();
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
    vi.resetAllMocks();
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
    vi.resetAllMocks();
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
