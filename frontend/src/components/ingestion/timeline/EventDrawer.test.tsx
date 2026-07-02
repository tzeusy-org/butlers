// @vitest-environment jsdom
/**
 * Unit tests for EventDrawer — focused on the per-session cost column (bu-glot6).
 *
 * Covers:
 * - cost_usd renders a formatted value when the session has a real cost
 * - cost_usd renders "—" only when cost_usd is genuinely null
 * - cost_usd = 0 renders "$0.00", not "—"
 * - Session tab loading and error states render correct placeholders
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ApiError } from "@/api/index.ts";
import type { IngestionEventSummary, IngestionEventSession } from "@/api/index.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mock API and hooks
// ---------------------------------------------------------------------------

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    replayIngestionEvent: vi.fn(),
  };
});

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("@/hooks/use-ingestion-events", () => ({
  useIngestionEventLineage: vi.fn(),
  useIngestionEventReplays: vi.fn(),
  useIngestionEventPayload: vi.fn(),
  useIngestionEventSessions: vi.fn(),
  useIngestionEventRollup: vi.fn(),
  useIngestionEventDetail: vi.fn(),
}));

import {
  useIngestionEventLineage,
  useIngestionEventReplays,
  useIngestionEventPayload,
  useIngestionEventSessions,
  useIngestionEventRollup,
  useIngestionEventDetail,
} from "@/hooks/use-ingestion-events";
import { EventDrawer } from "./EventDrawer";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function makeEvent(overrides: Partial<IngestionEventSummary> = {}): IngestionEventSummary {
  return {
    id: "aaaaaaaa-0000-0000-0000-000000000001",
    received_at: "2026-05-17T10:30:00Z",
    source_channel: "email",
    source_provider: null,
    source_endpoint_identity: null,
    source_sender_identity: "alice@example.com",
    source_thread_identity: null,
    external_event_id: null,
    dedupe_key: null,
    dedupe_strategy: null,
    ingestion_tier: null,
    policy_tier: "standard",
    triage_decision: null,
    triage_target: null,
    status: "ingested",
    filter_reason: null,
    error_detail: null,
    cost_usd: null,
    // bu-4utdw.3: list-provided row enrichment fields (default to "no data yet").
    tokens_in: null,
    tokens_out: null,
    session_count: 0,
    sessions: [],
    sender_display: null,
    ...overrides,
  };
}

function makeSession(overrides: Partial<IngestionEventSession> = {}): IngestionEventSession {
  return {
    id: "ssssssss-0000-0000-0000-000000000001",
    butler_name: "herald",
    trigger_source: "route",
    started_at: "2026-05-17T10:30:00Z",
    completed_at: "2026-05-17T10:30:05Z",
    success: true,
    input_tokens: 1000,
    output_tokens: 500,
    cost_usd: null,
    trace_id: null,
    model: "claude-sonnet",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe("EventDrawer — per-session cost column", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();

    // Default: replays, payload, and detail not loaded
    vi.mocked(useIngestionEventReplays).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventReplays>);

    vi.mocked(useIngestionEventPayload).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventPayload>);

    vi.mocked(useIngestionEventDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventDetail>);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  function renderDrawer(event = makeEvent()) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <EventDrawer
              event={event}
              onClose={vi.fn()}
              onOptimisticUpdate={vi.fn()}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function mockSessions(sessions: IngestionEventSession[]) {
    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: sessions },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: {
        data: undefined,
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });
  }

  it("renders the real cost when cost_usd is a positive number", () => {
    mockSessions([makeSession({ cost_usd: 0.0042 })]);
    renderDrawer();
    // formatCost(0.0042) → "$0.0042"
    expect(container.textContent).toContain("$0.0042");
  });

  it("renders $0.00 when cost_usd is zero (not em dash)", () => {
    mockSessions([makeSession({ cost_usd: 0 })]);
    renderDrawer();
    expect(container.textContent).toContain("$0.00");
  });

  it("renders em dash only when cost_usd is null", () => {
    mockSessions([makeSession({ cost_usd: null })]);
    renderDrawer();
    // formatCost(null) → "—"
    // The cost column should show "—" but not a dollar amount
    const sessionBlock = container.querySelector("[data-testid='sessions-tab-content']");
    expect(sessionBlock).not.toBeNull();
    expect(sessionBlock!.textContent).toContain("—");
    expect(sessionBlock!.textContent).not.toContain("$");
  });

  it("renders <$0.001 for sub-mill costs", () => {
    mockSessions([makeSession({ cost_usd: 0.0005 })]);
    renderDrawer();
    expect(container.textContent).toContain("<$0.001");
  });

  it("renders cost for each session independently", () => {
    mockSessions([
      makeSession({ id: "sess-1", butler_name: "herald", cost_usd: 0.0010 }),
      makeSession({ id: "sess-2", butler_name: "atlas", cost_usd: null }),
    ]);
    renderDrawer();
    const content = container.querySelector("[data-testid='sessions-tab-content']");
    expect(content).not.toBeNull();
    // First session cost present
    expect(content!.textContent).toContain("$0.0010");
    // Second session — null cost renders dash; both session names present
    expect(content!.textContent).toContain("herald");
    expect(content!.textContent).toContain("atlas");
  });

  it("shows empty state when no sessions", () => {
    mockSessions([]);
    renderDrawer();
    expect(container.querySelector("[data-testid='sessions-tab-empty']")).not.toBeNull();
  });

  it("explains a policy-bypass route in the empty state", () => {
    mockSessions([]);
    vi.mocked(useIngestionEventDetail).mockReturnValue({
      data: {
        data: {
          ...makeEvent({ triage_decision: "route_to", triage_target: "health" }),
          lifecycle_state: "parsed",
          decomposition_output: { routed: ["health"], policy_bypass: true },
        },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventDetail>);
    renderDrawer(makeEvent({ triage_decision: "route_to", triage_target: "health" }));
    const empty = container.querySelector("[data-testid='sessions-tab-empty']");
    expect(empty).not.toBeNull();
    expect(empty!.textContent).toContain("health");
    expect(empty!.textContent).toContain("policy-bypass");
  });

  it("shows loading skeleton while sessions are loading", () => {
    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: undefined,
        isLoading: true,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: {
        data: undefined,
        isLoading: true,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });
    renderDrawer();
    expect(container.querySelector("[data-testid='sessions-tab-loading']")).not.toBeNull();
  });

  it("renders honest empty states for error and filtered events", () => {
    mockSessions([]);

    renderDrawer(makeEvent({ status: "error", error_detail: "connector timeout" }));
    let empty = container.querySelector("[data-testid='sessions-tab-empty']");
    expect(empty).not.toBeNull();
    expect(empty!.textContent).toContain("Dispatch failed");
    expect(empty!.textContent).toContain("connector timeout");

    act(() => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    renderDrawer(makeEvent({ status: "filtered", filter_reason: "duplicate" }));
    empty = container.querySelector("[data-testid='sessions-tab-empty']");
    expect(empty).not.toBeNull();
    expect(empty!.textContent).toContain("Filtered before dispatch");
    expect(empty!.textContent).toContain("duplicate");
  });
});

describe("EventDrawer — raw tab", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();

    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: [] },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: {
        data: undefined,
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });

    vi.mocked(useIngestionEventReplays).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventReplays>);

    vi.mocked(useIngestionEventDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventDetail>);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  function renderDrawer(event = makeEvent()) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <EventDrawer
              event={event}
              onClose={vi.fn()}
              onOptimisticUpdate={vi.fn()}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("does not auto-fetch on a remembered raw tab, but offers an explicit audited-load affordance (no dead end)", () => {
    // Simulates landing on THIS event's drawer with 'raw' remembered from a
    // previously viewed event's tab choice (bu-10sw5 fix for the
    // gemini-code-assist audit-gate finding on PR #2854): the remembered
    // preference alone must never trigger the audited payload_read.
    sessionStorage.setItem("ingestion-drawer-tab", "raw");
    vi.mocked(useIngestionEventPayload).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventPayload>);

    renderDrawer();

    // The remembered tab is active...
    expect(
      container.querySelector("[data-testid='drawer-tab-raw']")?.className,
    ).toContain("border-foreground");
    // ...but the fetch must NOT have been auto-triggered from the remembered
    // preference alone: no loading state, and no dead end either — an
    // explicit "Load payload (audited)" button is present instead.
    expect(container.querySelector("[data-testid='raw-tab-loading']")).toBeNull();
    const loadButton = container.querySelector(
      "[data-testid='raw-tab-load-button']",
    ) as HTMLButtonElement;
    expect(loadButton).not.toBeNull();
    expect(vi.mocked(useIngestionEventPayload)).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ enabled: false }),
    );

    // Clicking the explicit affordance is the deliberate, per-event action
    // that triggers the audited read.
    act(() => loadButton.click());
    expect(container.querySelector("[data-testid='raw-tab-loading']")).not.toBeNull();
  });

  it("still fetches on-demand when the user clicks into the raw tab", () => {
    vi.mocked(useIngestionEventPayload).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventPayload>);

    renderDrawer();

    const tabButton = container.querySelector(
      "[data-testid='drawer-tab-raw']",
    ) as HTMLButtonElement;
    act(() => tabButton.click());

    expect(container.querySelector("[data-testid='raw-tab-loading']")).not.toBeNull();
  });

  it("does not carry an audited fetch over to a different event via the remembered tab (bu-10sw5)", () => {
    // Regression test for the gemini-code-assist finding on PR #2854: viewing
    // the raw tab for one event must not silently audit-log the payload read
    // for the NEXT event the user opens, even though 'raw' stays remembered
    // in sessionStorage and the tab itself stays visually selected.
    vi.mocked(useIngestionEventPayload).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventPayload>);

    const eventA = makeEvent({ id: "event-a-0000-0000-0000-000000000001" });
    renderDrawer(eventA);
    const tabButtonA = container.querySelector(
      "[data-testid='drawer-tab-raw']",
    ) as HTMLButtonElement;
    act(() => tabButtonA.click());
    expect(container.querySelector("[data-testid='raw-tab-loading']")).not.toBeNull();
    expect(vi.mocked(useIngestionEventPayload)).toHaveBeenCalledWith(
      eventA.id,
      expect.objectContaining({ enabled: true }),
    );

    // User closes the drawer (unmount) and opens a DIFFERENT event — this is
    // exactly how TimelineTab mounts a fresh EventDrawer per selected row.
    act(() => root.unmount());
    vi.mocked(useIngestionEventPayload).mockClear();

    root = createRoot(container);
    const eventB = makeEvent({ id: "event-b-0000-0000-0000-000000000002" });
    renderDrawer(eventB);

    // 'raw' is still the remembered/active tab for event B...
    expect(
      container.querySelector("[data-testid='drawer-tab-raw']")?.className,
    ).toContain("border-foreground");
    // ...but event B's payload must NOT have been fetched automatically.
    expect(container.querySelector("[data-testid='raw-tab-loading']")).toBeNull();
    expect(vi.mocked(useIngestionEventPayload)).not.toHaveBeenCalledWith(
      eventB.id,
      expect.objectContaining({ enabled: true }),
    );

    const loadButtonB = container.querySelector(
      "[data-testid='raw-tab-load-button']",
    ) as HTMLButtonElement;
    expect(loadButtonB).not.toBeNull();
    act(() => loadButtonB.click());
    expect(vi.mocked(useIngestionEventPayload)).toHaveBeenCalledWith(
      eventB.id,
      expect.objectContaining({ enabled: true }),
    );
  });

  it("shows single-owner-truthful copy on a 403, with no multi-tenant 'administrator' framing", () => {
    sessionStorage.setItem("ingestion-drawer-tab", "raw");
    vi.mocked(useIngestionEventPayload).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError("forbidden", "Forbidden", 403),
    } as unknown as ReturnType<typeof useIngestionEventPayload>);

    renderDrawer();

    const loadButton = container.querySelector(
      "[data-testid='raw-tab-load-button']",
    ) as HTMLButtonElement;
    act(() => loadButton.click());

    const gated = container.querySelector("[data-testid='raw-tab-gated']");
    expect(gated).not.toBeNull();
    expect(gated!.textContent).not.toContain("administrator");
    expect(gated!.textContent).toContain("disabled for this session");
    expect(gated!.textContent).toContain("audit log");
  });
});
