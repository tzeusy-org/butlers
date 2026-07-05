/**
 * Named poll-policy tokens (bu-qvnce.14 slice 3).
 *
 * A bare numeric `refetchInterval` value hides intent: is this the PRIMARY
 * update path for this data, or a safety-net reconciliation sweep sitting
 * behind a live event source? Before this, both shapes were spelled the same
 * way (a raw `30_000` or `5 * 60_000` literal), so a reviewer had to go
 * cross-reference event-cache-registry.ts to tell them apart.
 *
 * `POLL_BUS_RECONCILE_MS` names the one token this codebase actually needs
 * today: "this query's cache key is ALSO invalidated by the fleet event bus
 * (see event-cache-registry.ts) — the interval below is a reconciliation
 * sweep for the rare case the bus is down, not the primary path." Fixed at
 * 5 minutes to match the pattern use-sessions.ts / use-approvals.ts /
 * use-spend.ts already established (the "blessed Approvals pattern" the
 * 2026-07-04 JARVIS pursuit doc cites).
 *
 * Not every refetchInterval in the app has been migrated onto a named token
 * yet — see the eslint.config.js `POLL_POLICY_FILES` comment for the exact
 * (currently small) set of files this is enforced on, and the bu-qvnce.14
 * worker report for the follow-up to broaden it.
 */
export const POLL_BUS_RECONCILE_MS = 5 * 60_000;
