// @vitest-environment jsdom

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

// Mock the hooks so we don't need a real API
vi.mock("@/hooks/use-ingestion-events", () => ({
  useIngestionEvents: vi.fn(),
  useIngestionEventLineage: vi.fn(),
  useIngestionEventRollup: vi.fn(),
}));

import { replayIngestionEvent } from "@/api/index.ts";
import { toast } from "sonner";
import {
  useIngestionEvents,
  useIngestionEventRollup,
} from "@/hooks/use-ingestion-events";

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
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// ActionCell tests — isolated, testing Replay button states
// ---------------------------------------------------------------------------

// We test ActionCell indirectly through TimelineTab since it's not exported.
// For direct ActionCell behavior, we'll import the TimelineTab and manipulate events.

import { TimelineTab } from "./TimelineTab";

describe("TimelineTab — StatusBadge rendering", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();

    vi.mocked(useIngestionEventRollup).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEventRollup>);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function render(events: IngestionEventSummary[]) {
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: { data: events, meta: { total: events.length, limit: 50, offset: 0 } },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);

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
    expect(btn!.textContent).toContain("Replay");
  });

  it("shows Replay button for error events", () => {
    render([makeEvent({ status: "error" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).not.toBeNull();
    expect(btn!.textContent).toContain("Replay");
  });

  it("shows Retry button for replay_failed events", () => {
    render([makeEvent({ status: "replay_failed" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    expect(btn).not.toBeNull();
    expect(btn!.textContent).toContain("Retry");
  });

  it("shows spinner (not Replay button) for replay_pending events", () => {
    render([makeEvent({ status: "replay_pending" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    const spinner = container.querySelector("[data-testid='replay-pending-spinner']");
    expect(btn).toBeNull();
    expect(spinner).not.toBeNull();
  });

  it("shows no action button for ingested events", () => {
    render([makeEvent({ status: "ingested" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    const spinner = container.querySelector("[data-testid='replay-pending-spinner']");
    expect(btn).toBeNull();
    expect(spinner).toBeNull();
  });

  it("shows no action button for replay_complete events", () => {
    render([makeEvent({ status: "replay_complete" })]);
    const btn = container.querySelector("[data-testid='replay-button']");
    const spinner = container.querySelector("[data-testid='replay-pending-spinner']");
    expect(btn).toBeNull();
    expect(spinner).toBeNull();
  });
});

describe("TimelineTab — Replay button interaction", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();

    vi.mocked(useIngestionEventRollup).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEventRollup>);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  function render(events: IngestionEventSummary[]) {
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: { data: events, meta: { total: events.length, limit: 50, offset: 0 } },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);

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
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: { data: [event], meta: { total: 1, limit: 50, offset: 0 } },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);

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
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: {
        data: [{ ...event, status: "replay_complete" }],
        meta: { total: 1, limit: 50, offset: 0 },
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);

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

describe("TimelineTab — Status filter", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();

    vi.mocked(useIngestionEventRollup).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEventRollup>);

    vi.mocked(useIngestionEvents).mockReturnValue({
      data: { data: [], meta: { total: 0, limit: 50, offset: 0 } },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);
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

describe("TimelineTab — filtered events non-expandable", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();

    vi.mocked(useIngestionEventRollup).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEventRollup>);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    vi.clearAllMocks();
  });

  it("filtered rows have no expand chevron", () => {
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: {
        data: [makeEvent({ status: "filtered" })],
        meta: { total: 1, limit: 50, offset: 0 },
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);

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
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: {
        data: [makeEvent({ status: "error" })],
        meta: { total: 1, limit: 50, offset: 0 },
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);

    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} defaultStatuses={["ingested", "filtered", "error", "replay_pending", "replay_complete", "replay_failed"]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Error events never spawned a session, so flamegraph would be empty — no expand chevron
    expect(container.textContent).not.toContain("▼");
    expect(container.textContent).not.toContain("▲");
  });

  it("ingested rows have expand chevron", () => {
    vi.mocked(useIngestionEvents).mockReturnValue({
      data: {
        data: [makeEvent({ status: "ingested" })],
        meta: { total: 1, limit: 50, offset: 0 },
      },
      isLoading: false,
      isError: false,
    } as ReturnType<typeof useIngestionEvents>);

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
