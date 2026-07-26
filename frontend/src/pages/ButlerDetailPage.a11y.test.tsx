/**
 * ButlerDetailPage — axe-core accessibility test, run against the REAL
 * routed page (bu-86c4c.16).
 *
 * The previous version of this file (bu-sfeuw.4) ran axe against a local
 * `ActionsShell` stub built from ButlerStatusBadge plus hand-written
 * `<button>` markup that merely "mirrored" the real actions bar — a change
 * to the real ButlerDetailHeader/ButlerDetailActions/tab content could
 * regress accessibility without this suite ever noticing (JARVIS audit
 * move 11, critical finding: "the a11y gate is theater").
 *
 * This version reuses the same extensive hook-mocking harness already
 * established in ButlerDetailPage.test.tsx (vi.mock is file-scoped, so it
 * cannot be imported/shared — duplicated here deliberately) and drives the
 * actual `<ButlerDetailPage />` component through its real states: loading,
 * error, and the default Overview tab populated.
 *
 * Colour-contrast is disabled because jsdom cannot compute computed styles;
 * that gap is covered separately by src/lib/contrast.test.ts.
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useParams, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";

import ButlerDetailPage from "@/pages/ButlerDetailPage";
import { useButler } from "@/hooks/use-butlers";
import type { ButlerSummary } from "@/api/types";

expect.extend(toHaveNoViolations);

// ---------------------------------------------------------------------------
// Mocks — same set as ButlerDetailPage.test.tsx so the Overview tab (and its
// header/actions chrome) mounts without hitting real network/API calls.
// ---------------------------------------------------------------------------

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ name: "general" })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  };
});

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false })),
  useButlerConfig: vi.fn(() => ({ data: null, isLoading: false })),
  useButlerModules: vi.fn(() => ({ data: null, isLoading: false })),
  useButlerSkills: vi.fn(() => ({ data: null, isLoading: false })),
  useRuntimeConfig: vi.fn(() => ({ data: null, isLoading: false })),
  usePatchRuntimeConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(() => ({ data: null, isLoading: false })),
  useGlobalSessionDetail: vi.fn(() => ({ data: null, isLoading: false })),
  useSessionAggregate: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useUpcomingDates: vi.fn(() => ({ data: [], isLoading: false })),
}));

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useInstanceFacts: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useDatabaseFacts: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useBackupFacts: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useEgressFacts: vi.fn(() => ({ data: null, isLoading: false, error: null, isForbidden: false })),
  useHealthPosture: vi.fn(() => ({ data: undefined, isPending: false, isError: false, error: null })),
  useInsightDeliveryState: vi.fn(() => ({ data: undefined, isPending: true, isError: false, error: null })),
}));

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(() => ({ data: null, isLoading: false, isError: false, error: null })),
}));

vi.mock("@/hooks/use-delegation", () => ({
  useDelegationLedger: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-domain-events", () => ({
  useDomainEventSubscriptions: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useDomainEventDeliveries: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
}));

vi.mock("@/components/topology/TopologyGraph", () => ({
  default: () => <div data-testid="topology-graph-stub" />,
}));

vi.mock("@/hooks/use-butler-status-board", () => ({
  useButlerStatusBoard: vi.fn(() => ({
    rows: [],
    aggregates: { isLoading: false, isError: false, error: null, refetch: vi.fn() },
  })),
}));

vi.mock("@/hooks/use-schedules", () => ({
  useSchedules: vi.fn(() => ({ data: { data: [] }, isLoading: false })),
}));

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: null, isLoading: false })),
  useSetEligibility: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalActions: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
}));

vi.mock("@/hooks/use-butler-analytics", () => ({
  useButlerActivityFeed: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerHourlyActivity: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerDailyActivity: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerSessionKinds: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
  useButlerLatencyStats: vi.fn(() => ({ data: null, isLoading: false, isError: false })),
}));

vi.mock("@/components/chat/ChatPanel", () => ({
  ChatPanel: ({ butlerName, triggerLabel }: { butlerName: string; triggerLabel?: string }) => (
    <div data-testid="chat-panel">{triggerLabel ?? "Chat"}:{butlerName}</div>
  ),
}));

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    triggerButler: vi.fn(() => Promise.resolve({ success: true, session_id: null, output: "" })),
  };
});

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type UseButlerResult = ReturnType<typeof useButler>;

const BASE_BUTLER: ButlerSummary = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 0,
};

function setButlerState(butler: ButlerSummary | null, opts: Partial<UseButlerResult> = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: butler ? { data: butler } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseButlerResult);
}

async function checkA11y(): Promise<void> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const results = await axe(container, {
    rules: {
      "color-contrast": { enabled: false },
    },
  });
  expect(results).toHaveNoViolations();
}

beforeEach(() => {
  vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
  vi.mocked(useParams).mockReturnValue({ name: "general" });
  setButlerState(BASE_BUTLER);
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Story 1: Loading state
// ---------------------------------------------------------------------------

describe("a11y (real page): Loading state", () => {
  it("has zero axe violations", async () => {
    setButlerState(null, { isLoading: true });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 2: Error state
// ---------------------------------------------------------------------------

describe("a11y (real page): Error state", () => {
  it("has zero axe violations", async () => {
    setButlerState(null, { error: new Error("Failed to fetch butler data.") });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 3: Populated — default Overview tab, status ok
// ---------------------------------------------------------------------------

describe("a11y (real page): Overview tab, status ok", () => {
  it("has zero axe violations", async () => {
    setButlerState(BASE_BUTLER);
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 4: Populated — status degraded/down
// ---------------------------------------------------------------------------

describe("a11y (real page): Overview tab, status down", () => {
  it("has zero axe violations", async () => {
    setButlerState({ ...BASE_BUTLER, status: "down" });
    await checkA11y();
  });
});

// ---------------------------------------------------------------------------
// Story 5: Activity tab
// ---------------------------------------------------------------------------

describe("a11y (real page): Activity tab", () => {
  it("has zero axe violations", async () => {
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams("tab=activity"), vi.fn()]);
    setButlerState(BASE_BUTLER);
    await checkA11y();
  });
});
