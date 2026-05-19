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

import type { IngestionEventSummary } from "@/api/index.ts";

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
}));

// Mock the connector summaries hook (§2.9 — ConnectorAttentionStrip)
vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
}));

import { replayIngestionEvent } from "@/api/index.ts";
import { toast } from "sonner";
import {
  useIngestionEvents,
  useIngestionEventLineage,
  useIngestionEventRollup,
  useIngestionEventSenderContact,
  useIngestionEventSessions,
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

  // Default: no connector issues (strip hidden)
  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: { data: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useConnectorSummaries>);
}

// We test ActionCell indirectly through TimelineTab since it's not exported.
import { TimelineTab } from "./TimelineTab";

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

  it("shows Replay button for ingested events", () => {
    render([makeEvent({ status: "ingested" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute("title")).toBe("Replay");
  });

  it("shows Replay button for replay_complete events", () => {
    render([makeEvent({ status: "replay_complete" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute("title")).toBe("Replay");
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
    // Should have checkbox labels for each status
    expect(filterEl!.textContent).toContain("Ingested");
    expect(filterEl!.textContent).toContain("Filtered");
    expect(filterEl!.textContent).toContain("Error");
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
          <MemoryRouter initialEntries={[`/?expanded=${SESSION_ID}`]}>
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
          <MemoryRouter initialEntries={[`/?expanded=${SESSION_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const sessionIndex = container.querySelector("[data-testid='session-index']");
    expect(sessionIndex).not.toBeNull();
  });

  it("session index does not render when only one session exists", () => {
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
          <MemoryRouter initialEntries={[`/?expanded=${SESSION_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const sessionIndex = container.querySelector("[data-testid='session-index']");
    expect(sessionIndex).toBeNull();
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
          <MemoryRouter initialEntries={[`/?expanded=${SESSION_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const copyBtn = container.querySelector("[data-testid='copy-session-id']");
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

  it("shows resolved contact name when contact is resolved", () => {
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
          <MemoryRouter initialEntries={[`/?expanded=${EVENT_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    expect(container.textContent).toContain("Alice Smith");
    // Unresolved indicator should NOT appear
    const unresolvedEl = container.querySelector("[data-testid='sender-unresolved']");
    expect(unresolvedEl).toBeNull();
  });

  it("shows unresolved indicator when contact is not found", () => {
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
          <MemoryRouter initialEntries={[`/?expanded=${EVENT_ID}`]}>
            <TimelineTab isActive={true} defaultStatuses={["ingested"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const unresolvedEl = container.querySelector("[data-testid='sender-unresolved']");
    expect(unresolvedEl).not.toBeNull();
    expect(unresolvedEl!.textContent).toContain("unknown@example.com");
    expect(unresolvedEl!.textContent).toContain("unresolved");
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
