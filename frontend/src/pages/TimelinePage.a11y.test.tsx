// @vitest-environment jsdom
/**
 * TimelinePage — real-page axe coverage for the fleet timeline.
 *
 * The page owns the data-hook layer while TimelineLedger owns the rendered
 * stream, so rendering the real page exercises both surfaces together. The
 * fixtures cover each query outcome rather than replacing the ledger with a
 * test double.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe, toHaveNoViolations } from "jest-axe";

import type { TimelineEvent } from "@/api/types.ts";

expect.extend(toHaveNoViolations);

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

vi.mock("@/hooks/use-timeline-ledger", () => ({ useTimelineLedger: vi.fn() }));
vi.mock("@/hooks/use-timeline", () => ({
  useTimelineAttention: vi.fn(),
  useTimelineEvent: vi.fn(),
  useTimelineHistogram: vi.fn(),
}));
vi.mock("@/hooks/use-butlers", () => ({ useButlers: vi.fn() }));
vi.mock("@/hooks/use-timeline-saved-views", () => ({
  useTimelineSavedViews: vi.fn(),
  useCreateTimelineSavedView: vi.fn(),
  useDeleteTimelineSavedView: vi.fn(),
}));

import TimelinePage from "./TimelinePage";
import { useButlers } from "@/hooks/use-butlers";
import { useTimelineLedger } from "@/hooks/use-timeline-ledger";
import { useTimelineAttention, useTimelineEvent, useTimelineHistogram } from "@/hooks/use-timeline";
import {
  useCreateTimelineSavedView,
  useDeleteTimelineSavedView,
  useTimelineSavedViews,
} from "@/hooks/use-timeline-saved-views";

const CONTENDED_AXE_TIMEOUT_MS = 15_000;

type UseTimelineLedgerResult = ReturnType<typeof useTimelineLedger>;

function makeEvent(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    id: "timeline-session-1",
    type: "session",
    butler: "home",
    timestamp: "2026-07-16T14:32:00Z",
    summary: "Home session completed",
    is_heartbeat: false,
    data: {},
    ...overrides,
  };
}

function setLedger(partial: Partial<UseTimelineLedgerResult>): void {
  vi.mocked(useTimelineLedger).mockReturnValue({
    events: [],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    hasMore: false,
    loadMore: vi.fn(),
    loadMoreError: false,
    retryLoadMore: vi.fn(),
    isLoadingMore: false,
    pinned: true,
    newCount: 0,
    showNewEvents: vi.fn(),
    degradedSources: [],
    degradedButlers: [],
    heartbeatRollup: { ticks: 0, butlers: 0, failed: 0 },
    isLiveFeedDown: false,
    ...partial,
  } as unknown as UseTimelineLedgerResult);
}

function setSupportingHookMocks(): void {
  vi.mocked(useTimelineEvent).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTimelineEvent>);
  vi.mocked(useTimelineHistogram).mockReturnValue({
    data: {
      data: [],
      meta: {
        since: "2026-07-04T13:00:00Z",
        until: "2026-07-04T14:00:00Z",
        bucket_seconds: 60,
        availability: "complete",
        expected_sources: 0,
        healthy_sources: 0,
        degraded_sources: [],
        degraded_butlers: [],
      },
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTimelineHistogram>);
  vi.mocked(useTimelineAttention).mockReturnValue({
    data: {
      data: [],
      meta: {
        since: "2026-07-03T14:00:00Z",
        until: "2026-07-04T14:00:00Z",
        failed_sessions: 0,
        failed_notifications: 0,
        total: 0,
        has_more: false,
        availability: "complete",
        expected_sources: 0,
        healthy_sources: 0,
        degraded_sources: [],
        degraded_butlers: [],
      },
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTimelineAttention>);
  vi.mocked(useButlers).mockReturnValue({
    data: { data: [] },
  } as unknown as ReturnType<typeof useButlers>);
  vi.mocked(useTimelineSavedViews).mockReturnValue({
    data: { data: [] },
  } as unknown as ReturnType<typeof useTimelineSavedViews>);
  vi.mocked(useCreateTimelineSavedView).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCreateTimelineSavedView>);
  vi.mocked(useDeleteTimelineSavedView).mockReturnValue({
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useDeleteTimelineSavedView>);
}

async function checkA11y(
  partial: Partial<UseTimelineLedgerResult>,
  initialEntry = "/timeline",
): Promise<void> {
  setLedger(partial);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <TimelinePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const results = await axe(container, {
    rules: { "color-contrast": { enabled: false } },
  });
  expect(results).toHaveNoViolations();
}

beforeEach(() => {
  setSupportingHookMocks();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("a11y (real page): Timeline loading state", () => {
  it("has zero axe violations", { timeout: CONTENDED_AXE_TIMEOUT_MS }, async () => {
    await checkA11y({ isLoading: true });
  });
});

describe("a11y (real page): Timeline error state", () => {
  it("has zero axe violations", { timeout: CONTENDED_AXE_TIMEOUT_MS }, async () => {
    await checkA11y({ isError: true });
  });
});

describe("a11y (real page): Timeline empty state", () => {
  it("has zero axe violations", { timeout: CONTENDED_AXE_TIMEOUT_MS }, async () => {
    await checkA11y({ events: [] });
  });
});

describe("a11y (real page): Timeline populated state", () => {
  it("has zero axe violations", { timeout: CONTENDED_AXE_TIMEOUT_MS }, async () => {
    await checkA11y({
      events: [
        makeEvent(),
        makeEvent({
          id: "timeline-heartbeat-1",
          timestamp: "2026-07-16T14:31:00Z",
          is_heartbeat: true,
        }),
        makeEvent({
          id: "timeline-heartbeat-2",
          timestamp: "2026-07-16T14:30:00Z",
          is_heartbeat: true,
        }),
      ],
    });
  });
});

describe("a11y (real page): Timeline partial-source recovery states", () => {
  it("has zero axe violations", { timeout: CONTENDED_AXE_TIMEOUT_MS }, async () => {
    vi.mocked(useButlers).mockReturnValue({
      data: undefined,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useButlers>);
    vi.mocked(useTimelineSavedViews).mockReturnValue({
      data: undefined,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTimelineSavedViews>);

    await checkA11y({
      events: [makeEvent()],
      degradedSources: ["sessions"],
      degradedButlers: ["home"],
      hasMore: true,
      loadMoreError: true,
      retryLoadMore: vi.fn(),
    });
  });
});

describe("a11y (real page): Timeline trace scope", () => {
  it("has zero axe violations", { timeout: CONTENDED_AXE_TIMEOUT_MS }, async () => {
    await checkA11y({}, "/timeline?trace=trace-001");
  });
});
