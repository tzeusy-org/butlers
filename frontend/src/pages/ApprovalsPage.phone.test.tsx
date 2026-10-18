// @vitest-environment jsdom
/**
 * Phone layout for the deep-linked approval dossier (bu-h40h2b.18).
 *
 * /approvals/:id is the page a Telegram notification opens. Below md the workspace stacks, and
 * the dossier used to sit under the whole rail; a deep link now leads with the targeted dossier
 * and focuses its heading once. A plain /approvals visit keeps the rail first and focus alone.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ApprovalsPage from "@/pages/ApprovalsPage";

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
  getApprovalDetail,
  getApprovalGatedTools,
  getApprovalMetrics,
  getApprovalsFlat,
  getApprovalsHistory,
  getApprovalsPolicy,
  getApprovalRules,
  getAutonomySuggestions,
  getRulePromotionStats,
  getRulePromotionSuggestions,
  getUnroutableAttention,
} from "@/api/index.ts";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

function ok<T>(data: T) {
  return Promise.resolve({ data, meta: {} });
}

function summary(id: string) {
  return {
    id,
    butler: "general",
    tool_name: "send_email",
    status: "pending",
    why: null,
    created_at: "2026-05-17T10:00:00Z",
    expires_at: null,
  };
}

function detail(id: string) {
  return ok({
    id,
    title: `Send Email ${id}`,
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

async function flushUntil(predicate: () => boolean, max = 40): Promise<void> {
  for (let i = 0; i < max; i++) {
    if (predicate()) return;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe("ApprovalsPage dossier on a phone", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getApprovalGatedTools).mockReturnValue(ok([]) as AnyMock);
    vi.mocked(getApprovalMetrics).mockReturnValue(ok({ callback_secret_configured: null }) as AnyMock);
    vi.mocked(getRulePromotionSuggestions).mockReturnValue(
      ok({ pending: [], auto_applied: [] }) as AnyMock,
    );
    vi.mocked(getRulePromotionStats).mockReturnValue(ok({}) as AnyMock);
    vi.mocked(getUnroutableAttention).mockReturnValue(ok([]) as AnyMock);
    vi.mocked(getApprovalsHistory).mockReturnValue(ok([]) as AnyMock);
    vi.mocked(getApprovalsPolicy).mockReturnValue(
      ok({ quiet_start_hour: null, quiet_end_hour: null, timezone: "UTC" }) as AnyMock,
    );
    vi.mocked(getAutonomySuggestions).mockReturnValue(ok([]) as AnyMock);
    vi.mocked(getApprovalRules).mockReturnValue(ok([]) as AnyMock);
    vi.mocked(getApprovalsFlat).mockReturnValue(ok([summary("a1"), summary("a2")]) as AnyMock);
    vi.mocked(getApprovalDetail).mockImplementation(((id: string) => detail(id)) as AnyMock);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function renderAt(path: string) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={[path]}>
          <QueryClientProvider client={client}>
            <Routes>
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/approvals/:id" element={<ApprovalsPage />} />
            </Routes>
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
    await flushUntil(() => container.querySelector("h2[tabindex]") !== null || path === "/approvals");
    await flushUntil(() => container.querySelector('[data-testid="approval-dossier-pane"]') !== null);
  }

  it("leads with the targeted dossier below md and focuses its heading", async () => {
    await renderAt("/approvals/a2");
    await flushUntil(() => container.querySelector("h2[tabindex]") !== null);

    const pane = container.querySelector<HTMLElement>('[data-testid="approval-dossier-pane"]')!;
    expect(pane.className).toContain("max-md:-order-1");
    const heading = container.querySelector<HTMLElement>("h2[tabindex]")!;
    expect(heading.textContent).toBe("Send Email a2");
    expect(document.activeElement).toBe(heading);
  });

  it("keeps the rail first and focus where it was without a deep link", async () => {
    await renderAt("/approvals");
    await flushUntil(() => container.querySelector("h2[tabindex]") !== null);

    const pane = container.querySelector<HTMLElement>('[data-testid="approval-dossier-pane"]')!;
    expect(pane.className).not.toContain("max-md:-order-1");
    expect(document.activeElement).not.toBe(container.querySelector("h2[tabindex]"));
  });

  it("orders the dossier placeholder first too, so loading never pushes it below the rail", async () => {
    vi.mocked(getApprovalDetail).mockReturnValue(new Promise(() => undefined) as AnyMock);
    await renderAt("/approvals/a2");

    const pane = container.querySelector<HTMLElement>('[data-testid="approval-dossier-pane"]')!;
    expect(pane.textContent).toContain("loading");
    expect(pane.className).toContain("max-md:-order-1");
  });
});
