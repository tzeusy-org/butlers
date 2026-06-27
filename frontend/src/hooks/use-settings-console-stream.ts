/**
 * useSettingsConsoleStream — WS /api/settings/stream live ticker.
 *
 * Spec: openspec/specs/dashboard-settings-console/spec.md
 *   "Settings Console Live Stream" — the dashboard exposes
 *   `WS /api/settings/stream` emitting incremental updates that mirror the
 *   fields returned by `GET /api/settings/console`. The client MAY apply
 *   these events incrementally without re-fetching.
 *
 * The hook owns the live console state. It seeds from `initialData` (the
 * server-fetched `GET /api/settings/console` snapshot the page already holds)
 * and applies the typed WS events incrementally:
 *   - "snapshot"        → replace the whole console payload (full ConsoleData)
 *   - "header_delta"    → shallow-merge changed header counts
 *   - "attention_add"   → upsert an attention item (keyed by `kind`)
 *   - "attention_remove"→ drop the attention item with the given `kind`
 *
 * On (re)connect the server emits a fresh snapshot first, so a dropped socket
 * self-heals without the page polling. Reconnects use exponential back-off
 * capped at 30 s until the component unmounts.
 *
 * This replaces the prior reliance on a 30 s `refetchInterval` poll; the query
 * remains only as the initial-load / cold-start fetch.
 *
 * Mirrors the connection/back-off pattern of `use-spend-stream.ts` and
 * `use-approvals-stream.ts`.
 */

import { useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types (mirror SettingsConsolePage ConsoleData / GET /api/settings/console)
// ---------------------------------------------------------------------------

export interface AttentionItem {
  tone: "red" | "amber";
  kind: string;
  text: string;
  action_route: string;
}

export interface HeaderCounts {
  active_butlers: number;
  spend_mtd_usd: number;
  open_approvals: number;
  models_verified: number;
  models_total: number;
}

export interface ConsoleData {
  header_counts: HeaderCounts;
  attention: AttentionItem[];
  attention_truncated_count: number;
}

// Wire event shapes emitted by src/butlers/api/routers/settings_console.py
type SnapshotMessage = { type: "snapshot"; data: ConsoleData };
type HeaderDeltaMessage = { type: "header_delta"; data: Partial<HeaderCounts> };
type AttentionAddMessage = { type: "attention_add"; data: AttentionItem };
type AttentionRemoveMessage = { type: "attention_remove"; data: { kind: string } };

type ConsoleStreamMessage =
  | SnapshotMessage
  | HeaderDeltaMessage
  | AttentionAddMessage
  | AttentionRemoveMessage;

/** Back-off config for reconnects. */
const BASE_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;

// ---------------------------------------------------------------------------
// WS URL builder (shared convention with the other stream hooks)
// ---------------------------------------------------------------------------

function buildWsUrl(apiKey?: string): string {
  const apiBase: string = (
    typeof import.meta !== "undefined" ? (import.meta.env?.VITE_API_URL ?? "/api") : "/api"
  ) as string;

  let wsBase: string;
  if (apiBase.startsWith("http://")) {
    wsBase = "ws://" + apiBase.slice("http://".length);
  } else if (apiBase.startsWith("https://")) {
    wsBase = "wss://" + apiBase.slice("https://".length);
  } else {
    const proto =
      typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
    const host = typeof window !== "undefined" ? window.location.host : "localhost";
    wsBase = `${proto}://${host}${apiBase}`;
  }

  const url = `${wsBase}/settings/stream`;
  return apiKey ? `${url}?api_key=${encodeURIComponent(apiKey)}` : url;
}

// ---------------------------------------------------------------------------
// Reducer — apply one wire event to the current console state
// ---------------------------------------------------------------------------

export function applyConsoleEvent(
  prev: ConsoleData | undefined,
  msg: ConsoleStreamMessage,
): ConsoleData | undefined {
  switch (msg.type) {
    case "snapshot":
      // Full replace — authoritative payload (also the reconnect resync).
      return msg.data;

    case "header_delta": {
      if (!prev) return prev;
      return {
        ...prev,
        header_counts: { ...prev.header_counts, ...msg.data },
      };
    }

    case "attention_add": {
      if (!prev) return prev;
      // Upsert by `kind` so repeated adds for the same kind do not duplicate.
      const others = prev.attention.filter((it) => it.kind !== msg.data.kind);
      return { ...prev, attention: [...others, msg.data] };
    }

    case "attention_remove": {
      if (!prev) return prev;
      return {
        ...prev,
        attention: prev.attention.filter((it) => it.kind !== msg.data.kind),
      };
    }

    default:
      return prev;
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface UseSettingsConsoleStreamOptions {
  /** Optional DASHBOARD_API_KEY for query-param auth (dev mode: undefined). */
  apiKey?: string;
  /** Disable the hook (no connection). Defaults to enabled. */
  enabled?: boolean;
}

export interface UseSettingsConsoleStreamResult {
  /**
   * Live console state. Starts `undefined` until the server sends its first
   * (full) "snapshot" on connect, then advances via header_delta /
   * attention_add / attention_remove events. Callers should fall back to their
   * own GET /api/settings/console fetch while this is `undefined`.
   */
  data: ConsoleData | undefined;
  /** Current connection state. */
  status: "connecting" | "open" | "closed";
}

export function useSettingsConsoleStream(
  options: UseSettingsConsoleStreamOptions = {},
): UseSettingsConsoleStreamResult {
  const { apiKey, enabled = true } = options;

  const [data, setData] = useState<ConsoleData | undefined>(undefined);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("closed");

  const wsRef = useRef<WebSocket | null>(null);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryDelayRef = useRef<number>(BASE_DELAY_MS);
  const unmountedRef = useRef(false);

  useEffect(() => {
    if (!enabled) return;

    unmountedRef.current = false;

    function connect() {
      if (unmountedRef.current) return;

      setStatus("connecting");
      const ws = new WebSocket(buildWsUrl(apiKey));
      wsRef.current = ws;

      ws.onopen = () => {
        if (unmountedRef.current) {
          ws.close();
          return;
        }
        retryDelayRef.current = BASE_DELAY_MS;
        setStatus("open");
      };

      ws.onmessage = (ev) => {
        if (unmountedRef.current) return;
        let msg: ConsoleStreamMessage;
        try {
          msg = JSON.parse(ev.data as string) as ConsoleStreamMessage;
        } catch {
          return;
        }
        setData((prev) => applyConsoleEvent(prev, msg));
      };

      ws.onclose = () => {
        if (unmountedRef.current) return;
        setStatus("closed");
        wsRef.current = null;
        const delay = retryDelayRef.current;
        retryDelayRef.current = Math.min(delay * 2, MAX_DELAY_MS);
        retryTimeoutRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // onclose fires next and handles reconnect.
      };
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (retryTimeoutRef.current !== null) {
        clearTimeout(retryTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional close
        wsRef.current.close();
        wsRef.current = null;
      }
      setStatus("closed");
    };
  }, [enabled, apiKey]);

  return { data, status };
}
