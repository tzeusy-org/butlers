/**
 * useSettingsConsoleLive — live Settings Console state on the shared fleet
 * event bus (bu-3quv8, completes bu-qvnce.14 slice 2).
 *
 * Replaces the bespoke useSettingsConsoleStream WebSocket (deleted -- it
 * opened its own `/api/settings/stream` connection duplicating the shared
 * fleet event bus, which now carries the same header_delta / attention_add /
 * attention_remove events -- see `run_settings_console_delta_loop`'s
 * `emit_event(...)` calls in `src/butlers/api/routers/settings_console.py`).
 * This hook subscribes to that shared bus via EventBusProvider instead of
 * opening a second socket.
 *
 * Unlike the old hook, there is no bus-native "snapshot" message shaped like
 * `ConsoleData` to seed from on connect -- the unified bus's own "snapshot"
 * is a replay of raw ring-buffer events, not a settings-console-specific
 * payload. Instead this hook takes the page's own `GET /api/settings/console`
 * result as `initialData` and layers live deltas on top of it:
 *   - `initialData` changes (first load, or the page's periodic
 *     `POLL_BUS_RECONCILE_MS` reconciliation re-fetch) → full reseed.
 *   - "header_delta" / "attention_add" / "attention_remove" bus events
 *     (while live state exists) → incremental merge, exactly mirroring the
 *     old WS wire protocol's semantics.
 *
 * Replayed events (`meta.replayed`, sent on the shared bus's initial connect
 * and every reconnect) are always ignored here -- the reseed from
 * `initialData` already makes any missed or duplicated delta harmless
 * (state converges to the server's own aggregation on the next REST fetch
 * regardless of what the bus did or didn't deliver in between), so applying
 * a replayed delta on top of an already-fresh REST snapshot would only risk
 * re-introducing already-superseded state.
 */

import { useEffect, useState } from "react";

import { useBusEvent } from "@/lib/event-bus";
import type { FleetEvent } from "@/hooks/event-cache-registry";

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
  // Each field is null when its subsystem aggregation failed -- never a
  // confident 0/$0.00 (mirrors src/butlers/api/routers/settings_console.py
  // HeaderCounts).
  active_butlers: number | null;
  spend_mtd_usd: number | null;
  open_approvals: number | null;
  models_verified: number | null;
  models_total: number | null;
}

export interface ConsoleData {
  header_counts: HeaderCounts;
  attention: AttentionItem[];
  attention_truncated_count: number;
}

// ---------------------------------------------------------------------------
// Pure reducers -- one per bus event type
// ---------------------------------------------------------------------------

export function applyHeaderDelta(prev: ConsoleData, delta: Partial<HeaderCounts>): ConsoleData {
  return { ...prev, header_counts: { ...prev.header_counts, ...delta } };
}

/**
 * Upsert by `kind` so a repeated add for the same kind does not duplicate,
 * then re-sort red-before-amber to preserve the ordering contract
 * (openspec/specs/dashboard-settings-console/spec.md line 61: "items are
 * ordered with tone=red first, then tone=amber"). The initial REST snapshot
 * already comes pre-sorted from `_build_console_payload`, but a live add can
 * insert a red item after existing amber ones (or vice versa) -- a plain
 * append would leave the strip out of contract order until the next
 * POLL_BUS_RECONCILE_MS reseed.
 */
export function applyAttentionAdd(prev: ConsoleData, item: AttentionItem): ConsoleData {
  const others = prev.attention.filter((it) => it.kind !== item.kind);
  const merged = [...others, item];
  const sorted = [...merged].sort((a, b) => {
    if (a.tone === b.tone) return 0;
    return a.tone === "red" ? -1 : 1;
  });
  return { ...prev, attention: sorted };
}

export function applyAttentionRemove(prev: ConsoleData, kind: string): ConsoleData {
  return { ...prev, attention: prev.attention.filter((it) => it.kind !== kind) };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

function asPartialHeaderCounts(data: Record<string, unknown>): Partial<HeaderCounts> {
  return data as Partial<HeaderCounts>;
}

function asAttentionItem(data: Record<string, unknown>): AttentionItem {
  return data as unknown as AttentionItem;
}

function asAttentionKind(data: Record<string, unknown>): string | undefined {
  return typeof data.kind === "string" ? data.kind : undefined;
}

/**
 * Layer live header_delta/attention_add/attention_remove bus events on top
 * of `initialData` (the page's own `GET /api/settings/console` query result).
 * Returns `undefined` until `initialData` is first defined -- callers should
 * render their own loading state until then, same as before this port.
 */
export function useSettingsConsoleLive(initialData: ConsoleData | undefined): ConsoleData | undefined {
  const [liveData, setLiveData] = useState<ConsoleData | undefined>(initialData);

  // Reseed on every fresh REST snapshot (first load, or the page's
  // POLL_BUS_RECONCILE_MS reconciliation poll) -- this is what makes a
  // missed or replayed bus event harmless: state converges back to the
  // server's own aggregation on a fixed cadence regardless of what happened
  // on the bus in between.
  useEffect(() => {
    setLiveData(initialData);
  }, [initialData]);

  useBusEvent("header_delta", (event: FleetEvent, meta) => {
    if (meta.replayed) return;
    setLiveData((prev) => (prev ? applyHeaderDelta(prev, asPartialHeaderCounts(event.data)) : prev));
  });

  useBusEvent("attention_add", (event: FleetEvent, meta) => {
    if (meta.replayed) return;
    setLiveData((prev) => (prev ? applyAttentionAdd(prev, asAttentionItem(event.data)) : prev));
  });

  useBusEvent("attention_remove", (event: FleetEvent, meta) => {
    if (meta.replayed) return;
    const kind = asAttentionKind(event.data);
    setLiveData((prev) => (prev && kind ? applyAttentionRemove(prev, kind) : prev));
  });

  return liveData;
}
