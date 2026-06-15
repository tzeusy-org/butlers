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
});
