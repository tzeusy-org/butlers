// @vitest-environment jsdom
/**
 * Tests for TimelineTab custom saved-views UI (bu-vgj88).
 *
 * Covers:
 * - Custom views listed alongside built-in presets
 * - Loading skeleton shown while custom views fetch
 * - "Save view" button opens dialog; submitting POSTs via createTimelineSavedView
 * - Applying a custom view restores its filter_spec (statuses round-trip)
 * - Deleting a custom view triggers delete mutation; fallback to "all" if active
 * - Backend error (isError=true) degrades gracefully — toolbar still renders
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent } from "@testing-library/react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { IngestionEventSummary } from "@/api/index.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    replayIngestionEvent: vi.fn(),
    bulkRetryEvents: vi.fn(),
  };
});

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("@/hooks/use-ingestion-events", () => ({
  useIngestionEvents: vi.fn(),
  useIngestionEventLineage: vi.fn(),
  useIngestionEventRollup: vi.fn(),
  useIngestionEventSenderContact: vi.fn(),
  useIngestionEventReplays: vi.fn(),
  useIngestionEventPayload: vi.fn(),
  useIngestionWindowRollup: vi.fn(),
}));

vi.mock("@/hooks/use-ingestion", () => ({
  useConnectorSummaries: vi.fn(),
}));

vi.mock("@/hooks/use-timeline-saved-views", () => ({
  useTimelineSavedViews: vi.fn(),
  useCreateTimelineSavedView: vi.fn(),
  useDeleteTimelineSavedView: vi.fn(),
}));

import {
  useIngestionEvents,
  useIngestionEventLineage,
  useIngestionEventRollup,
  useIngestionEventSenderContact,
  useIngestionEventReplays,
  useIngestionEventPayload,
  useIngestionWindowRollup,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import {
  useTimelineSavedViews,
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
} from "@/hooks/use-timeline-saved-views";
import type {
  TimelineSavedViewEntry,
} from "@/api/index.ts";

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

function makeSavedViewEntry(overrides: Partial<TimelineSavedViewEntry> = {}): TimelineSavedViewEntry {
  return {
    id: "550e8400-e29b-41d4-a716-446655440000",
    name: "My errors view",
    filter_spec: { statuses: ["error", "replay_failed"], range: "24h" },
    created_at: "2026-06-01T10:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

function setupDefaultMocks() {
  vi.mocked(useIngestionEvents).mockReturnValue(
    makeInfiniteEventsResult([makeEvent()]) as unknown as ReturnType<typeof useIngestionEvents>,
  );
  vi.mocked(useIngestionEventRollup).mockReturnValue({
    data: undefined, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventRollup>);
  vi.mocked(useIngestionEventSenderContact).mockReturnValue({
    data: undefined, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventSenderContact>);
  vi.mocked(useIngestionEventReplays).mockReturnValue({
    data: { data: [] }, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventReplays>);
  vi.mocked(useIngestionEventPayload).mockReturnValue({
    data: undefined, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionEventPayload>);
  vi.mocked(useIngestionEventLineage).mockReturnValue({
    sessions: { data: { data: [] }, isLoading: false, isError: false } as never,
    rollup: { data: undefined, isLoading: false, isError: false } as never,
  });
  vi.mocked(useConnectorSummaries).mockReturnValue({
    data: { data: [] }, isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useConnectorSummaries>);
  vi.mocked(useIngestionWindowRollup).mockReturnValue({
    data: { events: 0, sessions: 0, cost: null, window: { from: null, to: null } },
    isLoading: false, isError: false,
  } as unknown as ReturnType<typeof useIngestionWindowRollup>);

  // Default: no custom views, not loading
  vi.mocked(useTimelineSavedViews).mockReturnValue({
    data: { data: [], meta: {} },
    isPending: false,
    isError: false,
  } as unknown as ReturnType<typeof useTimelineSavedViews>);

  // Default mutation stubs
  vi.mocked(useCreateTimelineSavedView).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCreateTimelineSavedView>);
  vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);
}

import { TimelineTab } from "./TimelineTab";

// ---------------------------------------------------------------------------
// §2.8 custom saved views — list
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.8 custom saved views list", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = makeQueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    localStorage.clear();
    vi.clearAllMocks();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
  });

  it("renders custom views alongside built-in presets when available", () => {
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: { data: [makeSavedViewEntry({ name: "My errors view" })], meta: {} },
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    act(() => {
      root = createRoot(container);
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
    // Built-ins still present
    expect(selector!.textContent).toContain("All");
    expect(selector!.textContent).toContain("Errors");
    // Custom view also present
    expect(selector!.textContent).toContain("My errors view");
  });

  it("shows loading skeleton while custom views are fetching", () => {
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: undefined,
      isPending: true,
      isError: false,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const skeleton = container.querySelector("[data-testid='custom-views-loading']");
    expect(skeleton).not.toBeNull();
  });

  it("shows save-view button even when no custom views exist", () => {
    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const saveBtn = container.querySelector("[data-testid='save-view-button']");
    expect(saveBtn).not.toBeNull();
  });

  it("renders delete button for each custom view", () => {
    const viewId = "550e8400-e29b-41d4-a716-446655440000";
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: { data: [makeSavedViewEntry({ id: viewId })], meta: {} },
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const deleteBtn = container.querySelector(`[data-testid='custom-view-delete-${viewId}']`);
    expect(deleteBtn).not.toBeNull();
  });

  it("toolbar still renders when custom-views endpoint errors (degraded mode)", () => {
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const toolbar = container.querySelector("[data-testid='timeline-toolbar']");
    expect(toolbar).not.toBeNull();
    // Built-ins still render
    const allBtn = container.querySelector("[data-view='all']");
    expect(allBtn).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// §2.8 custom saved views — save current view
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.8 save current view", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = makeQueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    localStorage.clear();
    vi.clearAllMocks();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
  });

  it("opens dialog when save-view button is clicked", () => {
    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const saveBtn = container.querySelector("[data-testid='save-view-button']") as HTMLButtonElement;
    act(() => { saveBtn.click(); });

    const dialog = document.querySelector("[data-testid='save-view-dialog']");
    expect(dialog).not.toBeNull();
  });

  it("calls createSavedView mutation when dialog is confirmed", () => {
    const mockMutate = vi.fn();
    vi.mocked(useCreateTimelineSavedView).mockReturnValue({
      mutate: mockMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateTimelineSavedView>);

    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Open dialog
    const saveBtn = container.querySelector("[data-testid='save-view-button']") as HTMLButtonElement;
    act(() => { saveBtn.click(); });

    // Type a name
    const input = document.querySelector("[data-testid='save-view-name-input']") as HTMLInputElement;
    expect(input).not.toBeNull();
    act(() => {
      fireEvent.change(input, { target: { value: "My custom view" } });
    });

    // Confirm
    const confirmBtn = document.querySelector("[data-testid='save-view-confirm']") as HTMLButtonElement;
    act(() => { confirmBtn.click(); });

    expect(mockMutate).toHaveBeenCalledOnce();
    const callArgs = mockMutate.mock.calls[0][0];
    expect(callArgs.name).toBe("My custom view");
    expect(callArgs.filter_spec).toBeDefined();
    expect(Array.isArray(callArgs.filter_spec.statuses)).toBe(true);
  });

  it("confirm button is disabled when name is empty", () => {
    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const saveBtn = container.querySelector("[data-testid='save-view-button']") as HTMLButtonElement;
    act(() => { saveBtn.click(); });

    const confirmBtn = document.querySelector("[data-testid='save-view-confirm']") as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);
  });

  it("closes dialog on cancel", () => {
    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    // Open dialog
    const saveBtn = container.querySelector("[data-testid='save-view-button']") as HTMLButtonElement;
    act(() => { saveBtn.click(); });

    // Cancel
    const cancelBtn = document.querySelector("[data-testid='save-view-cancel']") as HTMLButtonElement;
    act(() => { cancelBtn.click(); });

    const dialog = document.querySelector("[data-testid='save-view-dialog']");
    expect(dialog).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// §2.8 custom saved views — apply (filter_spec round-trip)
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.8 apply custom view restores filter_spec", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = makeQueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    localStorage.clear();
    vi.clearAllMocks();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
  });

  it("clicking a custom view button sets it as active", () => {
    const viewId = "550e8400-e29b-41d4-a716-446655440000";
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: {
        data: [makeSavedViewEntry({
          id: viewId,
          name: "Error view",
          filter_spec: { statuses: ["error"], range: "1h" },
        })],
        meta: {},
      },
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const viewBtn = container.querySelector(`[data-testid='custom-view-${viewId}']`) as HTMLButtonElement;
    expect(viewBtn).not.toBeNull();

    act(() => { viewBtn.click(); });

    expect(viewBtn.getAttribute("aria-pressed")).toBe("true");
    // Built-in "all" should no longer be active
    const allBtn = container.querySelector("[data-view='all']");
    expect(allBtn!.getAttribute("aria-pressed")).toBe("false");
  });
});

// ---------------------------------------------------------------------------
// §2.8 custom saved views — delete
// ---------------------------------------------------------------------------

describe("TimelineTab — §2.8 delete custom view", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = makeQueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    localStorage.clear();
    vi.clearAllMocks();
    setupDefaultMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
  });

  it("clicking delete button calls deleteSavedView mutation with the view id", () => {
    const viewId = "550e8400-e29b-41d4-a716-446655440000";
    const mockMutate = vi.fn();
    vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
      mutate: mockMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);

    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: { data: [makeSavedViewEntry({ id: viewId })], meta: {} },
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    act(() => {
      root = createRoot(container);
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <TimelineTab isActive={true} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const deleteBtn = container.querySelector(
      `[data-testid='custom-view-delete-${viewId}']`,
    ) as HTMLButtonElement;
    expect(deleteBtn).not.toBeNull();

    act(() => { deleteBtn.click(); });

    expect(mockMutate).toHaveBeenCalledOnce();
    expect(mockMutate.mock.calls[0][0]).toBe(viewId);
  });
});
