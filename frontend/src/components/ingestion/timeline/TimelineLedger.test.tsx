// @vitest-environment jsdom
/**
 * Unit tests for the Timeline ledger and drawer (bu-y25mj.4).
 *
 * Covers:
 * - Hour-grouping: events split into correct hour buckets
 * - HourFlameStrip: renders per-minute density bars
 * - deriveMinuteCounts: correct minute bucket counts
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
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import { TimelineTab } from "../TimelineTab";
import { deriveMinuteCounts } from "./deriveMinuteCounts";
import { HourFlameStrip } from "./HourFlameStrip";

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
}

// ---------------------------------------------------------------------------
// deriveMinuteCounts — unit tests (pure function)
// ---------------------------------------------------------------------------

describe("deriveMinuteCounts", () => {
  it("returns 60 zeros for empty input", () => {
    const result = deriveMinuteCounts([], "2026-05-17T14:00:00Z");
    expect(result).toHaveLength(60);
    expect(result.every((v) => v === 0)).toBe(true);
  });

  it("counts a single timestamp in the correct minute bucket", () => {
    // Event at 14:05:30 → minute 5
    const result = deriveMinuteCounts(
      ["2026-05-17T14:05:30Z"],
      "2026-05-17T14:00:00Z",
    );
    expect(result[5]).toBe(1);
    expect(result.filter((v) => v > 0)).toHaveLength(1);
  });

  it("groups multiple timestamps into correct minute buckets", () => {
    const result = deriveMinuteCounts(
      [
        "2026-05-17T14:00:15Z", // minute 0
        "2026-05-17T14:00:45Z", // minute 0
        "2026-05-17T14:01:00Z", // minute 1
        "2026-05-17T14:59:59Z", // minute 59
      ],
      "2026-05-17T14:00:00Z",
    );
    expect(result[0]).toBe(2);
    expect(result[1]).toBe(1);
    expect(result[59]).toBe(1);
  });

  it("ignores timestamps outside the hour window", () => {
    const result = deriveMinuteCounts(
      [
        "2026-05-17T13:59:59Z", // before hour start
        "2026-05-17T15:00:00Z", // after hour end
      ],
      "2026-05-17T14:00:00Z",
    );
    expect(result.every((v) => v === 0)).toBe(true);
  });

  it("handles null and undefined timestamps gracefully", () => {
    const result = deriveMinuteCounts(
      [null, undefined, "2026-05-17T14:02:00Z"],
      "2026-05-17T14:00:00Z",
    );
    expect(result[2]).toBe(1);
  });

  it("returns zeros for invalid hourStart", () => {
    const result = deriveMinuteCounts(["2026-05-17T14:02:00Z"], "not-a-date");
    expect(result.every((v) => v === 0)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// HourFlameStrip — rendering tests
// ---------------------------------------------------------------------------

describe("HourFlameStrip", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("renders an SVG with 60 rect elements for 60 minute counts", () => {
    const counts = Array(60).fill(0).map((_, i) => i % 5);
    act(() => {
      root.render(<HourFlameStrip minuteCounts={counts} />);
    });
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    const rects = svg!.querySelectorAll("rect");
    expect(rects).toHaveLength(60);
  });

  it("pads short arrays to 60 bars", () => {
    act(() => {
      root.render(<HourFlameStrip minuteCounts={[5, 10]} />);
    });
    const rects = container.querySelectorAll("svg rect");
    expect(rects).toHaveLength(60);
  });

  it("renders zero bars with fill-border class", () => {
    act(() => {
      root.render(<HourFlameStrip minuteCounts={Array(60).fill(0)} />);
    });
    const rects = Array.from(container.querySelectorAll("svg rect"));
    // All bars should have fill-border (empty bars)
    expect(rects.every((r) => r.classList.contains("fill-border"))).toBe(true);
  });

  it("renders non-zero bars with fill-foreground/40 class", () => {
    const counts = Array(60).fill(1);
    act(() => {
      root.render(<HourFlameStrip minuteCounts={counts} />);
    });
    const rects = Array.from(container.querySelectorAll("svg rect"));
    expect(rects.every((r) => r.classList.contains("fill-foreground/40"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// TimelineTab — hour grouping
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
    // Both events under one group = "2 events" in header
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

  it("renders an HourFlameStrip SVG inside each hour group", () => {
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

    // Each hour group header should have an SVG flame strip
    const group = container.querySelector("[data-testid='hour-group']");
    expect(group).not.toBeNull();
    const svg = group!.querySelector("svg");
    expect(svg).not.toBeNull();
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
    expect(gatedEl!.textContent).toContain("elevated permission");
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
