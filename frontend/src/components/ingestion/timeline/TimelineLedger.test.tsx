// @vitest-environment jsdom
/**
 * Unit tests for the Timeline ledger and drawer (bu-y25mj.4).
 *
 * Covers:
 * - Hour-grouping: events split into correct hour buckets
 * - Hour strip: honest histogram-sourced counts, minute click routes to
 *   scroll-in-ledger or scope-to-minute (bu-4utdw.7; HourFlameStrip's own
 *   rendering/stacking/a11y is unit-tested in HourFlameStrip.test.tsx)
 * - Range filter: toolbar range buttons write to URL state
 * - Status filter: chips narrow event list
 * - Drawer: opens when ?event=<id> is in URL
 * - Drawer: closes and clears ?event on dismiss
 * - Drawer raw payload: gated/unavailable state renders cleanly on 403
 * - Drawer session index: renders for opened event
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
}));

import {
  useIngestionEvents,
  useIngestionEventLineage,
  useIngestionEventRollup,
  useIngestionEventSenderContact,
  useIngestionEventReplays,
  useIngestionEventPayload,
  useIngestionEventDetail,
  useIngestionEventSessions,
  useIngestionWindowRollup,
  useIngestionEventsHistogram,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import { TimelineTab } from "../TimelineTab";

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

function makeSessions(n = 1): IngestionEventSession[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `ssssssss-0000-0000-0000-${String(i + 1).padStart(12, "0")}`,
    butler_name: `butler-${i + 1}`,
    trigger_source: null,
    started_at: "2026-05-17T10:30:00Z",
    completed_at: "2026-05-17T10:30:30Z",
    success: true,
    input_tokens: 100,
    output_tokens: 50,
    cost_usd: null,
    trace_id: null,
    model: "claude-sonnet",
  }));
}

function makeHistogramCounts(overrides: Record<string, number> = {}) {
  return {
    ingested: 0,
    skipped: 0,
    filtered: 0,
    error: 0,
    failed: 0,
    replay_pending: 0,
    replay_complete: 0,
    replay_failed: 0,
    ...overrides,
  };
}

function makeHistogramBucket(ts: string, overrides: Record<string, number> = {}) {
  return { ts, counts: makeHistogramCounts(overrides) };
}

function makeHistogramResult(buckets: ReturnType<typeof makeHistogramBucket>[] = []) {
  return {
    data: { buckets, bucket: "1m" },
    isLoading: false,
    isError: false,
  };
}

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

  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: { data: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useConnectorSummaries>);

  vi.mocked(useIngestionWindowRollup).mockReturnValue({
    data: { events: 0, sessions: 0, cost: null, window: { from: null, to: null } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionWindowRollup>);

  vi.mocked(useIngestionEventsHistogram).mockReturnValue(
    makeHistogramResult([]) as unknown as ReturnType<typeof useIngestionEventsHistogram>,
  );
}

// ---------------------------------------------------------------------------
// TimelineTab — hour grouping and hour strip (bu-4utdw.7)
//
// HourFlameStrip's own rendering/stacking/a11y is unit-tested in
// HourFlameStrip.test.tsx. These tests cover the TimelineTab-level wiring:
// hour bucketing, honest histogram-sourced header counts, and the
// scroll-vs-scope minute click decision.
// ---------------------------------------------------------------------------

describe("TimelineTab — hour grouping", () => {
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

  it("groups events in the same hour under a single hour-group header", () => {
    const events = [
      makeEvent({ id: "id-1", received_at: "2026-05-17T14:05:00Z" }),
      makeEvent({ id: "id-2", received_at: "2026-05-17T14:45:00Z" }),
    ];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    vi.mocked(useIngestionEventsHistogram).mockReturnValue(
      makeHistogramResult([
        makeHistogramBucket("2026-05-17T14:05:00Z", { ingested: 1 }),
        makeHistogramBucket("2026-05-17T14:45:00Z", { ingested: 1 }),
      ]) as unknown as ReturnType<typeof useIngestionEventsHistogram>,
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

    const groups = container.querySelectorAll("[data-testid='hour-group']");
    expect(groups).toHaveLength(1);
    // Both events under one group = "2 events" in header, sourced from the histogram.
    expect(groups[0].textContent).toContain("2 events");
  });

  it("splits events in different hours into separate hour-group headers", () => {
    const events = [
      makeEvent({ id: "id-1", received_at: "2026-05-17T14:05:00Z" }),
      makeEvent({ id: "id-2", received_at: "2026-05-17T15:05:00Z" }),
    ];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
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

    const groups = container.querySelectorAll("[data-testid='hour-group']");
    expect(groups).toHaveLength(2);
  });

  it("renders hour-strip minute buttons inside each hour group", () => {
    const events = [makeEvent({ id: "id-1", received_at: "2026-05-17T14:05:00Z" })];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
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

    const group = container.querySelector("[data-testid='hour-group']");
    expect(group).not.toBeNull();
    const minuteButtons = group!.querySelectorAll("[data-testid='hour-strip-minute']");
    expect(minuteButtons).toHaveLength(60);
  });

  it("header counts reflect histogram truth even when fewer pages have loaded", () => {
    // Only 1 event loaded for the hour, but the histogram (independent of
    // ledger pagination) reports the true totals for that hour.
    const events = [makeEvent({ id: "id-1", received_at: "2026-05-17T14:05:00Z" })];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    vi.mocked(useIngestionEventsHistogram).mockReturnValue(
      makeHistogramResult([
        makeHistogramBucket("2026-05-17T14:05:00Z", { ingested: 206, error: 6 }),
        makeHistogramBucket("2026-05-17T14:10:00Z", { replay_pending: 2 }),
      ]) as unknown as ReturnType<typeof useIngestionEventsHistogram>,
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

    const summary = container.querySelector("[data-testid='hour-group-summary']");
    expect(summary).not.toBeNull();
    expect(summary!.textContent).toContain("214 events");
    expect(summary!.textContent).toContain("6 errors");
    expect(summary!.textContent).toContain("2 replays");
  });

  it("header counts fold 'failed' (routing failure after ingestion, bu-lkzsf.1) into the honest event and error totals", () => {
    // A "failed" event was already ingested (backend histogram counts it
    // separately from "error", see _HISTOGRAM_STATUSES), but the header must
    // still count it as both an event and a trouble signal — it must never
    // silently vanish from the honest total (bu-lkzsf epic: "failure never
    // impersonates health").
    const events = [makeEvent({ id: "id-1", received_at: "2026-05-17T14:05:00Z" })];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    vi.mocked(useIngestionEventsHistogram).mockReturnValue(
      makeHistogramResult([
        makeHistogramBucket("2026-05-17T14:05:00Z", { ingested: 10, error: 1, failed: 3 }),
      ]) as unknown as ReturnType<typeof useIngestionEventsHistogram>,
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

    const summary = container.querySelector("[data-testid='hour-group-summary']");
    expect(summary).not.toBeNull();
    expect(summary!.textContent).toContain("14 events");
    expect(summary!.textContent).toContain("4 errors");
  });

  it("clicking a strip minute with a loaded ledger row scrolls it into view", () => {
    const scrollIntoViewMock = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoViewMock;

    const events = [makeEvent({ id: "id-1", received_at: "2026-05-17T14:10:00Z" })];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    vi.mocked(useIngestionEventsHistogram).mockReturnValue(
      makeHistogramResult([
        makeHistogramBucket("2026-05-17T14:10:00Z", { ingested: 1 }),
      ]) as unknown as ReturnType<typeof useIngestionEventsHistogram>,
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

    const minuteButton = container.querySelector(
      "[data-testid='hour-strip-minute'][data-minute-iso='2026-05-17T14:10:00.000Z']",
    ) as HTMLElement;
    expect(minuteButton).not.toBeNull();

    act(() => {
      minuteButton.click();
    });

    expect(scrollIntoViewMock).toHaveBeenCalled();
    expect(container.querySelector("[data-testid='minute-scope-banner']")).toBeNull();
  });

  it("clicking a strip minute with no loaded ledger row scopes the ledger to that minute", () => {
    // The only loaded event is at 14:05; the histogram reports an error-only
    // minute at 14:10 that has not (yet) loaded into the ledger.
    const events = [makeEvent({ id: "id-1", received_at: "2026-05-17T14:05:00Z", status: "ingested" })];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    vi.mocked(useIngestionEventsHistogram).mockReturnValue(
      makeHistogramResult([
        makeHistogramBucket("2026-05-17T14:05:00Z", { ingested: 1 }),
        makeHistogramBucket("2026-05-17T14:10:00Z", { error: 1 }),
      ]) as unknown as ReturnType<typeof useIngestionEventsHistogram>,
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

    const minuteButton = container.querySelector(
      "[data-testid='hour-strip-minute'][data-minute-iso='2026-05-17T14:10:00.000Z']",
    ) as HTMLElement;
    expect(minuteButton).not.toBeNull();
    expect(minuteButton.getAttribute("data-has-error")).toBe("true");

    act(() => {
      minuteButton.click();
    });

    // Scoping is URL-backed: the banner appears, and the events query is
    // re-issued with a from/to window collapsed to that exact minute.
    const banner = container.querySelector("[data-testid='minute-scope-banner']");
    expect(banner).not.toBeNull();

    const lastCall = vi.mocked(useIngestionEvents).mock.calls.at(-1);
    expect(lastCall?.[0]).toMatchObject({
      from: "2026-05-17T14:10:00.000Z",
      to: "2026-05-17T14:11:00.000Z",
    });
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — status filter
// ---------------------------------------------------------------------------

describe("TimelineTab — status filter narrows event list", () => {
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

  it("shows only ingested events when defaultStatuses=['ingested']", () => {
    const events = [
      makeEvent({ id: "id-ingested", received_at: "2026-05-17T14:05:00Z", status: "ingested" }),
      makeEvent({ id: "id-error", received_at: "2026-05-17T14:06:00Z", status: "error" }),
    ];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
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

    // Only 1 event should appear in the ledger
    const rows = container.querySelectorAll("[data-testid='ledger-row']");
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute("data-event-id")).toBe("id-ingested");
  });

  it("shows all events when all statuses are enabled", () => {
    const events = [
      makeEvent({ id: "id-1", received_at: "2026-05-17T14:05:00Z", status: "ingested" }),
      makeEvent({ id: "id-2", received_at: "2026-05-17T14:06:00Z", status: "error" }),
      makeEvent({ id: "id-3", received_at: "2026-05-17T14:07:00Z", status: "filtered" }),
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
              defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const rows = container.querySelectorAll("[data-testid='ledger-row']");
    expect(rows).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — drawer opens on ?event=<id>
// ---------------------------------------------------------------------------

describe("TimelineTab — drawer URL state", () => {
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
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("opens the drawer when ?event=<id> is in the URL", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ id: EVENT_ID, received_at: "2026-05-17T14:05:00Z", status: "ingested" }),
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

    const drawer = container.querySelector("[data-testid='event-drawer']");
    expect(drawer).not.toBeNull();
  });

  it("does NOT open a drawer when no ?event param is set", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ id: EVENT_ID, received_at: "2026-05-17T14:05:00Z", status: "ingested" }),
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

    const drawer = container.querySelector("[data-testid='event-drawer']");
    expect(drawer).toBeNull();
  });

  it("closing the drawer removes the event from the page", () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ id: EVENT_ID, received_at: "2026-05-17T14:05:00Z", status: "ingested" }),
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

    // Drawer should be open
    let drawer = container.querySelector("[data-testid='event-drawer']");
    expect(drawer).not.toBeNull();

    // Click the close button
    const closeBtn = container.querySelector("[data-testid='drawer-close-button']");
    expect(closeBtn).not.toBeNull();

    act(() => {
      (closeBtn as HTMLElement).click();
    });

    // Drawer should be gone
    drawer = container.querySelector("[data-testid='event-drawer']");
    expect(drawer).toBeNull();
  });

  it("shows drawer session index when event has sessions", () => {
    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: makeSessions(2) },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: {
        data: undefined,
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ id: EVENT_ID, received_at: "2026-05-17T14:05:00Z", status: "ingested" }),
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

    const sessionIndex = container.querySelector("[data-testid='drawer-session-index']");
    expect(sessionIndex).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Drawer — raw payload gated/unavailable state
// ---------------------------------------------------------------------------

describe("EventDrawer — raw payload tab gated state", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  const EVENT_ID = "eeeeeeee-0000-0000-0000-000000000001";

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
    // Clear sessionStorage so drawer tab starts fresh (not affected by other tests)
    sessionStorage.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("shows gated state when payload API returns 403", async () => {
    const { ApiError } = await import("@/api/index.ts");
    vi.mocked(useIngestionEventPayload).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      // ApiError(code, message, status) — status=403 triggers the gated state
      error: new ApiError("FORBIDDEN", "Payload access denied", 403),
    } as unknown as ReturnType<typeof useIngestionEventPayload>);

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ id: EVENT_ID, received_at: "2026-05-17T14:05:00Z", status: "ingested" }),
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

    // Open the drawer
    const drawer = container.querySelector("[data-testid='event-drawer']");
    expect(drawer).not.toBeNull();

    // Click the raw payload tab to enable it
    const rawTab = container.querySelector("[data-testid='drawer-tab-raw']");
    expect(rawTab).not.toBeNull();

    act(() => {
      (rawTab as HTMLElement).click();
    });

    // After clicking, rawEnabled=true → DrawerRawTab receives enabled=true
    // and since useIngestionEventPayload returns isError+403, it renders gated state.
    const gatedEl = container.querySelector("[data-testid='raw-tab-gated']");
    expect(gatedEl).not.toBeNull();
    expect(gatedEl!.textContent).toContain("disabled for this session");
    // Single-owner product: no multi-tenant "administrator" framing (bu-4utdw.11).
    expect(gatedEl!.textContent).not.toContain("administrator");
  });
});

// ---------------------------------------------------------------------------
// bu-rncqs: Flamegraph in-progress span clamping
// ---------------------------------------------------------------------------

describe("EventDrawer — flamegraph in-progress span clamping (bu-rncqs)", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  const EVENT_ID = "ffff0001-0000-0000-0000-000000000001";

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
    setupDefaultMocks();
    sessionStorage.clear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it("renders flamegraph bars for in-progress sessions with width <= 100%", () => {
    // A completed session and an in-progress session (completed_at = null).
    // The in-progress session's bar must not overflow the flamegraph container.
    const completedSession: IngestionEventSession = {
      id: "sess-0001-0000-0000-0000-000000000001",
      butler_name: "butler-a",
      trigger_source: null,
      started_at: "2026-05-17T10:30:00Z",
      completed_at: "2026-05-17T10:30:30Z",
      success: true,
      input_tokens: 100,
      output_tokens: 50,
      cost_usd: null,
      trace_id: null,
      model: "claude-sonnet",
    };
    const inProgressSession: IngestionEventSession = {
      id: "sess-0002-0000-0000-0000-000000000002",
      butler_name: "butler-a",
      trigger_source: null,
      started_at: "2026-05-17T10:30:05Z",
      completed_at: null, // in-progress — no end time
      success: null,
      input_tokens: null,
      output_tokens: null,
      cost_usd: null,
      trace_id: null,
      model: "claude-sonnet",
    };

    vi.mocked(useIngestionEventLineage).mockReturnValue({
      sessions: {
        data: { data: [completedSession, inProgressSession] },
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventSessions>,
      rollup: {
        data: undefined,
        isLoading: false,
        isError: false,
      } as unknown as ReturnType<typeof useIngestionEventRollup>,
    });

    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([
        makeEvent({ id: EVENT_ID, received_at: "2026-05-17T10:30:00Z", status: "ingested" }),
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

    // The drawer sessions tab should be visible
    const sessionsContent = container.querySelector("[data-testid='sessions-tab-content']");
    expect(sessionsContent).not.toBeNull();

    // All flamegraph bar widths must be <= 100%
    const flamegraphLinks = sessionsContent!.querySelectorAll(".absolute.rounded-sm");
    expect(flamegraphLinks.length).toBeGreaterThan(0);
    for (const link of Array.from(flamegraphLinks)) {
      const width = parseFloat((link as HTMLElement).style.width ?? "0");
      expect(width).toBeLessThanOrEqual(100);
    }
  });
});

// ---------------------------------------------------------------------------
// bu-mxtn2: Search input — toolbar renders search input and clear button
// ---------------------------------------------------------------------------

describe("TimelineTab — search input (bu-mxtn2)", () => {
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
    sessionStorage.clear();
  });

  it("renders the search input in the toolbar", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const searchInput = container.querySelector("[data-testid='search-input']");
    expect(searchInput).not.toBeNull();
    expect((searchInput as HTMLInputElement).type).toBe("search");
  });

  it("shows clear button when search query is pre-populated via URL param", () => {
    // Initialize with ?q=alice in the URL so searchInputValue starts non-empty
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/?q=alice"]}>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Search input should have the value from URL
    const searchInput = container.querySelector(
      "[data-testid='search-input']",
    ) as HTMLInputElement;
    expect(searchInput.value).toBe("alice");

    // Clear button should appear when value is present
    const clearBtn = container.querySelector("[data-testid='search-clear']");
    expect(clearBtn).not.toBeNull();
  });

  it("does not show clear button when search is empty", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/"]}>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // No clear button when empty
    expect(container.querySelector("[data-testid='search-clear']")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// bu-mxtn2: Channel chips — chips render and fire remove on click
// ---------------------------------------------------------------------------

describe("TimelineTab — channel filter chips (bu-mxtn2)", () => {
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
    sessionStorage.clear();
  });

  it("renders no channel chips when channels URL param is absent", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/"]}>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const chips = container.querySelector("[data-testid='channel-chips']");
    expect(chips).toBeNull();
  });

  it("renders channel chips when channels URL param is set", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/?channels=email,telegram"]}>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const chips = container.querySelector("[data-testid='channel-chips']");
    expect(chips).not.toBeNull();

    const emailChip = container.querySelector("[data-testid='channel-chip-email']");
    expect(emailChip).not.toBeNull();

    const telegramChip = container.querySelector("[data-testid='channel-chip-telegram']");
    expect(telegramChip).not.toBeNull();
  });

  it("clicking a channel chip removes that channel from the filter", () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/?channels=email,telegram"]}>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const emailChip = container.querySelector(
      "[data-testid='channel-chip-email']",
    ) as HTMLElement;
    expect(emailChip).not.toBeNull();

    act(() => {
      emailChip.click();
    });

    // After removing email, only telegram chip should remain
    const emailChipAfter = container.querySelector("[data-testid='channel-chip-email']");
    expect(emailChipAfter).toBeNull();
    const telegramChipAfter = container.querySelector("[data-testid='channel-chip-telegram']");
    expect(telegramChipAfter).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// bu-mxtn2: Footer rollup band — renders event/session/cost counters
// ---------------------------------------------------------------------------

describe("TimelineTab — footer rollup band (bu-mxtn2)", () => {
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
    sessionStorage.clear();
  });

  it("renders the footer rollup band with events and sessions", () => {
    vi.mocked(useIngestionWindowRollup).mockReturnValue({
      data: {
        events: 123,
        sessions: 45,
        cost: null,
        window: { from: null, to: null },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionWindowRollup>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const rollup = container.querySelector("[data-testid='footer-rollup-band']");
    expect(rollup).not.toBeNull();
    // Should show formatted event and session counts
    expect(rollup!.textContent).toContain("123");
    expect(rollup!.textContent).toContain("45");
  });

  it("renders cost as em dash when cost is null", () => {
    vi.mocked(useIngestionWindowRollup).mockReturnValue({
      data: {
        events: 10,
        sessions: 2,
        cost: null,
        window: { from: null, to: null },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionWindowRollup>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const rollup = container.querySelector("[data-testid='footer-rollup-band']");
    expect(rollup).not.toBeNull();
    // cost unavailable → em dash
    expect(rollup!.textContent).toContain("—");
  });

  it("keeps unknown session cost coverage visible beside a null subtotal", () => {
    vi.mocked(useIngestionWindowRollup).mockReturnValue({
      data: {
        events: 10,
        sessions: 2,
        cost: null,
        unpriced_session_count: 2,
        window: { from: null, to: null },
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionWindowRollup>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const rollup = container.querySelector("[data-testid='footer-rollup-band']");
    expect(rollup).not.toBeNull();
    expect(rollup!.textContent).toContain("—");
    expect(rollup!.textContent).toContain("2 unpriced");
  });

  it("renders loading state (ellipsis) when rollup is loading", () => {
    vi.mocked(useIngestionWindowRollup).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useIngestionWindowRollup>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const rollup = container.querySelector("[data-testid='footer-rollup-band']");
    expect(rollup).not.toBeNull();
    // Loading state renders "…" placeholders (3 cells × one each)
    expect(rollup!.textContent).toContain("…");
  });
});
