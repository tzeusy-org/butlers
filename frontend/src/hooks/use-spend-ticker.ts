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

export interface UseSpendTickerResult {
  /** Cumulative live spend (USD) received since this hook mounted. Never
   *  resets on its own -- SpendPage pins a baseline against it when a fresh
   *  server-fetched forecast lands. */
  streamedCostUsd: number;
}

function asNumber(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

export function useSpendTicker(): UseSpendTickerResult {
  const [streamedCostUsd, setStreamedCostUsd] = useState(0);

  useBusEvent("spend", (event: FleetEvent, meta) => {
    if (meta.replayed) return; // snapshot replay -- already in the baseline
    if (event.data.kind !== "call") return; // ignore non-call spend payloads
    setStreamedCostUsd((prev) => prev + asNumber(event.data.cost_usd));
  });

  return { streamedCostUsd };
}
