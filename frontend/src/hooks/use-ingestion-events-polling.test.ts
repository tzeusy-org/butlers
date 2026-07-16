/**
 * Polling-policy coverage for the ingestion-event timeline hook.
 *
 * The ingestion event cache prefix is invalidated by the fleet event bus, so
 * its default poll is a reconciliation safety net. Explicit caller overrides
 * remain available for focused views that need different behaviour.
 */

import { useInfiniteQuery } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useInfiniteQuery: vi.fn((options: unknown) => options),
  };
});

let mockStatus: "connecting" | "open" | "reconnecting" | "closed" = "open";
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: mockStatus, lastEventAt: null, subscribe: vi.fn() }),
}));

vi.mock("@/api/index.ts", () => ({
  listIngestionEvents: vi.fn(),
  getIngestionEvent: vi.fn(),
  getIngestionEventSessions: vi.fn(),
  getIngestionEventRollup: vi.fn(),
  getIngestionWindowRollup: vi.fn(),
  getIngestionEventsHistogram: vi.fn(),
  getIngestionEventReplays: vi.fn(),
  getIngestionEventSenderContact: vi.fn(),
  getIngestionEventPayload: vi.fn(),
}));

import { useIngestionEvents } from "./use-ingestion-events";
import { POLL_BUS_DOWN_FALLBACK_MS, POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

const mockUseInfiniteQuery = vi.mocked(useInfiniteQuery);

function lastRefetchInterval(): unknown {
  const calls = mockUseInfiniteQuery.mock.calls;
  return (calls[calls.length - 1][0] as { refetchInterval?: unknown }).refetchInterval;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockStatus = "open";
});

describe("useIngestionEvents polling", () => {
  it("uses the reconciliation cadence while the ingestion event bus is open", () => {
    mockStatus = "open";

    useIngestionEvents();

    expect(lastRefetchInterval()).toBe(POLL_BUS_RECONCILE_MS);
  });

  it("falls back to the primary cadence while the event bus reconnects", () => {
    mockStatus = "reconnecting";

    useIngestionEvents();

    expect(lastRefetchInterval()).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });

  it("preserves an explicit caller override", () => {
    useIngestionEvents({}, { refetchInterval: false });

    expect(lastRefetchInterval()).toBe(false);
  });
});
