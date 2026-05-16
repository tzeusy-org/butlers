// @vitest-environment jsdom
/**
 * Vitest tests for useSpendStream (§5.3)
 *
 * Strategy: mock the WebSocket constructor so we can control the message
 * flow without a real server. Tests verify that:
 *
 * 1. Snapshot messages populate `events` on connect.
 * 2. Individual "call" events are appended to `events`.
 * 3. `streamedCostUsd` accumulates correctly.
 * 4. "ping" messages are ignored (no state change).
 * 5. `disabled=true` prevents connection.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useSpendStream, type SpendCallEvent } from "./use-spend-stream"

// ---------------------------------------------------------------------------
// WebSocket mock
// ---------------------------------------------------------------------------

interface MockWsInstance {
  onopen: ((ev: Event) => void) | null
  onmessage: ((ev: MessageEvent) => void) | null
  onclose: ((ev: CloseEvent) => void) | null
  onerror: ((ev: Event) => void) | null
  close: ReturnType<typeof vi.fn>
  /** Trigger onopen from the test. */
  simulateOpen: () => void
  /** Trigger onmessage with a JSON payload from the test. */
  simulateMessage: (data: unknown) => void
  /** Trigger onclose from the test. */
  simulateClose: () => void
}

let lastWsInstance: MockWsInstance | null = null

class MockWebSocket implements MockWsInstance {
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  close = vi.fn(() => {
    // Intentional close — hook sets onclose=null before calling close()
    // so this should not trigger reconnect.
    if (this.onclose) {
      this.onclose({ type: "close" } as CloseEvent)
    }
  })

  constructor(_url: string) {
    lastWsInstance = this
  }

  simulateOpen() {
    this.onopen?.({ type: "open" } as Event)
  }

  simulateMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data), type: "message" } as MessageEvent)
  }

  simulateClose() {
    this.onclose?.({ type: "close" } as CloseEvent)
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeCallEvent(overrides: Partial<SpendCallEvent> = {}): SpendCallEvent {
  return {
    kind: "call",
    ts: 1_700_000_000.0,
    butler: "home",
    model: "claude-sonnet",
    tokens_in: 1000,
    tokens_out: 500,
    cost_usd: 0.00003,
    session_id: "sess-1",
    extra: {},
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe("useSpendStream", () => {
  beforeEach(() => {
    lastWsInstance = null
    vi.stubGlobal("WebSocket", MockWebSocket)
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it("starts in 'connecting' status", () => {
    const { result } = renderHook(() => useSpendStream())
    expect(result.current.status).toBe("connecting")
    expect(result.current.events).toHaveLength(0)
    expect(result.current.streamedCostUsd).toBe(0)
  })

  it("transitions to 'open' when WS connects", () => {
    const { result } = renderHook(() => useSpendStream())
    act(() => {
      lastWsInstance!.simulateOpen()
    })
    expect(result.current.status).toBe("open")
  })

  it("populates events from snapshot message", () => {
    const { result } = renderHook(() => useSpendStream())
    const snapEvent = makeCallEvent({ cost_usd: 0.001 })

    act(() => {
      lastWsInstance!.simulateOpen()
      lastWsInstance!.simulateMessage({ kind: "snapshot", events: [snapEvent] })
    })

    expect(result.current.events).toHaveLength(1)
    expect(result.current.events[0].cost_usd).toBe(0.001)
    expect(result.current.streamedCostUsd).toBeCloseTo(0.001)
  })

  it("appends individual call events", () => {
    const { result } = renderHook(() => useSpendStream())

    act(() => {
      lastWsInstance!.simulateOpen()
      // Empty snapshot first
      lastWsInstance!.simulateMessage({ kind: "snapshot", events: [] })
      // Then two live events
      lastWsInstance!.simulateMessage(makeCallEvent({ cost_usd: 0.001 }))
      lastWsInstance!.simulateMessage(makeCallEvent({ cost_usd: 0.002 }))
    })

    expect(result.current.events).toHaveLength(2)
    expect(result.current.streamedCostUsd).toBeCloseTo(0.003)
  })

  it("ignores ping messages without changing state", () => {
    const { result } = renderHook(() => useSpendStream())

    act(() => {
      lastWsInstance!.simulateOpen()
      lastWsInstance!.simulateMessage({ kind: "snapshot", events: [] })
    })

    const eventsBefore = result.current.events

    act(() => {
      lastWsInstance!.simulateMessage({ kind: "ping" })
    })

    // Reference equality — state must not have changed
    expect(result.current.events).toBe(eventsBefore)
  })

  it("does not connect when disabled=true", () => {
    renderHook(() => useSpendStream({ disabled: true }))
    expect(lastWsInstance).toBeNull()
  })

  it("respects maxEvents cap", () => {
    const { result } = renderHook(() => useSpendStream({ maxEvents: 3 }))

    act(() => {
      lastWsInstance!.simulateOpen()
      lastWsInstance!.simulateMessage({ kind: "snapshot", events: [] })
      for (let i = 0; i < 5; i++) {
        lastWsInstance!.simulateMessage(makeCallEvent({ cost_usd: 0.001 * i }))
      }
    })

    expect(result.current.events).toHaveLength(3)
  })

  it("transitions to 'closed' on disconnect", () => {
    const { result } = renderHook(() => useSpendStream())

    act(() => {
      lastWsInstance!.simulateOpen()
    })

    expect(result.current.status).toBe("open")

    const prevInstance = lastWsInstance!

    act(() => {
      prevInstance.simulateClose()
    })

    expect(result.current.status).toBe("closed")
  })
})
