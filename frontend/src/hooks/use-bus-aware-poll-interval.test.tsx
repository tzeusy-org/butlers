// @vitest-environment jsdom
/**
 * Tests for useBusAwarePollInterval (bu-01r64.3).
 *
 * Strategy: mirrors lib/event-bus.test.tsx -- mock useEventStream (the real
 * WebSocket hook underlying EventBusProvider) so tests can control the
 * connection status directly, then render useBusAwarePollInterval under a
 * real EventBusProvider.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, cleanup } from "@testing-library/react";
import type { ReactNode } from "react";
import type { EventBusHealth } from "@/hooks/use-event-stream";

let mockStatus: "connecting" | "open" | "reconnecting" | "closed" = "open";
let mockHealth: EventBusHealth = "healthy";

vi.mock("@/hooks/use-event-stream", () => ({
  useEventStream: () => ({
    status: mockStatus,
    health: mockHealth,
    lastEventAt: null,
    disconnect: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
  mockStatus = "open";
  mockHealth = "healthy";
});

// Import AFTER mocks are in place.
import { EventBusProvider } from "@/lib/event-bus";
import { useBusAwarePollInterval } from "./use-bus-aware-poll-interval";
import { POLL_BUS_DOWN_FALLBACK_MS, POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

function wrapper({ children }: { children: ReactNode }) {
  return <EventBusProvider>{children}</EventBusProvider>;
}

describe("useBusAwarePollInterval", () => {
  it("returns POLL_BUS_RECONCILE_MS while the shared health is healthy", () => {
    mockStatus = "open";
    mockHealth = "healthy";
    const { result } = renderHook(() => useBusAwarePollInterval(), { wrapper });
    expect(result.current).toBe(POLL_BUS_RECONCILE_MS);
  });

  it.each(["late", "down"] as const)("returns the default fallback while health is %s", (health) => {
    mockStatus = "open";
    mockHealth = health;
    const { result } = renderHook(() => useBusAwarePollInterval(), { wrapper });
    expect(result.current).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });

  it("honors a caller-supplied fallback while the bus is down", () => {
    mockStatus = "open";
    mockHealth = "late";
    const { result } = renderHook(() => useBusAwarePollInterval(5_000), { wrapper });
    expect(result.current).toBe(5_000);
  });

  it("ignores the caller-supplied fallback while the bus is healthy", () => {
    mockStatus = "open";
    mockHealth = "healthy";
    const { result } = renderHook(() => useBusAwarePollInterval(5_000), { wrapper });
    expect(result.current).toBe(POLL_BUS_RECONCILE_MS);
  });

  it("throws when used outside an EventBusProvider (wiring-bug guard)", () => {
    expect(() => renderHook(() => useBusAwarePollInterval())).toThrow(/EventBusProvider/);
  });
});
