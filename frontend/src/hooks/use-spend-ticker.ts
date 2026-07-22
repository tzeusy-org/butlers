/**
 * useSpendTicker — monotonic cumulative live-spend counter (bu-qvnce.14
 * slice 2).
 *
 * Replaces the bespoke useSpendStream WebSocket (deleted -- it opened its
 * own /api/spend/stream connection duplicating the shared fleet event bus,
 * which already carries every spend call event fanned onto the "spend" type
 * -- see emit_spend_event's `emit_event("spend", event)` call in
 * src/butlers/api/routers/spend.py). This hook subscribes to that shared
 * bus via EventBusProvider instead of opening a second socket.
 *
 * Semantics are unchanged from useSpendStream: only LIVE call events
 * increment the counter. Events replayed from the bus's ring-buffer snapshot
 * (sent on initial connect and on every reconnect) are excluded because
 * those costs are already reflected in the server-fetched MTD baseline the
 * caller (SpendPage) holds -- counting them again would double-count
 * exactly the bug bu-qvnce.2 fixed for the polled baseline path.
 *
 * Cache invalidation for cost-summary/daily-costs/top-sessions is handled
 * separately and globally by event-cache-registry.ts's spendPatch -- this
 * hook exists purely to drive the page's live ticker number.
 */
import { useState } from "react";

import { useBusEvent } from "@/lib/event-bus";
import type { FleetEvent } from "@/hooks/event-cache-registry";

/** A live call whose configured price is absent, not a known zero-cost call. */
export interface LiveUnpricedSpendEvent {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  cache_creation_tokens: number;
}

export interface UseSpendTickerResult {
  /** Cumulative live spend (USD) received since this hook mounted. Never
   *  resets on its own -- SpendPage pins a baseline against it when a fresh
   *  server-fetched forecast lands. */
  streamedCostUsd: number;
  /** Explicitly unpriced live calls stay separate from numeric spend so a
   *  missing price cannot become a zero-dollar total. */
  streamedUnpricedEvents: LiveUnpricedSpendEvent[];
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function asTokenCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, value) : 0;
}

function unpricedEvent(data: Record<string, unknown>): LiveUnpricedSpendEvent {
  return {
    model: typeof data.model === "string" && data.model ? data.model : "unknown",
    input_tokens: asTokenCount(data.tokens_in),
    output_tokens: asTokenCount(data.tokens_out),
    cached_input_tokens: asTokenCount(data.tokens_cached),
    cache_creation_tokens: asTokenCount(data.tokens_cache_write),
  };
}

export function useSpendTicker(): UseSpendTickerResult {
  const [streamedCostUsd, setStreamedCostUsd] = useState(0);
  const [streamedUnpricedEvents, setStreamedUnpricedEvents] = useState<LiveUnpricedSpendEvent[]>([]);

  useBusEvent("spend", (event: FleetEvent, meta) => {
    if (meta.replayed) return; // snapshot replay -- already in the baseline
    if (event.data.kind !== "call") return; // ignore non-call spend payloads
    if (event.data.cost_usd === null) {
      setStreamedUnpricedEvents((previous) => [...previous, unpricedEvent(event.data)]);
      return;
    }
    setStreamedCostUsd((previous) => previous + asNumber(event.data.cost_usd));
  });

  return { streamedCostUsd, streamedUnpricedEvents };
}
