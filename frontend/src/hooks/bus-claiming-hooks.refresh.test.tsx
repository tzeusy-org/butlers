// @vitest-environment jsdom
/**
 * Refresh-path contract for the dashboard hooks that used to claim bus
 * coverage (bu-2jtfw.18).
 *
 * `useBusAwarePollInterval()` polls only at POLL_BUS_RECONCILE_MS (5 min) while
 * the bus is healthy. These hooks read data that no fleet event type
 * invalidates (no event-cache-registry mapping, no manifest entry), so with a
 * healthy bus they were stale for up to a full sweep. Each one now declares
 * its own refresh path -- a local poll for operational feeds, none for
 * configuration data -- per docs/frontend/data-access-and-refresh.md
 * "Freshness model".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockGetDispatchAttempts = vi.fn();
const mockGetFleetHaltAttention = vi.fn();
const mockListLedger = vi.fn();
const mockGetHealingEvents = vi.fn();
const mockListDeliveries = vi.fn();
const mockListSubscriptions = vi.fn();
const mockListContracts = vi.fn();



vi.mock("@/api/client.ts", () => ({
getDispatchAttempts: (...args: unknown[]) => mockGetDispatchAttempts(...args),
getFleetHaltAttention: (...args: unknown[]) => mockGetFleetHaltAttention(...args),
listDelegationLedger: (...args: unknown[]) => mockListLedger(...args),
getHealingDispatchEvents: (...args: unknown[]) => mockGetHealingEvents(...args),
listDomainEventDeliveries: (...args: unknown[]) => mockListDeliveries(...args),
listDomainEventSubscriptions: (...args: unknown[]) => mockListSubscriptions(...args),
listDomainEventContracts: (...args: unknown[]) => mockListContracts(...args),
listDomainEventReactions: vi.fn(),
replayDomainEventDelivery: vi.fn()
}));

// A healthy bus is exactly the case that used to leave these views stale.
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({
    status: "open",
    health: "healthy",
    lastEventAt: null,
    subscribe: vi.fn(),
  }),
}));

import { useDelegationLedger } from "./use-delegation";
import {
  useDomainEventContracts,
  useDomainEventDeliveries,
  useDomainEventSubscriptions,
} from "./use-domain-events";
import { useFleetHaltStatus } from "./use-fleet-halt";
import { useHealingDispatchEvents } from "./use-healing";

const EMPTY_PAGE = { data: [], meta: { total: 0, offset: 0, limit: 10, has_more: false } };

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const ONE_MINUTE_MS = 60_000;

beforeEach(() => {
  vi.useFakeTimers();
  for (const mock of [
    mockGetDispatchAttempts,
    mockListLedger,
    mockGetHealingEvents,
    mockListDeliveries,
    mockListSubscriptions,
    mockListContracts,
  ]) {
    mock.mockReset();
    mock.mockResolvedValue(EMPTY_PAGE);
  }
  mockGetFleetHaltAttention.mockReset();
  mockGetFleetHaltAttention.mockResolvedValue({ data: { available: true, episode: null } });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("polled hooks refresh within a minute with a healthy bus", () => {
  it("useFleetHaltStatus refetches its denial queries", async () => {
    renderHook(() => useFleetHaltStatus(), { wrapper: wrapper() });
    await settle();
    const before = mockGetDispatchAttempts.mock.calls.length;
    expect(before).toBeGreaterThan(0);
    await advance(ONE_MINUTE_MS);
    expect(mockGetDispatchAttempts.mock.calls.length).toBeGreaterThan(before);
  });

  it("useFleetHaltStatus refetches the durable attention episode", async () => {
    renderHook(() => useFleetHaltStatus(), { wrapper: wrapper() });
    await settle();
    expect(mockGetFleetHaltAttention).toHaveBeenCalledTimes(1);
    await advance(ONE_MINUTE_MS);
    expect(mockGetFleetHaltAttention.mock.calls.length).toBeGreaterThan(1);
  });

  it("useDomainEventDeliveries refetches", async () => {
    renderHook(() => useDomainEventDeliveries(), { wrapper: wrapper() });
    await settle();
    expect(mockListDeliveries).toHaveBeenCalledTimes(1);
    await advance(ONE_MINUTE_MS);
    expect(mockListDeliveries.mock.calls.length).toBeGreaterThan(1);
  });

  it("useDelegationLedger refetches", async () => {
    renderHook(() => useDelegationLedger(), { wrapper: wrapper() });
    await settle();
    expect(mockListLedger).toHaveBeenCalledTimes(1);
    await advance(ONE_MINUTE_MS);
    expect(mockListLedger.mock.calls.length).toBeGreaterThan(1);
  });

  it("useHealingDispatchEvents refetches", async () => {
    renderHook(() => useHealingDispatchEvents(), { wrapper: wrapper() });
    await settle();
    expect(mockGetHealingEvents).toHaveBeenCalledTimes(1);
    await advance(ONE_MINUTE_MS);
    expect(mockGetHealingEvents.mock.calls.length).toBeGreaterThan(1);
  });
});

describe("configuration data has no interval", () => {
  const TEN_MINUTES_MS = 10 * ONE_MINUTE_MS;

  it("useDomainEventSubscriptions does not poll", async () => {
    renderHook(() => useDomainEventSubscriptions(), { wrapper: wrapper() });
    await settle();
    await advance(TEN_MINUTES_MS);
    expect(mockListSubscriptions).toHaveBeenCalledTimes(1);
  });

  it("useDomainEventContracts does not poll", async () => {
    renderHook(() => useDomainEventContracts(), { wrapper: wrapper() });
    await settle();
    await advance(TEN_MINUTES_MS);
    expect(mockListContracts).toHaveBeenCalledTimes(1);
  });
});
