/**
 * Regression test for use-notifications.ts's refetchInterval (bu-01r64.3).
 *
 * Before this change, useNotifications/useNotificationStats/
 * useButlerNotifications passed NO refetchInterval at all -- a dropped fleet
 * event bus socket meant infinite staleness with no fallback poll. This
 * asserts all three now wire a bus-aware refetchInterval through to
 * useQuery.
 *
 * Strategy: mock @tanstack/react-query's useQuery (capture the options
 * object) and @/lib/event-bus's useEventBus (control bus health)
 * so the hooks can be called directly without a full React render tree --
 * same pattern as use-issues.test.ts / use-butlers-polling.test.ts.
 */

import { useQuery } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn((opts: unknown) => opts),
  };
});

let mockStatus: "connecting" | "open" | "reconnecting" | "closed" = "open";
let mockHealth: "healthy" | "late" | "down" = "healthy";
vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({
    status: mockStatus,
    health: mockHealth,
    lastEventAt: null,
    subscribe: vi.fn(),
  }),
}));

vi.mock("@/api/index.ts", () => ({
  getNotifications: vi.fn(() => Promise.resolve({ data: [] })),
  getNotificationStats: vi.fn(() => Promise.resolve({ data: {} })),
  getButlerNotifications: vi.fn(() => Promise.resolve({ data: [] })),
  markNotificationRead: vi.fn(),
  acknowledgeAllFailed: vi.fn(),
}));

import {
  useButlerNotifications,
  useNotifications,
  useNotificationStats,
} from "./use-notifications";
import { POLL_BUS_DOWN_FALLBACK_MS, POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

const mockUseQuery = vi.mocked(useQuery);

function lastRefetchInterval(): unknown {
  const calls = mockUseQuery.mock.calls;
  return (calls[calls.length - 1][0] as { refetchInterval?: unknown }).refetchInterval;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockStatus = "open";
  mockHealth = "healthy";
});

describe("useNotifications", () => {
  it("passes a refetchInterval to useQuery (previously omitted entirely)", () => {
    useNotifications();
    expect(lastRefetchInterval()).toBeDefined();
  });

  it("polls at POLL_BUS_RECONCILE_MS while the bus is connected", () => {
    mockStatus = "open";
    mockHealth = "healthy";
    useNotifications();
    expect(lastRefetchInterval()).toBe(POLL_BUS_RECONCILE_MS);
  });

  it("tightens to POLL_BUS_DOWN_FALLBACK_MS while the bus is down", () => {
    mockStatus = "reconnecting";
    mockHealth = "late";
    useNotifications();
    expect(lastRefetchInterval()).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });
});

describe("useNotificationStats", () => {
  it("passes a refetchInterval to useQuery (previously omitted entirely)", () => {
    useNotificationStats();
    expect(lastRefetchInterval()).toBeDefined();
  });

  it("tightens to POLL_BUS_DOWN_FALLBACK_MS while the bus is down", () => {
    mockStatus = "closed";
    mockHealth = "down";
    useNotificationStats();
    expect(lastRefetchInterval()).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });
});

describe("useButlerNotifications", () => {
  it("passes a refetchInterval to useQuery (previously omitted entirely)", () => {
    useButlerNotifications("general");
    expect(lastRefetchInterval()).toBeDefined();
  });

  it("tightens to POLL_BUS_DOWN_FALLBACK_MS while the bus is down", () => {
    mockStatus = "connecting";
    mockHealth = "late";
    useButlerNotifications("general");
    expect(lastRefetchInterval()).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });
});
