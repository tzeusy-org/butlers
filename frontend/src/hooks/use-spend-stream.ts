/**
 * useSpendStream — §5.3
 *
 * Opens a WebSocket connection to /api/spend/stream and maintains an
 * up-to-date list of the most recent per-call cost events.  On connect,
 * the server emits a "snapshot" message with the last N events so the
 * client KPIs are immediately populated without waiting for the next call.
 *
 * Reconnects automatically on unexpected close (with exponential back-off
 * capped at 30 s) until the component unmounts.
 */

import { useEffect, useRef, useState } from "react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SpendCallEvent {
  kind: "call"
  ts: number
  butler: string
  model: string
  tokens_in: number
  tokens_out: number
  cost_usd: number
  session_id?: string
  extra?: Record<string, unknown>
}

type SpendSnapshotMessage = {
  kind: "snapshot"
  events: SpendCallEvent[]
}

type SpendPingMessage = {
  kind: "ping"
}

type SpendStreamMessage = SpendCallEvent | SpendSnapshotMessage | SpendPingMessage

/** Maximum events to keep in memory. */
const MAX_EVENTS = 100

/** Back-off config for reconnects. */
const BASE_DELAY_MS = 1_000
const MAX_DELAY_MS = 30_000

// ---------------------------------------------------------------------------
// WS URL builder
// ---------------------------------------------------------------------------

function buildWsUrl(path: string): string {
  const apiBase: string = import.meta.env.VITE_API_URL ?? ""
  // apiBase may be "/api" (relative) or "http://host/api" (absolute)
  if (apiBase.startsWith("http://") || apiBase.startsWith("https://")) {
    const wsBase = apiBase.replace(/^http/, "ws")
    return `${wsBase}${path}`
  }
  // Relative — derive from current origin
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
  const base = apiBase || "/api"
  return `${protocol}//${window.location.host}${base}${path}`
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UseSpendStreamOptions {
  /** Maximum recent events to keep in state (default: 100). */
  maxEvents?: number
  /**
   * When true the hook does not open a connection.  Useful for disabling
   * the stream in tests or when the user navigates away.
   */
  disabled?: boolean
}

export interface UseSpendStreamResult {
  /** Recent per-call cost events (newest last). */
  events: SpendCallEvent[]
  /** Current connection state. */
  status: "connecting" | "open" | "closed"
  /**
   * Monotonically increasing cumulative spend from live events received since
   * the hook mounted.  Snapshot events are excluded from this counter — only
   * real-time "call" events increment it.  Callers should use this to derive
   * the incremental spend since a known server-fetched MTD baseline.
   */
  streamedCostUsd: number
}

export function useSpendStream(options: UseSpendStreamOptions = {}): UseSpendStreamResult {
  const { maxEvents = MAX_EVENTS, disabled = false } = options

  const [events, setEvents] = useState<SpendCallEvent[]>([])
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("closed")
  // Monotonic cumulative counter for live "call" events only.  Stored as state
  // so that React re-renders are triggered only by actual new spend, not by
  // events leaving the sliding window.
  const [streamedCostUsd, setStreamedCostUsd] = useState<number>(0)

  const wsRef = useRef<WebSocket | null>(null)
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryDelayRef = useRef<number>(BASE_DELAY_MS)
  const unmountedRef = useRef(false)

  useEffect(() => {
    if (disabled) return

    unmountedRef.current = false

    function connect() {
      if (unmountedRef.current) return

      setStatus("connecting")
      const url = buildWsUrl("/spend/stream")
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (unmountedRef.current) {
          ws.close()
          return
        }
        retryDelayRef.current = BASE_DELAY_MS
        setStatus("open")
      }

      ws.onmessage = (ev) => {
        if (unmountedRef.current) return
        let msg: SpendStreamMessage
        try {
          msg = JSON.parse(ev.data as string) as SpendStreamMessage
        } catch {
          return
        }
        if (msg.kind === "snapshot") {
          // Snapshot populates the sliding window but does NOT increment the
          // cumulative counter — those costs are already captured in the
          // server-fetched MTD baseline the caller holds.
          setEvents((prev) => {
            const combined = [...prev, ...msg.events]
            return combined.slice(-maxEvents)
          })
        } else if (msg.kind === "call") {
          setEvents((prev) => {
            const next = [...prev, msg]
            return next.slice(-maxEvents)
          })
          // Increment monotonic counter for every live call event.
          setStreamedCostUsd((prev) => prev + msg.cost_usd)
        }
        // "ping" — ignore
      }

      ws.onclose = () => {
        if (unmountedRef.current) return
        setStatus("closed")
        wsRef.current = null
        // Schedule reconnect with exponential back-off
        const delay = retryDelayRef.current
        retryDelayRef.current = Math.min(delay * 2, MAX_DELAY_MS)
        retryTimeoutRef.current = setTimeout(connect, delay)
      }

      ws.onerror = () => {
        // onclose will fire next; handled there
      }
    }

    connect()

    return () => {
      unmountedRef.current = true
      if (retryTimeoutRef.current !== null) {
        clearTimeout(retryTimeoutRef.current)
      }
      if (wsRef.current) {
        wsRef.current.onclose = null // prevent reconnect on intentional close
        wsRef.current.close()
        wsRef.current = null
      }
      setStatus("closed")
    }
  }, [disabled, maxEvents])

  return { events, status, streamedCostUsd }
}
