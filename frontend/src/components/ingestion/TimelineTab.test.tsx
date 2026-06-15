// @vitest-environment jsdom
/**
 * Tests for TimelineTab component.
 *
 * Covers:
 * - StatusBadge rendering per event status
 * - Replay button states (filtered/error/replay_failed/replay_pending/ingested/replay_complete)
 * - Optimistic update on Replay click + override eviction
 * - Error toast on replay failure
 * - Status filter checkboxes
 * - Non-expandable rows (filtered/error)
 * - §2.5 Drawer: session anchor IDs, session index rail, copy-session-id button
 * - §2.6 Sender identity resolution (resolved / unresolved)
 * - §2.8 Saved Views: selector renders, view changes apply statuses, Priority is placeholder
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
  useIngestionWindowRollup: vi.fn(),
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
  useIngestionWindowRollup,
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
// TimelineTab — StatusBadge rendering
// ---------------------------------------------------------------------------

describe("TimelineTab — StatusBadge rendering", () => {
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
    expect(container.textContent).toContain("replay pending");

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

    // Badge should now show "replayed" (the label for replay_complete), not the stale "replay pending"
    expect(container.textContent).toContain("replayed");
    expect(container.textContent).not.toContain("replay pending");
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
    // Status filter buttons use short Dispatch-language labels
    expect(filterEl!.textContent).toContain("ok");
    expect(filterEl!.textContent).toContain("filtered");
    expect(filterEl!.textContent).toContain("error");
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — filtered events non-expandable
// ---------------------------------------------------------------------------

describe("TimelineTab — filtered events non-expandable", () => {
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

  it("filtered rows have no expand chevron", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ status: "filtered" })]) as unknown as ReturnType<typeof useIngestionEvents>,
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

    // Chevron characters ▼/▲ should not be present for filtered rows
    expect(container.textContent).not.toContain("▼");
    expect(container.textContent).not.toContain("▲");
  });

  it("error rows have no expand chevron", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ status: "error" })]) as unknown as ReturnType<typeof useIngestionEvents>,
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

    expect(container.textContent).not.toContain("▼");
    expect(container.textContent).not.toContain("▲");
  });

  it("ingested rows have expand chevron", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ status: "ingested" })]) as unknown as ReturnType<typeof useIngestionEvents>,
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

    expect(container.textContent).toContain("▼");
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
      cost: null,
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
    vi.mocked(useIngestionEventSenderContact).mockReturnValue({
      data: { data: { resolved: true, name: "Alice Smith", raw: "alice@example.com" } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventSenderContact>);

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: EVENT_ID, status: "ingested", source_sender_identity: "alice@example.com" })]) as unknown as ReturnType<typeof useIngestionEvents>,
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
    vi.mocked(useIngestionEventSenderContact).mockReturnValue({
      data: { data: { resolved: false, name: null, raw: "unknown@example.com" } },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionEventSenderContact>);

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([makeEvent({ id: EVENT_ID, status: "ingested", source_sender_identity: "unknown@example.com" })]) as unknown as ReturnType<typeof useIngestionEvents>,
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

    // Raw sender identity appears in the ledger row (resolver returned resolved=false)
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
    expect(selector!.textContent).toContain("Priority");
    expect(selector!.textContent).toContain("Spend");
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

  it("Priority view is marked as a placeholder with '(soon)' hint", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const priorityBtn = container.querySelector("[data-view='priority']");
    expect(priorityBtn).not.toBeNull();
    // Placeholder hint visible to users
    expect(priorityBtn!.textContent).toContain("soon");
    // Title attribute explains the placeholder status
    expect(priorityBtn!.getAttribute("title")).toContain("Wave 2");
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

    const spendBtn = container.querySelector("[data-view='spend']") as HTMLButtonElement;
    act(() => { spendBtn.click(); });

    const stored = localStorage.getItem("ingestion-saved-views");
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored!).activeView).toBe("spend");
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
    // Button text should change to "Copied!"
    expect(copyBtn.textContent).toContain("Copied!");
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
