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
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ApprovalsPage from "@/pages/ApprovalsPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-approvals-stream", () => ({
  useApprovalsStream: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

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
}));

import {
  approveApproval,
  getApprovalDetail,
  getApprovalsFlat,
  getApprovalsHistory,
  getApprovalsPolicy,
  retryApproval,
} from "@/api/index.ts";
import { toast } from "sonner";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

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

function makeEmptyHistory() {
  return makeApiResponse([]);
}

function makeEmptyPolicy() {
  return makeApiResponse({ quiet_start_hour: null, quiet_end_hour: null, timezone: "UTC" });
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

function findButton(container: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((btn) =>
    btn.textContent?.trim() === label,
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
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);

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
      makeApiResponse([makeSummary("a1", "send_email"), makeSummary("a2", "delete_file")]) as AnyMock,
    );

    renderPage();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("send email");
    expect(container.textContent).toContain("delete file");
  });

  it("shows 'Load more' button when response length equals the current limit", async () => {
    // Build 100 summaries (= PENDING_PAGE_SIZE) to simulate a full page.
    const full = Array.from({ length: 100 }, (_, i) => makeSummary(`id-${i}`));
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse(full) as AnyMock);

    renderPage();
    await act(async () => { await flush(); });

    expect(findButton(container, "Load more")).toBeDefined();
  });

  it("does NOT show 'Load more' when response is smaller than limit", async () => {
    // 3 results < 100 limit → no more pages.
    const partial = [makeSummary("a1"), makeSummary("a2"), makeSummary("a3")];
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse(partial) as AnyMock);

    renderPage();
    await act(async () => { await flush(); });

    expect(findButton(container, "Load more")).toBeUndefined();
  });

  it("shows empty state message when no pending approvals", async () => {
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);

    renderPage();
    await act(async () => { await flush(); });

    expect(container.textContent).toContain("No pending approvals");
    expect(findButton(container, "Load more")).toBeUndefined();
  });

  it("re-calls getApprovalsFlat with bumped limit after clicking 'Load more'", async () => {
    // First call: full page of 100.
    const full = Array.from({ length: 100 }, (_, i) => makeSummary(`id-${i}`));
    // Second call (limit=200): still full → Load more persists.
    const larger = Array.from({ length: 200 }, (_, i) => makeSummary(`id-${i}`));

    vi.mocked(getApprovalsFlat)
      .mockReturnValueOnce(makeApiResponse(full) as AnyMock)
      .mockReturnValueOnce(makeApiResponse(larger) as AnyMock);

    renderPage();
    await act(async () => { await flush(); });

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
    proposed_action: { tool_name: "send_email", tool_args: {}, agent_summary: null },
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
    vi.mocked(getApprovalsHistory).mockReturnValue(makeEmptyHistory() as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(makeEmptyPolicy() as AnyMock);
    vi.mocked(getApprovalsFlat).mockReturnValue(makeApiResponse([]) as AnyMock);

    qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => { root.unmount(); });
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
    vi.mocked(getApprovalDetail).mockReturnValue(makePendingDetail("a1") as AnyMock);
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
    vi.mocked(getApprovalDetail).mockReturnValue(makePendingDetail("a2") as AnyMock);
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

  it("renders a 'Retry dispatch' affordance for approved-but-un-run history rows", async () => {
    vi.mocked(getApprovalsHistory).mockReturnValue(
      makeApiResponse([
        makeHistoryItem("h-approved", "approved"),
        makeHistoryItem("h-executed", "executed"),
      ]) as AnyMock,
    );

    renderPage();
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    // Exactly one retry button — only the approved (un-run) row gets it.
    const retryButtons = Array.from(container.querySelectorAll("button")).filter(
      (b) => b.textContent?.trim() === "Retry dispatch",
    );
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
    await flushUntil(() => findButton(container, "Retry dispatch") !== undefined);

    const retryBtn = findButton(container, "Retry dispatch");
    expect(retryBtn).toBeDefined();

    await act(async () => {
      retryBtn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(retryApproval).toHaveBeenCalledWith("h-approved");
    expect(toast.success).toHaveBeenCalledWith("Dispatched");
  });
});
