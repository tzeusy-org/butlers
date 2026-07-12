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

let mockStatus: "connecting" | "open" | "reconnecting" | "closed" = "open";

vi.mock("@/hooks/use-event-stream", () => ({
  useEventStream: () => ({ status: mockStatus, lastEventAt: null, disconnect: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  mockStatus = "open";
});

// Import AFTER mocks are in place.
import { EventBusProvider } from "@/lib/event-bus";
import { useBusAwarePollInterval } from "./use-bus-aware-poll-interval";
import { POLL_BUS_DOWN_FALLBACK_MS, POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

function wrapper({ children }: { children: ReactNode }) {
  return <EventBusProvider>{children}</EventBusProvider>;
}

describe("useBusAwarePollInterval", () => {
  it("returns POLL_BUS_RECONCILE_MS while the bus is connected (open)", () => {
    mockStatus = "open";
    const { result } = renderHook(() => useBusAwarePollInterval(), { wrapper });
    expect(result.current).toBe(POLL_BUS_RECONCILE_MS);
  });

  it("returns the default fallback while connecting (cold start)", () => {
    mockStatus = "connecting";
    const { result } = renderHook(() => useBusAwarePollInterval(), { wrapper });
    expect(result.current).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });

  it("returns the default fallback while reconnecting", () => {
    mockStatus = "reconnecting";
    const { result } = renderHook(() => useBusAwarePollInterval(), { wrapper });
    expect(result.current).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });

  it("returns the default fallback once closed", () => {
    mockStatus = "closed";
    const { result } = renderHook(() => useBusAwarePollInterval(), { wrapper });
    expect(result.current).toBe(POLL_BUS_DOWN_FALLBACK_MS);
  });

  it("honors a caller-supplied fallback while the bus is down", () => {
    mockStatus = "reconnecting";
    const { result } = renderHook(() => useBusAwarePollInterval(5_000), { wrapper });
    expect(result.current).toBe(5_000);
  });

  it("ignores the caller-supplied fallback while the bus is connected", () => {
    mockStatus = "open";
    const { result } = renderHook(() => useBusAwarePollInterval(5_000), { wrapper });
    expect(result.current).toBe(POLL_BUS_RECONCILE_MS);
  });

  it("throws when used outside an EventBusProvider (wiring-bug guard)", () => {
    expect(() => renderHook(() => useBusAwarePollInterval())).toThrow(/EventBusProvider/);
  });
});
