// @vitest-environment jsdom
/**
 * Tests for TimelineTab component.
 *
 * Covers:
 * - Row status rendering per event status (RowStatus: quiet dot + word, bu-4utdw.4)
 * - Replay button states (filtered/error/replay_failed/replay_pending/ingested/replay_complete)
 * - Optimistic update on Replay click + override eviction
 * - Error toast on replay failure
 * - Status filter checkboxes
 * - Every row is expandable (bu-4utdw.4): filtered/error rows open the drawer too,
 *   keyboard focus + Enter opens a row, no filled status pills render in rows
 * - §2.5 Drawer: session anchor IDs, session index rail, copy-session-id button
 * - §2.6 Sender identity resolution (resolved / unresolved)
 * - §2.8 Saved Views: selector renders, view changes apply statuses, Priority
 *   placeholder retired (bu-4utdw.5)
 * - §2.9 Connector Attention Strip: strip renders on unhealthy connectors, hidden when all healthy
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { IngestionEventStatus, IngestionEventSummary } from "@/api/index.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mock API module
// ---------------------------------------------------------------------------

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    replayIngestionEvent: vi.fn(),
    bulkRetryEvents: vi.fn(),
  };
});

// Mock sonner toast so we can verify it's called
vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

// Mock the ingestion-events hooks so we don't need a real API
vi.mock("@/hooks/use-ingestion-events", () => ({
  useIngestionEvents: vi.fn(),
  useIngestionEventLineage: vi.fn(),
  useIngestionEventRollup: vi.fn(),
  useIngestionEventSenderContact: vi.fn(),
  useIngestionEventReplays: vi.fn(),
  useIngestionEventPayload: vi.fn(),
  useIngestionEventDetail: vi.fn(),
  useIngestionWindowRollup: vi.fn(),
  useIngestionEventsHistogram: vi.fn(),
}));

// Mock the connector summaries hook (§2.9 — ConnectorAttentionStrip)
vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
}));

import { ApiError, bulkRetryEvents, replayIngestionEvent } from "@/api/index.ts";
import { toast } from "sonner";
import {
  useIngestionEvents,
  useIngestionEventLineage,
  useIngestionEventRollup,
  useIngestionEventSenderContact,
  useIngestionEventSessions,
  useIngestionEventReplays,
  useIngestionEventPayload,
  useIngestionEventDetail,
  useIngestionWindowRollup,
  useIngestionEventsHistogram,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";

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
    id: "aabbccdd-0000-0000-0000-000000000001",
    received_at: "2026-01-01T10:00:00Z",
    source_channel: "gmail",
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

/** Build the mock return value for useIngestionEvents (InfiniteQuery shape). */
function makeInfiniteEventsResult(events: IngestionEventSummary[]) {
  return {
    data: {
      pages: [{ data: events, meta: { next_cursor: null, has_more: false } }],
      pageParams: [null],
    },
    isLoading: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    refetch: vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Default mock setup helpers
// ---------------------------------------------------------------------------

function setupDefaultMocks() {
  vi.mocked(useIngestionEventRollup).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionEventRollup>);

  vi.mocked(useIngestionEventSenderContact).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionEventSenderContact>);

  // Default: no replays, no payload (drawer stubs)
  vi.mocked(useIngestionEventReplays).mockReturnValue({
    data: { data: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionEventReplays>);

  vi.mocked(useIngestionEventPayload).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionEventPayload>);

  vi.mocked(useIngestionEventDetail).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionEventDetail>);

  // Default: no sessions (drawer stubs)
  vi.mocked(useIngestionEventLineage).mockReturnValue({
    sessions: { data: { data: [] }, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventSessions>,
    rollup: { data: undefined, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventRollup>,
  });

  // Default: no connector issues (strip hidden)
  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: { data: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useConnectorSummaries>);

  // Default: empty window rollup (bu-mxtn2)
  vi.mocked(useIngestionWindowRollup).mockReturnValue({
    data: { events: 0, sessions: 0, cost: null, window: { from: null, to: null } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionWindowRollup>);

  // Default: empty histogram (bu-4utdw.7 hour strip data source)
  vi.mocked(useIngestionEventsHistogram).mockReturnValue({
    data: { buckets: [], bucket: "1m" },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionEventsHistogram>);
}

// We test ActionCell indirectly through TimelineTab since it's not exported.
import { TimelineTab } from "./TimelineTab";

// ---------------------------------------------------------------------------
// TimelineTab — channel chip filter (bu-p5kdx)
//
// Verifies that the eventsFilters passed to useIngestionEvents reflect the
// active channel chips correctly:
//   - single channel  → channels="email"
//   - multi channel   → channels="email,telegram"
//   - no channels     → no channels param
//   - source_channel is NOT sent (old code path removed)
// ---------------------------------------------------------------------------

describe("TimelineTab — channel chip filter passes channels= CSV to useIngestionEvents", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function renderWithChannels(channelsParam: string) {
    const initialUrl = channelsParam ? `/?channels=${encodeURIComponent(channelsParam)}` : "/";
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[initialUrl]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("passes channels=email when one channel chip is active", () => {
    renderWithChannels("email");
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).toMatchObject({ channels: "email" });
    expect(lastFilters).not.toHaveProperty("source_channel");
  });

  it("passes channels=email,telegram when two channel chips are active", () => {
    renderWithChannels("email,telegram");
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).toMatchObject({ channels: "email,telegram" });
    expect(lastFilters).not.toHaveProperty("source_channel");
  });

  it("omits channels param when no channel chips are active", () => {
    renderWithChannels("");
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).not.toHaveProperty("channels");
    expect(lastFilters).not.toHaveProperty("source_channel");
  });

  it("never sends source_channel even for a single channel (old code path removed)", () => {
    renderWithChannels("email");
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    for (const [filters] of calls) {
      expect(filters).not.toHaveProperty("source_channel");
    }
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — status filter pushes statuses= CSV to useIngestionEvents
//
// Hidden statuses (e.g. "skipped" home_assistant sensor noise) must be
// excluded server-side so pages aren't dominated by rows the client filters
// out anyway.
// ---------------------------------------------------------------------------

describe("TimelineTab — status filter passes statuses= CSV to useIngestionEvents", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function renderWithStatuses(statuses?: IngestionEventStatus[]) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={statuses} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("passes the enabled statuses as a CSV when a subset is selected", () => {
    renderWithStatuses(["ingested", "error"]);
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).toMatchObject({ statuses: "ingested,error" });
  });

  it("excludes skipped and filtered by default (no defaultStatuses override)", () => {
    renderWithStatuses(undefined);
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0] as { statuses?: string };
    expect(lastFilters.statuses).toBeDefined();
    expect(lastFilters.statuses).not.toContain("skipped");
    expect(lastFilters.statuses).not.toContain("filtered");
    expect(lastFilters.statuses).toContain("ingested");
  });

  it("omits the statuses param when every status is enabled", () => {
    renderWithStatuses([
      "ingested",
      "skipped",
      "filtered",
      "error",
      "replay_pending",
      "replay_complete",
      "replay_failed",
    ]);
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).not.toHaveProperty("statuses");
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — row status rendering (RowStatus: quiet dot + word, bu-4utdw.4)
// ---------------------------------------------------------------------------

describe("TimelineTab — row status rendering", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function render(events: IngestionEventSummary[]) {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("renders 'ingested' badge text", () => {
    render([makeEvent({ status: "ingested" })]);
    expect(container.textContent).toContain("ingested");
  });

  it("renders 'filtered' badge text", () => {
    render([makeEvent({ status: "filtered", filter_reason: "rule matched" })]);
    expect(container.textContent).toContain("filtered");
  });

  it("renders 'error' badge text", () => {
    render([makeEvent({ status: "error" })]);
    expect(container.textContent).toContain("error");
  });

  it("renders 'replay pending' badge text for replay_pending", () => {
    render([makeEvent({ status: "replay_pending" })]);
    expect(container.textContent).toContain("replay pending");
  });

  it("shows Replay button for filtered events", () => {
    render([makeEvent({ status: "filtered" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute("title")).toBe("Replay");
  });

  it("shows Replay button for error events", () => {
    render([makeEvent({ status: "error" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute("title")).toBe("Replay");
  });

  it("shows Retry button for replay_failed events", () => {
    render([makeEvent({ status: "replay_failed" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute("title")).toBe("Retry");
  });

  it("shows spinner (not Replay button) for replay_pending events", () => {
    render([makeEvent({ status: "replay_pending" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    const spinner = container.querySelector("[data-testid='replay-pending-spinner']");
    expect(btn).toBeNull();
    expect(spinner).not.toBeNull();
  });

  it("does not show Replay button for ingested events (already processed)", () => {
    // ingested events are already processed — no replay needed
    render([makeEvent({ status: "ingested" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).toBeNull();
  });

  it("does not show Replay button for replay_complete events (already replayed)", () => {
    // replay_complete events have already been successfully replayed
    render([makeEvent({ status: "replay_complete" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — Replay button interaction
// ---------------------------------------------------------------------------

describe("TimelineTab — Replay button interaction", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function render(events: IngestionEventSummary[]) {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("calls replayIngestionEvent with correct id on Replay click and optimistically updates to replay_pending", async () => {
    const event = makeEvent({ status: "filtered", id: "test-event-id-1234" });
    vi.mocked(replayIngestionEvent).mockResolvedValueOnce({
      id: event.id,
      status: "replay_pending",
    });

    render([event]);

    const btn = container.querySelector("[data-testid='replay-button']") as HTMLButtonElement;
    expect(btn).not.toBeNull();

    await act(async () => {
      btn.click();
    });

    expect(replayIngestionEvent).toHaveBeenCalledWith("test-event-id-1234");
    // After successful replay, the badge should show "replay pending" optimistically
    expect(container.textContent).toContain("replay pending");
  });

  it("clears optimistic override when server returns non-replay_pending status after replay", async () => {
    const event = makeEvent({ status: "filtered", id: "test-event-id-evict" });
    vi.mocked(replayIngestionEvent).mockResolvedValueOnce({
      id: event.id,
      status: "replay_pending",
    });

    // Initial render: filtered event
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([event]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Click Replay — optimistic override sets replay_pending
    const btn = container.querySelector("[data-testid='replay-button']") as HTMLButtonElement;
    await act(async () => {
      btn.click();
    });
    // Scoped to the ledger (not the whole container) — the toolbar's status
    // filter chips always render the literal word "replay pending" as a
    // label, regardless of row state.
    const ledger = container.querySelector("[data-testid='timeline-ledger']") as HTMLElement;
    expect(ledger.textContent).toContain("replay pending");

    // Server refetch returns replay_complete — override should be evicted
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([{ ...event, status: "replay_complete" }]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    // Re-render with updated server data
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Row status should now show "replay complete" (the badge vocabulary word
    // for replay_complete, bu-4utdw.4), not the stale "replay pending".
    const ledgerAfter = container.querySelector("[data-testid='timeline-ledger']") as HTMLElement;
    expect(ledgerAfter.textContent).toContain("replay complete");
    expect(ledgerAfter.textContent).not.toContain("replay pending");
  });

  it("shows error toast when replay API call fails", async () => {
    const event = makeEvent({ status: "error", id: "test-event-id-err" });
    vi.mocked(replayIngestionEvent).mockRejectedValueOnce(
      new Error("Server error: 500"),
    );

    render([event]);

    const btn = container.querySelector("[data-testid='replay-button']") as HTMLButtonElement;
    expect(btn).not.toBeNull();

    await act(async () => {
      btn.click();
    });

    expect(toast.error).toHaveBeenCalledWith("Server error: 500");
    // Status should NOT change on failure — still shows Replay button
    const btnAfter = container.querySelector("[data-testid='replay-button']");
    expect(btnAfter).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — Status filter
// ---------------------------------------------------------------------------

describe("TimelineTab — Status filter", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("renders the status filter checkboxes", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const filterEl = container.querySelector("[data-testid='status-filter']");
    expect(filterEl).not.toBeNull();
    // Status filter chips use the exact badge vocabulary (bu-4utdw.5) —
    // "ingested", not the historical short-hand "ok".
    expect(filterEl!.textContent).toContain("ingested");
    expect(filterEl!.textContent).toContain("filtered");
    expect(filterEl!.textContent).toContain("error");
  });

  it("status filter chips share one vocabulary with the row status word (bu-4utdw.5)", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const filterEl = container.querySelector("[data-testid='status-filter']") as HTMLElement;
    for (const word of [
      "ingested",
      "skipped",
      "filtered",
      "error",
      "replay pending",
      "replay complete",
      "replay failed",
    ]) {
      expect(filterEl.textContent).toContain(word);
    }
    // The old, now-retired chip vocabulary must not survive anywhere in the toolbar.
    const toolbar = container.querySelector("[data-testid='timeline-toolbar']") as HTMLElement;
    expect(toolbar.textContent).not.toContain("ok");
    expect(toolbar.textContent).not.toContain("replayed");
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — every row is expandable (bu-4utdw.4)
//
// filtered/error rows used to be excluded from expansion entirely (their
// detail was tooltip-only). Now every row opens the drawer — filtered/error
// rows just also happen to render a Replay button in place of the chevron
// (they're both expandable AND replayable), while ingested/skipped/etc. rows
// show the chevron directly.
// ---------------------------------------------------------------------------

describe("TimelineTab — every row is expandable", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function render(events: IngestionEventSummary[]) {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("clicking a filtered row opens the drawer", () => {
    render([makeEvent({ id: "filtered-evt", status: "filtered", filter_reason: "rule matched" })]);

    expect(container.querySelector("[data-testid='event-drawer']")).toBeNull();

    const row = container.querySelector("[data-testid='ledger-row']") as HTMLElement;
    act(() => {
      row.click();
    });

    expect(container.querySelector("[data-testid='event-drawer']")).not.toBeNull();
  });

  it("clicking an error row opens the drawer", () => {
    render([makeEvent({ id: "error-evt", status: "error", error_detail: "boom" })]);

    expect(container.querySelector("[data-testid='event-drawer']")).toBeNull();

    const row = container.querySelector("[data-testid='ledger-row']") as HTMLElement;
    act(() => {
      row.click();
    });

    expect(container.querySelector("[data-testid='event-drawer']")).not.toBeNull();
  });

  it("ingested rows show the expand chevron", () => {
    render([makeEvent({ status: "ingested" })]);
    expect(container.textContent).toContain("▼");
  });

  it("all rows have aria-expanded reflecting drawer state", () => {
    render([makeEvent({ id: "aria-evt", status: "filtered" })]);
    const row = container.querySelector("[data-testid='ledger-row']") as HTMLElement;
    expect(row.getAttribute("aria-expanded")).toBe("false");

    act(() => {
      row.click();
    });

    expect(row.getAttribute("aria-expanded")).toBe("true");
  });

  it("pressing Enter on a focused row opens the drawer (keyboard access)", () => {
    render([makeEvent({ id: "kbd-evt", status: "ingested" })]);
    const row = container.querySelector("[data-testid='ledger-row']") as HTMLElement;
    expect(row.getAttribute("tabIndex") ?? row.tabIndex.toString()).toBeDefined();

    act(() => {
      row.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });

    expect(container.querySelector("[data-testid='event-drawer']")).not.toBeNull();
  });

  it("no ledger row renders a filled status pill (shadcn Badge slot)", () => {
    render([
      makeEvent({ id: "evt-1", status: "ingested" }),
      makeEvent({ id: "evt-2", status: "filtered" }),
      makeEvent({ id: "evt-3", status: "error" }),
      makeEvent({ id: "evt-4", status: "replay_pending" }),
    ]);

    const ledger = container.querySelector("[data-testid='timeline-ledger']") as HTMLElement;
    expect(ledger.querySelector("[data-slot='badge']")).toBeNull();
    // Every row instead renders the quiet dot+word status primitive.
    const statuses = ledger.querySelectorAll("[data-testid='row-status']");
    expect(statuses.length).toBe(4);
  });
});

// ---------------------------------------------------------------------------
// §2.5 Drawer additions — session index + copy button
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.5 Drawer: session index and copy button", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  const SESSION_ID = "bbbbbbbb-0000-0000-0000-000000000001";
  const SESSION_ID_2 = "cccccccc-0000-0000-0000-000000000002";

  function makeSessions(count: number) {
    return Array.from({ length: count }, (_, i) => ({
      id: i === 0 ? SESSION_ID : SESSION_ID_2,
      butler_name: `butler-${i + 1}`,
      trigger_source: null,
      started_at: "2026-01-01T10:00:00Z",
      completed_at: "2026-01-01T10:00:30Z",
      success: true,
      input_tokens: 100,
      output_tokens: 50,
      cost_usd: null,
      trace_id: null,
      model: "claude-sonnet",
    }));
  }

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("session table rows have id='session-<uuid>' anchors", () => {
    const sessions = makeSessions(1);
    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: sessions },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: { data: undefined, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: SESSION_ID, status: "ingested", source_sender_identity: null })]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/?event=${SESSION_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // The expanded row's session table should contain the anchor id
    const anchor = container.querySelector(`#session-${SESSION_ID}`);
    expect(anchor).not.toBeNull();
  });

  it("session index right rail renders when more than one session exists", () => {
    const sessions = makeSessions(2);
    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: sessions },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: { data: undefined, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: SESSION_ID, status: "ingested", source_sender_identity: null })]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/?event=${SESSION_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const sessionIndex = container.querySelector("[data-testid='drawer-session-index']");
    expect(sessionIndex).not.toBeNull();
  });

  it("session index renders even when only one session exists (drawer shows all sessions)", () => {
    const sessions = makeSessions(1);
    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: sessions },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: { data: undefined, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: SESSION_ID, status: "ingested", source_sender_identity: null })]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/?event=${SESSION_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Drawer renders session index even for single session (right rail navigation)
    const sessionIndex = container.querySelector("[data-testid='drawer-session-index']");
    expect(sessionIndex).not.toBeNull();
  });

  it("copy-session-id button is present for each session row", () => {
    const sessions = makeSessions(1);
    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: sessions },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: { data: undefined, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: SESSION_ID, status: "ingested", source_sender_identity: null })]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/?event=${SESSION_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // copy-button testid is used in the EventDrawer session blocks
    const copyBtn = container.querySelector("[data-testid='copy-button']");
    expect(copyBtn).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// §2.6 Drawer: sender identity resolution
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.6 Drawer: sender identity resolution", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  const EVENT_ID = "dddddddd-0000-0000-0000-000000000001";

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();

    vi.mocked(useIngestionEventRollup).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventRollup>);

    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: { data: { data: [] }, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: { data: undefined, isLoading: false, isError: false } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("shows resolved contact name in the ledger row when contact is resolved", () => {
    // bu-4utdw.3: sender_display is now a list-provided field (bulk-resolved
    // server-side), not a per-row useIngestionEventSenderContact hook result.
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({
          id: EVENT_ID,
          status: "ingested",
          source_sender_identity: "alice@example.com",
          sender_display: "Alice Smith",
        }),
      ]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/?event=${EVENT_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Resolved name appears in the ledger row sender column
    expect(container.textContent).toContain("Alice Smith");
  });

  it("shows raw sender identity in ledger row when contact is not resolved", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({
          id: EVENT_ID,
          status: "ingested",
          source_sender_identity: "unknown@example.com",
          sender_display: null,
        }),
      ]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Raw sender identity appears in the ledger row (sender_display is null)
    expect(container.textContent).toContain("unknown@example.com");
  });
});

// ---------------------------------------------------------------------------
// §2.8 Saved Views
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.8 Saved Views", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    // Clear localStorage before each test
    localStorage.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("renders the saved view selector with built-in views", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const selector = container.querySelector("[data-testid='saved-view-selector']");
    expect(selector).not.toBeNull();
    expect(selector!.textContent).toContain("All");
    expect(selector!.textContent).toContain("Errors");
    // "Priority" was a disabled "(soon)" placeholder — retired entirely
    // (bu-4utdw.5: roadmap is not UI).
    expect(selector!.textContent).not.toContain("Priority");
    expect(selector!.textContent).not.toContain("soon");
    expect(selector!.textContent).toContain("spend");
  });

  it("All view is active by default", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultViewId="all" />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const allBtn = container.querySelector("[data-view='all']");
    expect(allBtn).not.toBeNull();
    expect(allBtn!.getAttribute("aria-pressed")).toBe("true");
  });

  it("Priority view (disabled roadmap placeholder) was removed entirely (bu-4utdw.5)", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    expect(container.querySelector("[data-view='priority']")).toBeNull();
    const toolbar = container.querySelector("[data-testid='timeline-toolbar']") as HTMLElement;
    expect(toolbar.textContent).not.toContain("(soon)");
  });

  it("selecting Errors view updates aria-pressed", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultViewId="all" />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const errorsBtn = container.querySelector("[data-view='errors']") as HTMLButtonElement;
    expect(errorsBtn).not.toBeNull();

    act(() => {
      errorsBtn.click();
    });

    expect(errorsBtn.getAttribute("aria-pressed")).toBe("true");
    const allBtn = container.querySelector("[data-view='all']");
    expect(allBtn!.getAttribute("aria-pressed")).toBe("false");
  });

  it("Spend view is enabled (cost_usd now denormalized via core_126)", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const spendBtn = container.querySelector("[data-view='spend']");
    expect(spendBtn).not.toBeNull();
    // No longer a placeholder — cost_usd is now a real column (core_126)
    expect(spendBtn!.textContent).not.toContain("soon");
    // No disabled title
    expect(spendBtn!.getAttribute("title")).toBeNull();
  });

  it("persists active view to localStorage on selection", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const errorsBtn = container.querySelector("[data-view='errors']") as HTMLButtonElement;
    act(() => { errorsBtn.click(); });

    const stored = localStorage.getItem("ingestion-saved-views");
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored!).activeView).toBe("errors");
  });

  it("filters events by Errors view", () => {
    const events = [
      makeEvent({ id: "evt-1", status: "ingested" }),
      makeEvent({ id: "evt-2", status: "error" }),
      makeEvent({ id: "evt-3", status: "replay_failed" }),
    ];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultViewId="errors" />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // "Showing 2" — error + replay_failed
    expect(container.textContent).toContain("Showing 2");
  });
});

// ---------------------------------------------------------------------------
// §2.9 Connector Attention Strip
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.9 Connector Attention Strip", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  function makeConnector(overrides: Partial<{ connector_type: string; endpoint_identity: string; state: string; liveness: string; error_message: string | null }> = {}) {
    return {
      connector_type: "gmail",
      endpoint_identity: "inbox@example.com",
      liveness: "online",
      state: "healthy",
      error_message: null,
      version: null,
      uptime_s: null,
      last_heartbeat_at: null,
      first_seen_at: "2026-01-01T00:00:00Z",
      today: null,
      ...overrides,
    };
  }

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();

    vi.mocked(useIngestionEventRollup).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventRollup>);

    vi.mocked(useIngestionEventSenderContact).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventSenderContact>);

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("strip is hidden when all connectors are healthy", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: { data: [makeConnector()] },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useConnectorSummaries>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    expect(container.querySelector("[data-testid='connector-attention-strip']")).toBeNull();
  });

  it("strip is hidden when connector list is empty", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useConnectorSummaries>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    expect(container.querySelector("[data-testid='connector-attention-strip']")).toBeNull();
  });

  it("strip renders for connectors with state=error", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: {
        data: [
          makeConnector({ state: "healthy", liveness: "online" }),
          makeConnector({ connector_type: "telegram", endpoint_identity: "bot@t.me", state: "error", liveness: "online", error_message: "auth expired" }),
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useConnectorSummaries>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const strip = container.querySelector("[data-testid='connector-attention-strip']");
    expect(strip).not.toBeNull();
    expect(strip!.textContent).toContain("telegram");
    expect(strip!.textContent).toContain("bot@t.me");
  });

  it("strip renders for connectors with liveness=offline", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: {
        data: [
          makeConnector({ liveness: "offline", state: "healthy" }),
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useConnectorSummaries>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const strip = container.querySelector("[data-testid='connector-attention-strip']");
    expect(strip).not.toBeNull();
    const items = strip!.querySelectorAll("[data-testid='connector-attention-item']");
    expect(items.length).toBe(1);
  });

  it("shows multiple attention items when multiple connectors are unhealthy", () => {
    vi.mocked(useConnectorSummaries).mockReturnValue({
      data: {
        data: [
          makeConnector({ connector_type: "gmail", endpoint_identity: "a@example.com", state: "error" }),
          makeConnector({ connector_type: "gmail", endpoint_identity: "b@example.com", liveness: "offline" }),
          makeConnector({ connector_type: "telegram", endpoint_identity: "bot", state: "healthy", liveness: "online" }),
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useConnectorSummaries>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const items = container.querySelectorAll("[data-testid='connector-attention-item']");
    expect(items.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — BulkActionBar
// ---------------------------------------------------------------------------

describe("TimelineTab — BulkActionBar", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  const EVENT_ID_1 = "aabbccdd-0000-0000-0000-000000000001";
  const EVENT_ID_2 = "aabbccdd-0000-0000-0000-000000000002";

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  /** Render with a set of events; select the first N rows by clicking their checkboxes. */
  function renderAndSelectEvents(events: IngestionEventSummary[], selectCount: number) {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab
              isActive={true}
              defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Click checkboxes (the first child div of each ledger-row)
    const rows = container.querySelectorAll("[data-testid='ledger-row']");
    for (let i = 0; i < Math.min(selectCount, rows.length); i++) {
      const checkbox = rows[i].firstElementChild as HTMLElement;
      act(() => { checkbox.click(); });
    }
  }

  it("bar is hidden when no events are selected", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: EVENT_ID_1 })]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    expect(container.querySelector("[data-testid='bulk-action-bar']")).toBeNull();
  });

  it("bar appears and button is enabled when 1 event is selected", () => {
    renderAndSelectEvents([makeEvent({ id: EVENT_ID_1 })], 1);

    const bar = container.querySelector("[data-testid='bulk-action-bar']");
    expect(bar).not.toBeNull();
    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(false);
  });

  it("button is disabled when selected count exceeds 100", () => {
    // Build 101 events
    const events = Array.from({ length: 101 }, (_, i) =>
      makeEvent({ id: `aabbccdd-0000-0000-0000-${String(i).padStart(12, "0")}` }),
    );
    renderAndSelectEvents(events, 101);

    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBe(true);
    // Over-limit message shown
    const msg = container.querySelector("[data-testid='bulk-overlimit-msg']");
    expect(msg).not.toBeNull();
  });

  it("click calls bulkRetryEvents with selected IDs", async () => {
    vi.mocked(bulkRetryEvents).mockResolvedValueOnce({
      results: [{ event_id: EVENT_ID_1, status: "replay_pending" }],
      succeeded: 1,
      failed: 0,
    });

    renderAndSelectEvents(
      [makeEvent({ id: EVENT_ID_1 }), makeEvent({ id: EVENT_ID_2 })],
      1,
    );

    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    await act(async () => { btn.click(); });

    expect(bulkRetryEvents).toHaveBeenCalledWith([EVENT_ID_1]);
  });

  it("success path clears selection (bar disappears) and shows success toast", async () => {
    vi.mocked(bulkRetryEvents).mockResolvedValueOnce({
      results: [{ event_id: EVENT_ID_1, status: "replay_pending" }],
      succeeded: 1,
      failed: 0,
    });

    renderAndSelectEvents([makeEvent({ id: EVENT_ID_1 })], 1);

    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    await act(async () => { btn.click(); });

    // Bar should be gone (selection cleared)
    expect(container.querySelector("[data-testid='bulk-action-bar']")).toBeNull();
    // Success toast fired
    expect(toast.success).toHaveBeenCalledWith("1 event queued for replay");
  });

  it("error path surfaces error message inline without clearing selection", async () => {
    vi.mocked(bulkRetryEvents).mockRejectedValueOnce(new Error("Server error: 503"));

    renderAndSelectEvents([makeEvent({ id: EVENT_ID_1 })], 1);

    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    await act(async () => { btn.click(); });

    // Bar still visible (selection not cleared on error)
    expect(container.querySelector("[data-testid='bulk-action-bar']")).not.toBeNull();
    // Error message shown inline
    const errMsg = container.querySelector("[data-testid='bulk-error-msg']");
    expect(errMsg).not.toBeNull();
    expect(errMsg!.textContent).toContain("Server error: 503");
  });

  it("partial failure deselects only succeeded events and shows both success toast and error", async () => {
    vi.mocked(bulkRetryEvents).mockResolvedValueOnce({
      results: [
        { event_id: EVENT_ID_1, status: "replay_pending" },
        { event_id: EVENT_ID_2, status: "conflict", error: "Event is not retryable" },
      ],
      succeeded: 1,
      failed: 1,
    });

    renderAndSelectEvents(
      [makeEvent({ id: EVENT_ID_1 }), makeEvent({ id: EVENT_ID_2 })],
      2,
    );

    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    await act(async () => { btn.click(); });

    // Bar still visible — the failed event (EVENT_ID_2) remains selected
    expect(container.querySelector("[data-testid='bulk-action-bar']")).not.toBeNull();
    // Success toast for the succeeded event
    expect(toast.success).toHaveBeenCalledWith("1 event queued for replay");
    // Error shown inline and via toast for the failed event
    const errMsg = container.querySelector("[data-testid='bulk-error-msg']");
    expect(errMsg).not.toBeNull();
    expect(errMsg!.textContent).toContain("1 event failed to queue");
    expect(toast.error).toHaveBeenCalledWith("1 event failed to queue");
  });

  it("409 unsafe-channel rejection surfaces specific error message and toast", async () => {
    vi.mocked(bulkRetryEvents).mockRejectedValueOnce(
      new ApiError("UNSAFE_CHANNEL", "Batch contains replay-unsafe events", 409),
    );

    renderAndSelectEvents([makeEvent({ id: EVENT_ID_1, source_channel: "email" })], 1);

    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    await act(async () => { btn.click(); });

    // Bar still visible (selection not cleared on error)
    expect(container.querySelector("[data-testid='bulk-action-bar']")).not.toBeNull();
    // Specific unsafe-channel message in inline error
    const errMsg = container.querySelector("[data-testid='bulk-error-msg']");
    expect(errMsg).not.toBeNull();
    expect(errMsg!.textContent).toContain("email or replay-unsafe events");
    // Toast also fires with the same message
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("email or replay-unsafe events"),
    );
  });

  it("409 rejection with unsafe_events detail offers one-click deselect (bu-4utdw.5)", async () => {
    const unsafeId = EVENT_ID_1;
    vi.mocked(bulkRetryEvents).mockRejectedValueOnce(
      new ApiError("UNSAFE_CHANNEL", "Batch contains replay-unsafe events", 409, {
        error: "Batch contains replay-unsafe events",
        unsafe_events: [{ id: unsafeId, source_channel: "email", reason: "source_channel='email' is not replay-safe" }],
      }),
    );

    renderAndSelectEvents(
      [makeEvent({ id: EVENT_ID_1, source_channel: "email" }), makeEvent({ id: EVENT_ID_2 })],
      2,
    );

    const btn = container.querySelector("[data-testid='bulk-retry-button']") as HTMLButtonElement;
    await act(async () => { btn.click(); });

    const deselectBtn = container.querySelector(
      "[data-testid='bulk-deselect-ineligible-button']",
    ) as HTMLButtonElement;
    expect(deselectBtn).not.toBeNull();
    expect(deselectBtn.textContent).toContain("1");

    await act(async () => { deselectBtn.click(); });

    // The bar still shows the one remaining eligible selection.
    expect(container.textContent).toContain("1 selected");
    expect(container.querySelector("[data-testid='bulk-deselect-ineligible-button']")).toBeNull();
  });

  it("Copy IDs button copies selected event IDs to clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });

    renderAndSelectEvents(
      [makeEvent({ id: EVENT_ID_1 }), makeEvent({ id: EVENT_ID_2 })],
      2,
    );

    const copyBtn = container.querySelector(
      "[data-testid='bulk-copy-ids-button']",
    ) as HTMLButtonElement;
    expect(copyBtn).not.toBeNull();

    await act(async () => { copyBtn.click(); });

    // Should have called clipboard.writeText with newline-joined IDs
    expect(writeText).toHaveBeenCalledWith(`${EVENT_ID_1}\n${EVENT_ID_2}`);
    // Button text should change to "Copied" — no exclamation mark (voice
    // doctrine bans them, bu-4utdw.5).
    expect(copyBtn.textContent).toContain("Copied");
    expect(copyBtn.textContent).not.toContain("Copied!");
  });

  it("Copy IDs button shows error toast when Clipboard API is unavailable", async () => {
    // Simulate non-HTTPS context where navigator.clipboard is undefined.
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    renderAndSelectEvents([makeEvent({ id: EVENT_ID_1 })], 1);

    const copyBtn = container.querySelector(
      "[data-testid='bulk-copy-ids-button']",
    ) as HTMLButtonElement;
    expect(copyBtn).not.toBeNull();

    await act(async () => { copyBtn.click(); });

    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("Clipboard API not available"),
    );
    // Button should NOT show "Copied!" — copy did not succeed.
    expect(copyBtn.textContent).not.toContain("Copied!");
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — ineligible-status row selection guard (bu-7r2ev)
//
// Rows whose status makes them ineligible for bulk retry must:
//   1. render a disabled checkbox (data-testid="row-checkbox-disabled")
//   2. expose the ineligibility reason via aria-label or title
//   3. NOT be selectable — clicking their checkbox must not add them to the
//      selection, and the BulkActionBar must not appear after the click
//
// Ineligible statuses (mirrors backend ingestion_event_replay_request):
//   - replay_pending: already queued — backend returns "conflict"
//   - skipped:        skip-triaged — backend returns "conflict"
// ---------------------------------------------------------------------------

describe("TimelineTab — ineligible-status rows are non-selectable", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function renderEvent(event: IngestionEventSummary) {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([event]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab
              isActive={true}
              defaultStatuses={[
                "ingested", "filtered", "error",
                "replay_pending", "replay_complete", "replay_failed", "skipped",
              ]}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("replay_pending row has a disabled checkbox (data-testid=row-checkbox-disabled)", () => {
    renderEvent(makeEvent({ status: "replay_pending" }));
    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']");
    expect(disabledCb).not.toBeNull();
    // Should NOT have an enabled checkbox
    const enabledCb = container.querySelector("[data-testid='row-checkbox']");
    expect(enabledCb).toBeNull();
  });

  it("skipped row has a disabled checkbox (data-testid=row-checkbox-disabled)", () => {
    renderEvent(makeEvent({ status: "skipped" }));
    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']");
    expect(disabledCb).not.toBeNull();
  });

  it("replay_pending row disabled checkbox has aria-disabled=true", () => {
    renderEvent(makeEvent({ status: "replay_pending" }));
    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']");
    expect(disabledCb!.getAttribute("aria-disabled")).toBe("true");
  });

  it("skipped row disabled checkbox has aria-disabled=true", () => {
    renderEvent(makeEvent({ status: "skipped" }));
    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']");
    expect(disabledCb!.getAttribute("aria-disabled")).toBe("true");
  });

  it("replay_pending row checkbox surfaces ineligibility reason in aria-label", () => {
    renderEvent(makeEvent({ status: "replay_pending" }));
    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']");
    const label = disabledCb!.getAttribute("aria-label") ?? "";
    expect(label.length).toBeGreaterThan(0);
    // Should explain the reason (not a generic empty string)
    expect(label).not.toBe("Select event");
  });

  it("skipped row checkbox surfaces ineligibility reason in aria-label", () => {
    renderEvent(makeEvent({ status: "skipped" }));
    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']");
    const label = disabledCb!.getAttribute("aria-label") ?? "";
    expect(label.length).toBeGreaterThan(0);
    expect(label).not.toBe("Select event");
  });

  it("clicking a replay_pending row checkbox does NOT add it to selection (bar stays hidden)", () => {
    renderEvent(makeEvent({ id: "replay-pending-evt", status: "replay_pending" }));

    // Before click: bar hidden
    expect(container.querySelector("[data-testid='bulk-action-bar']")).toBeNull();

    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']") as HTMLElement;
    act(() => { disabledCb.click(); });

    // After click: bar still hidden — event was not added to selection
    expect(container.querySelector("[data-testid='bulk-action-bar']")).toBeNull();
  });

  it("clicking a skipped row checkbox does NOT add it to selection (bar stays hidden)", () => {
    renderEvent(makeEvent({ id: "skipped-evt", status: "skipped" }));

    expect(container.querySelector("[data-testid='bulk-action-bar']")).toBeNull();

    const disabledCb = container.querySelector("[data-testid='row-checkbox-disabled']") as HTMLElement;
    act(() => { disabledCb.click(); });

    expect(container.querySelector("[data-testid='bulk-action-bar']")).toBeNull();
  });

  it("eligible rows still have enabled checkboxes when mixed with ineligible ones", () => {
    const eligibleEvent = makeEvent({ id: "eligible-evt", status: "error" });
    const ineligibleEvent = makeEvent({ id: "ineligible-evt", status: "replay_pending" });

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([eligibleEvent, ineligibleEvent]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab
              isActive={true}
              defaultStatuses={["error", "replay_pending"]}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const enabledCbs = container.querySelectorAll("[data-testid='row-checkbox']");
    const disabledCbs = container.querySelectorAll("[data-testid='row-checkbox-disabled']");
    expect(enabledCbs.length).toBe(1);
    expect(disabledCbs.length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — failed-load retry button (bu-4utdw.4 honesty fix)
// ---------------------------------------------------------------------------

describe("TimelineTab — failed-load retry button", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("shows a retry button when the events query errors, which calls refetch on click", () => {
    const refetch = vi.fn();
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch,
    } as unknown as ReturnType<typeof useIngestionEvents>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    expect(container.textContent).toContain("Failed to load ingestion events.");
    const retryBtn = container.querySelector("[data-testid='events-retry-button']") as HTMLButtonElement;
    expect(retryBtn).not.toBeNull();

    act(() => {
      retryBtn.click();
    });

    expect(refetch).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — sender title attribute (bu-4utdw.4 honesty fix)
// ---------------------------------------------------------------------------

describe("TimelineTab — sender cell title attribute", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("sender cell exposes the resolved name via a title attribute", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ source_sender_identity: "alice@example.com", sender_display: "Alice Smith" }),
      ]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const senderEl = Array.from(container.querySelectorAll("span")).find(
      (el) => el.textContent === "Alice Smith",
    );
    expect(senderEl).toBeDefined();
    expect(senderEl!.getAttribute("title")).toBe("Alice Smith");
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — selection checkbox column demotion (bu-4utdw.4)
//
// The checkbox column is hidden by default (opacity-0, revealed on
// hover/focus) and forced visible once selection mode is active: the
// built-in "Errors only" view, or ≥1 row already selected.
// ---------------------------------------------------------------------------

describe("TimelineTab — selection checkbox column demotion", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("checkbox is visually demoted (opacity-0) when no view/selection forces it visible", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ status: "ingested" })]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultViewId="all" />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const checkbox = container.querySelector("[data-testid='row-checkbox']") as HTMLElement;
    expect(checkbox.className).toContain("opacity-0");
  });

  it("checkbox is forced visible (opacity-100) when the Errors view is active", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ status: "error" })]) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultViewId="errors" />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const checkbox = container.querySelector("[data-testid='row-checkbox']") as HTMLElement;
    expect(checkbox.className).toContain("opacity-100");
  });

  it("checkbox becomes forced-visible on every row once one row is selected (shift-click enters selection mode)", () => {
    const events = [
      makeEvent({ id: "evt-a", status: "ingested" }),
      makeEvent({ id: "evt-b", status: "ingested" }),
    ];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultViewId="all" />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const rows = container.querySelectorAll("[data-testid='ledger-row']");
    // Shift-click the first row's body (not the checkbox) — enters selection
    // mode without opening the drawer.
    act(() => {
      rows[0].dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, shiftKey: true }));
    });

    expect(container.querySelector("[data-testid='event-drawer']")).toBeNull();
    const checkboxes = container.querySelectorAll("[data-testid='row-checkbox']");
    checkboxes.forEach((cb) => expect(cb.className).toContain("opacity-100"));
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — BulkActionBar select-all-visible (bu-4utdw.4)
// ---------------------------------------------------------------------------

describe("TimelineTab — BulkActionBar select-all-visible", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  const EVENT_ID_1 = "aabbccdd-0000-0000-0000-000000000001";
  const EVENT_ID_2 = "aabbccdd-0000-0000-0000-000000000002";

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("selecting all visible expands the selection to every eligible visible row (capped at 100)", () => {
    const events = [
      makeEvent({ id: EVENT_ID_1, status: "ingested" }),
      makeEvent({ id: EVENT_ID_2, status: "error" }),
    ];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab
              isActive={true}
              defaultStatuses={["ingested", "error"]}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Select just the first row to open the bulk bar.
    const checkboxes = container.querySelectorAll("[data-testid='row-checkbox']");
    act(() => {
      (checkboxes[0] as HTMLElement).click();
    });

    const selectAllBtn = container.querySelector(
      "[data-testid='bulk-select-all-visible-button']",
    ) as HTMLButtonElement;
    expect(selectAllBtn).not.toBeNull();
    expect(selectAllBtn.textContent).toContain("2");

    act(() => {
      selectAllBtn.click();
    });

    expect(container.querySelector("[data-testid='bulk-action-bar']")!.textContent).toContain("2 selected");
  });

  it("still shows select-all-visible when a selected id falls outside the current view", () => {
    // Regression for a visibility bug: the button used to be gated on
    // `visibleEligibleIds.length > selectedCount`, so a stale selection made
    // under a previous filter (an id no longer in the current view) could
    // make `selectedCount` >= the visible count even though a visible,
    // unselected, eligible row still exists — incorrectly hiding the button.
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: EVENT_ID_1, status: "ingested" })]) as unknown as ReturnType<
        typeof useIngestionEvents
      >,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "error"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    act(() => {
      (container.querySelector("[data-testid='row-checkbox']") as HTMLElement).click();
    });

    // Simulate a filter change: the previously-selected event scrolls out of
    // view and a different, unselected eligible event takes its place.
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: EVENT_ID_2, status: "ingested" })]) as unknown as ReturnType<
        typeof useIngestionEvents
      >,
    );

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "error"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const selectAllBtn = container.querySelector(
      "[data-testid='bulk-select-all-visible-button']",
    ) as HTMLButtonElement | null;
    expect(selectAllBtn).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — ?trace= filter (drill-down spine, bu-86c4c.3)
//
// Landing on the timeline with ?trace=<id> (from SessionDetailDrawer's
// "Trace ID" link or notification-feed's "Trace" link — both used to discard
// the trace) must:
//   - forward trace_id to useIngestionEvents (SQL pushdown, not a client filter)
//   - NOT clip the query to the range picker's window (the traced event may
//     be older than the default range)
//   - render a "Scoped to trace" banner the owner can clear
//   - clearing the banner drops trace_id from the next query
// ---------------------------------------------------------------------------

describe("TimelineTab — ?trace= drill-down spine filter", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function renderWithTrace(traceId: string) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/?trace=${encodeURIComponent(traceId)}`]}>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("forwards trace_id to useIngestionEvents and omits the range window bound", () => {
    renderWithTrace("trace-abc-123");
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).toMatchObject({ trace_id: "trace-abc-123" });
    expect(lastFilters).not.toHaveProperty("from");
    expect(lastFilters).not.toHaveProperty("to");
  });

  it("omits trace_id when no ?trace= param is present", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).not.toHaveProperty("trace_id");
    // Without a trace, the range window bound is present as usual.
    expect(lastFilters).toHaveProperty("from");
  });

  it("renders a 'Scoped to trace' banner naming the trace", () => {
    renderWithTrace("trace-abc-123");
    expect(container.querySelector("[data-testid='trace-scope-banner']")?.textContent).toContain(
      "trace-abc-123",
    );
  });

  it("clearing the trace banner drops trace_id from the next query", () => {
    renderWithTrace("trace-abc-123");

    act(() => {
      (
        container.querySelector("[data-testid='trace-scope-clear']") as HTMLButtonElement
      ).click();
    });

    expect(container.querySelector("[data-testid='trace-scope-banner']")).toBeNull();
    const calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0];
    expect(lastFilters).not.toHaveProperty("trace_id");
  });

  it("shows a trace-scoped event even with a status hidden by the default 'all' view", () => {
    // "filtered" is hidden by DEFAULT_STATUSES / the "all" built-in view —
    // a trace-scoped landing must still surface it, not silently swallow the
    // very hop the trace link promised to land on.
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ id: "aabbccdd-0000-0000-0000-0000000000f1", status: "filtered" }),
      ]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    renderWithTrace("trace-abc-123");
    expect(container.querySelector("[data-event-id='aabbccdd-0000-0000-0000-0000000000f1']")).not.toBeNull();
  });

  it("reverts to the default view's narrower statuses after the trace is cleared", () => {
    // Regression: the built-in-view-baseline effect used to mark
    // appliedBuiltInViewRef as "applied" during the trace-scoped mount even
    // though it skipped the actual setEnabledStatuses call (guarded by
    // `if (urlTrace) return`) — so when the trace was later cleared, the
    // effect's ref-equality check short-circuited and enabledStatuses stayed
    // stuck on ALL_STATUSES forever, silently un-hiding "filtered"/"skipped"
    // rows even after the owner left the trace-scoped view.
    renderWithTrace("trace-abc-123");

    // While trace-scoped: no `statuses` param (ALL_STATUSES == no filter).
    let calls = vi.mocked(useIngestionEvents).mock.calls;
    expect(calls[calls.length - 1][0]).not.toHaveProperty("statuses");

    act(() => {
      (
        container.querySelector("[data-testid='trace-scope-clear']") as HTMLButtonElement
      ).click();
    });

    // After clearing: reverts to the default view's statuses, which excludes
    // "filtered" and "skipped".
    calls = vi.mocked(useIngestionEvents).mock.calls;
    const lastFilters = calls[calls.length - 1][0] as { statuses?: string };
    expect(lastFilters.statuses).toBeDefined();
    expect(lastFilters.statuses).not.toContain("filtered");
    expect(lastFilters.statuses).not.toContain("skipped");
  });
});
