// @vitest-environment jsdom
/**
 * TimelineTab — axe-core accessibility test, run against the REAL ingestion
 * ledger component (bu-86c4c.16).
 *
 * JARVIS audit move 11 names TimelineTab.tsx's LedgerRow explicitly as the
 * densest, most critical unsemantic-div offender in the whole dashboard
 * (role-less click-handler div, Enter-only activation). This suite exercises
 * the REAL component — mocked at the data-hook layer only, the same way
 * TimelineTab.test.tsx already does — through empty, populated, and
 * row-expanded (drawer open) states.
 *
 * Colour-contrast is disabled because jsdom cannot compute computed styles;
 * that gap is covered separately by src/lib/contrast.test.ts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe, toHaveNoViolations } from "jest-axe";

import type { IngestionEventSummary } from "@/api/index.ts";

expect.extend(toHaveNoViolations);

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Mocks — same pattern as TimelineTab.test.tsx
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
  useIngestionWindowRollup,
  useIngestionEventsHistogram,
} from "@/hooks/use-ingestion-events";
import { useConnectorSummaries } from "@/hooks/use-ingestion";
import { TimelineTab } from "./TimelineTab";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

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
    cost_usd: 0.01,
    tokens_in: null,
    tokens_out: null,
    session_count: 0,
    sessions: [],
    sender_display: "Alice",
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
    refetch: vi.fn(),
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

  vi.mocked(useIngestionEventLineage).mockReturnValue({
    sessions: { data: { data: [] }, isLoading: false, isError: false } as never,
    rollup: { data: undefined, isLoading: false, isError: false } as never,
  });

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

  vi.mocked(useIngestionEventsHistogram).mockReturnValue({
    data: { buckets: [], bucket: "1m" },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIngestionEventsHistogram>);
}

function renderTimeline(initialUrl = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <TimelineTab isActive defaultStatuses={["ingested", "filtered", "error"]} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function checkA11y(container: HTMLElement): Promise<void> {
  const results = await axe(container, {
    rules: {
      "color-contrast": { enabled: false },
    },
  });
  expect(results).toHaveNoViolations();
}

beforeEach(() => {
  setupDefaultMocks();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Story 1: Empty ledger
// ---------------------------------------------------------------------------

describe("a11y (real component): Empty ledger", () => {
  it("has zero axe violations", async () => {
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult([]) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    const { container } = renderTimeline();
    await checkA11y(container);
  });
});

// ---------------------------------------------------------------------------
// Story 2: Populated ledger — mixed statuses
// ---------------------------------------------------------------------------

describe("a11y (real component): Populated ledger", () => {
  it("has zero axe violations", async () => {
    const events = [
      makeEvent({ id: "evt-1", status: "ingested" }),
      makeEvent({ id: "evt-2", status: "filtered", filter_reason: "low priority sender" }),
      makeEvent({ id: "evt-3", status: "error", error_detail: "upstream timeout" }),
      makeEvent({ id: "evt-4", status: "replay_pending" }),
    ];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    const { container } = renderTimeline();
    await checkA11y(container);
  });
});

// ---------------------------------------------------------------------------
// Story 3: Row expanded (drawer open) — the DisclosureRow + EventDrawer pair
// ---------------------------------------------------------------------------

describe("a11y (real component): Row expanded, drawer open", () => {
  it("has zero axe violations", async () => {
    const events = [makeEvent({ id: "evt-1", status: "ingested" })];
    vi.mocked(useIngestionEvents).mockReturnValue(
      makeInfiniteEventsResult(events) as unknown as ReturnType<typeof useIngestionEvents>,
    );
    const user = userEvent.setup();
    const { container, getByTestId } = renderTimeline();
    await user.click(getByTestId("ledger-row"));
    await checkA11y(container);
  });
});
