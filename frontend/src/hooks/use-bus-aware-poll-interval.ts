/**
 * useBusAwarePollInterval — bus-aware refetchInterval for query hooks whose
 * cache key is invalidated by the fleet event bus (bu-01r64.3, final slice of
 * the cross-process event transport epic bu-01r64).
 *
 * The prior pattern (POLL_BUS_RECONCILE_MS used as a flat, always-on
 * refetchInterval — see poll-policy.ts) is honest while the bus is healthy
 * (the bus IS the primary update path; polling is only a reconciliation
 * sweep), but silently wrong the moment the socket drops: up to 5 minutes of
 * staleness with nothing telling the surface it has degraded.
 * use-notifications.ts had it worse — no refetchInterval at all, so a dead
 * socket meant infinite staleness.
 *
 * This hook reads the shared EventBusProvider's connection status
 * (useEventBus, see lib/event-bus.tsx) and switches cadence on it:
 *   - "open" (bus connected): POLL_BUS_RECONCILE_MS — the bus is doing the
 *     real-time work; polling is only a reconciliation sweep.
 *   - anything else ("connecting" | "reconnecting" | "closed"): `fallbackMs`
 *     — a fast fallback so the surface degrades to honest polling instead of
 *     silently going stale for the full reconciliation window.
 *
 * Must be called from a component/hook rendered under EventBusProvider (see
 * RootLayout.tsx, mounted once app-wide) — useEventBus() throws otherwise, by
 * design (a missing provider is a wiring bug, not a runtime condition this
 * hook should paper over).
 */
import { useEventBus } from "@/lib/event-bus";
import { POLL_BUS_DOWN_FALLBACK_MS, POLL_BUS_RECONCILE_MS } from "@/lib/poll-policy";

export function useBusAwarePollInterval(
  fallbackMs: number = POLL_BUS_DOWN_FALLBACK_MS,
): number {
  const { health, status } = useEventBus();
  // `status` remains a compatibility fallback for isolated legacy test
  // doubles; the provider's health is the production authority.
  return (health ?? (status === "open" ? "healthy" : "down")) === "healthy"
    ? POLL_BUS_RECONCILE_MS
    : fallbackMs;
}
